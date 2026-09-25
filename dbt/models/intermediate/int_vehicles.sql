-- Conformed vehicle universe at make / normalised model / model-year grain.
-- vPIC is the reference; vehicles that only appear in recalls, complaints or the catalog
-- are kept (so every fact row resolves) but flagged in_vpic = false.
with vpic as (
    select
        make,
        model_norm,
        model_year,
        arg_min(model_name, vpic_model_id)                     as model_name,
        arg_min(vehicle_type, case vehicle_type
                                  when 'Passenger Car' then 1
                                  when 'Multipurpose Passenger Vehicle (MPV)' then 2
                                  else 3 end)                  as vehicle_type
    from {{ ref('stg_vpic__models') }}
    where model_norm <> ''
    group by all
),

conformance as (
    select * from {{ ref('int_model_conformance') }}
),

nhtsa as (
    select make, model_norm, model_year, model_raw, 'recall' as seen_in
    from {{ ref('stg_nhtsa__recalls') }}
    union all
    select make, model_norm, model_year, model_raw, 'complaint'
    from {{ ref('stg_nhtsa__complaints') }}
),

observed as (
    select n.make, c.model_norm, n.model_year, n.model_raw as model_name, n.seen_in
    from nhtsa as n
    join conformance as c
        on c.make = n.make
       and c.source_model_norm = n.model_norm
       and c.model_year = n.model_year
    union all
    select make, model_norm, model_year, model_name, 'catalog'
    from {{ ref('int_fitment_exploded') }}
),

observed_vehicles as (
    select
        make,
        model_norm,
        model_year,
        mode(model_name)                        as model_name,
        list(distinct seen_in order by seen_in) as seen_in
    from observed
    where make is not null and model_year is not null and model_norm <> ''
    group by all
)

select
    {{ vehicle_key('coalesce(v.make, o.make)', 'coalesce(v.model_norm, o.model_norm)',
                   'coalesce(v.model_year, o.model_year)') }}  as vehicle_key,
    coalesce(v.make, o.make)                                   as make,
    coalesce(v.model_name, o.model_name)                       as model_name,
    coalesce(v.model_norm, o.model_norm)                       as model_norm,
    coalesce(v.model_year, o.model_year)                       as model_year,
    coalesce(v.vehicle_type, 'Unknown')                        as vehicle_type,
    v.make is not null                                         as in_vpic,
    coalesce(o.seen_in, [])                                    as seen_in
from vpic as v
full outer join observed_vehicles as o
    on v.make = o.make
   and v.model_norm = o.model_norm
   and v.model_year = o.model_year
