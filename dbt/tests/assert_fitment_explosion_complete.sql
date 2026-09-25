-- Exploding year ranges must yield exactly (year_end - year_start + 1) rows per fitment.
with expected as (
    select sum(year_end - year_start + 1) as n from {{ ref('stg_catalog__fitment') }}
),

actual as (
    select count(*) as n from {{ ref('int_fitment_exploded') }}
)

select e.n as expected_rows, a.n as actual_rows
from expected as e
cross join actual as a
where e.n <> a.n
