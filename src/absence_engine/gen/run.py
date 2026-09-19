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

    allocation = generate_allocation(cfg, employees, estate)
    counts["desk_allocation"] = _write(
        allocation[PUBLISHED_COLUMNS], RAW_DIR, "desk_allocation"
    )
    _write(allocation, TRUTH_DIR, "allocation_truth")

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
    assignment_days = expand_spans(
        assignments, "start_date", "end_date", ["assignment_city"]
    ) if not assignments.empty else {}

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

    bookings = pd.DataFrame(generator.booking_rows)
    counts["desk_bookings"] = _write(bookings, RAW_DIR, "desk_bookings")

    ground_truth = {
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
                str(city): round(float(group["truth_excess_workstations"].mul(
                    group["cost_per_workstation_month"]
                ).sum()), 2)
                for city, group in allocation.groupby("city")
            },
        },
    }
    ground_truth["allocation"]["monthly_saving_total"] = round(
        sum(ground_truth["allocation"]["monthly_saving_by_city"].values()), 2
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
