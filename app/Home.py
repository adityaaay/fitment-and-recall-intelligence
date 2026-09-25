import plotly.graph_objects as go
import streamlit as st
from common import SERIES, STATUS, STATUS_LABEL, page_header, query, require_warehouse, style

st.set_page_config(page_title="Fitment Intelligence", page_icon="🔧", layout="wide")
page_header(
    "Fitment & Recall Intelligence",
    "NHTSA recalls and owner complaints joined to an aftermarket parts catalog - "
    "where is field-failure demand outrunning catalog coverage?",
)
require_warehouse()

kpi = query("""
    select
        (select count(*) from marts.fct_complaint)                        as complaints,
        (select count(*) from marts.dim_recall_campaign)                  as campaigns,
        (select count(*) from marts.dim_vehicle where in_vpic)            as vehicles,
        (select count(*) from marts.dim_part where part_status = 'Active') as parts,
        (select count(*) from marts.fct_part_fitment)                     as fitments,
        (select count(*) filter (where coverage_status = 'uncovered')
         from marts.mart_coverage_gap where complaints_recent > 0)        as uncovered_pairs,
        (select max(received_date) from marts.fct_complaint)              as as_of
""").iloc[0]

row1, row2 = st.columns(3), st.columns(3)
row1[0].metric("Owner complaints", f"{kpi.complaints:,}")
row1[1].metric("Recall campaigns", f"{kpi.campaigns:,}")
row1[2].metric("Vehicles in reference", f"{kpi.vehicles:,}",
               help="Make / model / model-year combinations in NHTSA vPIC")
row2[0].metric("Active parts", f"{kpi.parts:,}")
row2[1].metric("Part-vehicle fitments", f"{kpi.fitments:,}")
row2[2].metric("Uncovered demand pairs", f"{kpi.uncovered_pairs:,}",
               help="Vehicle x part-category pairs with recent complaints and no catalog part")
st.caption(f"Complaint data through {kpi.as_of:%B %d, %Y}.")

left, right = st.columns([3, 2])

with left:
    st.subheader("Complaints by part category, monthly")
    trend = query("""
        select month, part_category, sum(complaints) as complaints
        from marts.mart_component_trends
        where part_category in (
            select part_category from marts.mart_component_trends
            where part_category in (select part_category from marts.dim_component
                                    where aftermarket_serviceable)
            group by 1 order by sum(complaints) desc limit 5)
          and month >= (select max(month) from marts.mart_component_trends) - interval 5 year
          and month < date_trunc('month', (select max(received_date) from marts.fct_complaint))
        group by all order by month
    """)
    fig = go.Figure()
    order = trend.groupby("part_category")["complaints"].sum().sort_values(ascending=False)
    for i, category in enumerate(order.index):
        d = trend[trend.part_category == category]
        fig.add_scatter(x=d.month, y=d.complaints, name=category, mode="lines",
                        line=dict(width=2, color=SERIES[i]),
                        hovertemplate="%{x|%b %Y}: %{y:,} complaints<extra>" + category
                        + "</extra>")
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(style(fig), use_container_width=True)
    st.caption("Top five aftermarket-serviceable categories by volume; the current "
               "partial month is excluded.")

with right:
    st.subheader("Coverage of recent demand")
    status = query("""
        select coverage_status, count(*) as pairs, sum(complaints_recent) as complaints
        from marts.mart_coverage_gap
        where complaints_recent > 0
        group by 1
    """).set_index("coverage_status").reindex(["covered", "thin", "uncovered"]).fillna(0)
    fig = go.Figure()
    for state in status.index:
        fig.add_bar(y=["Complaints"], x=[status.loc[state, "complaints"]], orientation="h",
                    name=STATUS_LABEL[state], marker_color=STATUS[state],
                    marker_line=dict(width=2, color="rgba(255,255,255,0.9)"),
                    hovertemplate="%{x:,} recent complaints<extra>" + STATUS_LABEL[state]
                    + "</extra>")
    fig.update_layout(barmode="stack", showlegend=True)
    fig.update_yaxes(showticklabels=False)
    st.plotly_chart(style(fig, height=200), use_container_width=True)
    total = status["complaints"].sum() or 1
    for state in status.index:
        share = status.loc[state, "complaints"] / total
        st.markdown(f"**{STATUS_LABEL[state]}** - {share:.0%} of recent complaints, "
                    f"{int(status.loc[state, 'pairs']):,} vehicle/category pairs")

st.subheader("Biggest catalog opportunities")
top = query("""
    select make, model_name, model_year, part_category, complaints_recent,
           complaints_severe, recall_campaigns, parts_covering, coverage_status,
           opportunity_score
    from marts.mart_coverage_gap
    where opportunity_score > 0
    order by opportunity_score desc
    limit 15
""")
top["coverage_status"] = top["coverage_status"].map(STATUS_LABEL)
st.dataframe(
    top, hide_index=True, use_container_width=True,
    column_config={
        "model_name": "Model", "make": "Make", "model_year": st.column_config.NumberColumn(
            "Year", format="%d"),
        "part_category": "Category", "complaints_recent": "Recent complaints",
        "complaints_severe": "Severe", "recall_campaigns": "Recalls",
        "parts_covering": "Parts", "coverage_status": "Coverage",
        "opportunity_score": st.column_config.ProgressColumn(
            "Opportunity", min_value=0, max_value=float(top["opportunity_score"].max() or 1),
            format="%.2f"),
    },
)
st.caption("Score = demand index (recent + severe complaints + recalls) x vehicle-age "
           "weight x coverage gap. See the Coverage Gaps page to filter and drill in.")
