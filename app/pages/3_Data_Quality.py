import plotly.graph_objects as go
import streamlit as st
from common import SERIES, page_header, query, require_warehouse, style

st.set_page_config(page_title="Data Quality", page_icon="🔧", layout="wide")
page_header("Data quality & pipeline health",
            "Every load is recorded, every rejected catalog row is kept with its reason, and "
            "every dbt test result is stored in the warehouse.")
require_warehouse()

st.subheader("Latest dbt build")
tests = query("""
    with latest as (
        select invocation_id from meta.dbt_run_results
        where command = 'build'
        order by recorded_at desc limit 1
    )
    select resource_type, status, count(*) as n
    from meta.dbt_run_results join latest using (invocation_id)
    group by all order by 1, 2
""")
if tests.empty:
    st.info("No dbt results recorded yet.")
else:
    pivot = tests.pivot_table(index="resource_type", columns="status", values="n",
                              fill_value=0)
    st.dataframe(pivot, use_container_width=True)
    failing = query("""
        with latest as (
            select invocation_id from meta.dbt_run_results where command = 'build'
            order by recorded_at desc limit 1
        )
        select unique_id, status, failures, message
        from meta.dbt_run_results join latest using (invocation_id)
        where status not in ('success', 'pass', 'no-op')
    """)
    if failing.empty:
        st.success("All models built and all data tests passed.")
    else:
        st.warning("Tests needing attention")
        st.dataframe(failing, hide_index=True, use_container_width=True)

st.subheader("Catalog feed validation")
dq = query("""
    with latest as (select run_id from meta.catalog_dq_runs order by run_at desc limit 1)
    select feed, rows_in, rows_valid, rows_quarantined, standardized, deduplicated,
           reason, reason_count
    from meta.catalog_dq_runs join latest using (run_id)
""")
summary = dq.drop_duplicates("feed")[["feed", "rows_in", "rows_valid", "rows_quarantined",
                                      "standardized", "deduplicated"]]
st.dataframe(summary, hide_index=True, use_container_width=True)
reasons = dq.dropna(subset=["reason"]).sort_values("reason_count")
if not reasons.empty:
    fig = go.Figure(go.Bar(
        x=reasons.reason_count, y=reasons.feed + " · " + reasons.reason, orientation="h",
        marker=dict(color=SERIES[0], cornerradius=4),
        hovertemplate="%{y}: %{x:,} rows<extra></extra>"))
    fig.update_xaxes(title="Rows quarantined")
    st.plotly_chart(style(fig, height=60 + 30 * len(reasons)), use_container_width=True)

st.subheader("Conformance to the vPIC vehicle reference")
conf = query("""
    select source, rows_total, rows_in_scope, rows_in_reference_years, matched_exact,
           matched_rule, matched_prefix, exact_match_rate, vpic_match_rate
    from marts.mart_data_conformance order by source
""")
st.dataframe(conf, hide_index=True, use_container_width=True, column_config={
    "exact_match_rate": st.column_config.ProgressColumn(
        "Exact names", min_value=0, max_value=1, format="percent"),
    "vpic_match_rate": st.column_config.ProgressColumn(
        "After conformance", min_value=0, max_value=1, format="percent")})
st.caption("NHTSA records trims and submodels (C300, F-250 SD, Accord Hybrid); the "
           "conformance layer maps them to vPIC base models by rule and longest prefix. "
           "Out-of-scope rows (motorcycles, RVs, heavy trucks) are counted, not dropped.")

st.subheader("Quarantined catalog rows (sample)")
feed = st.radio("Feed", ["catalog_fitment", "catalog_parts"], horizontal=True)
st.dataframe(query(f"select * from quarantine.{feed} limit 200"), hide_index=True,
             use_container_width=True)

st.subheader("Source loads")
st.dataframe(query("""
    select source, status, rows_loaded, columns_seen, columns_expected, etag, loaded_at
    from meta.ingest_manifest order by loaded_at desc limit 50
"""), hide_index=True, use_container_width=True)

st.subheader("Pipeline runs")
has_runs = query("""
    select count(*) as n from information_schema.tables
    where table_schema = 'meta' and table_name = 'pipeline_runs'
""")["n"].iloc[0]
if has_runs:
    st.dataframe(query("""
        select run_id, stage, status, seconds, finished_at
        from meta.pipeline_runs order by finished_at desc limit 30
    """), hide_index=True, use_container_width=True)
else:
    st.info("No orchestrated runs yet - `fitment run` records one row per stage.")
