-- NHTSA component paths ("FUEL SYSTEM, GASOLINE:DELIVERY:FUEL PUMP") mapped to the
-- aftermarket part categories a catalog is organised by, via the component_taxonomy seed.
with paths as (
    select component_path, nhtsa_system from {{ ref('stg_nhtsa__recalls') }}
    union
    select component_path, nhtsa_system from {{ ref('stg_nhtsa__complaints') }}
),

taxonomy as (
    select * from {{ ref('component_taxonomy') }}
)

select
    {{ component_key('p.component_path') }}              as component_key,
    p.component_path,
    p.nhtsa_system,
    nullif(trim(split_part(p.component_path, ':', 2)), '') as nhtsa_subsystem,
    coalesce(t.part_category, 'Unmapped')                as part_category,
    coalesce(t.aftermarket_serviceable, false)           as aftermarket_serviceable
from paths as p
left join taxonomy as t
    on t.nhtsa_system = p.nhtsa_system
where p.component_path is not null
