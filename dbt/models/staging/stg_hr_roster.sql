-- The roster, with the team's policy pattern expanded to one row per weekday.
--
-- Expanding here rather than parsing a comma-separated string in five
-- downstream models is the difference between one join and five chances to
-- parse it differently.

with roster as (

    select
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
        cast(hire_date as date)     as hire_date,
        cast(term_date as date)     as term_date,
        is_free_sharing,
        team_scheduled_weekdays
    from {{ source('raw', 'hr_roster') }}

)

select
    * exclude (team_scheduled_weekdays),
    cast(unnest(str_split(team_scheduled_weekdays, ',')) as integer) as team_weekday
from roster
