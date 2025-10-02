"""
Streamlit FRED Explorer

Quick start:
1) Save this file as app.py
2) Create a virtual env and install deps:
   pip install streamlit fredapi plotly pandas python-dateutil requests
3) Set your FRED API key (https://fred.stlouisfed.org/docs/api/api_key.html):
   macOS/Linux: export FRED_API_KEY=your_key_here
   Windows (Powershell): setx FRED_API_KEY your_key_here
4) Run the app:
   streamlit run app.py

Notes:
- No API key? The app will still render but you must provide one to fetch data.
- Data source: Federal Reserve Economic Data (FRED)
"""

import os
from datetime import date
from typing import List, Dict

import pandas as pd
import plotly.express as px
import streamlit as st
from dateutil.relativedelta import relativedelta
import requests

try:
    from fredapi import Fred
except Exception:
    Fred = None

APP_TITLE = "FRED Data Explorer"
DEFAULT_START = date.today() - relativedelta(years=10)
DEFAULT_END = date.today()

UNITS = {
    "lin": "Index (no transformation)",
    "chg": "Change from previous value",
    "ch1": "Change from year ago",
    "pch": "% change from previous value",
    "pc1": "% change from year ago",
    "pca": "% change annualized",
    "cch": "Compounded change from previous value",
    "cca": "Compounded annual rate of change",
    "log": "Natural log",
}

FREQUENCIES = {
    "": "Use native frequency",
    "d": "Daily",
    "w": "Weekly",
    "bw": "Bi-Weekly",
    "m": "Monthly",
    "q": "Quarterly",
    "sa": "Semi-Annual",
    "a": "Annual",
}

AGGREGATIONS = {
    "avg": "Average",
    "sum": "Sum",
    "eop": "End of Period",
}

# -------------- Helpers -------------- #
@st.cache_resource(show_spinner=False)
def get_fred(api_key: str):
    if not api_key:
        raise RuntimeError("Missing FRED API key. Set FRED_API_KEY env var.")
    return Fred(api_key=api_key)

@st.cache_data(show_spinner=False)
def fred_search(_fred: "Fred", api_key: str, query: str, limit: int = 50) -> pd.DataFrame:
    if not query.strip():
        return pd.DataFrame()
    try:
        df = _fred.search(
            text=query,
            order_by="popularity",
            sort_order="desc",
            limit=limit,
        )
    except Exception:
        # Fallback: call FRED REST API directly to avoid pandas timestamp parsing issues in fredapi
        params = {
            "api_key": api_key,
            "search_text": query,
            "file_type": "json",
            "limit": limit,
            "order_by": "popularity",
            "sort_order": "desc",
        }
        resp = requests.get("https://api.stlouisfed.org/fred/series/search", params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("seriess", [])
        df = pd.DataFrame(rows)

    keep_cols = [
        "id",
        "title",
        "frequency",
        "units",
        "seasonal_adjustment",
        "observation_start",
        "observation_end",
        "popularity",
    ]
    out = df[keep_cols].copy()
    # Convert problematic date-like columns to strings
    for col in ("observation_start", "observation_end"):
        if col in out.columns:
            out[col] = out[col].astype(str)
    return out.reset_index(drop=True)

@st.cache_data(show_spinner=False)
def fetch_series(
    _fred: "Fred",
    series_ids: List[str],
    start: date,
    end: date,
    units: str,
    frequency: str,
    aggregation_method: str,
) -> pd.DataFrame:
    frames = []
    for sid in series_ids:
        s = _fred.get_series(
            sid,
            observation_start=start,
            observation_end=end,
            units=units or None,
            frequency=frequency or None,
            aggregation_method=aggregation_method or None,
        )
        frames.append(s.rename(sid).to_frame())
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, axis=1)
    df.index.name = "Date"
    return df

# -------------- UI -------------- #
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# Sidebar: API key & global options
with st.sidebar:
    st.markdown("### Connection")
    api_key = st.text_input(
        "FRED API Key",
        value=os.getenv("FRED_API_KEY", ""),
        type="password",
        help="Store in env var FRED_API_KEY or paste here.",
    )
    if Fred is None:
        st.error("fredapi is not installed. Run: pip install fredapi")
    elif not api_key:
        st.info("Enter your FRED API key to begin.")

    st.markdown("---")
    st.markdown("### Query Options")
    units_key = st.selectbox("Transformation (units)", options=list(UNITS.keys()), format_func=lambda k: UNITS[k], index=0)
    freq_key = st.selectbox("Frequency (aggregation target)", options=list(FREQUENCIES.keys()), format_func=lambda k: FREQUENCIES[k], index=0)
    agg_key = st.selectbox("Aggregation method (if downsampling)", options=list(AGGREGATIONS.keys()), format_func=lambda k: AGGREGATIONS[k], index=0)

    st.markdown("---")
    st.markdown("### Date Range")
    start_date = st.date_input("Start", value=DEFAULT_START)
    end_date = st.date_input("End", value=DEFAULT_END)

# Main: Search & select
col1, col2 = st.columns([1, 1])
with col1:
    st.subheader("Search FRED series")
    query = st.text_input("Keyword(s)", placeholder="e.g., CPI, unemployment rate, M2, GDP")
    limit = st.slider("Max results", min_value=10, max_value=200, value=50, step=10)
    do_search = st.button("Search", type="primary", use_container_width=True)

    results_df = pd.DataFrame()
    fred_client = None
    if api_key and Fred is not None:
        try:
            fred_client = get_fred(api_key)
        except Exception as e:
            st.error(f"Could not connect to FRED: {e}")

    if do_search and fred_client is not None:
        try:
            results_df = fred_search(fred_client, api_key, query, limit)
            if results_df.empty:
                st.warning("No results. Try different keywords.")
        except Exception as e:
            st.error(f"Search failed: {e}")

    if not results_df.empty:
        st.dataframe(results_df, use_container_width=True, hide_index=True)

with col2:
    st.subheader("Pick series to plot")
    if not results_df.empty:
        options = results_df[["id", "title"]].apply(lambda r: f"{r['id']} — {r['title']}", axis=1).tolist()
        selection = st.multiselect("Results", options=options, help="Select one or more series to add to the chart")
        chosen_ids = [opt.split(" — ")[0] for opt in selection]
    else:
        chosen_text = st.text_input("Series IDs (comma-separated)", placeholder="CPIAUCSL, UNRATE")
        chosen_ids = [s.strip() for s in chosen_text.split(",") if s.strip()]

    st.caption("Tip: You can combine search selection with manual IDs. Duplicate IDs are deduped.")

    manual_add = st.text_input("Add series ID", placeholder="e.g., DGS10")
    add_clicked = st.button("Add", use_container_width=True)
    if add_clicked and manual_add:
        chosen_ids = list(dict.fromkeys(chosen_ids + [manual_add.strip()]))

    if chosen_ids:
        st.write("**Selected series:**", ", ".join(chosen_ids))

st.markdown("---")

# Fetch & plot
if chosen_ids and fred_client is not None:
    try:
        df = fetch_series(
            fred_client,
            series_ids=list(dict.fromkeys(chosen_ids)),
            start=start_date,
            end=end_date,
            units=units_key,
            frequency=freq_key,
            aggregation_method=agg_key,
        )
        if df.empty:
            st.warning("No observations for the selected range.")
        else:
            names = {}
            try:
                for sid in df.columns:
                    info = fred_client.get_series_info(sid)
                    names[sid] = f"{sid}: {info.title}" if hasattr(info, "title") else sid
            except Exception:
                names = {sid: sid for sid in df.columns}

            df_named = df.rename(columns=names)
            fig = px.line(df_named, x=df_named.index, y=df_named.columns, labels={"x": "Date", "value": "Value", "variable": "Series"})
            fig.update_layout(legend_title_text="Series", hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

            with st.expander("Summary stats"):
                st.dataframe(df_named.describe().T, use_container_width=True)

            csv = df_named.to_csv(index=True).encode("utf-8")
            st.download_button(
                label="Download CSV",
                data=csv,
                file_name="fred_data.csv",
                mime="text/csv",
                use_container_width=True,
            )
    except Exception as e:
        st.error(f"Data fetch/plot failed: {e}")
else:
    st.info("Use the search on the left, select one or more series, and I'll plot them here.")

# Footer
st.markdown(
    """
    <div style='text-align:center; opacity:0.7; font-size:0.9em;'>
    Built with <a href='https://streamlit.io' target='_blank'>Streamlit</a> · Powered by <a href='https://fred.stlouisfed.org/' target='_blank'>FRED</a>
    </div>
    """,
    unsafe_allow_html=True,
)
