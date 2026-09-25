-- Business rules of the opportunity score must hold on every row.
select *
from {{ ref('mart_coverage_gap') }}
where (coverage_status = 'uncovered' and parts_covering <> 0)
   or (coverage_status <> 'uncovered' and parts_covering = 0)
   or (coverage_status = 'covered' and opportunity_score <> 0)
   or (coverage_status <> 'covered' and demand_index > 0 and opportunity_score <= 0)
   or complaints_recent > complaints_total
   or complaints_severe > complaints_total
