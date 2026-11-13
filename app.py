"""Hedge Fund 13F Holdings Dashboard.

Run with: streamlit run app.py
Data must be built first: see README.md for the pipeline (resolve_ciks.py ->
fetch_sec_13f.py -> build_holdings.py).
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

import performance  # noqa: E402
import picks  # noqa: E402
import ranking  # noqa: E402
from ranking import row_display_labels  # noqa: E402
from sec_utils import TARGET_QUARTERS, quarter_label  # noqa: E402

SEQUENTIAL_BLUE_MID = "#2a78d6"
NO_MODEBAR = {"displayModeBar": False}
# Numeric dtype preserved (so st.dataframe's click-to-sort sorts by actual
# value, not the string "$1.2B"/"$450M" lexicographically); Streamlit's
# built-in "compact" format displays it as 1.2B/450M while keeping the sort
# correct.
VALUE_COLUMN_CONFIG = {"Value": st.column_config.NumberColumn(format="compact"), "Total Value": st.column_config.NumberColumn(format="compact")}

st.set_page_config(page_title="13F Hedge Fund Holdings Dashboard", layout="wide")

_TRAILING_ETF_WORD = re.compile(r"\s+ETF$", re.IGNORECASE)


def strip_etf_word(label: str) -> str:
    """Display-only: drop a trailing ' ETF' so Snapshot reads e.g. 'iShares Core S&P500' instead of '...S&P500 ETF'."""
    return _TRAILING_ETF_WORD.sub("", label) if label else label


def format_usd(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    v = float(value)
    av = abs(v)
    if av >= 1e12:
        return f"${v / 1e12:.1f}T"
    if av >= 1e9:
        return f"${v / 1e9:.1f}B"
    if av >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:,.0f}"


@st.cache_data
def get_data():
    return ranking.load_data()


@st.cache_data
def get_funds():
    return pd.read_csv(ranking.FUNDS_CSV, dtype={"cik": str})


@st.cache_data
def compute_rankings(method: str, weights_items: tuple | None, fund_tuple: tuple):
    """Full-universe ranking per quarter for a given filter combo -- cached so
    switching tabs, typing in search, etc. doesn't recompute this."""
    sub = df[df["fund_name"].isin(fund_tuple)] if fund_tuple else df.iloc[0:0]
    weights = dict(weights_items) if weights_items else None
    return ranking.rank_by_quarter(sub, TARGET_QUARTERS, method, weights)


PERF_DIR = Path(__file__).parent / "data" / "processed" / "performance"


@st.cache_data
def _read_performance_snapshot(mtime: float) -> dict | None:
    if not (PERF_DIR / "meta.json").exists():
        return None
    snap = {f.stem: pd.read_parquet(f) for f in PERF_DIR.glob("*.parquet")}
    snap["meta"] = json.loads((PERF_DIR / "meta.json").read_text())
    snap["as_of_label"] = pd.Timestamp(snap["meta"]["as_of"]).strftime("%b %-d, %Y")
    return snap


def get_performance_snapshot() -> dict | None:
    """Precomputed by src/build_performance.py (prices as of the last close)
    -- nothing is fetched at page load. Keyed on meta.json's mtime, so
    re-running the build is picked up without restarting the app."""
    meta = PERF_DIR / "meta.json"
    return _read_performance_snapshot(meta.stat().st_mtime if meta.exists() else 0.0)


def snapshot_missing_notice():
    st.info("No price snapshot yet. Build it with `python src/build_performance.py`, then reload.")


@st.cache_data
def cached_fund_holdings(fund_name: str, quarter: str):
    return ranking.fund_top_holdings(df, fund_name, quarter)


def split_by_asset_type(ranked: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    return ranked[ranked["asset_type"] == "Stock"], ranked[ranked["asset_type"] == "ETF"]


def ranked_table(ranked: pd.DataFrame) -> pd.DataFrame:
    # "score" is an internal ranking metric (used to sort/rank and to size the
    # bars in bar_chart) -- not shown to the user, who'd have no way to
    # interpret a dimensionless composite number. "total_value_usd" stays
    # numeric (not pre-formatted to a string) so st.dataframe's column sort
    # is by actual value, not lexicographic on "$1.2B" vs "$450M" text.
    out = ranked[["issuer_label", "num_funds", "total_value_usd"]].copy()
    out.loc[ranked["asset_type"] == "ETF", "issuer_label"] = out.loc[ranked["asset_type"] == "ETF", "issuer_label"].map(strip_etf_word)
    return out.rename(columns={"issuer_label": "Issuer", "num_funds": "# Funds Holding", "total_value_usd": "Total Value"})


def bar_chart(ranked: pd.DataFrame):
    plot_df = ranked.sort_values("score").copy()
    is_etf = plot_df["asset_type"] == "ETF"
    plot_df.loc[is_etf, "issuer_label"] = plot_df.loc[is_etf, "issuer_label"].map(strip_etf_word)
    plot_df["total_value_display"] = plot_df["total_value_usd"].map(format_usd)
    fig = px.bar(
        plot_df,
        x="score",
        y="issuer_label",
        orientation="h",
        color_discrete_sequence=[SEQUENTIAL_BLUE_MID],
        labels={"issuer_label": ""},
        custom_data=["num_funds", "total_value_display"],
    )
    # Bar length still encodes relative rank (that's the point of the chart);
    # the underlying score number itself isn't user-meaningful, so the axis
    # scale and raw value are hidden from both the axis and the hover tooltip.
    fig.update_traces(hovertemplate="<b>%{y}</b><br># Funds Holding: %{customdata[0]}<br>Total Value: %{customdata[1]}<extra></extra>")
    fig.update_xaxes(visible=False)
    fig.update_layout(template="plotly_white", height=max(300, 24 * len(ranked)), margin=dict(l=0, r=10, t=40, b=10))
    return fig


WINDOW_LABELS = {"return_6mo_pct": "6-Month Return", "return_1yr_pct": "1-Year Return"}


PCT = st.column_config.NumberColumn(format="%+.1f%%")
PTS = st.column_config.NumberColumn(format="%+.1f pts")
METHOD_NOTE = (
    "- Over each window, the fund is assumed to hold its **most recent quarter-end 13F portfolio** "
    "(buy-and-hold), switching to the next quarter's holdings at each quarter-end; the latest filed "
    "quarter is carried forward to the as-of date. Period returns are chained.\n"
    "- Every **long stock/ETF position** counts, weighted by reported value. Prices are daily closes from "
    "Yahoo Finance; where Yahoo has none at a quarter-end (e.g. a stock delisted after an acquisition, or "
    "a holding with no ticker), the **quarter-end price implied by the 13F itself** (reported value ÷ "
    "shares) is used instead. Anything still unpriced is dropped and the rest re-weighted — *Priced* is "
    "the share that was kept, in the weakest quarter.\n"
    "- Options are excluded (13F reports their underlying notional, not their value). Shorts, cash, "
    "non-US holdings, intra-quarter trades and fees don't appear in a 13F at all — so this measures "
    "**the stock/ETF picks a fund discloses**, not what its investors actually earned."
)


def render_fund_returns(fund_name: str):
    """Fund Drill-down: returns for the ONE fund picked there."""
    st.divider()
    snap = get_performance_snapshot()
    st.markdown(f"**13F stock & ETF returns** (as of {snap['as_of_label']})" if snap else "**13F stock & ETF returns**")
    if snap is None:
        snapshot_missing_notice()
        return
    st.caption("Return of only the stocks and ETFs this fund lists in its 13F, weighted by reported value — not the "
               "fund's actual net return. Ignores the sidebar quarter. See the Insights tab to compare funds.")

    summary = snap["fund_returns"]
    fund_summary = summary[summary["fund_name"] == fund_name]
    if fund_summary.empty:
        st.caption("No returns computed for this fund.")
        return
    cols = st.columns(len(fund_summary))
    for col, row in zip(cols, fund_summary.itertuples()):
        with col:
            if pd.isna(row.fund_return_pct):
                st.metric(WINDOW_LABELS[row.window], "—")
                st.caption(row.note or "Not enough holdings history.")
                continue
            st.metric(
                WINDOW_LABELS[row.window],
                f"{row.fund_return_pct:+.1f}%",
                delta=f"{row.fund_return_pct - row.benchmark_return_pct:+.1f} pts vs S&P 500 ({row.benchmark_return_pct:+.1f}%)",
            )
            st.caption(f"Priced {row.coverage:.1%} of the long stock/ETF book (weakest quarter).")
            if row.note:
                st.warning(row.note, icon="⚠️")

    opts = snap["fund_option_share"]
    opts = opts[opts["fund_name"] == fund_name].sort_values("quarter")
    if not opts.empty and opts["option_share"].iloc[-1] >= 0.01:
        st.caption(f"{opts['option_share'].iloc[-1]:.0%} of this fund's latest reported 13F value is options (puts/calls), which are excluded.")

    gaps = snap["fund_gaps"]
    gaps = gaps[gaps["fund_name"] == fund_name].sort_values("weight", ascending=False)
    if not gaps.empty:
        with st.expander(f"{len(gaps)} current position(s) not priced — {gaps['weight'].sum():.1%} of the long book"):
            st.dataframe(
                gaps.assign(weight=gaps["weight"] * 100)[["group_key", "cusip", "ticker", "weight", "reason"]].rename(
                    columns={"group_key": "Issuer", "cusip": "CUSIP", "ticker": "Ticker", "weight": "% of Long Book", "reason": "Reason"}),
                hide_index=True, width="stretch",
                column_config={"% of Long Book": st.column_config.NumberColumn(format="%.2f%%")},
            )

    with st.expander("How this is calculated"):
        st.markdown(METHOD_NOTE)
        seg = snap["fund_segments"]
        seg = seg[seg["fund_name"] == fund_name].drop(columns="fund_name")
        seg = seg.assign(window=seg["window"].map(WINDOW_LABELS), holdings_quarter=seg["holdings_quarter"].map(quarter_label),
                         coverage=seg["coverage"] * 100).rename(columns={
            "window": "Window", "from": "From", "to": "To", "holdings_quarter": "Holdings Used",
            "return_pct": "Period Return", "coverage": "Priced", "via_13f_prices": "Priced via 13F"})
        seg["Priced via 13F"] = seg["Priced via 13F"] * 100
        st.dataframe(seg, hide_index=True, width="stretch", column_config={
            "Period Return": st.column_config.NumberColumn(format="%.1f%%"),
            "Priced": st.column_config.NumberColumn(format="%.1f%%"),
            "Priced via 13F": st.column_config.NumberColumn(format="%.1f%%", help="Share of the book priced with a "
                                                            "13F-implied quarter-end price because Yahoo had none.")})


PICKS_NOTE = (
    "Each candidate gets a **radar score** = 45% performance + 35% under-the-radar breadth + 20% accumulation, "
    "each a percentile within stocks or within ETFs:\n"
    "- **Performance:** average percentile of the 6-month, 1-year, 2-year and 5-year price returns. A name needs at "
    "least 3 of the 4 windows, and must be up over both 6 months and 1 year, so it's still working, not a spike "
    "that already crashed.\n"
    "- **Under-the-radar breadth:** how many tracked funds hold it (and how many hold it as a *conviction* "
    "position, at least 0.05% of their long book), relative to its total 13F dollar value. Many holders with a "
    "small dollar footprint means funds like it more than its size would suggest.\n"
    "- **Accumulation:** how many funds were added since the oldest tracked quarter.\n\n"
    "**Excluded:** the ~100 largest holdings by total 13F value (Mag-7, big-cap tech, and the rest of the "
    "Performance tab), plus a list of famous tickers as a safety net. Stocks need at least 5 holders, 2 of them "
    "conviction holders. ETFs need at least 4 holders and a sector, commodity or country theme, so no broad index, "
    "style/factor, bond or leveraged products. At most one ETF per theme family.\n\n"
    "Based on 13F filings (up to 45 days stale) and past prices. Not investment advice."
)


def _pick_card(row):
    ticker = row["ticker"]
    url = f"https://www.google.com/search?q={quote_plus(ticker + ' stock')}"
    with st.container(border=True):
        st.markdown(f"#### [{ticker} ↗]({url})")
        name = strip_etf_word(row["issuer_label"]).title()
        st.caption(f"{name} · {row['theme']}" if isinstance(row.get("theme"), str) else name)
        delta = int(row["num_funds"] - row["num_funds_prev"])
        cols = st.columns(5)
        cols[0].markdown(f"**{int(row['num_funds'])}** funds" + (f"  \n{delta:+d} in 1y" if delta else ""))
        for col, key, label in zip(cols[1:], picks.RETURN_COLS, ["6M", "1Y", "2Y", "5Y"]):
            v = row[key]
            col.markdown(f"**{label}**  \n" + ("—" if pd.isna(v) else f":{'green' if v >= 0 else 'red'}[{v:+.0f}%]"))
        st.caption(row["why"])


def render_radar_picks():
    """Insights tab, above the leaderboard: 3 stock + 3 ETF picks (see src/picks.py)."""
    snap = get_performance_snapshot()
    st.subheader(f"Under-the-radar picks (as of {snap['as_of_label']})" if snap else "Under-the-radar picks")
    if snap is None or "pick_candidates" not in snap:
        st.info("No picks pool yet. Build it with `python src/build_performance.py --only picks`, then reload.")
        return
    st.caption("Names many tracked funds hold **relative to how big they are**, with strong returns over 6 months "
               "to 5 years. Mega-caps and well-known tech are excluded. Uses all funds, so it ignores the sidebar "
               "filters. Click a ticker to search it on Google.")
    top = picks.score_picks(snap["pick_candidates"])
    for col, (asset_type, title) in zip(st.columns(2), [("Stock", "Stocks"), ("ETF", "ETFs")]):
        with col:
            st.markdown(f"**{title}**")
            if top[asset_type].empty:
                st.caption("No candidates passed the filters.")
            for _, row in top[asset_type].iterrows():
                _pick_card(row)
    with st.expander("How picks are chosen"):
        st.markdown(PICKS_NOTE)
    st.divider()


def render_leaderboard(fund_names: list[str]):
    """Insights tab: every sidebar-selected fund ranked side by side."""
    snap = get_performance_snapshot()
    st.subheader(f"Fund leaderboard (as of {snap['as_of_label']})" if snap else "Fund leaderboard")
    if snap is None:
        snapshot_missing_notice()
        return
    st.caption("Which funds' disclosed **stock & ETF picks** did best — each fund's 13F long book, re-weighted every "
               "quarter, vs the S&P 500 over the same dates. Not the funds' actual net returns. Respects the "
               "sidebar fund filter; ignores the sidebar quarter.")
    summary = snap["fund_returns"]
    wide = summary.pivot(index="fund_name", columns="window")
    bench = summary.groupby("window")["benchmark_return_pct"].first()
    r1, r6 = wide[("fund_return_pct", "return_1yr_pct")], wide[("fund_return_pct", "return_6mo_pct")]
    board = pd.DataFrame({
        "Fund": wide.index,
        "1-Year Return": r1.values,
        "vs S&P 500 (1-Yr)": (r1 - bench["return_1yr_pct"]).values,
        "6-Month Return": r6.values,
        "vs S&P 500 (6-Mo)": (r6 - bench["return_6mo_pct"]).values,
        "Priced": (wide[("coverage", "return_1yr_pct")] * 100).values,
        "Note": wide[("note", "return_1yr_pct")].values,
    })
    board = board[board["Fund"].isin(fund_names)].sort_values("1-Year Return", ascending=False, na_position="last")
    if board.empty:
        st.info("Select at least one fund in the sidebar.")
        return
    board.insert(0, "Rank", range(1, len(board) + 1))
    st.dataframe(board, hide_index=True, width="stretch", column_config={
        "1-Year Return": PCT, "6-Month Return": PCT, "vs S&P 500 (1-Yr)": PTS, "vs S&P 500 (6-Mo)": PTS,
        "Priced": st.column_config.NumberColumn(format="%.1f%%", help="Share of the fund's long stock/ETF book (by value) "
                                                "that could be priced, in its weakest quarter. Lower = less reliable."),
    })
    st.caption(f"S&P 500 (SPY) over the same dates: 1-year {bench['return_1yr_pct']:+.1f}%, 6-month "
               f"{bench['return_6mo_pct']:+.1f}%. Click a column header to re-sort. Per-fund detail is in Fund Drill-down.")
    with st.expander("How this is calculated"):
        st.markdown(METHOD_NOTE)


df = get_data()
funds_df = get_funds()

st.title("Hedge Fund 13F Holdings Dashboard")
st.caption(
    "Top hedge fund stock holdings sourced directly from SEC EDGAR 13F-HR filings. "
    "The fund roster is a curated list (not a live ranking feed)."
)

if df.empty:
    st.warning("No holdings data found yet. Run the pipeline first: `python src/fetch_sec_13f.py && python src/build_holdings.py`")
    st.stop()

with st.sidebar:
    st.header("Filters")
    quarter = st.selectbox(
        "Quarter",
        TARGET_QUARTERS[::-1],
        index=0,
        format_func=lambda q: quarter_label(q) + (" (most recently filed)" if q == TARGET_QUARTERS[-1] else ""),
    )
    fund_names = funds_df["name"].tolist()
    selected_funds = st.multiselect("Funds", fund_names, default=fund_names)

    method_label = st.radio(
        "Ranking method",
        ["AUM-weighted", "Performance-weighted"],
        help="AUM-weighted surfaces stocks favored by large funds. Performance-weighted surfaces "
        "stocks favored by top-performing funds.",
    )
    method_map = {"AUM-weighted": "aum_weighted", "Performance-weighted": "performance_weighted"}
    method = method_map[method_label]
    top_n = st.slider("Show top N per group", 5, 50, 20)

weights_items = None
fund_tuple = tuple(sorted(selected_funds))
filtered = df[df["fund_name"].isin(selected_funds)] if selected_funds else df.iloc[0:0]
rankings_by_quarter = compute_rankings(method, weights_items, fund_tuple)

tab_snapshot, tab_fund, tab_query, tab_performance, tab_insights = st.tabs(["Snapshot", "Fund Drill-down", "Search", "Performance", "Insights"])

with tab_snapshot:
    st.subheader(f"Top holdings — {quarter_label(quarter)}")
    full_ranked = rankings_by_quarter[quarter]
    if full_ranked.empty:
        st.info("No data for this quarter / fund selection. Check your filters, or run the data pipeline if this is a fresh checkout.")
    else:
        stocks, etfs = split_by_asset_type(full_ranked)
        col_stocks, col_etfs = st.columns(2)
        with col_stocks:
            st.markdown(f"**Stocks** (top {top_n})")
            top_stocks = stocks.head(top_n)
            if top_stocks.empty:
                st.caption("No stocks in this selection.")
            else:
                st.plotly_chart(bar_chart(top_stocks), width="stretch", config=NO_MODEBAR)
                st.dataframe(ranked_table(top_stocks), width="stretch", hide_index=True, column_config=VALUE_COLUMN_CONFIG)
        with col_etfs:
            st.markdown(f"**ETFs** (top {top_n})")
            top_etfs = etfs.head(top_n)
            if top_etfs.empty:
                st.caption("No ETFs in this selection.")
            else:
                st.plotly_chart(bar_chart(top_etfs), width="stretch", config=NO_MODEBAR)
                st.dataframe(ranked_table(top_etfs), width="stretch", hide_index=True, column_config=VALUE_COLUMN_CONFIG)

with tab_performance:
    perf_snap = get_performance_snapshot()
    st.subheader(f"Price performance (as of {perf_snap['as_of_label']})" if perf_snap else "Price performance")
    st.info(
        "This tab is **independent of the sidebar filters** above — it always shows real stock/ETF price "
        "performance for the ~100 most widely-held securities across *all* tracked funds and quarters, "
        "not just your current fund/quarter/ranking selection. Use the filters below instead.",
        icon="ℹ️",
    )
    if perf_snap is None:
        snapshot_missing_notice()
    else:
        universe, omitted = perf_snap["security_returns"], perf_snap["security_omitted"]
        col_filter, col_search = st.columns([1, 2])
        with col_filter:
            asset_filter = st.radio("Show", ["All", "Stocks", "ETFs"], horizontal=True, key="perf_asset_filter")
        with col_search:
            perf_query = st.text_input("Search by name…", key="perf_search")

        view = universe
        if asset_filter != "All":
            view = view[view["asset_type"] == asset_filter[:-1]]  # "Stocks" -> "Stock", "ETFs" -> "ETF"
        if perf_query:
            view = view[view["issuer_label"].str.contains(perf_query, case=False, na=False)]

        if view.empty:
            st.caption("No matches.")
        else:
            view = view.assign(display_label=view["issuer_label"].where(view["asset_type"] != "ETF", view["issuer_label"].map(strip_etf_word)))
            # Default sort; click any column header in the table to re-sort by it instead.
            view = view.sort_values("return_1yr_pct", ascending=False)

            # num_funds already comes along for free: the universe this tab
            # fetches prices for IS the 13F ranking's output (see
            # performance.security_universe), which computes # of funds holding
            # each security as part of that ranking -- no separate join needed.
            table = view[["display_label", "ticker", "num_funds", "return_6mo_pct", "return_1yr_pct", "return_2yr_pct", "return_5yr_pct"]].rename(
                columns={
                    "display_label": "Issuer",
                    "ticker": "Ticker",
                    "num_funds": "# Funds Holding (current qtr)",
                    "return_6mo_pct": "6-Month Return",
                    "return_1yr_pct": "1-Year Return",
                    "return_2yr_pct": "2-Year Return",
                    "return_5yr_pct": "5-Year Return",
                }
            )
            st.dataframe(
                table,
                width="stretch",
                hide_index=True,
                column_config={
                    "6-Month Return": st.column_config.NumberColumn(format="%.1f%%"),
                    "1-Year Return": st.column_config.NumberColumn(format="%.1f%%"),
                    "2-Year Return": st.column_config.NumberColumn(format="%.1f%%"),
                    "5-Year Return": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )

        if not omitted.empty:
            with st.expander(f"{len(omitted)} security(ies) omitted"):
                st.dataframe(
                    omitted.rename(columns={"issuer_label": "Issuer", "asset_type": "Type", "reason": "Reason"}),
                    hide_index=True,
                    width="stretch",
                )

with tab_fund:
    st.subheader("Fund drill-down")
    if not selected_funds:
        st.info("Select at least one fund in the sidebar.")
    else:
        pick_fund = st.selectbox("Fund", selected_funds)
        fh = cached_fund_holdings(pick_fund, quarter)
        if fh.empty:
            st.info("No holdings for this fund/quarter.")
        else:
            fh_stocks = fh[fh["asset_type"] == "Stock"]
            fh_etfs = fh[fh["asset_type"] == "ETF"]
            col_s, col_e = st.columns(2)
            for col, title, data in [(col_s, "Stock holdings", fh_stocks), (col_e, "ETF holdings", fh_etfs)]:
                with col:
                    st.markdown(f"**{title}**")
                    if data.empty:
                        st.caption("None.")
                        continue
                    # row_display_labels (not plain clean_label(issuer)) because SEC
                    # data often reports a generic issuer name shared by many distinct
                    # ETFs (e.g. "ISHARES TR") -- without title_of_class, a fund
                    # holding several different iShares funds would show the same
                    # label for all of them.
                    plot_df = data.assign(label=row_display_labels(data)).sort_values("pct_of_portfolio")
                    fig = px.bar(
                        plot_df,
                        x="pct_of_portfolio",
                        y="label",
                        orientation="h",
                        color_discrete_sequence=[SEQUENTIAL_BLUE_MID],
                        labels={"pct_of_portfolio": "% of portfolio", "label": ""},
                    )
                    fig.update_layout(template="plotly_white", height=max(300, 24 * len(data)), margin=dict(l=0, r=10, t=40, b=10))
                    st.plotly_chart(fig, width="stretch", config=NO_MODEBAR)
                    display = data.assign(label=row_display_labels(data))[["label", "value_usd", "pct_of_portfolio"]].copy()
                    display["pct_of_portfolio"] = (display["pct_of_portfolio"] * 100).round(2)
                    st.dataframe(
                        display.rename(columns={"label": "Issuer", "value_usd": "Value", "pct_of_portfolio": "% of Portfolio"}),
                        width="stretch",
                        hide_index=True,
                        column_config=VALUE_COLUMN_CONFIG,
                    )

        render_fund_returns(pick_fund)

def _search_result_table(data: pd.DataFrame) -> pd.DataFrame:
    display = data.assign(label=row_display_labels(data))[["fund_name", "quarter", "label", "ticker", "value_usd", "pct_of_portfolio"]].copy()
    display["quarter"] = display["quarter"].map(quarter_label)
    display["pct_of_portfolio"] = (display["pct_of_portfolio"] * 100).round(2)
    return display.sort_values(["label", "quarter"]).rename(
        columns={"fund_name": "Fund", "quarter": "Quarter", "label": "Issuer", "ticker": "Ticker", "value_usd": "Value",
                 "pct_of_portfolio": "% of Portfolio"}
    )


def ticker_by_cusip() -> pd.Series:
    """CUSIP -> ticker, precomputed by build_performance.py (empty if not built)."""
    snap = get_performance_snapshot()
    if snap is None or "ticker_lookup" not in snap:
        return pd.Series(dtype=str)
    return snap["ticker_lookup"].set_index("cusip")["ticker"]


with tab_query:
    st.subheader("Search by issuer name or ticker")
    query = st.text_input("Issuer name contains, or exact ticker…").strip()
    if query:
        # Exact (not substring) ticker match: "a" shouldn't hit every ticker containing an A.
        tickers = filtered["cusip"].map(ticker_by_cusip())
        by_name = filtered["issuer_lower"].str.contains(query.lower(), regex=False, na=False)
        by_ticker = tickers.str.upper().eq(query.upper()).fillna(False)
        matches = filtered[by_name | by_ticker].assign(ticker=tickers)
        m_stocks = matches[matches["asset_type"] == "Stock"]
        m_etfs = matches[matches["asset_type"] == "ETF"]

        # Only split into two side-by-side columns when BOTH have results --
        # an empty side (e.g. no ETF matches for "amazon") is dropped
        # entirely rather than shown as a bare "No matches" column.
        sections = [("Matching stocks", m_stocks), ("Matching ETFs", m_etfs)]
        non_empty = [(title, data) for title, data in sections if not data.empty]
        if not non_empty:
            st.info("No matches.")
        elif len(non_empty) == 1:
            title, data = non_empty[0]
            st.markdown(f"**{title}**")
            st.dataframe(_search_result_table(data), width="stretch", hide_index=True, column_config=VALUE_COLUMN_CONFIG)
        else:
            col_s, col_e = st.columns(2)
            for col, (title, data) in zip([col_s, col_e], non_empty):
                with col:
                    st.markdown(f"**{title}**")
                    st.dataframe(_search_result_table(data), width="stretch", hide_index=True, column_config=VALUE_COLUMN_CONFIG)
    else:
        st.caption("Type part of a company name (e.g. brightspring) or a ticker (e.g. BTSG) to find which funds hold it, across quarters.")

with tab_insights:
    render_radar_picks()
    render_leaderboard(selected_funds)
