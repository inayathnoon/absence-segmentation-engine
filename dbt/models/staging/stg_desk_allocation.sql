select
    dept_l4,
    dept_l3,
    dept_l2,
    dept_l1,
    workplace_code,
    city,
    region,
    floor,
    allocated_workstations,
    cost_per_workstation_month
from {{ source('raw', 'desk_allocation') }}
