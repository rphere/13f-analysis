"""Download each tracked fund's 13F-HR information-table XML for each target quarter.

For every fund in data/processed/funds.csv and every quarter in
sec_utils.TARGET_QUARTERS: look up the fund's filings via SEC's submissions
API, find the filing whose reportDate matches that quarter (preferring the
most recently filed 13F-HR/13F-HR/A if more than one exists for the same
period -- e.g. a restated amendment), locate the information-table XML inside
that filing via the filing's index.json manifest (its filename varies filing
to filing, so we don't hardcode it), and cache it to
data/raw/{cik}/{quarter}.xml. Re-running skips files already cached.
"""

import csv
from pathlib import Path

from sec_utils import TARGET_QUARTERS, cik10, sec_get

ROOT = Path(__file__).resolve().parent.parent
FUNDS_CSV = ROOT / "data" / "processed" / "funds.csv"
RAW_DIR = ROOT / "data" / "raw"

HOLDINGS_FORMS = {"13F-HR", "13F-HR/A"}


def load_funds():
    with open(FUNDS_CSV, newline="") as f:
        return list(csv.DictReader(f))


def find_accession_for_quarter(cik: str, quarter: str) -> str | None:
    data = sec_get(f"https://data.sec.gov/submissions/CIK{cik10(cik)}.json").json()
    recent = data["filings"]["recent"]
    candidates = [
        (filing_date, accession)
        for form, accession, filing_date, report_date in zip(
            recent["form"], recent["accessionNumber"], recent["filingDate"], recent["reportDate"]
        )
        if form in HOLDINGS_FORMS and report_date == quarter
    ]
    if not candidates:
        return None
    candidates.sort()  # latest filing_date last
    return candidates[-1][1]


def find_infotable_filename(cik: str, accession: str) -> str | None:
    accession_nodash = accession.replace("-", "")
    cik_bare = str(int(cik))
    index = sec_get(f"https://www.sec.gov/Archives/edgar/data/{cik_bare}/{accession_nodash}/index.json").json()
    items = index["directory"]["item"]
    xml_files = [i for i in items if i["name"].lower().endswith(".xml") and i["name"].lower() != "primary_doc.xml"]
    if not xml_files:
        return None
    # if more than one candidate, the information table is almost always the larger file
    xml_files.sort(key=lambda i: int(i.get("size") or 0), reverse=True)
    return accession_nodash, xml_files[0]["name"]


def fetch_one(cik: str, quarter: str) -> str:
    """Returns a short status string for logging."""
    dest = RAW_DIR / cik / f"{quarter}.xml"
    if dest.exists():
        return "cached"

    accession = find_accession_for_quarter(cik, quarter)
    if accession is None:
        return "NO FILING FOUND for this quarter"

    found = find_infotable_filename(cik, accession)
    if found is None:
        return f"NO INFOTABLE XML in accession {accession}"
    accession_nodash, filename = found

    cik_bare = str(int(cik))
    xml_bytes = sec_get(f"https://www.sec.gov/Archives/edgar/data/{cik_bare}/{accession_nodash}/{filename}").content

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(xml_bytes)
    return f"fetched ({len(xml_bytes)} bytes, accession {accession})"


def main():
    funds = load_funds()
    for fund in funds:
        name, cik = fund["name"], fund["cik"]
        for quarter in TARGET_QUARTERS:
            status = fetch_one(cik, quarter)
            print(f"{name:40s} {quarter}  {status}")


if __name__ == "__main__":
    main()
