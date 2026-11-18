"""Build the research-report pages for the Insights tab's under-the-radar picks.

Each report = reports/template.html + reports/data/<TICKER>.json (the
researched narrative) + numbers pulled at build time: month-end prices from
Yahoo Finance, the pick's radar score (picks.score_picks) and its holders
from the 13F data. Output: reports/html/<TICKER>.html, plus
reports/manifest.json recording when each report was generated. The app
links to each report's published URL from manifest.json.

    .venv/bin/python reports/build_reports.py            # all reports in reports/data
    .venv/bin/python reports/build_reports.py BTSG RPRX  # just these
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "src"))
import picks  # noqa: E402
import ranking  # noqa: E402
from sec_utils import quarter_label  # noqa: E402

PERF_DIR = ROOT.parent / "data" / "processed" / "performance"
MANIFEST = ROOT / "manifest.json"


def monthly_prices(ticker: str, start: str) -> tuple[list, str, float]:
    """Month-end closes since `start`; the last point is the latest daily close."""
    hist = yf.Ticker(ticker).history(start=start, interval="1mo", auto_adjust=False)["Close"].dropna()
    daily = yf.Ticker(ticker).history(period="5d", auto_adjust=False)["Close"].dropna()
    last_day, last = daily.index[-1], float(daily.iloc[-1])
    # Yahoo labels a monthly bar with the month's first day; date it at
    # month-end so events snap to the month they happened in.
    points = [[(d.tz_localize(None) + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d"), round(float(v), 2)]
              for d, v in hist.items() if d.tz_localize(None) < last_day.tz_localize(None).replace(day=1)]
    points.append([last_day.strftime("%Y-%m-%d"), round(last, 2)])
    return points, last_day.strftime("%b %-d, %Y"), last


def holders(df: pd.DataFrame, group_key: str) -> dict:
    longs = df[(df["group_key"] == group_key) & (df["put_call"] == "")]
    quarters = sorted(df["quarter"].unique())
    per_q = {q: longs[longs["quarter"] == q].groupby("fund_name").agg(
        value=("value_usd", "sum"), weight=("pct_of_portfolio", "sum"), shares=("shares", "sum")) for q in quarters}
    latest, prev = per_q[quarters[-1]], per_q[quarters[-2]]

    def change(fund, shares):
        if fund not in prev.index:
            return "New position"
        before = prev.loc[fund, "shares"]
        if not before:
            return "—"
        pct = (shares / before - 1) * 100
        return "Unchanged" if abs(pct) < 0.5 else f"{'Added' if pct > 0 else 'Trimmed'} {abs(pct):.0f}% of shares"

    rows = [{"fund": f, "value": float(r.value), "weight": float(r.weight), "shares": int(r.shares),
             "change": change(f, r.shares)} for f, r in latest.sort_values("value", ascending=False).iterrows()]
    exited = sorted(set(prev.index) - set(latest.index))
    by_q = [[quarter_label(q), len(per_q[q]), ", ".join(per_q[q].index)] for q in quarters]
    return {"latest_label": quarter_label(quarters[-1]), "latest": rows, "exited": exited, "by_quarter": by_q}


def main(only: list[str]):
    cands = pd.read_parquet(PERF_DIR / "pick_candidates.parquet")
    meta = json.loads((PERF_DIR / "meta.json").read_text())
    scored = pd.concat(picks.score_picks(cands).values())
    df = ranking.load_data()
    template = (ROOT / "template.html").read_text()
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    (ROOT / "html").mkdir(exist_ok=True)

    now = datetime.now(timezone.utc)
    for path in sorted((ROOT / "data").glob("*.json")):
        ticker = path.stem
        if only and ticker not in only:
            continue
        data = json.loads(path.read_text())
        row = scored[scored["ticker"] == ticker]
        if row.empty:
            print(f"warning: {ticker} is not a current pick; scoring it from the candidate pool anyway")
            row = cands[cands["ticker"] == ticker]
        row = row.iloc[0]
        prices, asof, last = monthly_prices(ticker, data["price_start"])
        spy, _, _ = monthly_prices("SPY", data["price_start"])
        data.update({
            "ticker": ticker, "kind": row["asset_type"], "last_price": last, "prices_asof": asof,
            "prices": {ticker: prices, "SPY": spy},
            "generated": now.isoformat(timespec="seconds"),
            "generated_label": now.strftime("%b %-d, %Y, %H:%M UTC"),
            "picks_asof": pd.Timestamp(meta["as_of"]).strftime("%b %-d, %Y"),
            "holders": holders(df, row["group_key"]),
            "score": {k: float(row.get(k, float("nan"))) for k in ("score", "performance", "breadth", "accumulation")}
                     | {"why": row.get("why", "")},
        })
        html = (template.replace("__TITLE__", data["title"]).replace("__ACC__", data["accent"])
                .replace("__ACCD__", data["accent_dark"]).replace("__HERO__", data["hero"])
                .replace("__DATA__", json.dumps(data, ensure_ascii=False)))
        out = ROOT / "html" / f"{ticker}.html"
        out.write_text(html)
        manifest.setdefault(ticker, {}).update({"title": data["title"], "generated": data["generated"],
                                                "file": str(out.relative_to(ROOT.parent))})
        print(f"{ticker}: {out} ({len(html) // 1024} KB), price {last:.2f} as of {asof}")
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
