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

import json
import re

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
}


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", clean_label(s).upper())


def fetch_company_tickers() -> dict:
    """name (normalized) -> ticker, from SEC's free bulk ticker list."""
    data = sec_get(COMPANY_TICKERS_URL).json()
    by_name = {}
    for v in data.values():
        by_name.setdefault(_norm(v["title"]), v["ticker"])
    return by_name


def resolve_tickers(universe: pd.DataFrame) -> pd.DataFrame:
    """universe: rows with group_key, issuer_label, asset_type (see rank_stocks).
    Returns universe with a 'ticker' column added; unresolved rows get ticker=None."""
    name_to_ticker = fetch_company_tickers()
    tickers = []
    for _, row in universe.iterrows():
        if row["asset_type"] == "ETF":
            tickers.append(ETF_TICKER_BY_CUSIP.get(row["group_key"]))
        else:
            label = row["issuer_label"]
            tickers.append(STOCK_TICKER_OVERRIDES.get(label) or name_to_ticker.get(_norm(label)))
    return universe.assign(ticker=tickers)


RETURN_WINDOWS = {
    "return_6mo_pct": pd.DateOffset(months=6),
    "return_1yr_pct": pd.DateOffset(years=1),
    "return_2yr_pct": pd.DateOffset(years=2),
    "return_5yr_pct": pd.DateOffset(years=5),
}


def fetch_returns(tickers: list[str]) -> pd.DataFrame:
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
    end = pd.Timestamp.now(tz="UTC").tz_localize(None)
    start = end - pd.DateOffset(years=5, days=14)
    data = yf.download(tickers, start=start, end=end, interval="1d", progress=False, auto_adjust=True, group_by="column")
    close = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data[["Close"]].rename(columns={"Close": tickers[0]})

    latest = close.iloc[-1]
    now = close.index[-1]
    out = {}
    for col, offset in RETURN_WINDOWS.items():
        past = close.apply(lambda s: s.asof(now - offset))
        out[col] = (latest / past - 1) * 100
    return pd.DataFrame(out)
