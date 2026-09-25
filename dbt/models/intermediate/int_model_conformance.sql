-- Conform the model names NHTSA uses in recalls/complaints to vPIC's base models.
-- NHTSA often records a trim or submodel ("SILVERADO 1500", "F-250 SD", "ACCORD HYBRID",
-- "C300"); vPIC lists the base model ("Silverado", "F-250", "Accord", "C-Class").
-- Resolution order, per make / model / model year:
--   1. exact       - normalised names are equal
--   2. rule        - a seeded rewrite rule produces a vPIC model (model_rewrite_rules)
--   3. prefix      - the longest vPIC model that is a whole-word prefix of the NHTSA name
--   4. unmatched   - kept as-is (and flagged in_vpic = false downstream)
with observed as (
    select distinct make, model_norm, model_year
    from {{ ref('stg_nhtsa__recalls') }}
    where make is not null and model_year is not null and model_norm <> ''
    union
    select distinct make, model_norm, model_year
    from {{ ref('stg_nhtsa__complaints') }}
    where make is not null and model_year is not null and model_norm <> ''
),

vpic as (
    select distinct make, model_norm, model_year from {{ ref('stg_vpic__models') }}
),

rules as (
    select * from {{ ref('model_rewrite_rules') }}
),

candidates as (
    select o.*, v.model_norm as conformed, 1 as priority, 'exact' as method
    from observed as o
    join vpic as v using (make, model_norm, model_year)

    union all

    select o.*, v.model_norm, 2, 'rule'
    from observed as o
    join rules as r
        on r.make = o.make and regexp_matches(o.model_norm, r.pattern)
    join vpic as v
        on v.make = o.make
       and v.model_year = o.model_year
       and v.model_norm = regexp_replace(o.model_norm, r.pattern, r.replacement)

    union all

    select o.*, v.model_norm, 3, 'prefix'
    from observed as o
    join vpic as v
        on v.make = o.make
       and v.model_year = o.model_year
       and starts_with(o.model_norm, v.model_norm || ' ')
),

best as (
    select *
    from candidates
    qualify row_number() over (
        partition by make, model_norm, model_year
        order by priority, length(conformed) desc
    ) = 1
)

select
    o.make,
    o.model_norm                          as source_model_norm,
    o.model_year,
    coalesce(b.conformed, o.model_norm)   as model_norm,
    coalesce(b.method, 'unmatched')       as match_method
from observed as o
left join best as b using (make, model_norm, model_year)
