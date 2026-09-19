select
    country,
    cast(holiday_date as date)  as holiday_date,
    holiday_name
from {{ source('raw', 'public_holidays') }}
