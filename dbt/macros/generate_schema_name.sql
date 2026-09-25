{#- Use the configured schema name verbatim (staging, marts, ...) instead of
    dbt's default "<target>_<custom>" prefixing - one warehouse file per environment. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name else target.schema }}
{%- endmacro %}
