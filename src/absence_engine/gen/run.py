"""Generate every source, and the truth the pipeline is graded against.

Two output roots, and the separation is the whole integrity story:

* ``data/raw/``   - what a production pipeline would receive. No labels, no
                    causes, no requirement figures.
* ``data/truth/`` - what the simulator knows. Read only by the grading code
                    and the tests; never joined into the warehouse.

If a column would let the warehouse cheat, it lives in truth.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ..config import GROUND_TRUTH, RAW_DIR, TRUTH_DIR, Config, load_config
from .absence import (
    expand_spans,
    generate_assignments,
    generate_holidays,
    generate_leave,
    generate_travel,
)
from .allocation import PUBLISHED_COLUMNS, generate_allocation
from .attendance import DayGenerator
from .estate import generate_estate
from .org import generate_org


def _write(frame: pd.DataFrame, root: Path, name: str) -> int:
    out = root / name
    out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out / "part-000.parquet", index=False)
    return len(frame)


def _write_partitioned(frames: dict[date, pd.DataFrame], root: Path, name: str) -> int:
    total = 0
    for day, frame in frames.items():
        if frame is None or frame.empty:
            continue
        out = root / name / f"dt={day.isoformat()}"
        out.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(out / "part-000.parquet", index=False)
        total += len(frame)
    return total


def _realised_demand(
    cfg: Config,
    employees: pd.DataFrame,
    truth_frames: dict[date, pd.DataFrame],
    holidays_by_day: dict[date, set[str]],
) -> pd.DataFrame:
    """Desks consumed per allocation unit per day, with day eligibility.

    A day is eligible for sizing if it is one of the team's office weekdays and
    the unit's country was not on public holiday. Holidays and non-office days
    carry zero demand by construction; leaving them in drags the percentile
    toward zero and would plant a requirement nobody could meet.
    """
    lookup = employees.set_index("emp_id")
    country_of_city = {c.name: c.country for c in cfg.cities}
    team_days = {
        emp: set(days)
        for emp, days in zip(employees["emp_id"], employees["team_scheduled_weekdays"], strict=True)
    }

    rows: list[pd.DataFrame] = []
    for day, truth in truth_frames.items():
        if truth.empty:
            continue
        frame = truth.copy()
        frame["weekday"] = day.weekday()
        frame = frame.join(lookup[["dept_l4", "workplace_code", "floor", "city"]], on="emp_id")
        frame["country"] = frame["city"].map(country_of_city)
        holiday_countries = holidays_by_day.get(day, set())
        frame["is_eligible"] = [
            (day.weekday() in team_days.get(emp, set())) and (country not in holiday_countries)
            for emp, country in zip(frame["emp_id"], frame["country"], strict=True)
        ]
        rows.append(frame)

    combined = pd.concat(rows, ignore_index=True)
    return combined.groupby(
        ["dept_l4", "workplace_code", "floor", "local_date", "weekday"], as_index=False
    ).agg(
        desks_consumed=("truth_attended", "sum"),
        is_eligible=("is_eligible", "any"),
    )


def generate_all(cfg: Config | None = None, clean: bool = True) -> dict:
    cfg = cfg or load_config()
    if clean:
        for root in (RAW_DIR, TRUTH_DIR):
            if root.exists():
                shutil.rmtree(root)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)

    days = [cfg.start_date + timedelta(days=i) for i in range(cfg.profile.n_days)]
    counts: dict[str, int] = {}

    estate = generate_estate(cfg)
    counts["estate_floor"] = _write(estate, RAW_DIR, "estate_floor")

    employees, seating = generate_org(cfg, estate)
    roster = employees.drop(
        columns=[
            "attendance_propensity",
            "is_chronic_no_show",
            "has_shift_offset",
            "scheduled_weekdays",
        ]
    ).copy()
    # The roster publishes the team's policy pattern, which is a real reference
    # fact. It does not publish the individual's shifted pattern - that is the
    # thing P8 has to be inferred from evidence, not read off a column.
    roster["team_scheduled_weekdays"] = roster["team_scheduled_weekdays"].map(
        lambda days: ",".join(str(d) for d in days)
    )
    counts["hr_roster"] = _write(roster, RAW_DIR, "hr_roster")
    counts["team_seating"] = _write(seating, RAW_DIR, "team_seating")

    holidays = generate_holidays(cfg)
    counts["public_holidays"] = _write(holidays, RAW_DIR, "public_holidays")
    leave = generate_leave(cfg, employees)
    counts["leave_requests"] = _write(leave, RAW_DIR, "leave_requests")
    travel = generate_travel(cfg, employees)
    counts["travel_bookings"] = _write(travel, RAW_DIR, "travel_bookings")
    assignments = generate_assignments(cfg, employees)
    counts["assignments"] = _write(assignments, RAW_DIR, "assignments")

    # Expand spans once, here, so overlapping requests cannot be double counted
    # by two different consumers doing it their own way.
    leave_days = expand_spans(
        leave[leave["status"] == "approved"], "start_date", "end_date", ["leave_type"]
    )
    travel_days = expand_spans(
        travel[travel["booking_status"] == "confirmed"],
        "depart_date",
        "return_date",
        ["destination_city"],
    )
    assignment_days = (
        expand_spans(assignments, "start_date", "end_date", ["assignment_city"])
        if not assignments.empty
        else {}
    )

    leave_by_day: dict[date, set[str]] = defaultdict(set)
    for emp, day in leave_days:
        leave_by_day[day].add(emp)
    travel_by_day: dict[date, set[str]] = defaultdict(set)
    for emp, day in travel_days:
        travel_by_day[day].add(emp)
    assignment_by_day: dict[date, set[str]] = defaultdict(set)
    for emp, day in assignment_days:
        assignment_by_day[day].add(emp)
    holidays_by_day: dict[date, set[str]] = defaultdict(set)
    for row in holidays.itertuples(index=False):
        holidays_by_day[row.holiday_date].add(row.country)

    generator = DayGenerator(cfg, employees, estate)
    tap_frames: dict[date, pd.DataFrame] = {}
    truth_frames: dict[date, pd.DataFrame] = {}
    for day in days:
        taps, truth = generator.generate_day(
            day,
            leave_by_day.get(day, set()),
            travel_by_day.get(day, set()),
            assignment_by_day.get(day, set()),
            holidays_by_day.get(day, set()),
        )
        tap_frames[day] = taps
        truth_frames[day] = truth

    counts["badge_taps"] = _write_partitioned(tap_frames, RAW_DIR, "badge_taps")
    _write_partitioned(truth_frames, TRUTH_DIR, "employee_day_truth")

    # Allocation is sized AFTER attendance, from the demand the simulator
    # actually realised. Sizing it beforehand from attendance propensities
    # assumed nobody is ever on leave, which put the planted requirement
    # permanently above anything badge evidence could support.
    daily_demand = _realised_demand(cfg, employees, truth_frames, holidays_by_day)
    allocation = generate_allocation(cfg, employees, estate, daily_demand)
    counts["desk_allocation"] = _write(allocation[PUBLISHED_COLUMNS], RAW_DIR, "desk_allocation")
    _write(allocation, TRUTH_DIR, "allocation_truth")

    bookings = pd.DataFrame(generator.booking_rows)
    counts["desk_bookings"] = _write(bookings, RAW_DIR, "desk_bookings")

    ground_truth: dict = {
        "profile": cfg.profile_name,
        "seed": cfg.seed,
        "start_date": cfg.start_date.isoformat(),
        "end_date": cfg.end_date.isoformat(),
        "row_counts": counts,
        "label_counts": generator.tally.counts,
        "label_shares": generator.tally.shares(),
        "allocation": {
            "units": int(len(allocation)),
            "allocated_workstations": int(allocation["allocated_workstations"].sum()),
            "required_workstations": int(allocation["truth_required_workstations"].sum()),
            "excess_workstations": int(allocation["truth_excess_workstations"].sum()),
            "excess_by_cause": {
                str(k): int(v)
                for k, v in allocation.groupby("truth_excess_cause")["truth_excess_workstations"]
                .sum()
                .items()
            },
            "excess_by_city": {
                str(k): int(v)
                for k, v in allocation.groupby("city")["truth_excess_workstations"].sum().items()
            },
            "monthly_saving_by_city": {
                str(city): round(
                    float(
                        group["truth_excess_workstations"]
                        .mul(group["cost_per_workstation_month"])
                        .sum()
                    ),
                    2,
                )
                for city, group in allocation.groupby("city")
            },
        },
    }
    allocation_truth: dict = ground_truth["allocation"]
    allocation_truth["monthly_saving_total"] = round(
        sum(allocation_truth["monthly_saving_by_city"].values()), 2
    )
    GROUND_TRUTH.parent.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH.write_text(json.dumps(ground_truth, indent=2, sort_keys=True))
    return {"row_counts": counts, "ground_truth": ground_truth}


if __name__ == "__main__":  # pragma: no cover
    result = generate_all()
    for table, count in sorted(result["row_counts"].items()):
        print(f"{table:24s} {count:>12,}")
    truth = result["ground_truth"]["allocation"]
    print(
        f"\nplanted excess: {truth['excess_workstations']:,} desks of "
        f"{truth['allocated_workstations']:,} allocated "
        f"({truth['excess_workstations'] / truth['allocated_workstations']:.1%})"
    )
