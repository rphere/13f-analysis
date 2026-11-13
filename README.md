# Hedge Fund 13F Holdings Dashboard

Tracks what ~20 well-known hedge funds are buying, ranked a few different
ways, with a current-quarter snapshot and trends across the trailing year.

## Methodology & data caveats (read this first)

- **The fund list is curated, not algorithmically ranked.** There's no free
  API that ranks hedge funds by AUM or performance, so the roster in
  `data/processed/funds.csv` was assembled from public reporting (Wikipedia's
  sourced "List of hedge funds" AUM figures, general financial-press coverage
  of 2025 returns). Each row's `source_note` documents what's behind its
  numbers, and several funds have deliberately blank `aum_est_usd_billions` /
  `performance_est_pct_2025` values where no reliable public figure was found
  — see "Filling in missing fund data" below.
- **The "performance" category is one fixed list applied retroactively**
  across all 4 quarters, chosen with today's hindsight rather than re-derived
  per historical quarter. This is a known simplification (technically
  look-ahead bias) accepted for simplicity.
- **13F filings lag by up to 45 days.** The most recently *filed* quarter is
  one quarter behind the calendar "current" quarter. `TARGET_QUARTERS` in
  `src/sec_utils.py` is hardcoded to the 4 fully-filed quarters as of this
  project's build date (2025-09-30 through 2026-06-30) — update it as time
  moves on.
- **13F only covers US-listed equity long positions** with investment
  discretion — it omits shorts, derivatives, non-US securities, and cash, so
  "top holdings" means "largest disclosed long equity positions," not total
  fund exposure.
- Holdings are matched across funds by **CUSIP**, except that, within the
  *ranking* views (Snapshot/Search), multiple share classes of the same
  company (e.g. Alphabet Class A/C) are merged into one row by cleaned
  company name — see `ranking.load_data`'s `group_key` column. ETFs are
  **not** merged this way and stay keyed by CUSIP, since many distinct ETFs
  share a generic sponsor/trust name in SEC's data (e.g. "ISHARES TR").
- **The Performance and Insights tabs use real market prices, precomputed.**
  Nothing is fetched at page load: `src/build_performance.py` prices
  everything as of the last completed trading day and writes
  `data/processed/performance/` (the tabs show "as of <date>"). Re-run it to
  refresh.
  - *Performance tab:* the ~100 most widely-held securities (by aggregate
    13F value across *all* funds, ignoring the sidebar) with
    6-month/1-year/2-year/5-year returns. Anything with no ticker or no usable
    price history for *any* window is omitted with a reason (e.g. SpaceX,
    IPO'd Jun 2026).
  - *Insights tab (fund leaderboard) + Fund Drill-down returns:* the return
    of each fund's **13F-disclosed long stock/ETF book**, re-weighted at each
    quarter-end to the newest filing and chained over 6 months / 1 year. This
    is not a fund's actual net return: options are excluded (13F reports
    their underlying notional, not their value), and shorts, cash, non-US
    holdings, intra-quarter trades and fees aren't in a 13F at all. Where
    Yahoo has no price at a quarter-end (a delisted acquisition like WBD, or
    a holding with no ticker), the quarter-end price implied by the 13F itself
    (value ÷ shares) is used. Coverage is shown per fund.
- **Ticker resolution** (13F has CUSIPs + abbreviated names, not tickers), in
  order: hand-curated maps (`STOCK_TICKER_OVERRIDES`, `ETF_TICKER_BY_CUSIP` in
  `src/performance.py`); exact name match against SEC's `company_tickers.json`;
  an abbreviation-expanding prefix match that only accepts a *unique*
  company; then an OpenFIGI CUSIP->ticker map
  (`data/processed/cusip_tickers.csv`, built by `resolve_cusip_tickers.py`).
- **Options are kept separate.** `put_call` is parsed from each filing, so a
  stock and its puts/calls on the same CUSIP are distinct rows, labelled
  "(Call)"/"(Put)" in the tables.

## Pipeline

```
pip install -r requirements.txt

# 1. (Re-)verify the curated fund list's CIKs against SEC EDGAR
python src/resolve_ciks.py

# 2. Download each fund's 13F-HR information-table XML for each target quarter
python src/fetch_sec_13f.py

# 3. Parse + combine into one tidy table with pct-of-portfolio computed
python src/build_holdings.py

# 4. Map CUSIPs that name-matching can't resolve to tickers via OpenFIGI
#    (incremental + cached; ~15 min the first time without an API key --
#    set OPENFIGI_API_KEY for ~100x faster)
python src/resolve_cusip_tickers.py

# 5. Price everything as of yesterday's close (~5 min) -> data/processed/performance/
python src/build_performance.py

# 6. Launch the dashboard
streamlit run app.py
```

Re-running `fetch_sec_13f.py` is cheap — it skips any `{cik}/{quarter}.xml`
already cached in `data/raw/`. To force a refetch, delete the relevant file(s)
first.

## Refreshing for a new quarter

1. Add the new quarter's period-end date to `TARGET_QUARTERS` in
   `src/sec_utils.py` (and drop the oldest one if you want to keep a rolling
   4-quarter window).
2. Re-run steps 2-5 above. (To just refresh prices, re-run step 5 alone.)
3. `git add data/processed && git commit && git push` --
   unlike `data/raw/`, this file IS committed (see "Deploying" below), so the
   deployed app won't pick up a refresh until you push the regenerated file.

## Deploying to Streamlit Community Cloud

1. Push this repo to GitHub (already done if you're reading this on
   `github.com/rphere/13f-analysis`).
2. At [share.streamlit.io](https://share.streamlit.io), sign in with GitHub,
   click **New app**, and point it at this repo: branch `main`, main file
   path `app.py`.
3. If asked to pick a Python version, choose **3.10 or newer** (the code uses
   `X | None` type-hint syntax, which needs it).
4. Deploy. `.streamlit/config.toml` in the repo sets the theme (light,
   blue accent, sans-serif) so it matches local runs without extra setup.

The deployed app has no way to run the SEC-fetch/build pipeline itself, so it
depends on `data/processed/` (`funds.csv`, `holdings_long.parquet`,
`cusip_tickers.csv`, `performance/`) already being in the repo (all committed) -- see step 3 above for how to refresh
that data later.

## Filling in missing fund data

Several performance-category funds in `funds.csv` have blank AUM/performance
estimates because no reliably-sourced public figure was found during the
initial build. The AUM-weighted and performance-weighted rankings impute
blanks with the median of the funds that do have a value (see
`src/ranking.py:load_data`), so the dashboard still runs, but filling in real
numbers (from a source you trust — Form ADV, a paid data provider, etc.) will
make those two ranking lenses more meaningful. Edit the CSV directly; no
script writes those two columns automatically.

## Project layout

```
data/
  raw/                      cached per-fund, per-quarter 13F information-table XML
  processed/
    funds.csv               curated fund list: name, cik, category, aum/perf estimate, source
    holdings_long.parquet   fund-quarter-holding tidy table (built by build_holdings.py; committed -- see "Deploying")
    cusip_tickers.csv       OpenFIGI CUSIP->ticker cache (built by resolve_cusip_tickers.py)
    performance/            price snapshot read by the Performance/Insights tabs (built by build_performance.py)
src/
  sec_utils.py               shared SEC EDGAR HTTP helper + target quarters
  resolve_ciks.py             verifies/writes the fund roster's CIKs
  fetch_sec_13f.py            downloads each fund's 13F information-table XML
  parse_13f_xml.py            parses one filing's XML into tidy rows
  build_holdings.py           combines all funds/quarters into holdings_long.parquet
  ranking.py                  scoring functions consumed by the dashboard
  performance.py               ticker resolution, price fetch, 13F-book return calculation
  resolve_cusip_tickers.py     builds the OpenFIGI CUSIP->ticker cache
  build_performance.py         writes the as-of-yesterday price snapshot
app.py                        Streamlit dashboard
.streamlit/config.toml        theme (used locally and on Streamlit Community Cloud)
```
