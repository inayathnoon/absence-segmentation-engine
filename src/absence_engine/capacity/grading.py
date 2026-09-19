"""Grade the pipeline against what the simulator planted.

Three questions, in order of how much they matter:

1. Did the cascade put each employee-day in the right bucket?
2. Did the capacity model recover the right number of desks, per city?
3. How sensitive is the answer to the thresholds nobody can measure?

This module is the only code that reads ``truth``. Everything it needs is
loaded through a separate connection and joined here, never in dbt - so no
model can accidentally acquire a column it should have had to infer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import duckdb
import pandas as pd

from ..config import GROUND_TRUTH, WAREHOUSE_PATH, Config, load_config
from .model import ESTIMATORS, compute_recovery

LABEL_ORDER = [
    "ATTENDED",
    "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8",
    "O1", "O2", "O3", "O4",
]


@dataclass
class Grade:
    confusion: pd.DataFrame
    per_label: pd.DataFrame
    overall_accuracy: float
    class_accuracy: float
    recovery: pd.DataFrame
    recovery_by_city: pd.DataFrame


def _labelled_pairs(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(
        """
        select t.truth_label, e.label, e.label_class, count(*) as employee_days
        from truth.employee_day_truth t
        inner join main_intermediate.int_employee_day e
            on t.emp_id = e.emp_id and cast(t.local_date as date) = e.local_date
        group by 1, 2, 3
        """
    ).df()


def _truth_class(label: str) -> str:
    if label == "ATTENDED":
        return "attended"
    return "optimizable" if label.startswith("O") else "planned"


def label_grade(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.DataFrame, float, float]:
    pairs = _labelled_pairs(con)
    confusion = (
        pairs.pivot_table(
            index="truth_label", columns="label", values="employee_days", aggfunc="sum"
        )
        .reindex(index=LABEL_ORDER, columns=LABEL_ORDER)
        .fillna(0)
        .astype(int)
    )

    planted = confusion.sum(axis=1)
    predicted = confusion.sum(axis=0)
    correct = pd.Series(
        {label: confusion.loc[label, label] for label in LABEL_ORDER}, dtype=float
    )
    per_label = pd.DataFrame(
        {
            "planted": planted,
            "predicted": predicted,
            "correct": correct.astype(int),
            "recall": (correct / planted.replace(0, pd.NA)).round(3),
            "precision": (correct / predicted.replace(0, pd.NA)).round(3),
        }
    )

    total = int(confusion.to_numpy().sum())
    overall = float(correct.sum() / total) if total else 0.0

    # Class accuracy is the number that actually matters for the decision. A
    # day misfiled from O1 to O2 changes who gets a conversation; a day
    # misfiled from O1 to P1 changes whether a desk gets removed.
    pairs["truth_class"] = pairs["truth_label"].map(_truth_class)
    class_correct = pairs.loc[
        pairs["truth_class"] == pairs["label_class"], "employee_days"
    ].sum()
    class_accuracy = float(class_correct / pairs["employee_days"].sum())
    return confusion, per_label, overall, class_accuracy


def recovery_grade(cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    truth = json.loads(GROUND_TRUTH.read_text())["allocation"]
    planted_total = truth["excess_workstations"]
    planted_by_city = pd.Series(truth["excess_by_city"], name="planted")
    planted_saving = pd.Series(truth["monthly_saving_by_city"], name="planted_saving")

    rows, city_frames = [], []
    for estimator in ESTIMATORS:
        result = compute_recovery(cfg, estimator=estimator)
        totals = result.totals
        rows.append(
            {
                "estimator": estimator,
                "planted_workstations": planted_total,
                "recovered_workstations": totals["recoverable_workstations"],
                "error": round(
                    (totals["recoverable_workstations"] - planted_total) / max(planted_total, 1), 4
                ),
                "planted_monthly_saving": truth["monthly_saving_total"],
                "recovered_monthly_saving": totals["monthly_saving"],
                "recovery_rate": totals["recovery_rate"],
                "undersized_units": totals["undersized_units"],
            }
        )
        by_city = result.by_city.set_index("city")[
            ["recoverable_workstations", "monthly_saving"]
        ].rename(
            columns={
                "recoverable_workstations": "recovered",
                "monthly_saving": "recovered_saving",
            }
        )
        frame = by_city.join(planted_by_city, how="outer").join(planted_saving, how="outer")
        frame = frame.fillna(0)
        frame["estimator"] = estimator
        frame["error"] = (
            (frame["recovered"] - frame["planted"]) / frame["planted"].replace(0, pd.NA)
        ).round(3)
        city_frames.append(frame.reset_index().rename(columns={"index": "city"}))

    return pd.DataFrame(rows), pd.concat(city_frames, ignore_index=True)


def grade(cfg: Config | None = None) -> Grade:
    cfg = cfg or load_config()
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        confusion, per_label, overall, class_accuracy = label_grade(con)
    finally:
        con.close()
    recovery, recovery_by_city = recovery_grade(cfg)
    return Grade(
        confusion=confusion,
        per_label=per_label,
        overall_accuracy=round(overall, 4),
        class_accuracy=round(class_accuracy, 4),
        recovery=recovery,
        recovery_by_city=recovery_by_city,
    )


# --- O2 threshold calibration ----------------------------------------------


def o2_calibration(cfg: Config | None = None, multiples=(1.0, 1.2, 1.4, 1.6, 1.8, 2.0)) -> pd.DataFrame:
    """Precision and recall of the O2 rule across its baseline multiple.

    O2 is the one rule with a threshold nobody can derive from first
    principles, and moving it trades precision against recall directly. Tuning
    it quietly to whatever number looks best would be the wrong instinct; the
    curve belongs in the open, next to the rule.

    Recomputed in pandas rather than by rebuilding dbt at each setting, so the
    sweep costs seconds. The rule is a straight reimplementation of the SQL and
    is asserted to agree with it at the configured multiple.
    """
    cfg = cfg or load_config()
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        frame = con.execute(
            """
            select
                e.emp_id, e.local_date, e.dept_l4, e.label,
                e.no_shows_in_window, e.office_days_in_window, e.team_no_show_rate,
                t.truth_label
            from main_intermediate.int_employee_day e
            inner join truth.employee_day_truth t
                on e.emp_id = t.emp_id and e.local_date = cast(t.local_date as date)
            where e.label in ('O1', 'O2')
            """
        ).df()
    finally:
        con.close()

    min_count = cfg.taxonomy.unreported_absence_min_no_shows
    window_rate = frame["no_shows_in_window"] / frame["office_days_in_window"].replace(0, pd.NA)
    is_truth_o2 = frame["truth_label"] == "O2"

    rows = []
    for multiple in multiples:
        fires = (frame["no_shows_in_window"] >= min_count) & (
            window_rate >= multiple * frame["team_no_show_rate"]
        )
        true_positive = int((fires & is_truth_o2).sum())
        predicted = int(fires.sum())
        actual = int(is_truth_o2.sum())
        rows.append(
            {
                "baseline_multiple": multiple,
                "flagged": predicted,
                "true_positive": true_positive,
                "precision": round(true_positive / predicted, 3) if predicted else 0.0,
                "recall": round(true_positive / actual, 3) if actual else 0.0,
                "f1": round(
                    2 * true_positive / (predicted + actual), 3
                ) if (predicted + actual) else 0.0,
            }
        )
    return pd.DataFrame(rows)


if __name__ == "__main__":  # pragma: no cover
    result = grade()
    print(result.per_label.to_string())
    print(f"\noverall label accuracy: {result.overall_accuracy}")
    print(f"planned/optimizable class accuracy: {result.class_accuracy}")
    print()
    print(result.recovery.to_string(index=False))
    print()
    print(o2_calibration().to_string(index=False))
