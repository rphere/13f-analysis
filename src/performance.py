"""Resolve this dataset's most widely-held securities to tradable tickers and
fetch their price performance (6-month / 1-year % return).

No free, authoritative CUSIP->ticker mapping exists, so:
- Stocks are resolved by matching the cleaned issuer name against SEC's free
  company_tickers.json (same corporate-suffix cleaning applied to both sides,
  since SEC's 13F nameOfIssuer strips suffixes inconsistently with that file).
- ETFs are resolved via a small hand-curated CUSIP->ticker map, since that
  file covers ETPs poorly and 13F filers often report a generic sponsor/trust
  name (e.g. "ISHARES TR") shared by hundreds of distinct funds -- CUSIP is
  the only thing that actually identifies one. This project's universe is
  capped at the top ~100 most widely-held securities (see resolve_universe),
  so this list only needs to cover the handful of ETFs that actually land
  there -- verified against the real dataset's cusip/title_of_class, not
  guessed from the issuer name alone.

Anything that fails to resolve (e.g. SpaceX, which isn't publicly traded) is
omitted rather than guessed at.
"""

import re
import time
from pathlib import Path
from functools import lru_cache

import pandas as pd
import yfinance as yf

from ranking import clean_label
from sec_utils import sec_get

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

ETF_TICKER_BY_CUSIP = {
    "78462F103": "SPY",   # SPDR S&P 500 ETF Trust
    "46090E103": "QQQ",   # Invesco QQQ Trust
    "464287655": "IWM",   # iShares Russell 2000 ETF
    "464287200": "IVV",   # iShares Core S&P 500 ETF
    "92189F676": "SMH",   # VanEck Semiconductor ETF
    "78463V107": "GLD",   # SPDR Gold Shares
    "464287432": "TLT",   # iShares 20+ Year Treasury Bond ETF
    "464288513": "HYG",   # iShares iBoxx High Yield Corp Bond ETF
    "78467X109": "DIA",   # SPDR Dow Jones Industrial Average ETF Trust
    "81369Y605": "XLF",   # Financial Select Sector SPDR Fund
    "922908363": "VOO",   # Vanguard S&P 500 ETF
    "81369Y506": "XLE",   # Energy Select Sector SPDR Fund
    "464286772": "EWY",   # iShares MSCI South Korea ETF
    "464287515": "IGM",   # iShares Expanded Tech Sector ETF
    "25459W458": "SOXL",  # Direxion Daily Semiconductor Bull 3X Shares
}

# A handful of well-known large caps whose SEC 13F nameOfIssuer is abbreviated
# or restructured enough (e.g. "APPLIED MATLS", "TEXAS INSTRS") that exact
# name-matching against company_tickers.json misses them. Keyed by the
# dashboard's own cleaned issuer_label text. SpaceX is deliberately absent --
# it isn't publicly traded, so there's no ticker to map it to.
STOCK_TICKER_OVERRIDES = {
    "SPACE EXPLORATION TECHN": "SPCX",  # IPO'd 2026-06-12; not in company_tickers.json yet
    "TAIWAN SEMICONDUCTOR MANUFAC": "TSM",
    "APPLIED MATLS": "AMAT",
    "SEAGATE TECHNOLOGY HLDNGS PL": "STX",
    "ASML HLDG NV": "ASML",
    "BERKSHIRE HATHAWAY INC DEL": "BRK-B",
    "CROWDSTRIKE HLDGS": "CRWD",
    "COSTCO WHOLESALE": "COST",
    "WARNER BROS DISCOVERY": "WBD",
    "LUMENTUM HLDGS": "LITE",
    "MASTERCARD INCORPORATED": "MA",
    "WELLS FARGO": "WFC",
    "CAPITAL ONE FINL": "COF",
    "ARM HOLDINGS": "ARM",
    "TEXAS INSTRS": "TXN",
    "TRIPLE FLAG PRECIOUS METAL": "TFPM",
    "ROBINHOOD MKTS": "HOOD",
    "GENERAL MTRS": "GM",
    "CREDO TECHNOLOGY GROUP HOLDI": "CRDO",
    "QUALCOMM": "QCOM",
    "HEWLETT PACKARD ENTERPRISE C": "HPE",
    "CORNING": "GLW",
    "EXXON MOBIL": "XOM",
    "COMFORT SYS USA": "FIX",
    "CISCO SYS": "CSCO",
    "GE AEROSPACE": "GE",
    "INTERNATIONAL BUSINESS MACHS": "IBM",
    # Heavily-held names the abbreviation/prefix fallback can't reach
    # (word order, many same-name entities, or foreign filers missing
    # from company_tickers.json).
    "BROOKFIELD": "BN",
    "TAIWAN SEMICONDUCTOR MFG": "TSM",
    "ALIBABA GROUP HLDG": "BABA",
    "CRH": "CRH",
    "DISNEY WALT": "DIS",
    "FERGUSON ENTERPRISES": "FERG",
    "TELEPHONE & DATA SYS": "TDS",  # SEC lists a preferred (TDSPRV) first for this CIK
}


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", clean_label(s).upper())


@lru_cache(maxsize=1)
def fetch_company_tickers() -> dict:
    """name (normalized) -> ticker, from SEC's free bulk ticker list."""
    data = sec_get(COMPANY_TICKERS_URL).json()
    by_name = {}
    for v in data.values():
        by_name.setdefault(_norm(v["title"]), v["ticker"])
    return by_name


# SEC 13F nameOfIssuer abbreviations -> the word company_tickers.json spells
# out. Applied to both sides before the prefix fallback in _prefix_match.
_ABBREVIATIONS = {
    "HLDG": "HOLDING", "HLDGS": "HOLDINGS", "INTL": "INTERNATIONAL", "FINL": "FINANCIAL", "SVCS": "SERVICES",
    "SVC": "SERVICE", "MFG": "MANUFACTURING", "MATLS": "MATERIALS", "SYS": "SYSTEMS", 
    "MGMT": "MANAGEMENT", "INDS": "INDUSTRIES", "COS": "COMPANIES", "GRP": "GROUP", "AMER": "AMERICA",
    "NATL": "NATIONAL", "SOUTHN": "SOUTHERN", "AIRLS": "AIRLINES",
    "PPTYS": "PROPERTIES", "PPTY": "PROPERTY", "RES": "RESOURCES", "ENTMT": "ENTERTAINMENT", "CTLS": "CONTROLS",
    "TRANSN": "TRANSPORTATION", "RLTY": "REALTY", "APT": "APARTMENT", "CMNTYS": "COMMUNITIES", "MNG": "MINING",
    "PRODS": "PRODUCTS", "CHEMS": "CHEMICALS",
}
# Trailing noise words in 13F names that company_tickers.json doesn't carry.
_NOISE_WORDS = {"DEL", "NEW", "FORMERLY", "THE", "OF", "INC", "CORP", "CO", "LTD", "PLC", "LLC", "LP", "SA", "NV", "AG",
                "SE", "CORPORATION", "COMPANY", "LIMITED", "INCORPORATED"}
# company_tickers.json titles carry state-of-incorporation tags: "DANAHER CORP /DE/".
_STATE_TAG = re.compile(r"\s*[/\\][A-Z]{2,4}[/\\]?")


def _tokens(name: str) -> tuple[str, ...]:
    name = _STATE_TAG.sub(" ", (name or "").upper())
    words = re.sub(r"[^A-Z0-9 ]", " ", name.replace("&", " AND ")).split()
    # 13F nameOfIssuer is cut at 28 chars, leaving a stub of a suffix
    # ("INTERCONTINENTAL EXCHANGE IN", "MACOM ... HLDGS I") -- drop it.
    if len(words) > 1 and any(n.startswith(words[-1]) and n != words[-1] for n in _NOISE_WORDS):
        words = words[:-1]
    return tuple(_ABBREVIATIONS.get(w, w) for w in words if w not in _NOISE_WORDS)


@lru_cache(maxsize=1)
def _token_index() -> list[tuple[tuple[str, ...], str]]:
    """(title tokens, primary ticker), one entry per company (CIK). A company
    appears once per listed security (BAC, BAC-PB, BACRP, ...); SEC lists the
    primary common ticker first, so keep that -- otherwise every company with
    preferreds would look like an ambiguous multi-way match."""
    data = sec_get(COMPANY_TICKERS_URL).json()
    first_by_cik = {}
    for v in data.values():
        first_by_cik.setdefault(v["cik_str"], (_tokens(v["title"]), v["ticker"]))
    return list(first_by_cik.values())


@lru_cache(maxsize=None)
def _prefix_match(label: str) -> str | None:
    """Fallback for names exact matching misses (e.g. "UNION PAC" -> Union
    Pacific, "TAIWAN SEMICONDUCTOR MFG" -> TSM): every 13F token must be a
    prefix of the SEC title's token in the same position, same token count,
    and exactly ONE company may match -- an ambiguous match is left
    unresolved rather than guessed, since a wrong ticker silently gives a
    wrong return."""
    toks = _tokens(label)
    if not toks or sum(len(t) for t in toks) < 4:
        return None
    hits = {ticker for title, ticker in _token_index()
            if len(title) == len(toks) and all(t2.startswith(t1) for t1, t2 in zip(toks, title))}
    return hits.pop() if len(hits) == 1 else None


CUSIP_TICKERS_CSV = Path(__file__).resolve().parent.parent / "data" / "processed" / "cusip_tickers.csv"


def load_cusip_map() -> dict[str, str]:
    """CUSIP -> ticker from OpenFIGI (built by resolve_cusip_tickers.py). Empty if not built yet."""
    if not CUSIP_TICKERS_CSV.exists():
        return {}
    m = pd.read_csv(CUSIP_TICKERS_CSV, dtype=str).dropna(subset=["ticker"])
    return dict(zip(m["cusip"], m["ticker"]))


def resolve_tickers(universe: pd.DataFrame, use_cusip_map: bool = True) -> pd.DataFrame:
    """universe: rows with group_key, issuer_label, asset_type (see rank_stocks),
    optionally a cusip column. Returns universe with a 'ticker' column added;
    unresolved rows get ticker=None.

    Order: hand-curated maps, then exact name match, then the prefix
    fallback, then (if a CUSIP is available) the OpenFIGI CUSIP map. An ETF's
    group_key IS its CUSIP, so ETFs can always use the CUSIP map."""
    name_to_ticker = fetch_company_tickers()
    cusip_map = load_cusip_map() if use_cusip_map else {}
    tickers = []
    for row in universe.to_dict("records"):
        cusip = row.get("cusip") or (row["group_key"] if row["asset_type"] == "ETF" else None)
        if row["asset_type"] == "ETF":
            t = ETF_TICKER_BY_CUSIP.get(row["group_key"])
        else:
            label = row["issuer_label"]
            t = STOCK_TICKER_OVERRIDES.get(label) or name_to_ticker.get(_norm(label)) or _prefix_match(label)
        tickers.append(t or cusip_map.get(cusip))
    return universe.assign(ticker=tickers)


RETURN_WINDOWS = {
    "return_6mo_pct": pd.DateOffset(months=6),
    "return_1yr_pct": pd.DateOffset(years=1),
    "return_2yr_pct": pd.DateOffset(years=2),
    "return_5yr_pct": pd.DateOffset(years=5),
}


def download_closes(tickers: list[str], start, end, retries: int = 3) -> pd.DataFrame:
    """Daily adjusted closes, one column per ticker, from a batch yfinance call.

    Yahoo rate-limits large batches in bursts: seen in practice, a 2,400-
    ticker call came back with ~all columns empty, and re-requesting just
    those succeeded for every one. So all-empty columns are retried (with
    backoff) instead of being treated as "no data". If most columns are
    still empty after that, raise rather than return -- the callers cache
    for hours, and a mostly-empty frame would silently score everything on
    a handful of survivors until the cache expired.
    """
    tickers = list(dict.fromkeys(tickers))
    frames = [_download_chunk(tickers[i:i + DOWNLOAD_CHUNK], start, end, retries)
              for i in range(0, len(tickers), DOWNLOAD_CHUNK)]
    close = pd.concat(frames, axis=1).sort_index() if frames else pd.DataFrame()
    missing = [t for t in tickers if t not in close.columns]
    if missing and len(missing) <= MAX_SINGLE_RETRIES:
        # The batch endpoint returns NOTHING for some tickers the single-
        # ticker history endpoint still has -- notably delisted ones, e.g.
        # WBD (acquired, Oct 2026), whose final quote only shows up here.
        singles = {t: s for t in missing if not (s := _single_history(t, start, end)).empty}
        if singles:
            close = close.join(pd.DataFrame(singles), how="outer")
        missing = [t for t in tickers if t not in close.columns]
    if len(missing) > 0.5 * len(tickers):
        raise RuntimeError(f"price source returned no data for {len(missing)} of {len(tickers)} tickers "
                           "(likely rate-limited) -- try again in a few minutes")
    return close.reindex(columns=tickers)


# Pricing every 13F holding is ~8,000 tickers; one giant yfinance call is
# what triggers Yahoo's burst rate-limiting, so download in chunks.
DOWNLOAD_CHUNK = 500
MAX_SINGLE_RETRIES = 1500


def _single_history(ticker: str, start, end) -> pd.Series:
    # period= (not start/end): for a delisted ticker Yahoo answers an explicit
    # date range with "no price data found", but a period query still
    # returns its final quote(s). Trim to [start, end) afterwards.
    span_years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    period = next((p for p, yrs in (("1y", 1), ("2y", 2), ("5y", 5), ("10y", 10)) if span_years < yrs), "max")
    try:
        h = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    except Exception:  # noqa: BLE001 -- one bad ticker shouldn't sink the batch
        return pd.Series(dtype=float)
    if h.empty:
        return pd.Series(dtype=float)
    s = h["Close"]
    s.index = s.index.tz_localize(None).normalize()
    return s[(s.index >= pd.Timestamp(start)) & (s.index < pd.Timestamp(end))]


def _download_chunk(tickers: list[str], start, end, retries: int) -> pd.DataFrame:
    close = pd.DataFrame()
    pending = tickers
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(2 ** attempt)
        data = yf.download(pending, start=start, end=end, interval="1d", progress=False, auto_adjust=True, group_by="column")
        if data.empty:
            continue
        got = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data[["Close"]].rename(columns={"Close": pending[0]})
        got = got.reindex(columns=pending)
        filled = got.columns[got.notna().any()]
        close = got[filled] if close.empty else close.join(got[filled], how="outer")
        pending = [t for t in pending if t not in close.columns]
        if not pending:
            break
    return close


def fetch_returns(tickers: list[str], end: pd.Timestamp | None = None) -> pd.DataFrame:
    """One batch yfinance call -> DataFrame indexed by ticker with a return_*_pct
    column per RETURN_WINDOWS. Uses each ticker's actual date index (via
    .asof) rather than a fixed row offset, so a recently-IPO'd stock (e.g.
    Reddit, CoreWeave) correctly gets NaN for a 5-year return it has no
    history for, instead of a mislabeled "since IPO" number."""
    if not tickers:
        return pd.DataFrame(columns=list(RETURN_WINDOWS))

    # Explicit start/end (not period="5y") with a 2-week buffer: yfinance's
    # "5y" period boundary lands exactly one day short of today's actual
    # calendar 5-years-ago date, which made .asof() below return NaN for
    # EVERY ticker's 5-year return (the whole batch shares the same start
    # date, so they all missed the target by the same single day).
    # `end` is exclusive (yfinance semantics): pass today to get returns as of
    # yesterday's close.
    end = end if end is not None else pd.Timestamp.now(tz="UTC").tz_localize(None)
    start = end - pd.DateOffset(years=5, days=14)
    close = download_closes(tickers, start, end)

    latest = close.iloc[-1]
    now = close.index[-1]
    out = {}
    for col, offset in RETURN_WINDOWS.items():
        past = close.apply(lambda s: s.asof(now - offset))
        out[col] = (latest / past - 1) * 100
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# Fund-level returns of the 13F-disclosed long stock/ETF book only
# ---------------------------------------------------------------------------

FUND_RETURN_WINDOWS = {
    "return_6mo_pct": pd.DateOffset(months=6),
    "return_1yr_pct": pd.DateOffset(years=1),
}
BENCHMARK_TICKER = "SPY"
def _long_book(fund_rows: pd.DataFrame) -> pd.DataFrame:
    """Per quarter, the fund's long (non-option) positions, one row per CUSIP,
    weighted within the long book, with a resolved ticker. Every position is
    kept: this runs offline (build_performance.py), so there's no reason to
    trade coverage for speed. fund_returns sums weights by ticker, so share
    classes that resolve to the same ticker merge there."""
    longs = fund_rows[fund_rows["put_call"] == ""]
    book = longs.groupby(["quarter", "cusip", "group_key", "asset_type"], as_index=False)["value_usd"].sum()
    book["weight"] = book["value_usd"] / book.groupby("quarter")["value_usd"].transform("sum")
    return book.assign(ticker=book["cusip"].map(ticker_by_cusip(book)))


def ticker_by_cusip(rows: pd.DataFrame) -> pd.Series:
    """CUSIP -> ticker for every CUSIP in rows (needs cusip, group_key,
    asset_type); unresolved CUSIPs are absent. Resolves every (cusip, name)
    pair, then keeps any ticker found per CUSIP: filers occasionally label
    one CUSIP differently (seen: 962166104 as both "WEYERHAEUSER CO" and
    "WEYERHAEUSER CO MTN BE"), and only one of the names may be matchable."""
    ids = rows.drop_duplicates(["cusip", "group_key"])[["cusip", "group_key", "asset_type"]]
    ids = resolve_tickers(ids.assign(issuer_label=ids["group_key"]))
    return ids.dropna(subset=["ticker"]).drop_duplicates("cusip").set_index("cusip")["ticker"]


def fund_returns(fund_rows: pd.DataFrame, quarters: list[str], book: pd.DataFrame, close: pd.DataFrame,
                 implied: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Estimate a fund's trailing 6-month / 1-year return from its 13F holdings.

    Method (a standard "13F clone"): over the window, each stretch of time
    between quarter-ends is assumed to hold the most recent quarter-end
    portfolio, buy-and-hold, then rebalanced to the next quarter's
    holdings. E.g. a 1-year window ending today holds the Q3 portfolio
    until Dec 31, the Q4 portfolio until Mar 31, and so on, with the latest
    filed quarter carried forward to today. Stretch returns are chained.

    Only long stock/ETF positions are used -- options are excluded (13F
    reports their underlying notional, not their value, and gives no
    strikes/expiries) and shorts/cash/non-US holdings are invisible to a
    13F. Within each stretch, weights are renormalized over the positions
    that could be priced; `coverage` is the share of the long book that
    actually was (the minimum across stretches, i.e. the weakest link).

    `book` is this fund's _long_book; `close` is a price frame (see
    download_closes) covering its tickers plus BENCHMARK_TICKER -- passed
    in so all_fund_returns can price every fund with ONE download.
    `implied` is implied_quarter_end_prices(), the fallback for positions
    Yahoo can't price at a quarter-end.

    Returns (summary, segments, gaps):
      summary: one row per window -- fund return, SPY return over the same
        dates, and coverage.
      segments: one row per (window, stretch) -- dates, which quarter's
        holdings were used, stretch return, coverage. For transparency.
      gaps: positions in the CURRENT holdings that couldn't be priced, and why.
    """
    now = close.index[-1]

    quarter_ends = sorted(pd.Timestamp(q) for q in fund_rows["quarter"].unique())
    expected_quarter_ends = sorted(pd.Timestamp(q) for q in quarters)

    implied = implied if implied is not None else {}

    def prices(rows: pd.DataFrame, when: pd.Timestamp) -> tuple[pd.Series, pd.Series]:
        """Per-CUSIP price at `when` + whether it came from the 13F fallback.

        Primary: Yahoo's last close on or before `when` (quarter-ends fall on
        weekends/holidays). Fallback, only where Yahoo has nothing on/before
        that date (delisted earlier, listed later, or no ticker at all): the
        13F-implied quarter-end price, when `when` IS a quarter-end."""
        yahoo = close.reindex(columns=rows["ticker"].dropna().unique().tolist()).apply(lambda s: s.asof(when))
        p = rows["ticker"].map(yahoo).astype(float)
        p.index = rows["cusip"].values
        qkey = when.strftime("%Y-%m-%d")
        fallback = pd.Series([implied.get((c, qkey)) for c in p.index], index=p.index, dtype=float)
        use_fb = p.isna() & fallback.notna()
        return p.where(~use_fb, fallback), use_fb

    summary_rows, segment_rows, gaps = [], [], pd.DataFrame()
    for col, offset in FUND_RETURN_WINDOWS.items():
        win_start = now - offset
        bench = (close[BENCHMARK_TICKER].asof(now) / close[BENCHMARK_TICKER].asof(win_start) - 1) * 100
        held_from = [q for q in quarter_ends if q <= win_start]
        if not held_from:
            # No holdings known yet at the start of the window -- can't say
            # what the fund owned, so no return rather than a partial one.
            summary_rows.append({"window": col, "fund_return_pct": float("nan"), "benchmark_return_pct": bench,
                                 "coverage": float("nan"), "note": "no 13F holdings on file at the window start"})
            continue

        # Cut at every EXPECTED quarter-end (not just the ones this fund
        # filed), so a missing filing shows up as an explicit carry-forward.
        cuts = [win_start] + [q for q in expected_quarter_ends if win_start < q < now] + [now]
        growth, coverages, notes = 1.0, [], set()
        for a, b in zip(cuts[:-1], cuts[1:]):
            q = max(x for x in quarter_ends if x <= a)
            expected = max(x for x in expected_quarter_ends if x <= a)
            if q != expected:
                notes.add(f"{expected.date()} filing missing; {q.date()} holdings carried forward")
            qbook = book[book["quarter"] == q.strftime("%Y-%m-%d")]
            pa, fb_a = prices(qbook, a)
            pb, fb_b = prices(qbook, b)
            r = pb / pa - 1
            # A 13F-implied endpoint mixed with a Yahoo (split-adjusted) one
            # could straddle a split; a >5x move from that is noise, not return.
            mixed = fb_a | fb_b
            r[mixed & (r.abs() > 4)] = float("nan")
            w = pd.Series(qbook["weight"].values, index=qbook["cusip"].values)
            ok = r.notna()
            if b == now:
                # The stretch ending at the as-of date = the holdings currently
                # carried. Record what couldn't be priced, so the gap is inspectable.
                miss = qbook[~ok.values]
                gaps = miss.assign(reason=miss["ticker"].isna().map({
                    True: "no ticker for this CUSIP, and no 13F quarter-end price at both ends of the stretch",
                    False: "no price at both ends of the stretch (Yahoo or 13F)"}))[
                    ["cusip", "group_key", "asset_type", "ticker", "weight", "reason"]]
            cov = w[ok].sum()
            seg_ret = (w[ok] * r[ok]).sum() / cov if cov > 0 else float("nan")
            growth *= 1 + seg_ret
            coverages.append(cov)
            segment_rows.append({"window": col, "from": a.date(), "to": b.date(), "holdings_quarter": q.strftime("%Y-%m-%d"),
                                 "return_pct": seg_ret * 100, "coverage": cov, "via_13f_prices": w[ok & mixed].sum()})
        summary_rows.append({"window": col, "fund_return_pct": (growth - 1) * 100, "benchmark_return_pct": bench,
                             "coverage": min(coverages), "note": "; ".join(sorted(notes))})

    return pd.DataFrame(summary_rows), pd.DataFrame(segment_rows), gaps


def implied_quarter_end_prices(df: pd.DataFrame) -> dict[tuple[str, str], float]:
    """(cusip, quarter) -> quarter-end price implied by the 13F itself:
    reported value / reported shares, median across every fund holding it
    (robust to one filer's typo). This is how WBD -- acquired and delisted
    in Oct 2026, so Yahoo keeps only its final quote -- still gets a June 30
    price, and how a CUSIP with no ticker at all can still be priced over a
    full quarter (both ends are quarter-ends)."""
    longs = df[(df["put_call"] == "") & (df["shares"] > 0)]
    px = (longs["value_usd"] / longs["shares"]).groupby([longs["cusip"], longs["quarter"]]).median()
    return px[px > 0].to_dict()


def price_window_start(end: pd.Timestamp) -> pd.Timestamp:
    return min(end - o for o in FUND_RETURN_WINDOWS.values()) - pd.DateOffset(days=14)


def all_fund_returns(df: pd.DataFrame, quarters: list[str], end: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """fund_returns for every fund in df, priced with one (chunked)
    download_closes over the union of their tickers. `end` is exclusive --
    pass today to measure as of yesterday's close. Returns (summary,
    segments, gaps) with a fund_name column added to each."""
    books = {name: _long_book(rows) for name, rows in df.groupby("fund_name")}
    tickers = sorted({t for b in books.values() for t in b["ticker"].dropna()})
    end = end if end is not None else pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() + pd.DateOffset(days=1)
    # Start before the EARLIEST quarter-end too, not just the window: a stock
    # held at that quarter-end but delisted before the window opened then
    # still has its final quote, so .asof() carries it forward as cash (0%
    # return) instead of the position dropping out as "unpriced".
    start = min(price_window_start(end), pd.Timestamp(min(quarters)) - pd.DateOffset(days=14))
    close = download_closes(tickers + [BENCHMARK_TICKER], start, end)
    if close[BENCHMARK_TICKER].isna().all():
        raise RuntimeError(f"no prices for benchmark {BENCHMARK_TICKER} -- try again in a few minutes")

    implied = implied_quarter_end_prices(df)
    out = {"summary": [], "segments": [], "gaps": []}
    for name, rows in df.groupby("fund_name"):
        for key, frame in zip(out, fund_returns(rows, quarters, books[name], close, implied)):
            out[key].append(frame.assign(fund_name=name))
    return tuple(pd.concat(v, ignore_index=True) for v in out.values())


def security_universe(df: pd.DataFrame, quarter: str, top_n: int = 100, end: pd.Timestamp | None = None):
    """The top_n most widely-held securities (by total value in `quarter`,
    across ALL funds), with resolved tickers and RETURN_WINDOWS returns.
    Returns (priced, omitted) -- omitted carries a human-readable reason."""
    from ranking import rank_stocks

    universe = rank_stocks(df, quarter, "equal_weighted", top_n=None)
    universe = universe.sort_values("total_value_usd", ascending=False).head(top_n)
    resolved = resolve_tickers(universe)
    no_ticker = resolved[resolved["ticker"].isna()][["issuer_label", "asset_type"]].assign(reason="no ticker found")
    resolved = resolved[resolved["ticker"].notna()]
    returns = fetch_returns(resolved["ticker"].tolist(), end=end)
    merged = resolved.merge(returns, left_on="ticker", right_index=True, how="left")

    # A resolved ticker can still have zero usable price history for EVERY
    # window -- two different real causes seen in practice: WBD (Warner Bros
    # Discovery) was acquired for cash and delisted in Oct 2026, so Yahoo
    # only retains its final quote, not years of history; SPCX (SpaceX)
    # IPO'd so recently (Jun 2026) that even the 6-month lookback predates
    # its listing. Rather than a row of blank cells with no explanation,
    # treat "ticker resolved but no return could be computed" the same as
    # "no ticker" -- omitted, with a reason, not silently blank.
    return_cols = list(RETURN_WINDOWS)
    has_no_data = merged[return_cols].isna().all(axis=1)
    no_price_data = merged.loc[has_no_data, ["issuer_label", "asset_type"]].assign(
        reason="no return computable for any window (too recently listed, or delisted/acquired)"
    )
    merged = merged.loc[~has_no_data]
    return merged, pd.concat([no_ticker, no_price_data], ignore_index=True)


def option_share_by_quarter(fund_rows: pd.DataFrame) -> pd.Series:
    """Fraction of reported 13F value that is puts/calls, per quarter (excluded from fund_returns)."""
    total = fund_rows.groupby("quarter")["value_usd"].sum()
    opts = fund_rows[fund_rows["put_call"] != ""].groupby("quarter")["value_usd"].sum()
    return (opts / total).reindex(total.index).fillna(0.0)
