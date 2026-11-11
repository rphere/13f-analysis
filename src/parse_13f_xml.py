"""Parse one 13F information-table XML file into tidy holding rows.

Namespace prefixes vary between filers/years (seen "ns1:", but not
guaranteed), so elements are matched by local tag name rather than a
hardcoded namespace URI.

Note on units: SEC's 2023 rule change requires `value` to be reported in
whole US dollars. All of this project's target quarters (2025Q3-2026Q2)
postdate that change, so values below are treated as whole dollars as-is.
Older (pre-2023) filings reported value in thousands of dollars and would
need a x1000 adjustment if ever added.
"""

from xml.etree import ElementTree as ET


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_infotable(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    rows = []
    for info_table in root.iter():
        if _local(info_table.tag) != "infoTable":
            continue
        row = {"issuer": None, "title_of_class": None, "cusip": None, "value_usd": None, "shares": None, "investment_discretion": None}
        for child in info_table:
            tag = _local(child.tag)
            if tag == "nameOfIssuer":
                row["issuer"] = child.text
            elif tag == "titleOfClass":
                row["title_of_class"] = child.text
            elif tag == "cusip":
                row["cusip"] = child.text
            elif tag == "value":
                row["value_usd"] = int(child.text)
            elif tag == "shrsOrPrnAmt":
                for grandchild in child:
                    if _local(grandchild.tag) == "sshPrnamt":
                        row["shares"] = int(grandchild.text)
            elif tag == "investmentDiscretion":
                row["investment_discretion"] = child.text
        rows.append(row)
    return rows


if __name__ == "__main__":
    import sys

    path = sys.argv[1]
    parsed = parse_infotable(open(path, "rb").read())
    print(f"{len(parsed)} holdings parsed")
    for r in parsed[:5]:
        print(r)
