-- Monthly complaint and recall activity by make and part category.
with complaints as (
    select
        date_trunc('month', c.received_date)::date      as month,
        v.make,
        comp.part_category,
        count(distinct c.complaint_id)                   as complaints,
        count(distinct c.complaint_id) filter (where c.is_severe) as severe_complaints
    from {{ ref('fct_complaint') }} as c
    join {{ ref('fct_complaint_component') }} as cc using (complaint_id)
    join {{ ref('dim_component') }} as comp using (component_key)
    join {{ ref('dim_vehicle') }} as v using (vehicle_key)
    where c.received_date is not null
    group by all
),

recalls as (
    select
        date_trunc('month', r.report_received_date)::date as month,
        v.make,
        comp.part_category,
        count(distinct r.campaign_number)                  as recall_campaigns
    from {{ ref('fct_recall_vehicle_component') }} as r
    join {{ ref('dim_component') }} as comp using (component_key)
    join {{ ref('dim_vehicle') }} as v using (vehicle_key)
    where r.report_received_date is not null
    group by all
)

select
    coalesce(c.month, r.month)                 as month,
    coalesce(c.make, r.make)                   as make,
    coalesce(c.part_category, r.part_category) as part_category,
    coalesce(c.complaints, 0)                  as complaints,
    coalesce(c.severe_complaints, 0)           as severe_complaints,
    coalesce(r.recall_campaigns, 0)            as recall_campaigns
from complaints as c
full outer join recalls as r
    on c.month = r.month
   and c.make = r.make
   and c.part_category = r.part_category
