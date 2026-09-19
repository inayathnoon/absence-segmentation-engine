with expanded as (
    select
        emp_id,
        booking_id,
        destination_city,
        cast(unnest(generate_series(depart_date, return_date, interval '1 day')) as date)
            as travel_date
    from {{ ref('stg_travel_bookings') }}
    where is_effective
)

select emp_id, travel_date, destination_city, booking_id
from expanded
qualify row_number() over (partition by emp_id, travel_date order by booking_id) = 1
