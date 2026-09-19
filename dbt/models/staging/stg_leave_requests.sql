select
    leave_id,
    emp_id,
    leave_type,
    cast(start_date as date)    as start_date,
    cast(end_date as date)      as end_date,
    status,
    applied_ts,
    status = 'approved'         as is_effective
from {{ source('raw', 'leave_requests') }}
