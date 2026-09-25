-- ACES-style fitment ranges ("2016-2019 Civic") exploded to one row per model year,
-- which is the grain every analytical join needs.
with fitment as (
    select * from {{ ref('stg_catalog__fitment') }}
),

exploded as (
    select
        part_number,
        make,
        model_name,
        model_norm,
        unnest(range(year_start, year_end + 1)) as model_year,
        position,
        is_interchange
    from fitment
)

select
    {{ vehicle_key('make', 'model_norm', 'model_year') }} as vehicle_key,
    *
from exploded
