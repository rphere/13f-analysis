"""Resolve/verify the curated fund list's SEC CIKs and write data/processed/funds.csv.

The fund list itself (which funds, which category) is a curation decision made
during planning -- there's no free API that ranks hedge funds by AUM or
performance, so the roster below was assembled from public reporting (see
source_note per fund) and is the auditable record of that choice.

This script's job is narrower: given that roster, verify each CIK against
SEC EDGAR (confirm the entity name matches and that it actually files 13F-HR
holdings reports, not just 13F-NT notices -- a notice filer discloses no
holdings and is useless to us), then (re)write funds.csv. Manual annotations
(aum_est_usd_billions, performance_est_pct_2025, source_note) are preserved
across re-runs by merging onto this script's CIK/category/name columns.
"""

import csv
import sys
from pathlib import Path

from sec_utils import cik10, sec_get

ROOT = Path(__file__).resolve().parent.parent
FUNDS_CSV = ROOT / "data" / "processed" / "funds.csv"

# (display name, CIK, category, a keyword expected in SEC's registered entity name)
FUND_REGISTRY = [
    ("Bridgewater Associates", "0001350694", "aum", "BRIDGEWATER"),
    ("Citadel Advisors", "0001423053", "aum", "CITADEL"),
    ("Millennium Management", "0001273087", "aum", "MILLENNIUM"),
    ("D. E. Shaw & Co., Inc.", "0001009207", "aum", "SHAW"),
    ("Point72 Asset Management", "0001603466", "aum", "POINT72"),
    ("Two Sigma Investments", "0001179392", "aum", "TWO SIGMA"),
    ("AQR Capital Management", "0001167557", "aum", "AQR"),
    ("Renaissance Technologies", "0001037389", "aum", "RENAISSANCE"),
    ("Elliott Investment Management", "0001791786", "aum", "ELLIOTT"),
    ("Viking Global Investors", "0001103804", "aum", "VIKING"),
    ("D1 Capital Partners", "0001747057", "performance", "D1 CAPITAL"),
    ("Lone Pine Capital", "0001061165", "performance", "LONE PINE"),
    ("Tiger Global Management", "0001167483", "performance", "TIGER"),
    ("Coatue Management", "0001135730", "performance", "COATUE"),
    ("Third Point", "0001040273", "performance", "THIRD POINT"),
    ("Pershing Square Capital Management", "0001336528", "performance", "PERSHING SQUARE"),
    ("Appaloosa Management", "0001656456", "performance", "APPALOOSA"),
    ("Whale Rock Capital Management", "0001387322", "performance", "WHALE ROCK"),
    ("Baupost Group", "0001061768", "performance", "BAUPOST"),
    ("ValueAct Capital", "0001418814", "performance", "VALUEACT"),
]


def verify_fund(name: str, cik: str, keyword: str) -> dict:
    url = f"https://data.sec.gov/submissions/CIK{cik10(cik)}.json"
    data = sec_get(url).json()
    sec_name = data.get("name", "")
    forms = data.get("filings", {}).get("recent", {}).get("form", [])
    files_holdings = any(f == "13F-HR" or f == "13F-HR/A" for f in forms)
    ok = keyword.upper() in sec_name.upper() and files_holdings
    return {"sec_name": sec_name, "files_holdings": files_holdings, "ok": ok}


def main():
    existing_annotations = {}
    if FUNDS_CSV.exists():
        with open(FUNDS_CSV, newline="") as f:
            for row in csv.DictReader(f):
                existing_annotations[row["cik"]] = {
                    "aum_est_usd_billions": row.get("aum_est_usd_billions", ""),
                    "performance_est_pct_2025": row.get("performance_est_pct_2025", ""),
                    "source_note": row.get("source_note", ""),
                }

    rows = []
    problems = []
    for name, cik, category, keyword in FUND_REGISTRY:
        result = verify_fund(name, cik, keyword)
        if not result["ok"]:
            problems.append(f"{name} (CIK {cik}): sec_name={result['sec_name']!r}, files_holdings={result['files_holdings']}")
        annotation = existing_annotations.get(cik, {"aum_est_usd_billions": "", "performance_est_pct_2025": "", "source_note": ""})
        rows.append({
            "name": name,
            "cik": cik,
            "category": category,
            **annotation,
        })

    FUNDS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(FUNDS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "cik", "category", "aum_est_usd_billions", "performance_est_pct_2025", "source_note"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} funds to {FUNDS_CSV}")
    if problems:
        print("\nVerification problems (manual review needed):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
