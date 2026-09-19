"""Dagster asset graph, partitioned daily where partitioning earns its keep.

# NOTE: no `from __future__ import annotations` in this module. Dagster
# resolves the context parameter's type at decoration time and postponed
# annotations turn it into a string it cannot match.

The assets are thin wrappers over functions that already work standalone, so
the pipeline can be run from the Makefile or a REPL with Dagster absent.

Only one asset is partitioned, and deliberately so. Generation, loading and
dbt all operate on the whole window and partitioning them would be decoration.
The daily segmentation extract is genuinely per-day: it is the thing a
downstream consumer subscribes to, the thing that gets backfilled when a rule
changes, and the thing whose idempotency is worth a test.
"""

import hashlib
import json
import subprocess
from datetime import date, datetime

import duckdb
import pandas as pd
from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    AssetExecutionContext,
    AssetSelection,
    DailyPartitionsDefinition,
    Definitions,
    MetadataValue,
    Output,
    RunRequest,
    ScheduleDefinition,
    SensorEvaluationContext,
    SensorResult,
    SkipReason,
    asset,
    asset_check,
    define_asset_job,
    sensor,
)

from ..capacity.grading import grade
from ..capacity.model import compute_recovery
from ..config import DBT_DIR, OUT_DIR, WAREHOUSE_PATH, load_config
from ..gen.run import generate_all
from ..reporting.charts import render_all
from ..reporting.reports import generate_weekly_reports
from ..warehouse.loader import load_raw

_cfg = load_config()
daily_partitions = DailyPartitionsDefinition(
    start_date=_cfg.start_date.isoformat(),
    end_date=(_cfg.end_date).isoformat(),
)

SEGMENTATION_DIR = OUT_DIR / "segmentation"


@asset(
    group_name="sources",
    compute_kind="python",
    description="Ten synthetic source extracts plus the truth set.",
)
def source_data(context: AssetExecutionContext) -> Output[dict]:
    cfg = load_config()
    result = generate_all(cfg)
    counts = result["row_counts"]
    truth = result["ground_truth"]["allocation"]
    return Output(
        result,
        metadata={
            "profile": cfg.profile_name,
            "rows_total": MetadataValue.int(sum(counts.values())),
            "row_counts": MetadataValue.json(counts),
            "planted_excess_workstations": MetadataValue.int(truth["excess_workstations"]),
        },
    )


@asset(
    group_name="warehouse",
    deps=[source_data],
    compute_kind="duckdb",
    description="Raw extracts loaded into DuckDB under Pandera contracts.",
)
def warehouse_raw(context: AssetExecutionContext) -> Output[dict]:
    results = load_raw()
    rows = {r.table: r.rows for r in results}
    return Output(rows, metadata={"rows": MetadataValue.json(rows)})


@asset(
    group_name="warehouse",
    deps=[warehouse_raw],
    compute_kind="dbt",
    description="dbt staging, intermediate and mart models, with their tests.",
)
def dbt_models(context: AssetExecutionContext) -> Output[dict]:
    proc = subprocess.run(
        ["dbt", "build", "--profiles-dir", "."],
        cwd=DBT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    tail = "\n".join(proc.stdout.strip().splitlines()[-20:])
    if proc.returncode != 0:
        context.log.error(tail)
        raise RuntimeError(f"dbt build failed with exit code {proc.returncode}")
    return Output({"returncode": 0}, metadata={"dbt_output": MetadataValue.md(f"```\n{tail}\n```")})


@asset(
    group_name="segmentation",
    deps=[dbt_models],
    partitions_def=daily_partitions,
    compute_kind="python",
    description="One day of labelled employee-days, written as a Parquet partition.",
)
def segmentation_daily(context: AssetExecutionContext) -> Output[dict]:
    """The per-day extract downstream consumers subscribe to.

    Writing the partition to a temporary file and moving it into place means a
    failed run leaves the previous partition intact rather than a half-written
    one. Re-materialising a partition overwrites it with identical bytes, which
    is what makes a backfill safe to re-run - asserted by
    tests/test_orchestration.py.
    """
    partition = context.partition_key
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        frame = con.execute(
            """
            select emp_id, local_date, dept_l1, dept_l2, dept_l3, dept_l4,
                   workplace_code, city, region, floor,
                   label, label_class, label_reason, consumed_desk, hours_present
            from main_intermediate.int_employee_day
            where local_date = cast(? as date)
            order by emp_id
            """,
            [partition],
        ).df()
    finally:
        con.close()

    folder = SEGMENTATION_DIR / f"dt={partition}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "part-000.parquet"
    staging = folder / "part-000.parquet.tmp"
    frame.to_parquet(staging, index=False)
    staging.replace(target)

    digest = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
    by_class = frame["label_class"].value_counts().to_dict() if not frame.empty else {}
    return Output(
        {"rows": len(frame), "sha256": digest},
        metadata={
            "partition": partition,
            "rows": MetadataValue.int(len(frame)),
            "by_class": MetadataValue.json({str(k): int(v) for k, v in by_class.items()}),
            "content_digest": MetadataValue.text(digest),
        },
    )


@asset(
    group_name="capacity",
    deps=[dbt_models],
    compute_kind="python",
    description="Recoverable capacity, savings, and the grade against planted truth.",
)
def recovery_model(context: AssetExecutionContext) -> Output[dict]:
    cfg = load_config()
    result = compute_recovery(cfg)
    scored = grade(cfg)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "recovery_by_city.csv").write_text(result.by_city.to_csv(index=False))
    (OUT_DIR / "grade.json").write_text(
        json.dumps(
            {
                "label_accuracy": scored.overall_accuracy,
                "class_accuracy": scored.class_accuracy,
                "recovery": scored.recovery.to_dict(orient="records"),
            },
            indent=2,
        )
    )
    return Output(
        {"totals": result.totals, "label_accuracy": scored.overall_accuracy},
        metadata={
            "recoverable_workstations": MetadataValue.int(
                result.totals["recoverable_workstations"]
            ),
            "monthly_saving": MetadataValue.float(result.totals["monthly_saving"]),
            "label_accuracy": MetadataValue.float(scored.overall_accuracy),
            "class_accuracy": MetadataValue.float(scored.class_accuracy),
            "recovery": MetadataValue.md(scored.recovery.to_markdown(index=False)),
        },
    )


@asset(
    group_name="reporting",
    deps=[recovery_model],
    compute_kind="python",
    description="Per-role weekly reports in HTML and PDF.",
)
def weekly_reports(context: AssetExecutionContext) -> Output[dict]:
    artifacts = generate_weekly_reports()
    return Output(
        {"count": len(artifacts)},
        metadata={
            "reports": MetadataValue.int(len(artifacts)),
            "paths": MetadataValue.json([str(a.html_path) for a in artifacts]),
        },
    )


@asset(
    group_name="reporting",
    deps=[recovery_model],
    compute_kind="python",
    description="README charts.",
)
def charts(context: AssetExecutionContext) -> Output[dict]:
    paths = render_all()
    return Output(
        {"count": len(paths)}, metadata={"charts": MetadataValue.json([str(p) for p in paths])}
    )


# --- Asset checks ----------------------------------------------------------


def _scalar(sql: str) -> float:
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        row = con.execute(sql).fetchone()
        return float((row[0] if row else 0) or 0)
    finally:
        con.close()


@asset_check(asset=dbt_models, name="every_employee_day_has_exactly_one_label", blocking=True)
def check_label_coverage() -> AssetCheckResult:
    total = _scalar("select count(*) from main_intermediate.int_employee_day")
    labelled = _scalar(
        "select count(*) from main_intermediate.int_employee_day where label is not null"
    )
    return AssetCheckResult(
        passed=total > 0 and total == labelled,
        severity=AssetCheckSeverity.ERROR,
        metadata={
            "employee_days": MetadataValue.int(int(total)),
            "labelled": MetadataValue.int(int(labelled)),
            "why": MetadataValue.text(
                "An unlabelled employee-day is silently excluded from both the planned and the "
                "optimizable bucket, so the split adds up and is still wrong."
            ),
        },
    )


@asset_check(asset=dbt_models, name="allocation_units_join_to_demand", blocking=True)
def check_allocation_join() -> AssetCheckResult:
    orphans = _scalar(
        """
        select count(*) from main_staging.stg_desk_allocation a
        left join (
            select distinct dept_l4, workplace_code, floor
            from main_marts.fct_allocation_unit_daily
        ) d on a.dept_l4 = d.dept_l4 and a.workplace_code = d.workplace_code
           and a.floor = d.floor
        where d.dept_l4 is null
        """
    )
    return AssetCheckResult(
        passed=orphans == 0,
        severity=AssetCheckSeverity.ERROR,
        metadata={
            "orphan_units": MetadataValue.int(int(orphans)),
            "why": MetadataValue.text(
                "An allocation unit with no demand rows holds desks that never enter the "
                "model, so its capacity is invisible and can never be recovered."
            ),
        },
    )


@asset_check(asset=recovery_model, name="recovery_within_tolerance_of_plant", blocking=False)
def check_recovery_accuracy() -> AssetCheckResult:
    """Non-blocking on purpose.

    This compares against the simulator's truth, which a production deployment
    would not have. It is a quality signal for this repository, not a gate a
    real pipeline could ever run - so it reports and does not block.
    """
    scored = grade(load_config())
    parametric = scored.recovery[scored.recovery["estimator"] == "parametric"].iloc[0]
    error = abs(float(parametric["error"]))
    return AssetCheckResult(
        passed=error <= 0.10,
        severity=AssetCheckSeverity.WARN,
        metadata={
            "absolute_error": MetadataValue.float(round(error, 4)),
            "planted": MetadataValue.int(int(parametric["planted_workstations"])),
            "recovered": MetadataValue.int(int(parametric["recovered_workstations"])),
        },
    )


# --- Jobs, schedules, sensors ----------------------------------------------

full_refresh_job = define_asset_job(
    name="full_refresh",
    selection=AssetSelection.all() - AssetSelection.assets(segmentation_daily),
    description="Generate, load, build, segment, score and report.",
)

segmentation_backfill_job = define_asset_job(
    name="segmentation_backfill",
    selection=AssetSelection.assets(segmentation_daily),
    partitions_def=daily_partitions,
    description="Re-materialise daily segmentation partitions. Safe to re-run.",
)

weekly_report_job = define_asset_job(
    name="weekly_reports",
    selection=AssetSelection.assets(recovery_model, weekly_reports, charts),
    description="Rebuild the capacity model and render every role's report.",
)

daily_schedule = ScheduleDefinition(
    name="daily_refresh",
    job=full_refresh_job,
    cron_schedule="30 5 * * *",
    execution_timezone="UTC",
)

weekly_schedule = ScheduleDefinition(
    name="weekly_reporting",
    job=weekly_report_job,
    # Monday 07:00: after the weekend has landed, before the week's first
    # space-planning conversation.
    cron_schedule="0 7 * * 1",
    execution_timezone="UTC",
)


def alert(channel: str, message: str) -> dict:
    """Alert sink stub.

    An interface with no integration behind it, on purpose: wiring a real
    webhook into a portfolio repository means either a dead URL or a secret in
    a public repo. The shape is what matters - a channel, a message, and a
    return value a caller can assert on.
    """
    payload = {"channel": channel, "message": message, "sent_at": datetime.utcnow().isoformat()}
    print(f"[ALERT:{channel}] {message}")
    return payload


@sensor(job=full_refresh_job, minimum_interval_seconds=300)
def freshness_sensor(context: SensorEvaluationContext):
    """Trigger a refresh when the warehouse falls behind the source window.

    Compares the newest labelled day against the simulator's window end. In a
    real deployment the right-hand side is wall-clock; here it is the end of
    the generated window, so the sensor is testable rather than dependent on
    what day it happens to be.
    """
    if not WAREHOUSE_PATH.exists():
        return SkipReason("No warehouse yet; run the pipeline once first.")

    cfg = load_config()
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        row = con.execute(
            "select max(local_date) from main_intermediate.int_employee_day"
        ).fetchone()
    finally:
        con.close()

    latest = row[0] if row else None
    if latest is None:
        return SensorResult(run_requests=[RunRequest(run_key="bootstrap")])

    latest_date = latest if isinstance(latest, date) else pd.to_datetime(latest).date()
    lag_days = (cfg.end_date - latest_date).days
    if lag_days <= 0:
        return SkipReason(f"Warehouse is current to {latest_date}.")

    alert("workplace-data", f"Segmentation is {lag_days} day(s) behind {cfg.end_date}.")
    return SensorResult(run_requests=[RunRequest(run_key=f"stale-{latest_date}")])


defs = Definitions(
    assets=[
        source_data,
        warehouse_raw,
        dbt_models,
        segmentation_daily,
        recovery_model,
        weekly_reports,
        charts,
    ],
    asset_checks=[check_label_coverage, check_allocation_join, check_recovery_accuracy],
    jobs=[full_refresh_job, segmentation_backfill_job, weekly_report_job],
    schedules=[daily_schedule, weekly_schedule],
    sensors=[freshness_sensor],
)
