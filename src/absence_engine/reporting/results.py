"""The results table `make demo` prints.

One question: did the pipeline recover what the simulator planted? Everything
else is context for that.
"""

from __future__ import annotations

import duckdb

from ..capacity.grading import grade, o2_calibration
from ..capacity.model import bootstrap_recovery_ci, compute_recovery
from ..config import WAREHOUSE_PATH, Config, load_config

LAYERS = {
    "raw": [
        "estate_floor",
        "hr_roster",
        "badge_taps",
        "leave_requests",
        "travel_bookings",
        "assignments",
        "public_holidays",
        "desk_allocation",
        "desk_bookings",
        "team_seating",
    ],
    "main_staging": [
        "stg_estate_floor",
        "stg_hr_roster",
        "stg_hr_employee",
        "stg_badge_taps",
        "stg_leave_requests",
        "stg_travel_bookings",
        "stg_assignments",
        "stg_public_holidays",
        "stg_desk_allocation",
        "stg_desk_bookings",
    ],
    "main_intermediate": [
        "int_date_spine",
        "int_calendar",
        "int_leave_days",
        "int_travel_days",
        "int_assignment_days",
        "int_tap_day",
        "int_employee_pattern",
        "int_employee_day_base",
        "int_employee_day",
    ],
    "main_marts": [
        "dim_workplace",
        "dim_employee",
        "fct_absence_segmentation_daily",
        "fct_allocation_unit_daily",
    ],
}


def render(cfg: Config) -> str:
    out: list[str] = []
    rule = "=" * 80
    out += [
        rule,
        f"absence-segmentation-engine  |  profile: {cfg.profile_name}  |  seed: {cfg.seed}",
        f"window: {cfg.start_date} to {cfg.end_date}",
        rule,
    ]

    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        out.append("\nROW COUNTS BY LAYER")
        for schema, tables in LAYERS.items():
            total = 0
            lines = []
            for table in tables:
                row = con.execute(f"select count(*) from {schema}.{table}").fetchone()
                count = int(row[0]) if row else 0
                total += count
                lines.append(f"    {table:32s} {count:>12,}")
            out.append(f"  {schema}  ({total:,} rows)")
            out.extend(lines)

        out.append("\nSEGMENTATION WATERFALL - every employee-day, by class")
        frame = con.execute(
            """
            select label_class, label, sum(employee_days) as employee_days
            from main_marts.fct_absence_segmentation_daily
            group by 1, 2 order by 1, 2
            """
        ).df()
    finally:
        con.close()

    grand = frame["employee_days"].sum()
    for label_class, group in frame.groupby("label_class"):
        subtotal = group["employee_days"].sum()
        out.append(f"  {label_class:14s} {subtotal:>10,}  ({subtotal / grand:6.2%})")
        for row in group.itertuples(index=False):
            out.append(
                f"      {row.label:4s} {int(row.employee_days):>10,}  "
                f"({row.employee_days / grand:6.2%})"
            )

    result = compute_recovery(cfg)
    point, low, high = bootstrap_recovery_ci(result.units)

    out.append("\nRECOVERABLE CAPACITY")
    out.append(f"  allocated workstations        {result.totals['allocated_workstations']:>12,}")
    out.append(
        f"  required at P{int(cfg.capacity.demand_percentile * 100)} "
        f"+ {cfg.capacity.buffer:.0%} buffer  {result.totals['required_workstations']:>12,}"
    )
    out.append(f"  recoverable                   {result.totals['recoverable_workstations']:>12,}")
    out.append(
        f"  recovery rate                 {point:>11.2%}  "
        f"(95% CI {low:.2%} to {high:.2%}, bootstrapped over allocation units)"
    )
    out.append(f"  estimated monthly saving      {result.totals['monthly_saving']:>12,.0f}")
    out.append(f"  under-sized units flagged     {result.totals['undersized_units']:>12,}")

    scored = grade(cfg)
    out.append("\nPLANTED VS RECOVERED - recoverable workstations")
    out.append(
        f"  {'estimator':12s} {'planted':>9s} {'recovered':>10s} {'error':>8s} "
        f"{'planted saving':>16s} {'recovered saving':>18s}"
    )
    for row in scored.recovery.itertuples(index=False):
        out.append(
            f"  {row.estimator:12s} {int(row.planted_workstations):>9,} "
            f"{int(row.recovered_workstations):>10,} {row.error:>+8.1%} "
            f"{row.planted_monthly_saving:>16,.0f} {row.recovered_monthly_saving:>18,.0f}"
        )

    out.append("\nSEGMENTATION ACCURACY vs planted cause")
    out.append(f"  label accuracy                          {scored.overall_accuracy:>8.2%}")
    out.append(f"  planned/optimizable class accuracy      {scored.class_accuracy:>8.2%}")
    weak = scored.per_label[scored.per_label["recall"] < 0.9].index.tolist()
    if weak:
        out.append(f"  labels below 0.90 recall: {', '.join(weak)}")
        for label in weak:
            row = scored.per_label.loc[label]
            out.append(
                f"      {label:4s} recall {row['recall']:.3f}  precision {row['precision']:.3f}"
            )

    calibration = o2_calibration(cfg)
    best = calibration.loc[calibration["f1"].idxmax()]
    out.append(
        f"\nO2 THRESHOLD  configured {cfg.taxonomy.unreported_absence_baseline_multiple}, "
        f"best F1 at {best['baseline_multiple']} "
        f"(precision {best['precision']:.2f}, recall {best['recall']:.2f})"
    )

    out.append("\nTOP CITIES BY OPPORTUNITY")
    for row in result.by_city.head(6).itertuples(index=False):
        out.append(
            f"  {row.city:14s} {int(row.recoverable_workstations):>6,} desks  "
            f"{row.monthly_saving:>12,.0f} / month  ({row.recovery_rate:.1%})"
        )

    out.append(rule)
    return "\n".join(out)


def main() -> int:
    cfg = load_config()
    if not WAREHOUSE_PATH.exists():
        print("No warehouse found. Run `make pipeline` first.")
        return 1
    print(render(cfg))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
