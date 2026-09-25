-- Reconciliation: every in-scope vehicle complaint landed in bronze must appear exactly
-- once in fct_complaint - nothing dropped, nothing duplicated by the component fan-out.
with bronze as (
    select count(distinct c.odino) as complaints
    from {{ source('nhtsa', 'nhtsa_complaints') }} as c
    join {{ ref('make_aliases') }} as a
        on a.raw_make = {{ normalize_make('c.maketxt') }}
    where c.prod_type = 'V'
      and nullif(try_cast(c.yeartxt as integer), 9999) is not null
      and {{ normalize_model('c.modeltxt') }} <> ''
),

warehouse as (
    select count(*) as complaints from {{ ref('fct_complaint') }}
)

select b.complaints as bronze_complaints, w.complaints as warehouse_complaints
from bronze as b
cross join warehouse as w
where b.complaints <> w.complaints
