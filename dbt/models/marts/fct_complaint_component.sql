{{
    config(
        materialized='incremental',
        unique_key=['complaint_id', 'component_key'],
        incremental_strategy='delete+insert'
    )
}}

-- Bridge between complaints and the components they cite (a complaint can cite several;
-- a few cite the same component twice under different CMPLIDs, collapsed here).
select
    c.complaint_id,
    {{ component_key('c.component_path') }} as component_key,
    max(c._loaded_at)                       as _loaded_at
from {{ ref('stg_nhtsa__complaints') }} as c
where c.make is not null
  and c.model_year is not null
  and c.model_norm <> ''
  and c.component_path is not null
{% if is_incremental() %}
  and c._loaded_at > (select coalesce(max(_loaded_at), '1900-01-01') from {{ this }})
{% endif %}
group by 1, 2
