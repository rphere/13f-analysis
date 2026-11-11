"""Scoring and aggregation functions for ranking stock holdings across funds.

Grouping key: for a Stock, the group is the CLEANED COMPANY NAME (so
multiple share classes of the same company -- e.g. Alphabet's Class A/C,
different CUSIPs -- roll up into one "Alphabet" row, which is what a reader
expects from a "top holdings" ranking). For an ETF, the group stays the
CUSIP: ETF sponsors routinely file many distinct funds under one generic
issuer name (SEC data literally reports "ISHARES TR" as the nameOfIssuer for
hundreds of different iShares ETFs), so collapsing by name would wrongly
merge unrelated products -- CUSIP is what actually identifies one. This also
means ETF display labels can't rely on nameOfIssuer alone; they're built
from issuer + title_of_class (see _group_labels), since that's where the
actual fund description (e.g. "CORE S&P500 ETF") lives.

Four ranking lenses are supported, matching the methods the dashboard exposes:

- aum_weighted:          sum(pct_of_portfolio * normalized_fund_aum)
- performance_weighted:  sum(pct_of_portfolio * normalized_fund_performance)
- equal_weighted:        mean(pct_of_portfolio) across funds holding it (popularity, fund-size-agnostic)
- composite:             user-weighted blend of the three normalized scores above

Funds missing an aum/performance estimate (see funds.csv source_note) are
imputed with the median of the funds that do have one, so an unscored fund
neither dominates nor zeroes out a ranking that depends on that metric.

rank_stocks() returns the FULL ranked universe by default (top_n=None) --
callers slice with .head(n) for display. This matters for performance: it
lets the dashboard cache one ranking per (quarter, method, weights, funds)
and reuse it for every view (snapshot table, trend lookup, search) instead
of recomputing per widget.
"""

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HOLDINGS_PATH = ROOT / "data" / "processed" / "holdings_long.parquet"
FUNDS_CSV = ROOT / "data" / "processed" / "funds.csv"

# Heuristic ETF/fund-trust classifier. Deliberately narrow: generic words like
# bare "TRUST" or "VANGUARD" would misclassify real operating companies (REITs
# are routinely named "... Realty Trust Inc"; "American Vanguard Corp" is a
# chemicals company, not a Vanguard fund). Each term here only ever appears in
# practice as part of an ETF/ETP sponsor or product name.
_ETF_PATTERN = re.compile(
    r"\bETF|\bISHARES|\bSPDR|\bINVESCO QQQ|\bPROSHARES|\bDIREXION|\bWISDOMTREE TR|\bVANECK|\bGLOBAL X|\bFIRST TRUST|\bFLEXSHARES|\bBONDBLOXX"
    r"|\bVANGUARD\b(?!.*\bCORP\b)",
    re.IGNORECASE,
)

# Trailing corporate/trust suffixes stripped for display + company-grouping
# purposes only -- the underlying issuer/cusip identity used for search is
# untouched (see load_data: "issuer" stays the raw SEC name).
_SUFFIX_PATTERN = re.compile(r"[\s,]+(&\s*)?(INC|CORP|CORPORATION|CO|LTD|LLC|LP|PLC|TR|TRUST)\.?$", re.IGNORECASE)


def classify_asset_type(issuer: str) -> str:
    return "ETF" if _ETF_PATTERN.search(issuer or "") else "Stock"


def clean_label(name: str) -> str:
    """Strip trailing corporate/trust suffixes for readability (display + grouping)."""
    if not name:
        return name
    prev = None
    while prev != name:
        prev = name
        name = _SUFFIX_PATTERN.sub("", name).strip()
    return name


def _norm_ws(series: pd.Series) -> pd.Series:
    return series.str.replace(r"\s+", " ", regex=True).str.strip()


def load_data() -> pd.DataFrame:
    holdings = pd.read_parquet(HOLDINGS_PATH)
    holdings["issuer"] = _norm_ws(holdings["issuer"])
    holdings["title_of_class"] = _norm_ws(holdings["title_of_class"].fillna(""))
    holdings["issuer_lower"] = holdings["issuer"].str.lower()
    holdings["asset_type"] = holdings["issuer"].map(classify_asset_type)
    # Stocks group by cleaned company name (merges share classes like
    # Alphabet Class A/C); ETFs group by cusip (one row per distinct fund).
    holdings["group_key"] = holdings["cusip"].where(holdings["asset_type"] == "ETF", holdings["issuer"].map(clean_label))

    funds = pd.read_csv(FUNDS_CSV, dtype={"cik": str})
    funds["aum_est_usd_billions"] = funds["aum_est_usd_billions"].fillna(funds["aum_est_usd_billions"].median())
    funds["performance_est_pct_2025"] = funds["performance_est_pct_2025"].fillna(funds["performance_est_pct_2025"].median())
    funds["aum_norm"] = _minmax(funds["aum_est_usd_billions"])
    funds["performance_norm"] = _minmax(funds["performance_est_pct_2025"])
    return holdings.merge(funds[["cik", "aum_norm", "performance_norm"]], on="cik", how="left")


def _minmax(series: pd.Series) -> pd.Series:
    lo, hi = series.min(), series.max()
    if hi == lo:
        return series * 0 + 0.5
    return (series - lo) / (hi - lo)


def row_display_labels(df: pd.DataFrame) -> pd.Series:
    """Per-ROW display label (for ungrouped, cusip-level holdings like fund_top_holdings).

    Same reasoning as _group_labels' ETF branch: nameOfIssuer alone is often
    a generic sponsor/trust name (e.g. "ISHARES TR") shared by many distinct
    ETFs, so without title_of_class, a single fund's different iShares
    positions would all render as the same "ISHARES" label and look like
    duplicates. Stocks don't need title_of_class -- the company name alone
    is already specific.
    """
    stock_label = df["issuer"].map(clean_label)
    etf_label = (df["issuer"].map(clean_label) + " " + df["title_of_class"]).str.strip()
    return stock_label.where(df["asset_type"] != "ETF", etf_label)


def _group_labels(df: pd.DataFrame) -> pd.DataFrame:
    """One representative display label + asset type per group_key.

    Stocks: the group_key IS the cleaned company name already.
    ETFs: nameOfIssuer alone is often a generic sponsor/trust name shared by
    hundreds of distinct funds (e.g. "ISHARES TR") -- title_of_class carries
    the actual product description, so the label combines both.
    """
    is_etf = df["asset_type"] == "ETF"
    stock_labels = df.loc[~is_etf, ["group_key"]].drop_duplicates().assign(issuer_label=lambda d: d["group_key"], asset_type="Stock")

    etf_rows = df.loc[is_etf]
    etf_issuer = etf_rows.groupby("group_key")["issuer"].agg(lambda s: clean_label(s.value_counts().idxmax()))
    etf_title = etf_rows.groupby("group_key")["title_of_class"].agg(lambda s: s[s != ""].value_counts().idxmax() if (s != "").any() else "")
    etf_labels = pd.DataFrame({"issuer_label": (etf_issuer + " " + etf_title).str.strip(), "asset_type": "ETF"}).reset_index()

    return pd.concat([stock_labels[["group_key", "issuer_label", "asset_type"]], etf_labels], ignore_index=True)


_EMPTY_RANKED_COLUMNS = [
    "group_key", "num_funds", "total_value_usd", "equal_weighted", "aum_weighted",
    "performance_weighted", "score", "issuer_label", "asset_type",
]


def rank_stocks(df: pd.DataFrame, quarter: str, method: str, weights: dict | None = None, top_n: int | None = None) -> pd.DataFrame:
    qdf = df[df["quarter"] == quarter]
    if qdf.empty:
        # Same column set as the non-empty path (callers rely on issuer_label/
        # asset_type regardless of whether any fund/quarter matched).
        return pd.DataFrame(columns=_EMPTY_RANKED_COLUMNS)

    # Stage 1: collapse multiple cusips of the same fund+company (e.g. a fund
    # holding both Alphabet share classes) BEFORE cross-fund aggregation, so a
    # company's per-fund exposure is the sum of its classes, not an average.
    per_fund = qdf.groupby(["fund_name", "group_key"], as_index=False).agg(
        value_usd=("value_usd", "sum"),
        pct_of_portfolio=("pct_of_portfolio", "sum"),
        aum_norm=("aum_norm", "first"),
        performance_norm=("performance_norm", "first"),
    )
    per_fund["aum_component"] = per_fund["pct_of_portfolio"] * per_fund["aum_norm"]
    per_fund["perf_component"] = per_fund["pct_of_portfolio"] * per_fund["performance_norm"]

    grouped = per_fund.groupby("group_key").agg(
        num_funds=("fund_name", "nunique"),
        total_value_usd=("value_usd", "sum"),
        equal_weighted=("pct_of_portfolio", "mean"),
        aum_weighted=("aum_component", "sum"),
        performance_weighted=("perf_component", "sum"),
    ).reset_index()

    if method == "composite":
        w = weights or {"aum": 1 / 3, "performance": 1 / 3, "equal": 1 / 3}
        total_w = sum(w.values()) or 1.0
        source_col = {"aum": "aum_weighted", "performance": "performance_weighted", "equal": "equal_weighted"}
        for k, col in source_col.items():
            grouped[f"{k}_norm_score"] = _minmax(grouped[col])
        grouped["score"] = (
            w.get("aum", 0) / total_w * grouped["aum_norm_score"]
            + w.get("performance", 0) / total_w * grouped["performance_norm_score"]
            + w.get("equal", 0) / total_w * grouped["equal_norm_score"]
        )
    elif method in ("aum_weighted", "performance_weighted", "equal_weighted"):
        grouped["score"] = grouped[method]
    else:
        raise ValueError(f"unknown ranking method: {method}")

    grouped = grouped.merge(_group_labels(qdf), on="group_key", how="left")
    ranked = grouped.sort_values("score", ascending=False)
    return ranked.head(top_n) if top_n else ranked


def rank_by_quarter(df: pd.DataFrame, quarters: list[str], method: str, weights: dict | None = None) -> dict[str, pd.DataFrame]:
    """Full-universe ranking per quarter, computed once -- for reuse across snapshot/search views."""
    return {q: rank_stocks(df, q, method, weights, top_n=None) for q in quarters}


def fund_top_holdings(df: pd.DataFrame, fund_name: str, quarter: str, top_n: int = 15) -> pd.DataFrame:
    sub = df[(df["fund_name"] == fund_name) & (df["quarter"] == quarter)]
    return sub.sort_values("pct_of_portfolio", ascending=False).head(top_n)
