-- Current state of every part from the SCD2 snapshot, plus how many price/status
-- versions it has been through.
with history as (
    select * from {{ ref('snap_catalog_parts') }}
),

versions as (
    select part_number, count(*) as version_count, min(dbt_valid_from) as first_seen_at
    from history
    group by part_number
)

select
    h.part_number,
    h.brand,
    h.part_type,
    h.part_category,
    h.description,
    cast(h.list_price as decimal(10, 2)) as list_price,
    h.status                              as part_status,
    cast(h.introduced_date as date)       as introduced_date,
    v.version_count,
    v.first_seen_at,
    h.dbt_valid_from                      as current_version_from
from history as h
join versions as v using (part_number)
where h.dbt_valid_to is null
