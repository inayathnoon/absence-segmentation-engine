-- One row per employee. The weekday expansion lives in stg_hr_roster.
select distinct
    emp_id,
    employee_type,
    dept_l1,
    dept_l2,
    dept_l3,
    dept_l4,
    workplace_code,
    city,
    region,
    floor,
    hire_date,
    term_date,
    is_free_sharing
from {{ ref('stg_hr_roster') }}
