-- The segmentation cascade, second pass: P8 and O2.
--
-- Both are patterns rather than events, so both need the first pass to exist
-- before they can be decided. P8 is applied before O2: a planned shift pattern
-- beats a no-show pattern, because the days in question were never days that
-- person was expected in.
--
-- O2 is a pattern, not an event. An employee who misses one scheduled day is
-- an O1; an employee who has missed {{ var('unreported_absence_min_no_shows') }}
-- of their last {{ var('unreported_absence_window_days') }} scheduled days is
-- something a manager should be told about, and the desk they hold is a
-- different kind of waste.
--
-- The window runs over *scheduled days*, not calendar days. Over calendar days
-- a three-day-a-week team can never accumulate enough misses to trigger, and
-- the rule would only ever fire for five-day teams.

with pattern as (

    select emp_id, weekday_mon0, is_inferred_shift_offset, is_habitual_weekday
    from {{ ref('int_employee_pattern') }}

),

-- P8 first: a shift-offset worker absent on a team office day that is not one
-- of their own working days has a planned absence, not a no-show.
with_p8 as (

    select
        b.*,
        case
            when b.label_first_pass = 'O1'
                 and b.is_team_office_day
                 and coalesce(p.is_inferred_shift_offset, false)
                 and not coalesce(p.is_habitual_weekday, false)
            then 'P8'
            else b.label_first_pass
        end as label_after_p8
    from {{ ref('int_employee_day_base') }} b
    left join pattern p
        on b.emp_id = p.emp_id and b.weekday_mon0 = p.weekday_mon0

),

office_days as (

    select
        emp_id,
        local_date,
        label_after_p8 in ('O1', 'O4') as is_no_show,
        row_number() over (partition by emp_id order by local_date) as office_day_seq
    from with_p8
    where is_team_office_day
      and label_after_p8 not in ('P1', 'P2', 'P3', 'P4', 'P6', 'P7', 'P8')

),

windowed as (

    select
        emp_id,
        local_date,
        sum(case when is_no_show then 1 else 0 end) over w as no_shows_in_window,
        count(*) over w                                    as office_days_in_window
    from office_days
    window w as (
        partition by emp_id
        order by office_day_seq
        rows between {{ var('unreported_absence_window_days') | int - 1 }} preceding
                 and current row
    )

),

-- The baseline this rule is relative to. An absolute "three misses in ten"
-- threshold is only meaningful at a particular attendance rate: where a team
-- attends 60% of its office days, the average person misses four of every ten
-- by chance, and an absolute rule labels almost the whole team as an
-- unreported absence. Measuring against the team's own rate keeps the bucket
-- meaning what it says - this person is an outlier among their peers.
team_baseline as (

    select
        dept_l4,
        count(*) filter (where label_after_p8 in ('O1', 'O4')) * 1.0
            / nullif(count(*), 0) as team_no_show_rate
    from with_p8
    where is_team_office_day
      and label_after_p8 not in ('P1', 'P2', 'P3', 'P4', 'P6', 'P7', 'P8')
    group by 1

),

labelled as (

    select
        b.* exclude (label_first_pass, label_after_p8),
        coalesce(w.no_shows_in_window, 0)       as no_shows_in_window,
        coalesce(w.office_days_in_window, 0)    as office_days_in_window,
        coalesce(w.no_shows_in_window, 0) * 1.0
            / nullif(w.office_days_in_window, 0) as no_show_rate_in_window,
        coalesce(t.team_no_show_rate, 0)        as team_no_show_rate,
        case
            when b.label_after_p8 = 'O1'
                 and coalesce(w.no_shows_in_window, 0)
                     >= {{ var('unreported_absence_min_no_shows') }}
                 and coalesce(w.no_shows_in_window, 0) * 1.0 / nullif(w.office_days_in_window, 0)
                     >= {{ var('unreported_absence_baseline_multiple') }}
                        * coalesce(t.team_no_show_rate, 1)
            then 'O2'
            else b.label_after_p8
        end as label
    from with_p8 b
    left join windowed w
        on b.emp_id = w.emp_id and b.local_date = w.local_date
    left join team_baseline t on b.dept_l4 = t.dept_l4

)

select
    *,
    case label
        when 'P1' then 'P1 approved leave'
        when 'P2' then 'P2 public holiday'
        when 'P3' then 'P3 business travel'
        when 'P4' then 'P4 on assignment in another city'
        when 'P5' then 'P5 not a scheduled office day'
        when 'P6' then 'P6 not yet onboarded'
        when 'P7' then 'P7 post exit'
        when 'P8' then 'P8 shift pattern offset (inferred from attendance)'
        when 'O1' then 'O1 no-show on a scheduled day'
        when 'O2' then 'O2 unreported absence: recurring no-shows on scheduled days'
        when 'O3' then 'O3 partial day: present under the dwell threshold'
        when 'O4' then 'O4 desk booked and never used'
        else 'Attended'
    end as label_reason,
    case
        when label in ('O1', 'O2', 'O3', 'O4') then 'optimizable'
        when label = 'ATTENDED'                then 'attended'
        else 'planned'
    end as label_class,
    -- Desk demand: a day that actually consumed a desk. A partial day still
    -- occupies one while the person is there, so it counts; it is flagged
    -- separately rather than discounted, because a half-used desk cannot be
    -- half-removed.
    label in ('ATTENDED', 'O3') as consumed_desk
from labelled
