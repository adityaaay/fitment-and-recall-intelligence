{{ config(severity='warn') }}

-- New NHTSA component systems appear over time. Warn when more than 1% of complaint
-- component citations fall outside the taxonomy seed so the mapping gets extended.
with cited as (
    select comp.part_category
    from {{ ref('fct_complaint_component') }} as cc
    join {{ ref('dim_component') }} as comp using (component_key)
)

select
    count(*) filter (where part_category = 'Unmapped') as unmapped,
    count(*) as total
from cited
having count(*) filter (where part_category = 'Unmapped') > 0.01 * count(*)
