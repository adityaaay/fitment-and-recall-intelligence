-- Grain: part x vehicle (model year) x position.
select
    f.part_number,
    f.vehicle_key,
    f.position,
    f.is_interchange,
    v.in_vpic as fitment_verified_in_vpic
from {{ ref('int_fitment_exploded') }} as f
join {{ ref('dim_part') }} as p using (part_number)
join {{ ref('dim_vehicle') }} as v using (vehicle_key)
