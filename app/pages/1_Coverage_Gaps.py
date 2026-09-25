import plotly.graph_objects as go
import streamlit as st
from common import STATUS, STATUS_LABEL, page_header, query, require_warehouse, style

st.set_page_config(page_title="Coverage Gaps", page_icon="🔧", layout="wide")
page_header("Coverage gaps",
            "Vehicle x part-category pairs ranked by field-failure demand the catalog does "
            "not yet serve.")
require_warehouse()

categories = query("""
    select distinct part_category from marts.mart_coverage_gap order by 1
""")["part_category"].tolist()
makes = query("select distinct make from marts.mart_coverage_gap order by 1")["make"].tolist()
bands = ["0-2 (warranty)", "3-5 (early aftermarket)", "6-12 (aftermarket core)",
         "13+ (late life)"]

f1, f2, f3, f4 = st.columns([2, 2, 2, 2])
category = f1.selectbox("Part category", ["All categories", *categories])
make = f2.selectbox("Make", ["All makes", *makes])
band = f3.multiselect("Vehicle age", bands, default=bands[1:3])
statuses = f4.multiselect("Coverage", ["uncovered", "thin", "covered"],
                          default=["uncovered", "thin"], format_func=STATUS_LABEL.get)

where, params = ["complaints_total + recall_campaigns > 0"], []
if category != "All categories":
    where.append("part_category = ?")
    params.append(category)
if make != "All makes":
    where.append("make = ?")
    params.append(make)
if band:
    where.append(f"age_band in ({', '.join('?' * len(band))})")
    params.extend(band)
if statuses:
    where.append(f"coverage_status in ({', '.join('?' * len(statuses))})")
    params.extend(statuses)

data = query(f"""
    select make, model_name, model_year, age_band, part_category, complaints_total,
           complaints_recent, complaints_severe, recall_campaigns, parts_covering,
           category_median_parts, coverage_status, demand_index, opportunity_score
    from marts.mart_coverage_gap
    where {' and '.join(where)}
    order by opportunity_score desc, demand_index desc
""", tuple(params))

m1, m2, m3 = st.columns(3)
m1.metric("Pairs shown", f"{len(data):,}")
m2.metric("Recent complaints in view", f"{int(data['complaints_recent'].sum()):,}")
m3.metric("Of which on uncovered pairs",
          f"{int(data.loc[data.coverage_status == 'uncovered', 'complaints_recent'].sum()):,}")

st.subheader("Where recent demand is served - and where it is not")
# Same filters except coverage status, so the chart shows the full picture per category.
status_idx = next((i for i, w in enumerate(where) if w.startswith("coverage_status")), None)
chart_where = [w for i, w in enumerate(where) if i != status_idx]
chart_params = params[: len(params) - len(statuses)] if statuses else params
by_category = query(f"""
    select part_category, coverage_status, sum(complaints_recent) as complaints
    from marts.mart_coverage_gap
    where {' and '.join(chart_where)}
    group by all
""", tuple(chart_params))
if not by_category.empty:
    totals = by_category.groupby("part_category")["complaints"].sum()
    totals = totals[totals > 0]
    uncovered = (by_category[by_category.coverage_status == "uncovered"]
                 .set_index("part_category")["complaints"])
    order = (uncovered.reindex(totals.index).fillna(0) / totals).sort_values().index.tolist()
    fig = go.Figure()
    for state in ["covered", "thin", "uncovered"]:
        d = by_category[by_category.coverage_status == state].set_index("part_category")
        d = d.reindex(order).fillna(0)
        fig.add_bar(
            y=order, x=d["complaints"], orientation="h", name=STATUS_LABEL[state],
            marker=dict(color=STATUS[state], line=dict(width=2, color="rgba(255,255,255,1)")),
            customdata=(d["complaints"] / totals.reindex(order)).fillna(0),
            hovertemplate="%{y}: %{x:,} recent complaints (%{customdata:.0%})<extra>"
            + STATUS_LABEL[state] + "</extra>",
        )
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title="Recent complaints (last 36 months)")
    st.plotly_chart(style(fig, height=80 + 34 * len(order)), use_container_width=True)
    st.caption("Categories sorted by the share of recent complaints on vehicles the catalog "
               "does not cover. Demand index = ln(1+recent) + 2·ln(1+severe) + "
               "1.5·ln(1+recalls).")

st.subheader("Ranked opportunities")
table = data.head(500).copy()
table["coverage_status"] = table["coverage_status"].map(STATUS_LABEL)
st.dataframe(
    table, hide_index=True, use_container_width=True, height=460,
    column_config={
        "model_year": st.column_config.NumberColumn("Year", format="%d"),
        "category_median_parts": st.column_config.NumberColumn("Category median", format="%.1f"),
        "demand_index": st.column_config.NumberColumn("Demand", format="%.2f"),
        "opportunity_score": st.column_config.NumberColumn("Opportunity", format="%.2f"),
    },
)
st.download_button("Download CSV", data.to_csv(index=False).encode(), "coverage_gaps.csv",
                   "text/csv")
