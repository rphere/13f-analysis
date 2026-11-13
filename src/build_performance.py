"""Precompute every price-dependent number the dashboard shows, as of the most
recent completed trading day (yesterday's close), and save it to
data/processed/performance/.

Why a snapshot instead of computing at page load: the fund leaderboard prices
every 13F long position (~8,000 tickers) and Yahoo rate-limits bursts, so a
live computation takes 30s+ and can fail mid-way. The app now just reads
these files -- instant, and identical for every viewer. Re-run this to
refresh (e.g. daily), then commit the output (the Streamlit Cloud deploy has
nowhere to run it).

Run: python src/build_performance.py
     (after build_holdings.py, and ideally resolve_cusip_tickers.py)
"""

import json
from pathlib import Path

import pandas as pd

import performance
import ranking
from sec_utils import TARGET_QUARTERS

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed" / "performance"


def main():
    df = ranking.load_data()
    # yfinance `end` is exclusive: today's date -> data through yesterday's
    # close, so a run during market hours never mixes in a partial day.
    end = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()

    print("Pricing every fund's 13F long book…")
    summary, segments, gaps = performance.all_fund_returns(df, TARGET_QUARTERS, end=end)
    print("Pricing the most widely-held securities…")
    securities, omitted = performance.security_universe(df, TARGET_QUARTERS[-1], end=end)
    option_share = pd.concat(
        {name: performance.option_share_by_quarter(rows) for name, rows in df.groupby("fund_name")}, names=["fund_name", "quarter"]
    ).rename("option_share").reset_index()

    # The last price date actually present -- what "as of" means on screen.
    as_of = pd.Timestamp(segments["to"].max())

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary.to_parquet(OUT_DIR / "fund_returns.parquet", index=False)
    segments.assign(**{c: segments[c].astype(str) for c in ("from", "to")}).to_parquet(OUT_DIR / "fund_segments.parquet", index=False)
    gaps.to_parquet(OUT_DIR / "fund_gaps.parquet", index=False)
    option_share.to_parquet(OUT_DIR / "fund_option_share.parquet", index=False)
    securities.to_parquet(OUT_DIR / "security_returns.parquet", index=False)
    omitted.to_parquet(OUT_DIR / "security_omitted.parquet", index=False)
    (OUT_DIR / "meta.json").write_text(json.dumps({
        "as_of": as_of.strftime("%Y-%m-%d"),
        "built_at": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "benchmark": performance.BENCHMARK_TICKER,
    }, indent=2))

    cov = summary[summary["window"] == "return_1yr_pct"].set_index("fund_name")["coverage"].sort_values()
    print(f"\nAs of {as_of.date()}. 1-year coverage by fund:")
    print((cov * 100).round(1).to_string())
    print(f"\nWrote {OUT_DIR}")


if __name__ == "__main__":
    main()
