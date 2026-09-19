"""Planned absence: public holidays, leave, travel and out-of-city postings.

These are the P1-P4 causes. Each is generated at its natural grain - holidays
per country-date, leave and travel per request - and expanded to employee-days
by the caller, because request-grain overlap has to be collapsed exactly once.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from ..config import Config
from .rng import choice_from_mix, lognormal_from_mean, substream


def generate_holidays(cfg: Config) -> pd.DataFrame:
    """Public holidays, per country.

    Country-specific and not aligned across the estate, which is the point: a
    global attendance number that ignores holidays reports a collapse in
    attendance every time one country has a day off.
    """
    rng = substream(cfg.seed, "holidays")
    countries = sorted({c.country for c in cfg.cities})
    weekdays = [
        cfg.start_date + timedelta(days=i)
        for i in range(cfg.profile.n_days)
        if (cfg.start_date + timedelta(days=i)).weekday() < 5
    ]
    rows: list[dict] = []
    per_country = min(cfg.planted.public_holiday_days_per_country, len(weekdays))
    for country in countries:
        chosen = rng.choice(len(weekdays), size=per_country, replace=False)
        for idx in chosen:
            rows.append(
                {
                    "country": country,
                    "holiday_date": weekdays[int(idx)],
                    "holiday_name": f"{country} Public Holiday",
                }
            )
    return pd.DataFrame(rows).sort_values(["country", "holiday_date"]).reset_index(drop=True)


def generate_leave(cfg: Config, employees: pd.DataFrame) -> pd.DataFrame:
    rng = substream(cfg.seed, "leave")
    lv = cfg.planted.leave
    n_emp, n_days = len(employees), cfg.profile.n_days

    spells = rng.binomial(n_days, lv["daily_hazard"], size=n_emp)
    total = int(spells.sum())
    columns = ["leave_id", "emp_id", "leave_type", "start_date", "end_date", "status", "applied_ts"]
    if total == 0:
        return pd.DataFrame(columns=columns)

    duration_cfg = lv["duration_days"]
    durations = np.clip(
        np.round(lognormal_from_mean(rng, duration_cfg["mean"], duration_cfg["sigma"], total)),
        1,
        duration_cfg["max"],
    ).astype(int)
    starts = np.array(
        [cfg.start_date + timedelta(days=int(o)) for o in rng.integers(0, n_days, size=total)]
    )
    ends = np.array([s + timedelta(days=int(d) - 1) for s, d in zip(starts, durations, strict=True)])
    applied = [
        datetime.combine(s - timedelta(days=int(lead)), time(9, 0))
        for s, lead in zip(starts, rng.integers(1, 28, size=total), strict=True)
    ]

    return pd.DataFrame(
        {
            "leave_id": [f"LV{i:08d}" for i in range(total)],
            "emp_id": np.repeat(employees["emp_id"].to_numpy(), spells),
            "leave_type": choice_from_mix(rng, lv["type_mix"], total),
            "start_date": starts,
            "end_date": ends,
            "status": choice_from_mix(rng, lv["status_mix"], total),
            "applied_ts": applied,
        }
    )


def generate_travel(cfg: Config, employees: pd.DataFrame) -> pd.DataFrame:
    rng = substream(cfg.seed, "travel")
    tv = cfg.planted.travel
    n_emp, n_days = len(employees), cfg.profile.n_days

    trips = rng.binomial(n_days, tv["trip_hazard"], size=n_emp)
    total = int(trips.sum())
    columns = [
        "booking_id", "emp_id", "origin_city", "destination_city",
        "depart_date", "return_date", "booking_status",
    ]
    if total == 0:
        return pd.DataFrame(columns=columns)

    origins = np.repeat(employees["city"].to_numpy(), trips)
    city_names = [c.name for c in cfg.cities]
    destinations = np.array(
        [rng.choice([c for c in city_names if c != o]) if len(city_names) > 1 else o for o in origins]
    )
    departs = np.array(
        [cfg.start_date + timedelta(days=int(o)) for o in rng.integers(0, n_days, size=total)]
    )
    lengths = rng.integers(tv["duration_days"]["min"], tv["duration_days"]["max"] + 1, size=total)
    returns = np.array([d + timedelta(days=int(k)) for d, k in zip(departs, lengths, strict=True)])

    return pd.DataFrame(
        {
            "booking_id": [f"TR{i:08d}" for i in range(total)],
            "emp_id": np.repeat(employees["emp_id"].to_numpy(), trips),
            "origin_city": origins,
            "destination_city": destinations,
            "depart_date": departs,
            "return_date": returns,
            "booking_status": np.where(
                rng.random(total) < tv["cancelled_share"], "cancelled", "confirmed"
            ),
        }
    )


def generate_assignments(cfg: Config, employees: pd.DataFrame) -> pd.DataFrame:
    """Postings to another city for a period - planned, and not travel."""
    rng = substream(cfg.seed, "assignments")
    posted = employees[rng.random(len(employees)) < cfg.planted.assignment_share]
    if posted.empty:
        return pd.DataFrame(
            columns=["assignment_id", "emp_id", "assignment_city", "start_date", "end_date"]
        )
    city_names = [c.name for c in cfg.cities]
    rows = []
    for i, row in enumerate(posted.itertuples(index=False)):
        options = [c for c in city_names if c != row.city]
        if not options:
            continue
        start = cfg.start_date + timedelta(days=int(rng.integers(0, max(cfg.profile.n_days // 2, 1))))
        rows.append(
            {
                "assignment_id": f"AS{i:06d}",
                "emp_id": row.emp_id,
                "assignment_city": options[int(rng.integers(len(options)))],
                "start_date": start,
                "end_date": start + timedelta(days=int(rng.integers(15, 120))),
            }
        )
    return pd.DataFrame(rows)


def expand_spans(
    frame: pd.DataFrame, start_col: str, end_col: str, keep: list[str]
) -> dict[tuple[str, date], dict]:
    """Expand request-grain spans to (emp_id, date) -> attributes.

    Collapsing here, once, is deliberate: two overlapping approved requests are
    still one person on one day, and expanding them independently downstream is
    how absence gets double counted.
    """
    out: dict[tuple[str, date], dict] = {}
    for row in frame.itertuples(index=False):
        day = getattr(row, start_col)
        end = getattr(row, end_col)
        while day <= end:
            out.setdefault((row.emp_id, day), {k: getattr(row, k) for k in keep})
            day += timedelta(days=1)
    return out
