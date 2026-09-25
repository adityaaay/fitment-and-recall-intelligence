select
    part_number,
    make,
    model                                as model_name,
    {{ normalize_model('model') }}       as model_norm,
    cast(year_start as integer)          as year_start,
    cast(year_end as integer)            as year_end,
    position,
    coalesce(note = 'Interchange', false) as is_interchange,
    make_standardized,
    model_standardized,
    _loaded_at
from {{ source('catalog', 'catalog_fitment') }}
