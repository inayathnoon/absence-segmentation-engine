select
    booking_id,
    emp_id,
    origin_city,
    destination_city,
    cast(depart_date as date)   as depart_date,
    cast(return_date as date)   as return_date,
    booking_status,
    booking_status = 'confirmed' as is_effective
from {{ source('raw', 'travel_bookings') }}
