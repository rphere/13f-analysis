"""Research report page: /report?ticker=BTSG.

Shows a prebuilt report from reports/html/ (see reports/build_reports.py)
inside the app. The report is a self-contained HTML page we build ourselves
(trusted content), so it runs in an iframe with its own scrolling, chapter nav
and charts.
"""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "reports" / "manifest.json"
REPORT_HEIGHT = 1100


@st.cache_data
def load_manifest(mtime: float) -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


reports = load_manifest(MANIFEST.stat().st_mtime if MANIFEST.exists() else 0.0)
ticker = str(st.query_params.get("ticker", "")).upper()

st.page_link("dashboard.py", label="Back to dashboard", icon=":material/arrow_back:")

entry = reports.get(ticker)
html_path = ROOT / entry["file"] if entry else None
if not entry or not html_path.exists():
    st.title("Research reports")
    if ticker:
        st.warning(f"There's no research report for {ticker}. Pick one below.")
    for t, r in sorted(reports.items()):
        if (ROOT / r["file"]).exists():
            st.page_link("views/report.py", label=f"{t} · {r['title']}", icon="📄", query_params={"ticker": t})
    st.stop()

stamp = pd.Timestamp(entry["generated"]).strftime("%b %-d, %Y %H:%M UTC")
st.caption(f"Report generated {stamp}. It summarizes public information as of that time and is not investment advice.")

others = [t for t in sorted(reports) if t != ticker and (ROOT / reports[t]["file"]).exists()]
if others:
    cols = st.columns(len(others) + 1)
    cols[0].markdown("**Other reports:**")
    for col, t in zip(cols[1:], others):
        col.page_link("views/report.py", label=t, query_params={"ticker": t})

st.iframe(html_path, height=REPORT_HEIGHT)
