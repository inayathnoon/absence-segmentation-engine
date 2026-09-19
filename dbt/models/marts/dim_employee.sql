select
    e.emp_id,
    e.employee_type,
    e.dept_l1,
    e.dept_l2,
    e.dept_l3,
    e.dept_l4,
    e.workplace_code,
    e.city,
    e.region,
    e.floor,
    e.hire_date,
    e.term_date,
    e.is_free_sharing,
    coalesce(p.is_inferred_shift_offset, false) as is_inferred_shift_offset,
    coalesce(p.total_attended_days, 0)          as attended_days,
    coalesce(p.off_pattern_share, 0)            as off_pattern_share,
    p.habitual_weekdays
from {{ ref('stg_hr_employee') }} e
left join (
    -- int_employee_pattern is at (employee, weekday) grain; the dimension
    -- wants one row per employee, so the weekday detail is collapsed to a
    -- readable list rather than dropped.
    select
        emp_id,
        max(total_attended_days)                                    as total_attended_days,
        max(off_pattern_share)                                      as off_pattern_share,
        bool_or(is_inferred_shift_offset)                           as is_inferred_shift_offset,
        string_agg(cast(weekday_mon0 as varchar), ',' order by weekday_mon0)
            filter (where is_habitual_weekday)                      as habitual_weekdays
    from {{ ref('int_employee_pattern') }}
    group by emp_id
) p on e.emp_id = p.emp_id
