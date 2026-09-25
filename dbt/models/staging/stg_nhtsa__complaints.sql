-- CMPLID is documented as "updateable": the same id can re-appear in a later file.
-- Keep the most recently loaded version so re-deliveries never double count.
with source as (
    select * from {{ source('nhtsa', 'nhtsa_complaints') }}
    where prod_type = 'V'
    qualify row_number() over (partition by cmplid order by _loaded_at desc, datea desc) = 1
),

aliases as (
    select * from {{ ref('make_aliases') }}
)

select
    cast(s.cmplid as bigint)                             as complaint_component_id,
    cast(s.odino as bigint)                              as complaint_id,
    {{ normalize_make('s.maketxt') }}                    as make_raw,
    a.canonical_make                                     as make,
    s.modeltxt                                           as model_raw,
    {{ normalize_model('s.modeltxt') }}                  as model_norm,
    nullif(try_cast(s.yeartxt as integer), 9999)         as model_year,
    upper(trim(s.compdesc))                              as component_path,
    upper(trim(split_part(s.compdesc, ':', 1)))          as nhtsa_system,
    {{ yes_no('s.crash') }}                              as is_crash,
    {{ yes_no('s.fire') }}                               as is_fire,
    coalesce(try_cast(s.injured as integer), 0)          as injuries,
    coalesce(try_cast(s.deaths as integer), 0)           as deaths,
    {{ yes_no('s.medical_attn') }}                       as needed_medical_attention,
    {{ yes_no('s.vehicles_towed_yn') }}                  as vehicle_towed,
    {{ parse_yyyymmdd('s.faildate') }}                   as failure_date,
    {{ parse_yyyymmdd('s.ldate') }}                      as received_date,
    {{ parse_yyyymmdd('s.datea') }}                      as added_date,
    nullif(try_cast(s.miles as integer), 0)              as odometer_miles,
    s.state                                              as owner_state,
    s.cmpl_type                                          as complaint_source,
    s.drive_train,
    s.fuel_type,
    s.trans_type                                         as transmission_type,
    s.cdescr                                             as narrative,
    s._loaded_at
from source as s
left join aliases as a
    on a.raw_make = {{ normalize_make('s.maketxt') }}
