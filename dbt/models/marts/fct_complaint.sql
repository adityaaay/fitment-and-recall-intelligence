{{
    config(
        materialized='incremental',
        unique_key='complaint_id',
        incremental_strategy='delete+insert',
        on_schema_change='append_new_columns'
    )
}}

-- Grain: one owner complaint (ODINO). The source stores one row per complaint x component
-- and repeats crash / injury figures on each, so severity is taken once per complaint.
-- Incremental: only complaints whose rows arrived in a newer load are rebuilt.
with rows as (
    select c.* exclude (model_norm), m.model_norm
    from {{ ref('stg_nhtsa__complaints') }} as c
    join {{ ref('int_model_conformance') }} as m
        on m.make = c.make
       and m.source_model_norm = c.model_norm
       and m.model_year = c.model_year
    {% if is_incremental() %}
    where c.complaint_id in (
          select complaint_id from {{ ref('stg_nhtsa__complaints') }}
          where _loaded_at > (select coalesce(max(_loaded_at), '1900-01-01') from {{ this }})
      )
    {% endif %}
)

select
    complaint_id,
    {{ vehicle_key('any_value(make)', 'any_value(model_norm)', 'any_value(model_year)') }}
                                            as vehicle_key,
    min(received_date)                      as received_date,
    min(failure_date)                       as failure_date,
    bool_or(is_crash)                       as is_crash,
    bool_or(is_fire)                        as is_fire,
    max(injuries)                           as injuries,
    max(deaths)                             as deaths,
    bool_or(is_crash) or bool_or(is_fire) or max(injuries) > 0 or max(deaths) > 0
                                            as is_severe,
    bool_or(needed_medical_attention)       as needed_medical_attention,
    bool_or(vehicle_towed)                  as vehicle_towed,
    max(odometer_miles)                     as odometer_miles,
    any_value(owner_state)                  as owner_state,
    any_value(complaint_source)             as complaint_source,
    count(distinct component_path)          as components_cited,
    arg_max(narrative, length(narrative))   as narrative,
    max(_loaded_at)                         as _loaded_at
from rows
group by complaint_id
