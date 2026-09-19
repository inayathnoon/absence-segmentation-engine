"""Pandera schemas for every dataframe crossing a module boundary.

Contracts, not data quality rules. A contract failure means the code is wrong -
a column renamed, a type drifted, a grain broken - and should stop the pipeline
immediately. Whether the *data* is plausible is a separate question, answered
in the taxonomy and capacity layers.
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Column, DataFrameSchema

REGIONS = ["AMER", "EMEA", "APAC"]
LEAVE_TYPES = ["annual", "sick", "parental", "unpaid", "comp"]

ESTATE_FLOOR = DataFrameSchema(
    {
        "workplace_code": Column(str),
        "workplace_name": Column(str),
        "city": Column(str),
        "country": Column(str),
        "region": Column(str, pa.Check.isin(REGIONS)),
        "timezone": Column(str),
        "floor": Column(int, pa.Check.gt(0)),
        "cost_per_workstation_month": Column(float, pa.Check.gt(0)),
        "delivered_workstations": Column(int, pa.Check.ge(0)),
        "free_sharing_workstations": Column(int, pa.Check.ge(0)),
    },
    unique=["workplace_code", "floor"],
    strict=True,
    coerce=True,
)

HR_ROSTER = DataFrameSchema(
    {
        "emp_id": Column(str, unique=True),
        "employee_type": Column(str),
        "dept_l1": Column(str),
        "dept_l2": Column(str),
        "dept_l3": Column(str),
        "dept_l4": Column(str),
        "workplace_code": Column(str),
        "city": Column(str),
        "region": Column(str, pa.Check.isin(REGIONS)),
        "floor": Column(int, pa.Check.gt(0)),
        "hire_date": Column(object),
        "term_date": Column(object, nullable=True),
        "is_free_sharing": Column(bool),
        "team_scheduled_weekdays": Column(str),
    },
    strict=True,
    coerce=True,
)

BADGE_TAPS = DataFrameSchema(
    {
        "emp_id": Column(str),
        "workplace_code": Column(str),
        "floor": Column(int, pa.Check.gt(0)),
        "tap_ts_utc": Column("datetime64[ns]"),
        "direction": Column(str, pa.Check.isin(["in", "out"])),
        "local_date": Column(object),
    },
    strict=True,
    coerce=True,
)

LEAVE_REQUESTS = DataFrameSchema(
    {
        "leave_id": Column(str, unique=True),
        "emp_id": Column(str),
        "leave_type": Column(str, pa.Check.isin(LEAVE_TYPES)),
        "start_date": Column(object),
        "end_date": Column(object),
        "status": Column(str, pa.Check.isin(["approved", "pending", "cancelled"])),
        "applied_ts": Column("datetime64[ns]"),
    },
    strict=True,
    coerce=True,
)

TRAVEL_BOOKINGS = DataFrameSchema(
    {
        "booking_id": Column(str, unique=True),
        "emp_id": Column(str),
        "origin_city": Column(str),
        "destination_city": Column(str),
        "depart_date": Column(object),
        "return_date": Column(object),
        "booking_status": Column(str, pa.Check.isin(["confirmed", "cancelled"])),
    },
    strict=True,
    coerce=True,
)

ASSIGNMENTS = DataFrameSchema(
    {
        "assignment_id": Column(str, unique=True),
        "emp_id": Column(str),
        "assignment_city": Column(str),
        "start_date": Column(object),
        "end_date": Column(object),
    },
    strict=True,
    coerce=True,
)

PUBLIC_HOLIDAYS = DataFrameSchema(
    {
        "country": Column(str),
        "holiday_date": Column(object),
        "holiday_name": Column(str),
    },
    unique=["country", "holiday_date"],
    strict=True,
    coerce=True,
)

DESK_ALLOCATION = DataFrameSchema(
    {
        "dept_l4": Column(str),
        "dept_l3": Column(str),
        "dept_l2": Column(str),
        "dept_l1": Column(str),
        "workplace_code": Column(str),
        "city": Column(str),
        "region": Column(str, pa.Check.isin(REGIONS)),
        "floor": Column(int, pa.Check.gt(0)),
        "allocated_workstations": Column(int, pa.Check.gt(0)),
        "cost_per_workstation_month": Column(float, pa.Check.gt(0)),
    },
    unique=["dept_l4", "workplace_code", "floor"],
    strict=True,
    coerce=True,
)

DESK_BOOKINGS = DataFrameSchema(
    {
        "booking_id": Column(str, unique=True),
        "emp_id": Column(str),
        "workplace_code": Column(str),
        "floor": Column(int, pa.Check.gt(0)),
        "booking_date": Column(object),
        "created_ts": Column("datetime64[ns]"),
        "status": Column(str),
    },
    strict=True,
    coerce=True,
)

TEAM_SEATING = DataFrameSchema(
    {
        "dept_l1": Column(str),
        "dept_l2": Column(str),
        "dept_l3": Column(str),
        "dept_l4": Column(str),
        "workplace_code": Column(str),
        "city": Column(str),
        "region": Column(str, pa.Check.isin(REGIONS)),
        "floor": Column(int, pa.Check.gt(0)),
        "seat_share": Column(float, pa.Check.in_range(0, 1)),
    },
    unique=["dept_l4", "workplace_code", "floor"],
    strict=True,
    coerce=True,
)

SOURCE_SCHEMAS: dict[str, DataFrameSchema] = {
    "estate_floor": ESTATE_FLOOR,
    "hr_roster": HR_ROSTER,
    "badge_taps": BADGE_TAPS,
    "leave_requests": LEAVE_REQUESTS,
    "travel_bookings": TRAVEL_BOOKINGS,
    "assignments": ASSIGNMENTS,
    "public_holidays": PUBLIC_HOLIDAYS,
    "desk_allocation": DESK_ALLOCATION,
    "desk_bookings": DESK_BOOKINGS,
    "team_seating": TEAM_SEATING,
}

PARTITIONED = {"badge_taps"}
