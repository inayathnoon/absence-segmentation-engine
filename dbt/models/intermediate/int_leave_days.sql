-- Approved leave expanded to employee-days, overlaps collapsed.
--
-- Two overlapping approved requests are still one person on one day. Expanding
-- them independently is the standard way absence gets double counted, and it
-- inflates the planned bucket at the expense of the optimizable one - which is
-- the direction that makes an estate look better than it is.

with expanded as (
    select
        emp_id,
        leave_id,
        leave_type,
        applied_ts,
        cast(unnest(generate_series(start_date, end_date, interval '1 day')) as date) as leave_date
    from {{ ref('stg_leave_requests') }}
    where is_effective
)

select emp_id, leave_date, leave_type, leave_id
from expanded
qualify row_number() over (partition by emp_id, leave_date order by applied_ts, leave_id) = 1
