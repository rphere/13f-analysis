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
- **The Performance tab shows real market price returns, not 13F data.**
  It's a separate pipeline: the ~100 most widely-held securities (by
  aggregate 13F value, across *all* funds/quarters, ignoring the sidebar)
  are resolved to tickers and their 6-month/1-year returns are fetched live
  via `yfinance`. Stock tickers are resolved by name-matching against SEC's
  free `company_tickers.json`, with a small manual override list
  (`src/performance.py:STOCK_TICKER_OVERRIDES`) for well-known names whose
  13F-reported name is too abbreviated to match (e.g. "APPLIED MATLS").
  ETF tickers use a hand-curated CUSIP map
  (`src/performance.py:ETF_TICKER_BY_CUSIP`) covering the ETFs that actually
  land in that top-100 universe, since that file doesn't reliably cover
  ETPs. Anything that fails to resolve (e.g. SpaceX, which isn't publicly
  traded) is omitted — see the tab's "omitted" expander — rather than
  guessed at. This universe is capped at ~100 by design (see planning
  discussion); expanding it would need broader ticker-resolution logic.

## Pipeline

```
pip install -r requirements.txt

# 1. (Re-)verify the curated fund list's CIKs against SEC EDGAR
python src/resolve_ciks.py

# 2. Download each fund's 13F-HR information-table XML for each target quarter
python src/fetch_sec_13f.py

# 3. Parse + combine into one tidy table with pct-of-portfolio computed
python src/build_holdings.py

# 4. Launch the dashboard
streamlit run app.py
```

Re-running `fetch_sec_13f.py` is cheap — it skips any `{cik}/{quarter}.xml`
already cached in `data/raw/`. To force a refetch, delete the relevant file(s)
first.

## Refreshing for a new quarter

1. Add the new quarter's period-end date to `TARGET_QUARTERS` in
   `src/sec_utils.py` (and drop the oldest one if you want to keep a rolling
   4-quarter window).
2. Re-run steps 2-3 above.

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
    holdings_long.parquet   fund-quarter-holding tidy table (built by build_holdings.py)
src/
  sec_utils.py               shared SEC EDGAR HTTP helper + target quarters
  resolve_ciks.py             verifies/writes the fund roster's CIKs
  fetch_sec_13f.py            downloads each fund's 13F information-table XML
  parse_13f_xml.py            parses one filing's XML into tidy rows
  build_holdings.py           combines all funds/quarters into holdings_long.parquet
  ranking.py                  scoring functions consumed by the dashboard
  performance.py               ticker resolution + yfinance price-return fetch (Performance tab)
app.py                        Streamlit dashboard
```
