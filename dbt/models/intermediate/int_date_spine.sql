-- Date spine over the generated window, derived from the tap feed rather than
-- hardcoded so it can never be narrower than the data it serves.

with bounds as (
    select min(local_date) as min_date, max(local_date) as max_date
    from {{ ref('stg_badge_taps') }}
)

select
    cast(unnest(generate_series(min_date, max_date, interval '1 day')) as date) as date_day
from bounds
