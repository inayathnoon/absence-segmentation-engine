-- Taps in UTC and workplace-local time, assigned to a business day.
--
-- The local date is what attendance is measured on, and it is derived from the
-- workplace timezone rather than assumed to match the UTC date.
--
-- The business date is a second, separate idea. Somebody who badges in at
-- 19:40 and out at 03:50 has worked one day, not two. Left on local date the
-- entry and the exit land on different days, and both are wrong: the first day
-- looks like a one-tap visit of zero hours - which the partial-day rule then
-- reports as waste - and the second looks like a phantom visit nobody made.
--
-- The rule: an exit inherits the business date of the entry it follows. An
-- exit with no preceding entry falls back to its own local date.

with localised as (

    select
        t.emp_id,
        t.workplace_code,
        t.floor,
        t.direction,
        t.tap_ts_utc,
        timezone(e.timezone, timezone('UTC', t.tap_ts_utc))                 as tap_ts_local,
        cast(timezone(e.timezone, timezone('UTC', t.tap_ts_utc)) as date)   as local_date,
        e.city,
        e.region,
        e.country
    from {{ source('raw', 'badge_taps') }} t
    inner join {{ ref('stg_estate_floor') }} e
        on t.workplace_code = e.workplace_code and t.floor = e.floor

),

anchored as (

    select
        *,
        last_value(
            case when direction = 'in' then local_date end ignore nulls
        ) over (
            partition by emp_id
            order by tap_ts_local
            rows between unbounded preceding and current row
        ) as entry_anchor_date
    from localised

)

select
    emp_id,
    workplace_code,
    floor,
    direction,
    tap_ts_utc,
    tap_ts_local,
    local_date,
    cast(coalesce(entry_anchor_date, local_date) as date) as business_date,
    city,
    region,
    country
from anchored
