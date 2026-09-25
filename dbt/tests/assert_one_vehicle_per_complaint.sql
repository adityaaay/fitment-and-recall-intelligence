-- fct_complaint collapses component rows to one row per complaint and takes the vehicle
-- from them. That is only valid if a complaint never names more than one vehicle.
select complaint_id, count(distinct make || '|' || model_norm || '|' || model_year) as vehicles
from {{ ref('stg_nhtsa__complaints') }}
where make is not null and model_year is not null and model_norm <> ''
group by complaint_id
having count(distinct make || '|' || model_norm || '|' || model_year) > 1
