{#- Model years must be plausible: owners do file complaints about 1960s classics, so the
    floor is 1950; model years run ahead of the calendar by at most two years. -#}
{% test valid_model_year(model, column_name, min_year=1950) %}
select {{ column_name }}
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ min_year }}
       or {{ column_name }} > extract(year from current_date) + 2)
{% endtest %}
