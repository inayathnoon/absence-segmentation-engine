select
    booking_id,
    emp_id,
    workplace_code,
    floor,
    cast(booking_date as date)  as booking_date,
    created_ts,
    status,
    status = 'confirmed'        as is_effective
from {{ source('raw', 'desk_bookings') }}
