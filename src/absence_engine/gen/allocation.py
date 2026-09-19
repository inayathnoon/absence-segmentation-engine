"""Desk allocation per (team, floor), and the excess planted on top of it.

This module is where the recoverable capacity comes from. Each allocation unit
is sized correctly first, then a planted excess is added on top from one of
three causes. The pipeline never sees the excess; it has to recover it from
badge evidence alone.

WHAT "SIZED CORRECTLY" MEANS, AND WHY IT CHANGED
------------------------------------------------
The first version sized each unit analytically, from its members' attendance
propensities: mean plus z times the standard deviation on the busiest weekday.
That is a clean definition and it was wrong for this purpose, because it
assumes every member is available every day. In reality eight to ten percent
of them are on leave, travelling, posted elsewhere or not yet employed, so the
requirement it produced sat permanently above anything demand evidence could
support. The pipeline dutifully reported the difference as recoverable, and
over-recovered by 40%.

So the requirement is now derived from the demand the simulator actually
realised: the mean and standard deviation of desks consumed on the unit's
busiest weekday, over days the unit's office was open to it. That is the same
quantity the pipeline estimates from badge taps, which is the point - the two
are measuring the same thing, and any gap between them is measurement error in
the pipeline rather than a difference of definition.

This does mean the parametric estimator shares a formula with the generator.
What is being tested is therefore the measurement chain - taps de-duplicated,
business-dated, joined, labelled, aggregated to desk-days - and not the
statistics. The empirical estimator, which shares nothing with the generator,
is reported alongside it for exactly that reason.
"""

from __future__ import annotations

from statistics import NormalDist

import numpy as np
import pandas as pd

from ..config import Config
from .rng import substream

EXCESS_CAUSES = {
    "o5_allocation_exceeds_schedule": "O5",
    "o6_structural_oversupply": "O6",
    "o7_stale_allocation": "O7",
}

UNIT_KEYS = ["dept_l4", "workplace_code", "floor"]


def peak_weekday_requirement(
    daily_demand: pd.DataFrame, percentile: float, buffer: float
) -> pd.DataFrame:
    """Requirement per unit, from realised demand on its busiest weekday."""
    z = NormalDist().inv_cdf(percentile)
    by_weekday = (
        daily_demand[daily_demand["is_eligible"]]
        .groupby([*UNIT_KEYS, "weekday"], as_index=False)
        .agg(
            weekday_mean=("desks_consumed", "mean"),
            weekday_sd=("desks_consumed", "std"),
            weekday_n=("desks_consumed", "size"),
        )
    )
    if by_weekday.empty:
        return pd.DataFrame(columns=[*UNIT_KEYS, "truth_required_workstations"])

    peak = by_weekday.sort_values("weekday_mean", ascending=False).drop_duplicates(UNIT_KEYS)
    peak["weekday_sd"] = peak["weekday_sd"].fillna(0.0)
    peak["truth_p90_demand"] = peak["weekday_mean"] + z * peak["weekday_sd"]
    peak["truth_required_workstations"] = (
        np.ceil(peak["truth_p90_demand"] * (1 + buffer)).clip(lower=1).astype(int)
    )
    return peak.rename(columns={"weekday": "truth_peak_weekday"})[
        [
            *UNIT_KEYS,
            "truth_peak_weekday",
            "weekday_mean",
            "weekday_sd",
            "weekday_n",
            "truth_p90_demand",
            "truth_required_workstations",
        ]
    ]


def generate_allocation(
    cfg: Config, employees: pd.DataFrame, estate: pd.DataFrame, daily_demand: pd.DataFrame
) -> pd.DataFrame:
    """One row per allocation unit, with its true requirement and its excess."""
    rng = substream(cfg.seed, "allocation")
    excess_cfg = cfg.planted.allocation_excess

    requirement = peak_weekday_requirement(
        daily_demand, cfg.capacity.demand_percentile, cfg.capacity.buffer
    )
    floor_costs = estate.set_index(["workplace_code", "floor"])[
        "cost_per_workstation_month"
    ].to_dict()

    members = (
        employees.groupby(UNIT_KEYS)
        .agg(
            headcount=("emp_id", "size"),
            dept_l3=("dept_l3", "first"),
            dept_l2=("dept_l2", "first"),
            dept_l1=("dept_l1", "first"),
            city=("city", "first"),
            region=("region", "first"),
        )
        .reset_index()
    )
    units = members.merge(requirement, on=UNIT_KEYS, how="left")
    # A unit nobody ever attended still holds desks. Requiring one desk rather
    # than zero keeps the allocation table meaningful; the excess above it is
    # the purest possible O7.
    units["truth_required_workstations"] = (
        units["truth_required_workstations"].fillna(1).astype(int)
    )

    causes, magnitudes = [], []
    for _ in range(len(units)):
        draw = rng.random()
        cause, magnitude, cumulative = "correctly_sized", 0.0, 0.0
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
        causes.append(cause)
        magnitudes.append(magnitude)

    units["truth_excess_cause"] = causes
    units["allocated_workstations"] = np.maximum(
        1, np.round(units["truth_required_workstations"] * (1 + np.array(magnitudes)))
    ).astype(int)
    units["truth_excess_workstations"] = (
        units["allocated_workstations"] - units["truth_required_workstations"]
    ).clip(lower=0)
    units["cost_per_workstation_month"] = [
        floor_costs.get((w, f), 500.0)
        for w, f in zip(units["workplace_code"], units["floor"], strict=True)
    ]
    return units


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
