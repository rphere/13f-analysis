"""Under-the-radar picks for the Insights tab.

Goal: names that many tracked funds hold *relative to how big/famous they
are*, with strong returns across 6mo/1y/2y/5y -- not mega-cap tech everyone
already knows. Two halves:

- build_candidates() runs offline (build_performance.py): picks a candidate
  pool from the latest quarter's 13F data and prices 5 years of history for
  it. The top-100-by-value universe (security_returns) can't be reused: by
  construction it IS the famous set.
- score_picks() runs in the app on that small table, so the formula stays
  cheap and visible.
"""

import re

import pandas as pd

import performance
from ranking import rank_stocks

# The current Performance-tab universe (top 100 by aggregate 13F value) is
# excluded wholesale; this list is a safety net for famous names that might
# slip under that cut in some quarter.
WELL_KNOWN_TICKERS = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA", "AVGO", "TSM", "ORCL", "NFLX", "AMD",
    "PLTR", "CRM", "ADBE", "INTC", "QCOM", "IBM", "CSCO", "UBER", "SHOP", "ASML", "MU", "BRK-B", "BRK-A",
}

# ETFs: allowlist of sector / commodity / country / theme keywords (13F
# labels are abbreviated, hence the stems). Anything without one -- broad
# index, style/factor, bond, leveraged products -- isn't a "pick": funds hold
# those for beta, hedging or cash management. Order matters: first match is
# the ETF's theme, and two ETFs sharing a theme count as the same bet.
_ETF_THEMES = [
    ("Gold miners", r"GOLD MIN|GOLD MN|GOLD MINE|JUNIOR GOLD"), ("Silver miners", r"SILV MINE|GLOBAL X SILVER"),
    ("Gold", r"\bGOLD\b"), ("Silver", r"\bSILVER\b"), ("Copper", r"COPPER"), ("Uranium", r"URANI"),
    ("Metals & mining", r"\bMETAL|BAS MTL|\bMATER"), ("Natural resources", r"\bNAT\b|NATURAL RES"),
    ("Oil services", r"OIL SERV"), ("Oil & gas E&P", r"O&G|OIL GS|EXPL"), ("Energy", r"ENERG"),
    ("Utilities", r"\bUTIL"), ("Regional banks", r"REGBNK|REGIONAL BANK"), ("Banks", r"\bBANK"),
    ("Insurance", r"\bINS\b|INSURANCE"), ("BDCs", r"\bBDC"), ("Financials", r"\bFINL|FINANCIAL"),
    ("Biotech", r"BIOT"), ("Medical devices", r"MED DVC|MEDICAL DEV"), ("Health care", r"\bCARE\b|HEALTH"),
    ("Homebuilders", r"HOME CONS|HOMEBUILD|\bSP HOME"), ("Real estate", r"REAL ES|\bREIT|\bREAL\b"),
    ("Aerospace & defense", r"AER DEF|AEROSPACE|DEFENSE"), ("Airlines", r"JETS|AIRLINE"),
    ("Industrials", r"\bINDL|INDUSTRIAL"), ("Consumer staples", r"STAPL"),
    ("Consumer discretionary", r"DISCR|CONSUM DIS"), ("Communication services", r"COMM SRVC|\bSVC\b"),
    ("Semiconductors", r"SEMI"), ("Innovation", r"INNOVATION"),
    ("South Korea", r"KOR"), ("Japan", r"JAPAN"), ("Canada", r"\bCDA\b|CANADA"), ("India", r"INDIA"),
    ("Brazil", r"BRAZIL"), ("Mexico", r"MEXICO"), ("China", r"CHINA"), ("Saudi Arabia", r"SAUDI"),
    ("Spain", r"SPAIN"), ("Europe", r"EUROPE"), ("Latin America", r"LATN|LATIN"),
]
# Themes in one family are the same underlying bet for dedupe purposes.
_THEME_FAMILY = {
    "Energy": "Energy", "Oil services": "Energy", "Oil & gas E&P": "Energy", "Natural resources": "Energy",
    "Gold": "Precious metals", "Silver": "Precious metals", "Gold miners": "Precious metals",
    "Silver miners": "Precious metals", "Copper": "Metals", "Metals & mining": "Metals",
    "Banks": "Banks", "Regional banks": "Banks", "Biotech": "Health", "Medical devices": "Health",
    "Health care": "Health",
}
_ETF_THEME_PATTERNS = [(name, re.compile(pat)) for name, pat in _ETF_THEMES]
_ETF_EXCLUDE = re.compile(r"\b(2X|3X|BULL|BEAR|ULTRA\w*|INVERSE|SHORT|LEVERAGED)\b")

# A fund counts as a "conviction holder" when the position is at least this
# share of its long book. Quant/multi-strat books hold 2,400-7,000 names, so
# merely appearing in one says little; 0.05% of a $50B book is ~$25M.
CONVICTION_WEIGHT = 0.0005
MIN_CONVICTION_FUNDS = {"Stock": 2, "ETF": 0}  # ETF positions are rarely that large

MIN_FUNDS = {"Stock": 5, "ETF": 4}
POOL_SIZE = {"Stock": 300, "ETF": 80}
RETURN_COLS = list(performance.RETURN_WINDOWS)
WEIGHTS = {"performance": 0.45, "breadth": 0.35, "accumulation": 0.20}


def _breadth_signal(frame: pd.DataFrame) -> pd.Series:
    """Many (conviction) holders, small total dollar footprint. 13F dollar
    value roughly tracks market cap, so it stands in for size/fame."""
    holders = (frame["num_funds"].rank(pct=True) + frame["conviction_funds"].rank(pct=True)) / 2
    return holders - frame["total_value_usd"].rank(pct=True)


def etf_theme(label: str) -> str | None:
    label = (label or "").upper()
    if _ETF_EXCLUDE.search(label):
        return None
    return next((name for name, pat in _ETF_THEME_PATTERNS if pat.search(label)), None)


def build_candidates(df: pd.DataFrame, quarters: list[str], end: pd.Timestamp | None = None,
                     famous_keys: set[str] | None = None) -> pd.DataFrame:
    """Candidate pool for score_picks, priced over RETURN_WINDOWS.

    df: ranking.load_data(). famous_keys: group_keys to exclude; defaults to
    the top-100 Performance universe."""
    latest, oldest = quarters[-1], quarters[0]
    longs = df[df["put_call"] == ""]
    now = rank_stocks(longs, latest, "equal_weighted", top_n=None)
    if famous_keys is None:
        # Same cut as performance.security_universe (options included there).
        everything = rank_stocks(df, latest, "equal_weighted", top_n=None)
        famous_keys = set(everything.sort_values("total_value_usd", ascending=False).head(100)["group_key"])
    prev_funds = rank_stocks(longs, oldest, "equal_weighted", top_n=None).set_index("group_key")["num_funds"]

    q = longs[longs["quarter"] == latest]
    weight = q.groupby(["fund_name", "group_key"])["pct_of_portfolio"].sum()
    conviction = weight[weight >= CONVICTION_WEIGHT].groupby("group_key").size()

    pool = now[~now["group_key"].isin(famous_keys)].copy()
    pool["conviction_funds"] = pool["group_key"].map(conviction).fillna(0).astype(int)
    pool["theme"] = pool["issuer_label"].map(etf_theme).where(pool["asset_type"] == "ETF")
    pool = pool[(pool["num_funds"] >= pool["asset_type"].map(MIN_FUNDS))
                & (pool["conviction_funds"] >= pool["asset_type"].map(MIN_CONVICTION_FUNDS))
                & ((pool["asset_type"] == "Stock") | pool["theme"].notna())]

    # Pre-rank on breadth so only the most promising few hundred get priced.
    parts = []
    for asset_type, frame in pool.groupby("asset_type"):
        frame = frame.assign(_b=_breadth_signal(frame)).sort_values("_b", ascending=False)
        parts.append(frame.head(POOL_SIZE[asset_type]).drop(columns="_b"))
    pool = pd.concat(parts, ignore_index=True)

    # Most common CUSIP per group_key, so resolve_tickers can fall back to the
    # OpenFIGI CUSIP map for names whose 13F label doesn't match SEC's list.
    cusip = q.groupby("group_key")["cusip"].agg(lambda s: s.value_counts().idxmax())
    pool["cusip"] = pool["group_key"].map(cusip)
    pool = performance.resolve_tickers(pool)
    pool = pool[pool["ticker"].notna() & ~pool["ticker"].isin(WELL_KNOWN_TICKERS)]
    pool = pool.drop_duplicates("ticker")

    returns = performance.fetch_returns(pool["ticker"].tolist(), end=end)
    pool = pool.merge(returns, left_on="ticker", right_index=True, how="left")
    pool["num_funds_prev"] = pool["group_key"].map(prev_funds).fillna(0).astype(int)
    keep = ["group_key", "issuer_label", "asset_type", "ticker", "theme", "num_funds", "num_funds_prev", "conviction_funds",
            "total_value_usd", *RETURN_COLS]
    return pool[keep].reset_index(drop=True)


def _fmt_usd(v: float) -> str:
    return f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.0f}M"


def _why(row) -> str:
    delta = int(row["num_funds"] - row["num_funds_prev"])
    held = f"Held by {int(row['num_funds'])} funds" + (f" ({delta:+d} in a year)" if delta else "")
    rets = ", ".join(f"{row[c]:+.0f}% {lbl}" for c, lbl in
                     zip(RETURN_COLS, ["6-mo", "1-yr", "2-yr", "5-yr"]) if pd.notna(row[c]))
    return f"{held} with only {_fmt_usd(row['total_value_usd'])} of total 13F value; {rets}."


def score_picks(cands: pd.DataFrame, n: int = 3) -> dict[str, pd.DataFrame]:
    """Top-n per asset type -> {"Stock": frame, "ETF": frame}, each with a
    score, its three signals (0-1) and a human-readable 'why'.

    score = 0.45*performance + 0.35*breadth + 0.20*accumulation, each a
    percentile within the asset type:
    - performance: mean percentile rank over the 6mo/1y/2y/5y returns; needs
      >= 3 windows and positive 6mo AND 1y returns (still working now, not a
      spike that already crashed).
    - breadth: many (conviction) holders relative to total dollar footprint.
    - accumulation: funds added since the oldest tracked quarter."""
    out = {}
    for asset_type in ("Stock", "ETF"):
        c = cands[cands["asset_type"] == asset_type].copy()
        c = c[(c[RETURN_COLS].notna().sum(axis=1) >= 3) & (c["return_6mo_pct"] > 0) & (c["return_1yr_pct"] > 0)]
        if c.empty:
            out[asset_type] = c
            continue
        c["performance"] = c[RETURN_COLS].rank(pct=True).mean(axis=1)
        c["breadth"] = _breadth_signal(c).rank(pct=True)
        c["accumulation"] = (c["num_funds"] - c["num_funds_prev"]).rank(pct=True)
        c["score"] = sum(w * c[k] for k, w in WEIGHTS.items())
        c = c.sort_values("score", ascending=False)
        if asset_type == "ETF":
            # One ETF per theme family: two gold-miner (or energy) ETFs are one bet.
            c = c[~c["theme"].map(lambda t: _THEME_FAMILY.get(t, t)).duplicated()]
        top = c.head(n)
        out[asset_type] = top.assign(why=top.apply(_why, axis=1))
    return out
