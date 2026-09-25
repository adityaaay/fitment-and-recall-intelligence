with source as (
    select * from {{ source('nhtsa', 'nhtsa_recalls') }}
    where rcltypecd = 'V'  -- vehicle campaigns (excludes tires, equipment, child seats)
),

aliases as (
    select * from {{ ref('make_aliases') }}
)

select
    cast(s.record_id as bigint)                          as recall_record_id,
    s.campno                                             as campaign_number,
    {{ normalize_make('s.maketxt') }}                    as make_raw,
    a.canonical_make                                     as make,
    s.modeltxt                                           as model_raw,
    {{ normalize_model('s.modeltxt') }}                  as model_norm,
    nullif(try_cast(s.yeartxt as integer), 9999)         as model_year,
    upper(trim(s.compname))                              as component_path,
    upper(trim(split_part(s.compname, ':', 1)))          as nhtsa_system,
    s.mfgname                                            as manufacturer,
    s.mfgcampno                                          as manufacturer_campaign_number,
    try_cast(s.potaff as bigint)                         as campaign_units_affected,
    s.influenced_by                                      as initiated_by,
    {{ parse_yyyymmdd('s.rcdate') }}                     as report_received_date,
    {{ parse_yyyymmdd('s.odate') }}                      as owner_notification_date,
    {{ parse_yyyymmdd('s.bgman') }}                      as manufacture_begin_date,
    {{ parse_yyyymmdd('s.endman') }}                     as manufacture_end_date,
    s.desc_defect                                        as defect_summary,
    s.consequence_defect                                 as consequence_summary,
    s.corrective_action                                  as remedy_summary,
    s.mfr_comp_name                                      as manufacturer_component_name,
    s.mfr_comp_ptno                                      as manufacturer_part_number,
    {{ yes_no('s.do_not_drive') }}                       as do_not_drive,
    {{ yes_no('s.park_outside') }}                       as park_outside,
    s._loaded_at
from source as s
left join aliases as a
    on a.raw_make = {{ normalize_make('s.maketxt') }}
