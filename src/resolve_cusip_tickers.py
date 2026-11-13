"""Map 13F CUSIPs to US tickers via OpenFIGI, for holdings that name matching
(performance.resolve_tickers) can't resolve.

Why: 13F filings identify securities by CUSIP + an abbreviated issuer name.
Name matching against SEC's company_tickers.json misses ~half of all CUSIPs
(most ETFs, small caps, foreign filers, odd abbreviations). OpenFIGI
(Bloomberg's free identifier-mapping API) maps CUSIP -> ticker directly.

Incremental + cached: results (including "no match") are appended to
data/processed/cusip_tickers.csv, so re-runs only query CUSIPs not seen
before -- e.g. after adding a new quarter. Without an API key OpenFIGI allows
25 requests/minute x 10 CUSIPs each; set OPENFIGI_API_KEY (free signup) for
~100x the throughput.

Run: python src/resolve_cusip_tickers.py   (after build_holdings.py)
"""

import csv
import os
import time
from pathlib import Path

import requests

import performance
import ranking

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "processed" / "cusip_tickers.csv"
URL = "https://api.openfigi.com/v3/mapping"
FIELDS = ["cusip", "ticker", "security_type", "name", "tried"]

# US exchange codes, plus Mexico's (MM/MF/MU) -- the BMV cross-lists US
# stocks as "EA*", and keeps doing so after a US delisting, which is how a
# taken-private name like Electronic Arts still maps to its old ticker.
_US_EXCH = {"US", "UN", "UW", "UQ", "UA", "UP", "UR", "UV", "UF"}
_MX_EXCH = {"MM", "MF", "MU"}

# Lookup passes, tried in order until one finds a ticker. Each CUSIP records
# which passes it has had ("tried"), so re-runs only do new work.
#   cusip_us:  the CUSIP on a US exchange (resolves the large majority)
#   cins_us:   letter-first IDs are really CINS (non-US issuers: Accenture
#              G1151C101, Royal Caribbean V7780T103) -- OpenFIGI only finds
#              them under ID_CINS
#   cusip_any: no exchange filter; keep only a US or Mexican line (delisted
#              US names: EA, Qorvo, AvalonBay's old CUSIP)
PASSES = ["cusip_us", "cins_us", "cusip_any"]


def _load_cache() -> dict[str, dict]:
    if not OUT_PATH.exists():
        return {}
    with open(OUT_PATH, newline="") as f:
        # Rows from before passes existed only had the first pass.
        return {r["cusip"]: {**r, "tried": r.get("tried") or "cusip_us"} for r in csv.DictReader(f)}


def _save_cache(cache: dict[str, dict]):
    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for c in sorted(cache):
            writer.writerow({k: cache[c].get(k, "") for k in FIELDS})


def _applies(pass_name: str, cusip: str) -> bool:
    return pass_name != "cins_us" or cusip[:1].isalpha()


def _job(pass_name: str, cusip: str) -> dict:
    if pass_name == "cusip_us":
        return {"idType": "ID_CUSIP", "idValue": cusip, "exchCode": "US"}
    if pass_name == "cins_us":
        return {"idType": "ID_CINS", "idValue": cusip, "exchCode": "US"}
    return {"idType": "ID_CUSIP", "idValue": cusip}


def _pick(results: list[dict], pass_name: str) -> dict | None:
    """Prefer common stock / ETF over e.g. a when-issued or OTC line. For the
    unfiltered pass, only accept US or Mexican listings -- a German or London
    ticker ("CSA", "DBK") wouldn't price on Yahoo as a plain symbol."""
    if pass_name == "cusip_any":
        results = [r for r in results if r.get("exchCode") in _US_EXCH | _MX_EXCH]
        results.sort(key=lambda r: r.get("exchCode") not in _US_EXCH)  # US line first if any
    if not results:
        return None
    rank = {"Common Stock": 0, "ETP": 0, "Mutual Fund": 1, "Depositary Receipt": 1, "REIT": 1}
    return min(results, key=lambda x: rank.get(x.get("securityType2") or x.get("securityType"), 5))


def _query(jobs: list[dict], headers: dict) -> list[dict]:
    while True:
        resp = requests.post(URL, json=jobs, headers=headers, timeout=60)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("ratelimit-reset", 10)) + 1)
            continue
        resp.raise_for_status()
        return resp.json()


def main():
    api_key = os.environ.get("OPENFIGI_API_KEY")
    headers = {"Content-Type": "application/json", **({"X-OPENFIGI-APIKEY": api_key} if api_key else {})}
    # No fixed pause without a key: _query sleeps until the window resets on
    # HTTP 429, which uses the full 25-requests-per-minute allowance.
    batch_size = 100 if api_key else 10

    df = ranking.load_data()
    longs = df[df["put_call"] == ""].drop_duplicates("cusip")
    resolved = performance.resolve_tickers(longs.assign(issuer_label=longs["group_key"]), use_cusip_map=False)
    need = sorted(set(resolved.loc[resolved["ticker"].isna(), "cusip"]))
    cache = _load_cache()
    print(f"{len(need)} CUSIPs unresolved by name")

    for pass_name in PASSES:
        todo = [c for c in need
                if not (cache.get(c) or {}).get("ticker")
                and pass_name not in (cache.get(c) or {}).get("tried", "").split(";")]
        applicable = [c for c in todo if _applies(pass_name, c)]
        print(f"pass {pass_name}: {len(applicable)} to query")
        for c in todo:
            if not _applies(pass_name, c):
                entry = cache.setdefault(c, {"cusip": c, "ticker": "", "tried": ""})
                entry["tried"] = ";".join(filter(None, [entry["tried"], pass_name]))
        found = 0
        for i in range(0, len(applicable), batch_size):
            batch = applicable[i:i + batch_size]
            for cusip, res in zip(batch, _query([_job(pass_name, c) for c in batch], headers)):
                entry = cache.setdefault(cusip, {"cusip": cusip, "ticker": "", "tried": ""})
                entry["tried"] = ";".join(filter(None, [entry["tried"], pass_name]))
                hit = _pick(res.get("data", []), pass_name)
                if hit:
                    found += 1
                    # FIGI writes share classes as "BRK/B" (Yahoo: "BRK-B")
                    # and Mexican cross-listings as "EA*".
                    entry.update(ticker=hit["ticker"].replace("/", "-").rstrip("*"),
                                 security_type=hit.get("securityType2") or "", name=hit.get("name") or "")
            _save_cache(cache)
            print(f"  {min(i + batch_size, len(applicable))}/{len(applicable)} ({found} found)", flush=True)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
