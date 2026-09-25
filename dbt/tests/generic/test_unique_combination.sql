{#- Grain test without a package dependency: the given columns must be unique together. -#}
{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as occurrences
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
