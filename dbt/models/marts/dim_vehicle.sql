-- Vehicle age drives aftermarket demand: brand-new vehicles are still under warranty and
-- serviced at the dealer, while 6-12 year old vehicles are the independent-repair core.
with vehicles as (
    select * from {{ ref('int_vehicles') }}
),

aged as (
    select
        *,
        greatest(extract(year from current_date)::integer - model_year, 0) as vehicle_age_years
    from vehicles
)

select
    vehicle_key,
    make,
    model_name,
    model_norm,
    model_year,
    vehicle_type,
    in_vpic,
    seen_in,
    vehicle_age_years,
    case
        when vehicle_age_years <= 2 then '0-2 (warranty)'
        when vehicle_age_years <= 5 then '3-5 (early aftermarket)'
        when vehicle_age_years <= 12 then '6-12 (aftermarket core)'
        else '13+ (late life)'
    end as age_band,
    case
        when vehicle_age_years <= 2 then 0.6
        when vehicle_age_years <= 5 then 1.0
        when vehicle_age_years <= 12 then 1.25
        else 0.8
    end as aftermarket_age_weight
from aged
