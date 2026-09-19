select
    assignment_id,
    emp_id,
    assignment_city,
    cast(start_date as date)    as start_date,
    cast(end_date as date)      as end_date
from {{ source('raw', 'assignments') }}
