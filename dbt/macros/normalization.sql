{#- Model names differ in punctuation across sources ("F-150" vs "F150", "CR-V" vs "CR V").
    Upper-case, turn every non-alphanumeric run into one space, then trim. -#}
{% macro normalize_model(col) -%}
    trim(regexp_replace(upper(coalesce({{ col }}, '')), '[^A-Z0-9]+', ' ', 'g'))
{%- endmacro %}

{% macro normalize_make(col) -%}
    trim(regexp_replace(upper(coalesce({{ col }}, '')), '\s+', ' ', 'g'))
{%- endmacro %}

{% macro parse_yyyymmdd(col) -%}
    try_strptime(nullif({{ col }}, ''), '%Y%m%d')::date
{%- endmacro %}

{% macro yes_no(col) -%}
    (upper(coalesce({{ col }}, 'N')) in ('Y', 'YES'))
{%- endmacro %}

{#- Surrogate key for a vehicle at the make / normalised model / model-year grain. -#}
{% macro vehicle_key(make, model_norm, model_year) -%}
    md5(concat_ws('|', {{ make }}, {{ model_norm }}, cast({{ model_year }} as varchar)))
{%- endmacro %}

{% macro component_key(component_path) -%}
    md5(upper(trim({{ component_path }})))
{%- endmacro %}
