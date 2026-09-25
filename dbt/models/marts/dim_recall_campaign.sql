-- One row per recall campaign. POTAFF (units affected) is a campaign-level number that the
-- source repeats on every vehicle/component row, so it lives here - summing it from the
-- raw rows would overstate exposure many times over.
select
    campaign_number,
    arg_max(manufacturer, _loaded_at)                 as manufacturer,
    max(campaign_units_affected)                      as units_affected,
    min(report_received_date)                         as report_received_date,
    min(owner_notification_date)                      as owner_notification_date,
    arg_max(initiated_by, _loaded_at)                 as initiated_by,
    bool_or(do_not_drive)                             as do_not_drive,
    bool_or(park_outside)                             as park_outside,
    arg_max(defect_summary, length(defect_summary))   as defect_summary,
    arg_max(consequence_summary, length(consequence_summary)) as consequence_summary,
    arg_max(remedy_summary, length(remedy_summary))   as remedy_summary,
    count(distinct make || '|' || model_norm || '|' || model_year) as vehicles_named,
    count(distinct component_path)                    as components_named
from {{ ref('stg_nhtsa__recalls') }}
group by campaign_number
