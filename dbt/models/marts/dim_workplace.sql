select
    workplace_code,
    min(workplace_name)                 as workplace_name,
    min(city)                           as city,
    min(country)                        as country,
    min(region)                         as region,
    min(timezone)                       as timezone,
    count(*)                            as floor_count,
    sum(delivered_workstations)         as delivered_workstations,
    sum(free_sharing_workstations)      as free_sharing_workstations,
    min(cost_per_workstation_month)     as cost_per_workstation_month
from {{ ref('stg_estate_floor') }}
group by workplace_code
