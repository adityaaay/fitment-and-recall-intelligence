-- Where is field-failure demand high but catalog coverage missing or thin?
-- Grain: vehicle x part category (aftermarket-serviceable categories only), for every
-- vehicle/category pair that has at least one complaint or recall signal.
{% set recent_months = var('recent_months') %}

with components as (
    select * from {{ ref('dim_component') }} where aftermarket_serviceable
),

anchor as (
    -- Anchor "recent" to the newest complaint in the data, not wall-clock time, so a
    -- given snapshot of the data always scores the same way.
    select max(received_date) as as_of_date from {{ ref('fct_complaint') }}
),

complaint_signal as (
    select
        c.vehicle_key,
        comp.part_category,
        count(distinct c.complaint_id)                                  as complaints_total,
        count(distinct c.complaint_id) filter (
            where c.received_date > a.as_of_date - interval {{ recent_months }} month
        )                                                               as complaints_recent,
        count(distinct c.complaint_id) filter (where c.is_severe)       as complaints_severe
    from {{ ref('fct_complaint') }} as c
    join {{ ref('fct_complaint_component') }} as cc using (complaint_id)
    join components as comp using (component_key)
    cross join anchor as a
    group by all
),

recall_signal as (
    select
        r.vehicle_key,
        comp.part_category,
        count(distinct r.campaign_number) as recall_campaigns
    from {{ ref('fct_recall_vehicle_component') }} as r
    join components as comp using (component_key)
    group by all
),

coverage as (
    select
        f.vehicle_key,
        p.part_category,
        count(distinct f.part_number) as parts_covering
    from {{ ref('fct_part_fitment') }} as f
    join {{ ref('dim_part') }} as p using (part_number)
    where p.part_status = 'Active'
    group by all
),

category_depth as (
    -- Typical depth of a covered vehicle in each category; below half of it is "thin".
    select part_category, median(parts_covering) as median_parts
    from coverage
    group by part_category
),

signals as (
    select
        coalesce(cs.vehicle_key, rs.vehicle_key)     as vehicle_key,
        coalesce(cs.part_category, rs.part_category) as part_category,
        coalesce(cs.complaints_total, 0)             as complaints_total,
        coalesce(cs.complaints_recent, 0)            as complaints_recent,
        coalesce(cs.complaints_severe, 0)            as complaints_severe,
        coalesce(rs.recall_campaigns, 0)             as recall_campaigns
    from complaint_signal as cs
    full outer join recall_signal as rs
        on cs.vehicle_key = rs.vehicle_key
       and cs.part_category = rs.part_category
),

scored as (
    select
        s.*,
        v.make,
        v.model_name,
        v.model_year,
        v.age_band,
        v.aftermarket_age_weight,
        coalesce(cv.parts_covering, 0)               as parts_covering,
        d.median_parts                               as category_median_parts,
        case
            when coalesce(cv.parts_covering, 0) = 0 then 'uncovered'
            when cv.parts_covering < d.median_parts / 2.0 then 'thin'
            else 'covered'
        end                                          as coverage_status,
        ln(1 + s.complaints_recent)
            + 2.0 * ln(1 + s.complaints_severe)
            + 1.5 * ln(1 + s.recall_campaigns)       as demand_index
    from signals as s
    join {{ ref('dim_vehicle') }} as v using (vehicle_key)
    left join coverage as cv
        on cv.vehicle_key = s.vehicle_key
       and cv.part_category = s.part_category
    left join category_depth as d
        on d.part_category = s.part_category
)

select
    vehicle_key,
    make,
    model_name,
    model_year,
    age_band,
    part_category,
    complaints_total,
    complaints_recent,
    complaints_severe,
    recall_campaigns,
    parts_covering,
    category_median_parts,
    coverage_status,
    round(demand_index, 3)                                         as demand_index,
    round(
        demand_index * aftermarket_age_weight
        * case coverage_status when 'uncovered' then 1.0 when 'thin' then 0.5 else 0 end,
        3
    )                                                              as opportunity_score,
    rank() over (
        partition by part_category
        order by demand_index * aftermarket_age_weight
                 * case coverage_status when 'uncovered' then 1.0 when 'thin' then 0.5 else 0 end
                 desc
    )                                                              as category_rank
from scored
