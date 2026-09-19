"""Desk allocation per (team, floor), and the excess planted on top of it.

This module is where the recoverable capacity comes from. Each allocation unit
is sized correctly first - at the 90th percentile of its own peak-weekday
demand, plus a buffer - and then a planted excess is added on top, drawn from
one of three causes. The pipeline never sees the excess; it has to recover it
from attendance evidence alone.

Sizing the truth analytically rather than by simulating and measuring matters:
it makes the planted number independent of the very sample the pipeline will
measure, so recovering it is a real test rather than an identity.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from .rng import substream

# 90th percentile of the standard normal. Hardcoded rather than pulled from
# scipy: one constant is not worth a dependency, and writing it down makes the
# normal approximation to the Poisson-binomial explicit rather than hidden
# inside a library call.
Z_90 = 1.2815515655446004

EXCESS_CAUSES = {
    "o5_allocation_exceeds_schedule": "O5",
    "o6_structural_oversupply": "O6",
    "o7_stale_allocation": "O7",
}


def _peak_day_demand(members: pd.DataFrame) -> tuple[float, float, int]:
    """Analytic peak-weekday demand for one allocation unit.

    Daily attendance is a sum of independent Bernoulli draws with different
    probabilities - a Poisson-binomial. For anything but a tiny team the normal
    approximation is close enough, and it is transparent: mean plus z times the
    standard deviation, on whichever weekday has the highest mean.
    """
    best_mean, best_sd, best_day = 0.0, 0.0, 1
    for weekday in range(5):
        scheduled = members[
            members["scheduled_weekdays"].map(lambda days, w=weekday: w in days)
        ]
        if scheduled.empty:
            continue
        p = scheduled["attendance_propensity"].to_numpy()
        mean = float(p.sum())
        if mean > best_mean:
            best_mean, best_sd, best_day = mean, float(np.sqrt((p * (1 - p)).sum())), weekday
    return best_mean, best_sd, best_day


def generate_allocation(cfg: Config, employees: pd.DataFrame, estate: pd.DataFrame) -> pd.DataFrame:
    """One row per allocation unit, with its true requirement and its excess."""
    rng = substream(cfg.seed, "allocation")
    excess_cfg = cfg.planted.allocation_excess
    buffer = cfg.capacity.buffer

    floor_costs = estate.set_index(["workplace_code", "floor"])[
        "cost_per_workstation_month"
    ].to_dict()

    rows: list[dict] = []
    for (team, workplace, floor), members in employees.groupby(
        ["dept_l4", "workplace_code", "floor"], sort=True
    ):
        mean, sd, peak_weekday = _peak_day_demand(members)
        p90_demand = mean + Z_90 * sd
        required = int(max(1, np.ceil(p90_demand * (1 + buffer))))

        # Which kind of excess, if any, this unit carries.
        draw = rng.random()
        cause, magnitude = "correctly_sized", 0.0
        cumulative = 0.0
        for key, code in EXCESS_CAUSES.items():
            spec = excess_cfg[key]
            cumulative += spec["share"]
            if draw < cumulative:
                cause = code
                magnitude = float(
                    max(0.0, rng.normal(spec["magnitude"]["mean"], spec["magnitude"]["sigma"]))
                )
                break
        else:
            if draw < cumulative + excess_cfg["under_sized_share"]:
                cause = "under_sized"
                magnitude = -float(
                    max(
                        0.0,
                        rng.normal(
                            excess_cfg["under_sized_magnitude"]["mean"],
                            excess_cfg["under_sized_magnitude"]["sigma"],
                        ),
                    )
                )

        allocated = int(max(1, round(required * (1 + magnitude))))
        rows.append(
            {
                "dept_l4": team,
                "dept_l3": members["dept_l3"].iloc[0],
                "dept_l2": members["dept_l2"].iloc[0],
                "dept_l1": members["dept_l1"].iloc[0],
                "workplace_code": workplace,
                "city": members["city"].iloc[0],
                "region": members["region"].iloc[0],
                "floor": int(floor),
                "headcount": int(len(members)),
                "allocated_workstations": allocated,
                "cost_per_workstation_month": floor_costs.get((workplace, floor), 500.0),
                # --- truth, never published to data/raw ---
                "truth_required_workstations": required,
                "truth_p90_demand": round(p90_demand, 3),
                "truth_peak_weekday": peak_weekday,
                "truth_excess_cause": cause,
                "truth_excess_workstations": max(allocated - required, 0),
            }
        )
    return pd.DataFrame(rows)


PUBLISHED_COLUMNS = [
    "dept_l4",
    "dept_l3",
    "dept_l2",
    "dept_l1",
    "workplace_code",
    "city",
    "region",
    "floor",
    "allocated_workstations",
    "cost_per_workstation_month",
]
