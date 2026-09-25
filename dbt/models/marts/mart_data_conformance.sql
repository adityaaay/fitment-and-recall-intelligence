-- How well does each source conform to the vPIC vehicle reference?
-- Rates are measured on rows inside vPIC's model-year window (older model years are out of
-- the reference by design). Out-of-scope rows (motorcycles, RVs, heavy trucks, unknown
-- makes) are counted, not silently dropped.
with reference_years as (
    select min(model_year) as first_year, max(model_year) as last_year
    from {{ ref('stg_vpic__models') }}
),

nhtsa as (
    select 'nhtsa_recalls' as source, make, model_norm, model_year
    from {{ ref('stg_nhtsa__recalls') }}
    union all
    select 'nhtsa_complaints', make, model_norm, model_year
    from {{ ref('stg_nhtsa__complaints') }}
),

nhtsa_classified as (
    select
        n.source,
        n.make is not null                                          as in_scope,
        n.make is not null and n.model_year is not null
            and n.model_norm <> ''                                  as resolvable,
        n.model_year between y.first_year and y.last_year           as in_reference_years,
        m.match_method
    from nhtsa as n
    cross join reference_years as y
    left join {{ ref('int_model_conformance') }} as m
        on m.make = n.make
       and m.source_model_norm = n.model_norm
       and m.model_year = n.model_year
),

catalog_classified as (
    select
        'catalog_fitment'                                           as source,
        true                                                        as in_scope,
        true                                                        as resolvable,
        f.model_year between y.first_year and y.last_year           as in_reference_years,
        case when v.in_vpic then 'exact' else 'unmatched' end       as match_method
    from {{ ref('int_fitment_exploded') }} as f
    cross join reference_years as y
    left join {{ ref('int_vehicles') }} as v using (vehicle_key)
),

classified as (
    select * from nhtsa_classified
    union all
    select * from catalog_classified
)

select
    source,
    count(*)                                                        as rows_total,
    count(*) filter (where in_scope)                                as rows_in_scope,
    count(*) filter (where resolvable)                              as rows_resolvable,
    count(*) filter (where resolvable and in_reference_years)       as rows_in_reference_years,
    count(*) filter (where in_reference_years and match_method = 'exact')  as matched_exact,
    count(*) filter (where in_reference_years and match_method = 'rule')   as matched_rule,
    count(*) filter (where in_reference_years and match_method = 'prefix') as matched_prefix,
    round(count(*) filter (where in_reference_years and match_method = 'exact')
          / nullif(count(*) filter (where resolvable and in_reference_years), 0), 4)
                                                                    as exact_match_rate,
    round(count(*) filter (where in_reference_years and match_method <> 'unmatched')
          / nullif(count(*) filter (where resolvable and in_reference_years), 0), 4)
                                                                    as vpic_match_rate
from classified
group by source
