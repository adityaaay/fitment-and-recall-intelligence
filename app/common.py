"""Shared helpers for the Streamlit pages: read-only queries, palette, chart styling."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fitment_intel.config import get_settings  # noqa: E402

SETTINGS = get_settings()

# Categorical slots in fixed order (validated for CVD separation on adjacent pairs).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7",
          "#e34948"]
SEQUENTIAL = "#2a78d6"
# Status colours are reserved for state and always shipped with a text label.
STATUS = {"uncovered": "#d03b3b", "thin": "#ec835a", "covered": "#0ca30c"}
STATUS_LABEL = {"uncovered": "✖ Uncovered", "thin": "▲ Thin", "covered": "✔ Covered"}
MUTED = "#8a8984"


@st.cache_data(ttl=600, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    con = duckdb.connect(str(SETTINGS.warehouse_path), read_only=True)
    try:
        return con.execute(sql, list(params)).df()
    finally:
        con.close()


def warehouse_ready() -> bool:
    if not SETTINGS.warehouse_path.exists():
        return False
    try:
        return bool(query("select count(*) as n from information_schema.tables "
                          "where table_schema = 'marts'")["n"].iloc[0])
    except duckdb.Error:
        return False


def require_warehouse() -> None:
    if not warehouse_ready():
        st.error("The warehouse has not been built yet. Run `fitment run --sample` "
                 "(fixtures, ~1 minute) or `fitment run` (full NHTSA data) first.")
        st.stop()


def style(fig: go.Figure, height: int = 360) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=64, b=8),
        font=dict(family="Inter, system-ui, sans-serif", size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.04, x=0, title=None),
        hoverlabel=dict(font_size=12),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        bargap=0.25,
    )
    fig.update_xaxes(showgrid=False, linecolor=MUTED, ticks="outside", tickcolor=MUTED)
    fig.update_yaxes(gridcolor="rgba(138,137,132,0.18)", zeroline=False)
    return fig


def page_header(title: str, caption: str) -> None:
    st.title(title)
    st.caption(caption)
