-- The segmentation, aggregated to (date, allocation unit, label).
--
-- Long rather than wide: one row per label per unit per day. A wide table with
-- a column per bucket has to be altered every time the taxonomy gains a rule,
-- and every consumer has to be altered with it.

select
    ed.local_date,
    ed.iso_year_week,
    ed.workplace_code,
    ed.city,
    ed.region,
    ed.dept_l1,
    ed.dept_l2,
    ed.dept_l3,
    ed.dept_l4,
    ed.floor,
    ed.label,
    ed.label_class,
    ed.label_reason,
    count(*)                                        as employee_days,
    count(*) filter (where ed.consumed_desk)        as desk_days_consumed,
    avg(ed.hours_present) filter (where ed.attended) as avg_hours_present
from {{ ref('int_employee_day') }} ed
group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13
