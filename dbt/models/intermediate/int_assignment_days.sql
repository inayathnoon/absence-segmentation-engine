with expanded as (
    select
        emp_id,
        assignment_id,
        assignment_city,
        cast(unnest(generate_series(start_date, end_date, interval '1 day')) as date)
            as assignment_date
    from {{ ref('stg_assignments') }}
)

select emp_id, assignment_date, assignment_city, assignment_id
from expanded
qualify row_number() over (partition by emp_id, assignment_date order by assignment_id) = 1
