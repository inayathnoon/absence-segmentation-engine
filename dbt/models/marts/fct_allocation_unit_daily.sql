-- Supply against realised demand, per allocation unit per day.
--
-- The allocation unit is (team, workplace, floor) - the grain desks are
-- actually assigned at, and therefore the only grain at which a resizing
-- decision can be made. Reporting waste per building is fine for a headline
-- and useless for an action: nobody can hand back half a floor of desks that
-- belong to four different teams.

with demand as (

    select
        ed.local_date,
        ed.iso_year_week,
        ed.dept_l4,
        ed.workplace_code,
        ed.floor,
        count(*) filter (where ed.consumed_desk)                    as desks_consumed,
        count(*) filter (where ed.label_class = 'optimizable')      as optimizable_days,
        count(*) filter (where ed.label_class = 'planned')          as planned_days,
        count(*) filter (where ed.label = 'O1')                     as o1_no_show,
        count(*) filter (where ed.label = 'O2')                     as o2_unreported,
        count(*) filter (where ed.label = 'O3')                     as o3_partial,
        count(*) filter (where ed.label = 'O4')                     as o4_booked_not_used,
        count(*) filter (
            where ed.local_date >= ed.hire_date
              and (ed.term_date is null or ed.local_date <= ed.term_date)
        )                                                           as headcount
    from {{ ref('int_employee_day') }} ed
    group by 1, 2, 3, 4, 5

),

-- A day is *eligible* for demand measurement if the office was open to this
-- unit at all. Holidays and non-office days carry zero attendance by
-- construction; leaving them in the distribution drags the percentile down and
-- the pipeline would report capacity as recoverable that is needed every
-- working day.
eligibility as (

    select
        local_date,
        dept_l4,
        workplace_code,
        floor,
        bool_or(is_team_office_day)                                     as is_office_day,
        bool_and(is_public_holiday)                                     as all_on_holiday
    from {{ ref('int_employee_day_base') }}
    group by 1, 2, 3, 4

)

select
    d.local_date,
    d.iso_year_week,
    a.dept_l1,
    a.dept_l2,
    a.dept_l3,
    d.dept_l4,
    d.workplace_code,
    a.city,
    a.region,
    d.floor,
    a.allocated_workstations,
    a.cost_per_workstation_month,
    d.headcount,
    d.desks_consumed,
    d.optimizable_days,
    d.planned_days,
    d.o1_no_show,
    d.o2_unreported,
    d.o3_partial,
    d.o4_booked_not_used,
    e.is_office_day,
    e.all_on_holiday,
    e.is_office_day and not e.all_on_holiday        as is_demand_eligible_day,
    a.allocated_workstations - d.desks_consumed     as desks_idle
from demand d
inner join {{ ref('stg_desk_allocation') }} a
    on d.dept_l4 = a.dept_l4 and d.workplace_code = a.workplace_code and d.floor = a.floor
left join eligibility e
    on d.local_date = e.local_date and d.dept_l4 = e.dept_l4
   and d.workplace_code = e.workplace_code and d.floor = e.floor
