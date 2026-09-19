"""Orchestration behaviour that has to hold for a backfill to be safe."""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest

from absence_engine.config import WAREHOUSE_PATH
from absence_engine.orchestration.definitions import (
    SEGMENTATION_DIR,
    alert,
    defs,
    segmentation_daily,
)


@pytest.mark.skipif(not WAREHOUSE_PATH.exists(), reason="run `make pipeline` first")
def test_segmentation_partition_is_idempotent(cfg):
    """Re-materialising a partition must produce identical bytes.

    Without this a backfill is a gamble: re-running yesterday could change
    yesterday's numbers, and nobody could tell whether a difference came from
    a rule change or from the rerun itself.
    """
    from dagster import materialize

    partition = (cfg.start_date + timedelta(days=3)).isoformat()
    target = SEGMENTATION_DIR / f"dt={partition}" / "part-000.parquet"

    materialize([segmentation_daily], partition_key=partition)
    assert target.exists()
    first = hashlib.sha256(target.read_bytes()).hexdigest()

    materialize([segmentation_daily], partition_key=partition)
    second = hashlib.sha256(target.read_bytes()).hexdigest()
    assert first == second


@pytest.mark.skipif(not WAREHOUSE_PATH.exists(), reason="run `make pipeline` first")
def test_partition_contains_only_its_own_day(cfg):
    import pandas as pd
    from dagster import materialize

    partition = (cfg.start_date + timedelta(days=4)).isoformat()
    materialize([segmentation_daily], partition_key=partition)
    frame = pd.read_parquet(SEGMENTATION_DIR / f"dt={partition}" / "part-000.parquet")
    assert not frame.empty
    assert set(frame["local_date"].astype(str)) == {partition}


def test_no_temporary_files_are_left_behind():
    leftovers = list(SEGMENTATION_DIR.glob("**/*.tmp")) if SEGMENTATION_DIR.exists() else []
    assert leftovers == []


def test_alert_stub_returns_a_payload():
    payload = alert("workplace-data", "test")
    assert payload["channel"] == "workplace-data"
    assert payload["message"] == "test"
    assert payload["sent_at"]


def test_definitions_expose_the_expected_surface():
    assert {j.name for j in defs.jobs} == {
        "full_refresh",
        "segmentation_backfill",
        "weekly_reports",
    }
    assert {s.name for s in defs.schedules} == {"daily_refresh", "weekly_reporting"}
    assert [s.name for s in defs.sensors] == ["freshness_sensor"]
    assert len(list(defs.asset_checks)) == 3
