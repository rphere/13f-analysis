"""Entry point: routes between the dashboard and the in-app research reports.

Run with: streamlit run app.py
The dashboard itself lives in dashboard.py; research reports (one page,
selected by ?ticker=) in views/report.py.
"""

import streamlit as st

st.set_page_config(page_title="13F Hedge Fund Holdings Dashboard", layout="wide")

page = st.navigation(
    [
        st.Page("dashboard.py", title="Dashboard", default=True),
        st.Page("views/report.py", title="Research report", url_path="report"),
    ],
    position="hidden",
)
page.run()
