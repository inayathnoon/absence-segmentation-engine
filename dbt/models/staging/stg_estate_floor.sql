select
    workplace_code,
    workplace_name,
    city,
    country,
    region,
    timezone,
    floor,
    delivered_workstations,
    free_sharing_workstations,
    cost_per_workstation_month
from {{ source('raw', 'estate_floor') }}
