-- Inferred working patterns, per employee and per weekday.
--
-- P8 (shift pattern offset) is the one planned cause with no source record
-- behind it. The HR roster publishes the team's policy pattern, not an
-- individual's variation from it, so the variation has to be inferred from
-- where that person's attendance actually falls.
--
-- Two things are inferred, and they are different questions:
--
--   is_inferred_shift_offset  does this person work a different set of days
--                             from their team at all?
--   is_habitual_weekday       is THIS weekday one of the days they work?
--
-- Both are needed. Knowing only the first, the rule cannot tell a shift
-- worker's planned day off from their unplanned one, and would excuse every
-- absence they ever have.
--
-- The thresholds are deliberately conservative. A loose rule relabels ordinary
-- no-shows as a planned shift pattern, which moves waste out of the
-- optimizable bucket and makes the estate look better than it is. The cost is
-- that a genuine shift worker with little history stays in O1 until enough
-- evidence accumulates - this is a lagging classifier, and it should be.

with employee_days as (

    select
        b.emp_id,
        b.local_date,
        b.weekday_mon0,
        b.is_team_office_day,
        b.attended
    from {{ ref('int_employee_day_base') }} b
    -- Only days the person could have attended. Counting leave and holidays as
    -- "did not attend this weekday" would make everyone look irregular.
    where b.label_first_pass not in ('P1', 'P2', 'P3', 'P4', 'P6', 'P7')
      and not b.is_weekend

),

by_weekday as (

    select
        emp_id,
        weekday_mon0,
        count(*)                                        as occurrences,
        count(*) filter (where attended)                as attended_days,
        count(*) filter (where attended) * 1.0 / nullif(count(*), 0) as attend_share,
        bool_or(is_team_office_day)                     as is_team_weekday
    from employee_days
    group by 1, 2

),

by_employee as (

    select
        emp_id,
        sum(attended_days)                                                  as attended_days,
        sum(attended_days) filter (where not is_team_weekday)               as attended_off_pattern,
        sum(attended_days) filter (where not is_team_weekday) * 1.0
            / nullif(sum(attended_days), 0)                                 as off_pattern_share
    from by_weekday
    group by 1

)

select
    w.emp_id,
    w.weekday_mon0,
    w.occurrences,
    w.attended_days                                     as weekday_attended_days,
    w.attend_share,
    w.is_team_weekday,
    e.attended_days                                     as total_attended_days,
    e.off_pattern_share,
    coalesce(e.attended_days, 0) >= 3
        and coalesce(e.off_pattern_share, 0) >= 0.6     as is_inferred_shift_offset,
    w.occurrences >= 2 and w.attend_share >= 0.5        as is_habitual_weekday
from by_weekday w
left join by_employee e on w.emp_id = e.emp_id
