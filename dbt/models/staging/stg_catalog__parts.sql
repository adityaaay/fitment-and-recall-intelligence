select
    part_number,
    brand,
    part_type,
    part_category,
    description,
    cast(list_price as decimal(10, 2))   as list_price,
    status                               as part_status,
    cast(introduced_date as date)        as introduced_date,
    nullif(superseded_by, '')            as superseded_by,
    _loaded_at
from {{ source('catalog', 'catalog_parts') }}
