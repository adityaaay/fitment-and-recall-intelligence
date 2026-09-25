import plotly.graph_objects as go
import streamlit as st
from common import SERIES, STATUS_LABEL, page_header, query, require_warehouse, style

st.set_page_config(page_title="Vehicle Explorer", page_icon="🔧", layout="wide")
page_header("Vehicle explorer",
            "Everything the warehouse knows about one vehicle: recalls, complaints and the "
            "parts that fit it.")
require_warehouse()

makes = query("""
    select distinct make from marts.dim_vehicle where in_vpic order by 1
""")["make"].tolist()
c1, c2, c3 = st.columns(3)
make = c1.selectbox("Make", makes, index=makes.index("HONDA") if "HONDA" in makes else 0)
models = query("""
    select model_name, count(*) as n from marts.dim_vehicle
    where make = ? and in_vpic group by 1 order by 1
""", (make,))["model_name"].tolist()
model = c2.selectbox("Model", models,
                     index=models.index("CR-V") if make == "HONDA" and "CR-V" in models else 0)
years = query("""
    select v.model_year, count(c.complaint_id) as complaints
    from marts.dim_vehicle v
    left join marts.fct_complaint c using (vehicle_key)
    where v.make = ? and v.model_name = ? and v.in_vpic
    group by 1 order by 1 desc
""", (make, model))
# Default to the model year with the most complaints - the most informative view.
year = c3.selectbox("Model year", years["model_year"].tolist(),
                    index=int(years["complaints"].values.argmax()))

vehicle = query("""
    select * from marts.dim_vehicle
    where make = ? and model_name = ? and model_year = ? and in_vpic
""", (make, model, year)).iloc[0]
vk = vehicle.vehicle_key

stats = query("""
    select
        (select count(*) from marts.fct_complaint where vehicle_key = ?) as complaints,
        (select count(*) from marts.fct_complaint
         where vehicle_key = ? and is_severe) as severe,
        (select count(distinct campaign_number) from marts.fct_recall_vehicle_component
         where vehicle_key = ?) as recalls,
        (select count(distinct part_number) from marts.fct_part_fitment
         where vehicle_key = ?) as parts
""", (vk, vk, vk, vk)).iloc[0]
k = st.columns(5)
short_type = {"Passenger Car": "Car", "Multipurpose Passenger Vehicle (MPV)": "MPV"}
k[0].metric("Vehicle type", short_type.get(vehicle.vehicle_type, vehicle.vehicle_type))
k[1].metric("Age band", vehicle.age_band.split(" ")[0] + " yrs")
k[2].metric("Owner complaints", f"{stats.complaints:,}",
            help=f"{stats.severe:,} involved a crash, fire, injury or death")
k[3].metric("Recall campaigns", f"{stats.recalls:,}")
k[4].metric("Catalog parts", f"{stats.parts:,}")

left, right = st.columns([3, 2])
with left:
    st.subheader("Complaints and coverage by part category")
    gap = query("""
        select part_category, complaints_total, complaints_severe, recall_campaigns,
               parts_covering, coverage_status, opportunity_score
        from marts.mart_coverage_gap where vehicle_key = ?
        order by complaints_total desc
    """, (vk,))
    if gap.empty:
        st.info("No complaints or recalls on file for this vehicle.")
    else:
        g = gap.sort_values("complaints_total")
        fig = go.Figure(go.Bar(
            x=g.complaints_total, y=g.part_category, orientation="h",
            marker=dict(color=SERIES[0], cornerradius=4),
            customdata=g[["complaints_severe", "parts_covering"]],
            hovertemplate=("%{y}: %{x:,} complaints (%{customdata[0]:,} severe), "
                           "%{customdata[1]} parts<extra></extra>"),
        ))
        fig.update_xaxes(title="Complaints", rangemode="tozero",
                         dtick=1 if g.complaints_total.max() < 6 else None)
        st.plotly_chart(style(fig, height=max(240, 34 * len(g))), use_container_width=True)
        gap["coverage_status"] = gap["coverage_status"].map(STATUS_LABEL)
        st.dataframe(gap, hide_index=True, use_container_width=True)

with right:
    st.subheader("Recall campaigns")
    recalls = query("""
        select distinct c.campaign_number, c.report_received_date, c.manufacturer,
               c.units_affected, c.do_not_drive, c.defect_summary
        from marts.fct_recall_vehicle_component r
        join marts.dim_recall_campaign c using (campaign_number)
        where r.vehicle_key = ?
        order by c.report_received_date desc
    """, (vk,))
    if recalls.empty:
        st.info("No recall campaigns name this vehicle.")
    for row in recalls.itertuples():
        flag = " · ⚠ Do not drive" if row.do_not_drive else ""
        with st.expander(f"{row.campaign_number} · {row.report_received_date:%b %Y}{flag}"):
            st.write(row.defect_summary)
            st.caption(f"{row.manufacturer} · {row.units_affected:,} units affected "
                       "(campaign-wide)")

st.subheader("Parts that fit")
parts = query("""
    select p.part_number, p.part_type, p.part_category, f.position, p.brand, p.list_price,
           p.part_status, f.is_interchange
    from marts.fct_part_fitment f
    join marts.dim_part p using (part_number)
    where f.vehicle_key = ?
    order by p.part_category, p.part_type, f.position
""", (vk,))
st.dataframe(parts, hide_index=True, use_container_width=True,
             column_config={"list_price": st.column_config.NumberColumn("List price",
                                                                         format="$%.2f")})

st.subheader("Recent owner complaints")
complaints = query("""
    select received_date, is_crash, is_fire, injuries, odometer_miles, narrative
    from marts.fct_complaint where vehicle_key = ?
    order by received_date desc limit 25
""", (vk,))
st.dataframe(complaints, hide_index=True, use_container_width=True)
