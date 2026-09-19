select
    date_day,
    case when extract(dow from date_day) = 0 then 6
         else cast(extract(dow from date_day) as integer) - 1 end   as weekday_mon0,
    extract(dow from date_day) in (0, 6)                            as is_weekend,
    strftime(date_day, '%Y-W%V')                                    as iso_year_week,
    strftime(date_day, '%A')                                        as day_name
from {{ ref('int_date_spine') }}
