-- Grain: campaign x vehicle x component. The source repeats this combination once per
-- manufacturer component id; those repeats are collapsed here.
select distinct
    r.campaign_number,
    {{ vehicle_key('r.make', 'm.model_norm', 'r.model_year') }} as vehicle_key,
    {{ component_key('r.component_path') }}                     as component_key,
    r.report_received_date
from {{ ref('stg_nhtsa__recalls') }} as r
join {{ ref('int_model_conformance') }} as m
    on m.make = r.make
   and m.source_model_norm = r.model_norm
   and m.model_year = r.model_year
