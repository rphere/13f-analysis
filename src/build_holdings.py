"""Combine all cached raw 13F XML into one tidy holdings table.

Reads data/raw/{cik}/{quarter}.xml for every fund x quarter, parses each with
parse_13f_xml, groups duplicate cusip/class lines within a fund-quarter
(a filer occasionally reports the same security across multiple
voting-authority rows), and computes each holding's share of that fund's
total reported 13F portfolio value that quarter. Writes the result to
data/processed/holdings_long.parquet.
"""

import csv
from pathlib import Path

import pandas as pd

from parse_13f_xml import parse_infotable

ROOT = Path(__file__).resolve().parent.parent
FUNDS_CSV = ROOT / "data" / "processed" / "funds.csv"
RAW_DIR = ROOT / "data" / "raw"
OUT_PATH = ROOT / "data" / "processed" / "holdings_long.parquet"


def load_funds():
    with open(FUNDS_CSV, newline="") as f:
        return list(csv.DictReader(f))


def build():
    funds = load_funds()
    all_rows = []

    for fund in funds:
        name, cik = fund["name"], fund["cik"]
        fund_dir = RAW_DIR / cik
        if not fund_dir.exists():
            print(f"skip {name}: no raw data directory (run fetch_sec_13f.py first)")
            continue

        for xml_path in sorted(fund_dir.glob("*.xml")):
            quarter = xml_path.stem
            holdings = parse_infotable(xml_path.read_bytes())
            if not holdings:
                print(f"warn {name} {quarter}: parsed 0 holdings")
                continue

            df = pd.DataFrame(holdings)
            df["fund_name"] = name
            df["cik"] = cik
            df["category"] = fund["category"]
            df["quarter"] = quarter

            grouped = (
                df.groupby(["fund_name", "cik", "category", "quarter", "cusip", "issuer", "title_of_class"], as_index=False)
                .agg(value_usd=("value_usd", "sum"), shares=("shares", "sum"))
            )
            fund_total = grouped["value_usd"].sum()
            grouped["pct_of_portfolio"] = grouped["value_usd"] / fund_total if fund_total else 0.0
            grouped["fund_total_value_usd"] = fund_total

            all_rows.append(grouped)

    if not all_rows:
        raise SystemExit("No holdings parsed -- did you run fetch_sec_13f.py first?")

    result = pd.concat(all_rows, ignore_index=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(OUT_PATH, index=False)
    print(f"Wrote {len(result)} holding rows across {result['fund_name'].nunique()} funds and {result['quarter'].nunique()} quarters to {OUT_PATH}")


if __name__ == "__main__":
    build()
