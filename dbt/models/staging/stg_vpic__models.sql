select distinct
    upper(trim(make_name))                  as make,
    cast(make_id as integer)                as vpic_make_id,
    cast(model_id as integer)               as vpic_model_id,
    trim(model_name)                        as model_name,
    {{ normalize_model('model_name') }}     as model_norm,
    cast(model_year as integer)             as model_year,
    vehicle_type
from {{ source('nhtsa', 'vpic_models') }}
where model_name is not null
