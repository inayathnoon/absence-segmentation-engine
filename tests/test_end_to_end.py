"""One end-to-end test, asserting the claims the README makes.

Deliberately a single test: if this passes the repo does what it says, and if
it fails none of the narrower tests matter.
"""

from __future__ import annotations

import json

import pytest

from absence_engine.capacity.grading import grade, o2_calibration
from absence_engine.capacity.model import compute_recovery
from absence_engine.config import GROUND_TRUTH, WAREHOUSE_PATH


@pytest.mark.skipif(not WAREHOUSE_PATH.exists(), reason="run `make pipeline` first")
def test_pipeline_recovers_what_the_simulator_planted(cfg):
    scored = grade(cfg)

    # 1. Every employee-day is labelled, and the split that drives the
    #    decision is right almost always.
    assert scored.class_accuracy >= 0.98
    assert scored.overall_accuracy >= 0.95

    # 2. The causes with unambiguous evidence recover exactly.
    for label in ("P1", "P2", "P3", "P4", "P6", "P7", "O3", "O4"):
        assert scored.per_label.loc[label, "recall"] >= 0.99, label

    # 3. Recoverable capacity is within the 10% the brief asks for, using the
    #    parametric estimator.
    parametric = scored.recovery[scored.recovery["estimator"] == "parametric"].iloc[0]
    assert abs(float(parametric["error"])) <= 0.10

    # 4. The empirical estimator is meaningfully worse at this horizon. If it
    #    ever stops being worse, the claim in the README is stale.
    empirical = scored.recovery[scored.recovery["estimator"] == "empirical"].iloc[0]
    assert abs(float(empirical["error"])) > abs(float(parametric["error"]))

    # 5. The configured O2 threshold is the one the sweep picks.
    calibration = o2_calibration(cfg)
    best = calibration.loc[calibration["f1"].idxmax(), "baseline_multiple"]
    assert best == pytest.approx(cfg.taxonomy.unreported_absence_baseline_multiple)

    # 6. The savings number is consistent with the desks it came from.
    result = compute_recovery(cfg)
    truth = json.loads(GROUND_TRUTH.read_text())["allocation"]
    assert result.totals["monthly_saving"] > 0
    assert truth["excess_workstations"] > 0
    implied = result.totals["monthly_saving"] / max(result.totals["recoverable_workstations"], 1)
    assert 100 < implied < 5000, "implied cost per desk is outside any plausible range"
