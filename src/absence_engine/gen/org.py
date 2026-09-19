"""Departments, teams, employees, and where each of them sits.

An employee belongs to one L4 team; a team is seated on one to three floors of
one workplace. The (team, floor) pair is the *allocation unit* - the grain
desks are actually assigned at, and therefore the grain a resizing decision is
made at.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from ..config import Config
from .rng import beta_around, choice_from_mix, lognormal_from_mean, substream

WEEKDAYS = [0, 1, 2, 3, 4]


def _build_teams(cfg: Config, rng: np.random.Generator) -> list[dict]:
    teams: list[dict] = []
    for l1 in cfg.org.departments_l1:
        for i2 in range(
            1, int(rng.integers(cfg.org.l2_per_l1["min"], cfg.org.l2_per_l1["max"] + 1)) + 1
        ):
            l2 = f"{l1} Division {i2}"
            for i3 in range(
                1, int(rng.integers(cfg.org.l3_per_l2["min"], cfg.org.l3_per_l2["max"] + 1)) + 1
            ):
                l3 = f"{l2} Group {i3}"
                for i4 in range(
                    1, int(rng.integers(cfg.org.l4_per_l3["min"], cfg.org.l4_per_l3["max"] + 1)) + 1
                ):
                    teams.append(
                        {
                            "dept_l1": l1,
                            "dept_l2": l2,
                            "dept_l3": l3,
                            "dept_l4": f"{l3} Team {i4}",
                        }
                    )
    return teams


def _scheduled_weekdays(cfg: Config, rng: np.random.Generator, n_days: int) -> tuple[int, ...]:
    weights = np.array(cfg.planted.weekday_weights, dtype=float)
    chosen = rng.choice(WEEKDAYS, size=n_days, replace=False, p=weights / weights.sum())
    return tuple(sorted(int(d) for d in chosen))


def generate_org(cfg: Config, estate: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (employees, team_seating).

    ``team_seating`` is one row per allocation unit: team, workplace, floor,
    and the share of the team's headcount seated there.
    """
    rng = substream(cfg.seed, "org")
    teams = _build_teams(cfg, rng)
    n = cfg.profile.n_employees

    # Assign teams to workplaces, weighted by delivered capacity so large
    # buildings hold more teams.
    by_workplace = (
        estate.groupby(["workplace_code", "city", "region"], as_index=False)[
            "delivered_workstations"
        ]
        .sum()
        .sort_values("delivered_workstations", ascending=False)
    )
    codes = by_workplace["workplace_code"].to_numpy()
    capacity_p = by_workplace["delivered_workstations"].to_numpy(dtype=float)
    capacity_p = capacity_p / capacity_p.sum()
    code_city = dict(zip(codes, by_workplace["city"], strict=True))
    code_region = dict(zip(codes, by_workplace["region"], strict=True))
    floors_by_code: dict[str, list[int]] = (
        estate.groupby("workplace_code")["floor"].apply(list).to_dict()
    )

    team_workplace = rng.choice(codes, size=len(teams), p=capacity_p)
    seating_rows: list[dict] = []
    team_floor_options: dict[str, list[int]] = {}
    for team, code in zip(teams, team_workplace, strict=True):
        available = floors_by_code[code]
        n_floors = min(
            int(rng.integers(cfg.org.floors_per_team["min"], cfg.org.floors_per_team["max"] + 1)),
            len(available),
        )
        floors = sorted(rng.choice(available, size=n_floors, replace=False).tolist())
        team_floor_options[team["dept_l4"]] = floors
        weights = rng.dirichlet(np.full(len(floors), 4.0))
        for floor, weight in zip(floors, weights, strict=True):
            seating_rows.append(
                {
                    **team,
                    "workplace_code": code,
                    "city": code_city[code],
                    "region": code_region[code],
                    "floor": int(floor),
                    "seat_share": float(weight),
                }
            )
    seating = pd.DataFrame(seating_rows)

    # Employees, distributed over teams by a skewed size draw.
    sizes = np.maximum(
        cfg.org.team_size["min"],
        lognormal_from_mean(rng, cfg.org.team_size["mean"], cfg.org.team_size["sigma"], len(teams)),
    )
    team_idx = rng.choice(len(teams), size=n, p=sizes / sizes.sum())
    team_df = pd.DataFrame([teams[i] for i in team_idx]).reset_index(drop=True)

    workplace = np.array([team_workplace[i] for i in team_idx])
    region = np.array([code_region[c] for c in workplace])

    # Seat each employee on one of their team's floors, matching the team's
    # seat shares so floor populations are consistent with the seating table.
    floor = np.empty(n, dtype=int)
    share_lookup = {
        (row.dept_l4, row.floor): row.seat_share for row in seating.itertuples(index=False)
    }
    for team_name, group in team_df.groupby("dept_l4").groups.items():
        options = team_floor_options[str(team_name)]
        probs = np.array([share_lookup[(team_name, f)] for f in options], dtype=float)
        probs = probs / probs.sum()
        idx = np.asarray(group)
        floor[idx] = rng.choice(options, size=len(idx), p=probs)

    tenure = np.minimum(
        lognormal_from_mean(rng, cfg.org.tenure_days["mean"], cfg.org.tenure_days["sigma"], n),
        cfg.org.tenure_days["max"],
    )
    hire_date = np.array([cfg.end_date - timedelta(days=int(t)) for t in tenure], dtype="object")

    # Joiners and leavers inside the window, so P6 (not yet onboarded) and P7
    # (post exit) are reachable states rather than theoretical ones.
    joiners = rng.random(n) < cfg.org.joiner_share
    joiner_offsets = rng.integers(0, cfg.profile.n_days, size=int(joiners.sum()))
    hire_date[joiners] = [cfg.start_date + timedelta(days=int(o)) for o in joiner_offsets]

    term_date = np.full(n, None, dtype="object")
    leavers = (rng.random(n) < cfg.org.leaver_share) & ~joiners
    leaver_offsets = rng.integers(0, cfg.profile.n_days, size=int(leavers.sum()))
    term_date[leavers] = [cfg.start_date + timedelta(days=int(o)) for o in leaver_offsets]

    propensity = np.array(
        [
            beta_around(
                rng,
                cfg.planted.attendance_rate_by_region[r],
                cfg.planted.propensity_concentration,
                1,
            )[0]
            for r in region
        ]
    )
    # O2 cohort: chronically absent over and above ordinary variation. These
    # are the people the unreported-absence rule has to find, and they are the
    # reason O1 and O2 are different buckets rather than one.
    chronic = rng.random(n) < cfg.planted.chronic_no_show_share
    propensity = np.where(chronic, cfg.planted.chronic_no_show_attendance_rate, propensity)

    employees = pd.DataFrame(
        {
            "emp_id": [f"E{200000 + i}" for i in range(n)],
            "employee_type": choice_from_mix(rng, cfg.org.employee_type_mix, n),
            "dept_l1": team_df["dept_l1"],
            "dept_l2": team_df["dept_l2"],
            "dept_l3": team_df["dept_l3"],
            "dept_l4": team_df["dept_l4"],
            "workplace_code": workplace,
            "city": [code_city[c] for c in workplace],
            "region": region,
            "floor": floor,
            "hire_date": hire_date,
            "term_date": term_date,
            "attendance_propensity": propensity,
            "is_chronic_no_show": chronic,
            # P8: a minority work a shifted weekday pattern, so their absence
            # on a nominal office day is planned rather than optimizable.
            "has_shift_offset": rng.random(n) < cfg.planted.shift_offset_share,
            "is_free_sharing": rng.random(n) < cfg.estate.free_sharing_share,
        }
    )
    # The team's policy pattern, one per L4 team so colleagues share a schedule.
    team_pattern = {
        team["dept_l4"]: _scheduled_weekdays(
            cfg, rng, cfg.planted.scheduled_days_per_week[team["dept_l1"]]
        )
        for team in teams
    }
    employees["team_scheduled_weekdays"] = [team_pattern[team] for team in employees["dept_l4"]]

    # Both patterns are kept, and that is what makes P8 a reachable state. A
    # shift-offset employee is absent on a day their team is expected in, for a
    # planned reason - which is a different thing from a no-show, and the only
    # way to tell them apart is to know both patterns.
    employees["scheduled_weekdays"] = [
        tuple(sorted((d + 2) % 5 for d in days)) if offset else days
        for days, offset in zip(
            employees["team_scheduled_weekdays"], employees["has_shift_offset"], strict=True
        )
    ]
    return employees, seating
