"""Hedge Fund 13F Holdings Dashboard.

Run with: streamlit run app.py
Data must be built first: see README.md for the pipeline (resolve_ciks.py ->
fetch_sec_13f.py -> build_holdings.py).
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

import performance  # noqa: E402
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


@st.cache_data(ttl=4 * 3600, show_spinner="Fetching price performance…")
def get_performance_universe(top_n: int = 100):
    """The ~top_n most widely-held securities across ALL funds/quarters
    (ignores every sidebar filter -- see the Performance tab's note), with
    resolved tickers and 6-month/1-year price returns. Cached for 4 hours:
    this hits an external price API, and the whole point of this tab is that
    it's a slow-changing, filter-independent snapshot, not a live recompute."""
    universe = ranking.rank_stocks(df, TARGET_QUARTERS[-1], "equal_weighted", top_n=None)
    universe = universe.sort_values("total_value_usd", ascending=False).head(top_n)
    resolved = performance.resolve_tickers(universe)
    no_ticker = resolved[resolved["ticker"].isna()][["issuer_label", "asset_type"]].assign(reason="no ticker found")
    resolved = resolved[resolved["ticker"].notna()]
    returns = performance.fetch_returns(resolved["ticker"].tolist())
    merged = resolved.merge(returns, left_on="ticker", right_index=True, how="left")

    # A resolved ticker can still have zero usable price history for EVERY
    # window -- two different real causes seen in practice: WBD (Warner Bros
    # Discovery) was acquired for cash and delisted in Oct 2026, so Yahoo
    # only retains its final quote, not years of history; SPCX (SpaceX)
    # IPO'd so recently (Jun 2026) that even the 6-month lookback predates
    # its listing. Rather than a row of blank cells with no explanation,
    # treat "ticker resolved but no return could be computed" the same as
    # "no ticker" -- omitted, with a reason, not silently blank.
    return_cols = list(performance.RETURN_WINDOWS)
    has_no_data = merged[return_cols].isna().all(axis=1)
    no_price_data = merged.loc[has_no_data, ["issuer_label", "asset_type"]].assign(
        reason="no return computable for any window (too recently listed, or delisted/acquired)"
    )
    merged = merged.loc[~has_no_data]

    omitted = pd.concat([no_ticker, no_price_data], ignore_index=True)
    return merged, omitted


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

tab_snapshot, tab_fund, tab_query, tab_performance = st.tabs(["Snapshot", "Fund Drill-down", "Search", "Performance"])

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
    st.subheader("Price performance")
    st.info(
        "This tab is **independent of the sidebar filters** above — it always shows real stock/ETF price "
        "performance for the ~100 most widely-held securities across *all* tracked funds and quarters, "
        "not just your current fund/quarter/ranking selection. Use the filters below instead.",
        icon="ℹ️",
    )
    try:
        universe, omitted = get_performance_universe()
    except Exception as e:  # noqa: BLE001 -- network-dependent external call; degrade gracefully
        st.error(f"Couldn't fetch price performance right now: {e}")
    else:
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
            # get_performance_universe), which computes # of funds holding
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

def _search_result_table(data: pd.DataFrame) -> pd.DataFrame:
    display = data.assign(label=row_display_labels(data))[["fund_name", "quarter", "label", "value_usd", "pct_of_portfolio"]].copy()
    display["quarter"] = display["quarter"].map(quarter_label)
    display["pct_of_portfolio"] = (display["pct_of_portfolio"] * 100).round(2)
    return display.sort_values(["label", "quarter"]).rename(
        columns={"fund_name": "Fund", "quarter": "Quarter", "label": "Issuer", "value_usd": "Value", "pct_of_portfolio": "% of Portfolio"}
    )


with tab_query:
    st.subheader("Search by issuer name")
    query = st.text_input("Issuer name contains…")
    if query:
        matches = filtered[filtered["issuer_lower"].str.contains(query.lower(), regex=False, na=False)]
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
        st.caption("Type part of a company name to find which funds hold it, across quarters.")
