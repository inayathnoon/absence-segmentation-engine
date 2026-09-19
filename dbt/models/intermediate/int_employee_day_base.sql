-- The segmentation cascade, first pass.
--
-- One row per employee per day, every row carrying exactly one label and the
-- rule that produced it.
--
-- Two labels are NOT decided here, because both are patterns rather than
-- events and both need this model's own output to exist first:
--
--   P8  needs the employee's inferred weekday pattern
--   O2  needs a rolling window over the O1 labels produced here
--
-- Both are applied in int_employee_day. The split is a dependency fact, not a
-- design preference: a single model cannot read its own output.
--
-- PRECEDENCE - first match wins. The order differs from the brief's numbering
-- in three places, each for a reason recorded in docs/decisions/0002:
--
--   * employment status (P7, P6) is evaluated first: nothing else can be true
--     of someone who had left or had not started.
--   * a public holiday (P2) beats approved leave (P1): a day the office is
--     shut is not leave anybody spent.
--   * attendance is evaluated before the schedule rules (P5): somebody who
--     came in on a day they were not expected has attended, and filing that
--     under "not a scheduled office day" hides real demand in a planned bucket.
--
-- O4 is evaluated before O1 because it is strictly more specific evidence: a
-- booked desk that was never used is a no-show with a receipt. Under the
-- brief's literal ordering O1 swallows it and the bucket is unreachable.

with spine as (

    select e.*, c.date_day as local_date, c.weekday_mon0, c.is_weekend, c.iso_year_week
    from {{ ref('stg_hr_employee') }} e
    cross join {{ ref('int_calendar') }} c

),

evidence as (

    select
        s.emp_id,
        s.local_date,
        s.weekday_mon0,
        s.is_weekend,
        s.iso_year_week,
        s.dept_l1, s.dept_l2, s.dept_l3, s.dept_l4,
        s.workplace_code,
        s.city,
        s.region,
        s.floor,
        s.employee_type,
        s.is_free_sharing,
        s.hire_date,
        s.term_date,
        f.country,

        sch.team_weekday is not null            as is_team_office_day,
        h.holiday_date is not null              as is_public_holiday,
        lv.leave_date is not null               as is_on_leave,
        lv.leave_type,
        tv.travel_date is not null              as is_on_travel,
        asg.assignment_date is not null         as is_on_assignment,
        bk.booking_id is not null               as has_desk_booking,
        tap.emp_id is not null                  as attended,
        tap.hours_present,
        tap.tap_workplace,
        tap.tap_floor

    from spine s
    left join {{ ref('stg_estate_floor') }} f
        on s.workplace_code = f.workplace_code and s.floor = f.floor
    left join {{ ref('stg_hr_roster') }} sch
        on s.emp_id = sch.emp_id and s.weekday_mon0 = sch.team_weekday
    left join {{ ref('stg_public_holidays') }} h
        on f.country = h.country and s.local_date = h.holiday_date
    left join {{ ref('int_leave_days') }} lv
        on s.emp_id = lv.emp_id and s.local_date = lv.leave_date
    left join {{ ref('int_travel_days') }} tv
        on s.emp_id = tv.emp_id and s.local_date = tv.travel_date
    left join {{ ref('int_assignment_days') }} asg
        on s.emp_id = asg.emp_id and s.local_date = asg.assignment_date
    left join {{ ref('stg_desk_bookings') }} bk
        on s.emp_id = bk.emp_id and s.local_date = bk.booking_date and bk.is_effective
    left join {{ ref('int_tap_day') }} tap
        on s.emp_id = tap.emp_id and s.local_date = tap.local_date

)

select
    *,
    case
        when term_date is not null and local_date > term_date            then 'P7'
        when local_date < hire_date                                      then 'P6'
        when is_public_holiday                                           then 'P2'
        when is_on_leave                                                 then 'P1'
        when is_on_travel                                                then 'P3'
        when is_on_assignment                                            then 'P4'
        when attended and hours_present < {{ var('partial_day_hours_threshold') }} then 'O3'
        when attended                                                    then 'ATTENDED'
        when not is_team_office_day                                      then 'P5'
        when has_desk_booking                                            then 'O4'
        else 'O1'
    end as label_first_pass
from evidence
