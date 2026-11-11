"""Shared helpers for talking to SEC EDGAR: a rate-limited, User-Agent-tagged HTTP GET."""

import time

import requests

USER_AGENT = "13F-Dashboard-Research/1.0 (contact: research@13f-analysis-project.local)"

_last_request_ts = 0.0
_MIN_INTERVAL = 0.15  # keeps us comfortably under SEC's ~10 req/sec fair-access limit


def sec_get(url: str) -> requests.Response:
    global _last_request_ts
    elapsed = time.monotonic() - _last_request_ts
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    _last_request_ts = time.monotonic()
    resp.raise_for_status()
    return resp


def cik10(cik: str) -> str:
    """Zero-pad a CIK to the 10-digit form SEC's data.sec.gov endpoints expect."""
    return str(int(cik)).zfill(10)


# Last 4 fully-filed quarters as of this project's build date (2026-11-11).
# A 13F-HR is due 45 days after period end, so Q3 2026 (ended 2026-09-30, due
# 2026-11-14) is NOT yet fully filed and is intentionally excluded.
TARGET_QUARTERS = [
    "2025-09-30",
    "2025-12-31",
    "2026-03-31",
    "2026-06-30",
]

_QUARTER_END_MONTH_TO_LABEL = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}


def quarter_label(period_end: str) -> str:
    """'2026-06-30' -> '2026 Q2' (quarter of the period that ENDED on this date)."""
    year, month, _ = period_end.split("-")
    return f"{year} {_QUARTER_END_MONTH_TO_LABEL[month]}"
