-- One row per employee per BUSINESS day of presence, with dwell time.
--
-- Grouped on business_date so a shift that runs past local midnight is one
-- visit rather than a zero-hour visit followed by a phantom one.
--
-- Dwell comes from the first and last tap of the day. That is an upper bound on
-- time at a desk, not a measurement of it - somebody who badges in, leaves for
-- five hours and badges out at six looks like a full day. The partial-day rule
-- uses it anyway, because the alternative is sensor data this platform does not
-- have, and the bound is conservative in the right direction: it under-detects
-- partial days rather than inventing them.

select
    emp_id,
    business_date as local_date,
    min(tap_ts_local)                                               as first_tap_ts,
    max(tap_ts_local)                                               as last_tap_ts,
    count(*)                                                        as tap_count,
    arg_min(workplace_code, tap_ts_local)                           as tap_workplace,
    arg_min(floor, tap_ts_local)                                    as tap_floor,
    date_diff('minute', min(tap_ts_local), max(tap_ts_local)) / 60.0 as hours_present
from {{ ref('stg_badge_taps') }}
group by emp_id, business_date
