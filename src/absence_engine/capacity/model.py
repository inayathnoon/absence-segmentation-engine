"""Recoverable capacity and what it is worth.

The chain is short and every step is a choice worth defending:

    demand        the desks a unit actually consumed, on days it could
    requirement   the Pth percentile of that demand, plus a buffer
    recoverable   allocated minus requirement, floored at zero
    saving        recoverable times the floor's cost per desk per month

Two decisions in that chain do most of the work.

**Sizing to a high percentile, not the mean.** An estate sized to mean demand
is short of desks on roughly half of all working days, which is not a rounding
error - it is a building people cannot sit in. The percentile is configurable
and the sensitivity sweep shows what it costs.

**Only days the office was open to the unit count.** Holidays, weekends and
non-office days carry zero attendance by construction. Leaving them in the
distribution drags the percentile toward zero and the model would report
capacity as recoverable that the unit needs every single working day. This is
the single easiest way to produce a large, confident and completely wrong
savings number.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist

import duckdb
import numpy as np
import pandas as pd

from ..config import WAREHOUSE_PATH, Config, load_config

# Two ways to turn a unit's demand history into a requirement.
#
# "empirical" takes the percentile of observed daily demand directly. It
# assumes nothing about the shape of the distribution, and it needs a lot of
# observations: a 90th percentile estimated from four Wednesdays is close to
# the maximum of four draws and is biased low, badly.
#
# "parametric" models daily demand as a sum of independent per-person
# attendance draws - a Poisson-binomial - and uses the normal approximation:
# mean plus z times the standard deviation. It buys stability at short horizons
# with an assumption that is reasonable for a team of more than a handful.
#
# Both are implemented and both are reported, because the gap between them is
# the honest measure of how much history a resizing decision needs.
ESTIMATORS = ("parametric", "empirical")


@dataclass
class RecoveryResult:
    units: pd.DataFrame
    by_city: pd.DataFrame
    totals: dict
    behavioural: pd.DataFrame


def _demand_frame(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Daily demand per allocation unit, restricted to eligible days."""
    return con.execute(
        """
        select
            u.dept_l4, u.workplace_code, u.floor, u.city, u.region,
            u.dept_l1, u.dept_l2, u.dept_l3,
            u.local_date, u.iso_year_week,
            c.weekday_mon0,
            u.allocated_workstations,
            u.cost_per_workstation_month,
            u.headcount,
            u.desks_consumed,
            u.o1_no_show, u.o2_unreported, u.o3_partial, u.o4_booked_not_used
        from main_marts.fct_allocation_unit_daily u
        inner join main_intermediate.int_calendar c on u.local_date = c.date_day
        where u.is_demand_eligible_day
        """
    ).df()


def _peak_weekday_requirement(
    demand: pd.DataFrame, keys: list[str], percentile: float, estimator: str
) -> pd.DataFrame:
    """Size each unit on its busiest weekday, not on all weekdays pooled.

    Demand is not stationary across the week: a three-day team with a Tuesday
    to Thursday pattern has near-zero demand on Monday and Friday, and pooling
    those days into one distribution drags the percentile down. Sizing on the
    pooled distribution produces a building that works on average and is short
    of desks every Wednesday - which is exactly the failure the percentile was
    supposed to prevent.
    """
    by_weekday = demand.groupby([*keys, "weekday_mon0"], as_index=False).agg(
        weekday_mean=("desks_consumed", "mean"),
        weekday_sd=("desks_consumed", "std"),
        weekday_n=("desks_consumed", "size"),
    )
    if estimator == "empirical":
        quantile = (
            demand.groupby([*keys, "weekday_mon0"])["desks_consumed"]
            .quantile(percentile)
            .rename("weekday_quantile")
            .reset_index()
        )
        by_weekday = by_weekday.merge(quantile, on=[*keys, "weekday_mon0"], how="left")

    peak = by_weekday.sort_values("weekday_mean", ascending=False).drop_duplicates(keys)
    peak["weekday_sd"] = peak["weekday_sd"].fillna(0.0)

    z = NormalDist().inv_cdf(percentile)
    if estimator == "parametric":
        peak["percentile_demand"] = peak["weekday_mean"] + z * peak["weekday_sd"]
    else:
        peak["percentile_demand"] = peak["weekday_quantile"]
    return peak[[*keys, "weekday_mon0", "weekday_mean", "weekday_sd", "weekday_n", "percentile_demand"]].rename(
        columns={"weekday_mon0": "peak_weekday"}
    )


def compute_recovery(
    cfg: Config | None = None,
    percentile: float | None = None,
    buffer: float | None = None,
    estimator: str = "parametric",
) -> RecoveryResult:
    cfg = cfg or load_config()
    if estimator not in ESTIMATORS:
        raise ValueError(f"estimator must be one of {ESTIMATORS}, got {estimator!r}")
    percentile = cfg.capacity.demand_percentile if percentile is None else percentile
    buffer = cfg.capacity.buffer if buffer is None else buffer

    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        demand = _demand_frame(con)
    finally:
        con.close()

    keys = ["dept_l4", "workplace_code", "floor"]
    grouped = demand.groupby(keys, as_index=False)

    units = grouped.agg(
        city=("city", "first"),
        region=("region", "first"),
        dept_l1=("dept_l1", "first"),
        dept_l2=("dept_l2", "first"),
        dept_l3=("dept_l3", "first"),
        allocated_workstations=("allocated_workstations", "max"),
        cost_per_workstation_month=("cost_per_workstation_month", "max"),
        headcount=("headcount", "max"),
        eligible_days=("local_date", "nunique"),
        weeks_observed=("iso_year_week", "nunique"),
        mean_demand=("desks_consumed", "mean"),
        peak_demand=("desks_consumed", "max"),
        o1_no_show=("o1_no_show", "sum"),
        o2_unreported=("o2_unreported", "sum"),
        o3_partial=("o3_partial", "sum"),
        o4_booked_not_used=("o4_booked_not_used", "sum"),
    )
    units = units.merge(
        _peak_weekday_requirement(demand, keys, percentile, estimator), on=keys, how="left"
    )

    units["required_workstations"] = np.ceil(
        units["percentile_demand"] * (1 + buffer)
    ).clip(lower=1).astype(int)
    units["recoverable_workstations"] = (
        units["allocated_workstations"] - units["required_workstations"]
    ).clip(lower=0)

    # A unit with too little history is not resized. Reporting a saving from
    # two days of evidence is how a sizing exercise loses its licence.
    thin = units["weeks_observed"] < cfg.capacity.min_weeks_evidence
    units.loc[thin, "recoverable_workstations"] = 0
    units["has_sufficient_evidence"] = ~thin

    units["monthly_saving"] = (
        units["recoverable_workstations"] * units["cost_per_workstation_month"]
    ).round(2)
    units["recovery_rate"] = (
        units["recoverable_workstations"] / units["allocated_workstations"]
    ).round(4)
    units["is_undersized"] = units["required_workstations"] > units["allocated_workstations"]

    by_city = (
        units.groupby("city", as_index=False)
        .agg(
            units=("dept_l4", "count"),
            allocated_workstations=("allocated_workstations", "sum"),
            required_workstations=("required_workstations", "sum"),
            recoverable_workstations=("recoverable_workstations", "sum"),
            monthly_saving=("monthly_saving", "sum"),
            undersized_units=("is_undersized", "sum"),
        )
        .sort_values("recoverable_workstations", ascending=False)
    )
    by_city["recovery_rate"] = (
        by_city["recoverable_workstations"] / by_city["allocated_workstations"]
    ).round(4)

    totals = {
        "percentile": percentile,
        "buffer": buffer,
        "estimator": estimator,
        "units": int(len(units)),
        "units_with_evidence": int(units["has_sufficient_evidence"].sum()),
        "allocated_workstations": int(units["allocated_workstations"].sum()),
        "required_workstations": int(units["required_workstations"].sum()),
        "recoverable_workstations": int(units["recoverable_workstations"].sum()),
        "recovery_rate": round(
            float(units["recoverable_workstations"].sum() / units["allocated_workstations"].sum()),
            4,
        ),
        "monthly_saving": round(float(units["monthly_saving"].sum()), 2),
        "undersized_units": int(units["is_undersized"].sum()),
    }
    return RecoveryResult(
        units=units,
        by_city=by_city,
        totals=totals,
        behavioural=behavioural_opportunity(units, cfg),
    )


def behavioural_opportunity(units: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """The O1 and O4 lever, reported separately and NOT added to capacity.

    The brief this repo is built from asks for a policy fraction of O1 and O4
    to be added to the recoverable number. It should not be, and this is the
    one place the implementation departs from the specification on purpose.

    A no-show consumes no desk. Their absence is therefore already inside the
    demand distribution the percentile is taken over, and the desk they did not
    use has already been counted as recoverable once. Adding a fraction of it
    again double counts, and does so in the flattering direction.

    O1 and O4 are still worth reporting - they are a genuine lever, just a
    different one. Converting a chronic no-show from an allocated desk to the
    sharing pool changes *who holds* a desk rather than how many exist, and the
    saving it produces is realised only if the behaviour actually changes. So
    it is reported as an opportunity, with the policy fraction visible, and the
    headline capacity number does not include it.
    """
    frame = units.copy()
    days = frame["eligible_days"].replace(0, np.nan)
    frame["o1_desk_days_per_day"] = frame["o1_no_show"] / days
    frame["o4_desk_days_per_day"] = frame["o4_booked_not_used"] / days
    frame["behavioural_desks"] = (
        cfg.capacity.o1_recoverable_fraction * frame["o1_desk_days_per_day"]
        + cfg.capacity.o4_recoverable_fraction * frame["o4_desk_days_per_day"]
    ).fillna(0.0)
    frame["behavioural_monthly_value"] = (
        frame["behavioural_desks"] * frame["cost_per_workstation_month"]
    ).round(2)
    return frame[
        [
            "dept_l4",
            "workplace_code",
            "floor",
            "city",
            "o1_no_show",
            "o4_booked_not_used",
            "behavioural_desks",
            "behavioural_monthly_value",
        ]
    ]


def sensitivity_sweep(cfg: Config | None = None, estimator: str = "parametric") -> pd.DataFrame:
    """Recovery rate and saving across the percentile and buffer grid.

    This is the chart that shows the answer is a choice, not a measurement. The
    same estate yields very different savings depending on how much peak-day
    risk the organisation is willing to carry, and anyone presenting a single
    number without this sweep behind it is presenting an opinion.
    """
    cfg = cfg or load_config()
    rows = []
    for percentile in cfg.capacity.sensitivity["percentiles"]:
        for buffer in cfg.capacity.sensitivity["buffers"]:
            result = compute_recovery(
                cfg, percentile=percentile, buffer=buffer, estimator=estimator
            )
            rows.append(
                {
                    "percentile": percentile,
                    "buffer": buffer,
                    "recoverable_workstations": result.totals["recoverable_workstations"],
                    "recovery_rate": result.totals["recovery_rate"],
                    "monthly_saving": result.totals["monthly_saving"],
                    "undersized_units": result.totals["undersized_units"],
                }
            )
    return pd.DataFrame(rows)


if __name__ == "__main__":  # pragma: no cover
    for name in ESTIMATORS:
        result = compute_recovery(estimator=name)
        print(name, result.totals)
