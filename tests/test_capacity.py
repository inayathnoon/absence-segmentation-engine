"""The capacity model, and the ways it can be quietly wrong."""

from __future__ import annotations

import pytest

from absence_engine.capacity.model import (
    ESTIMATORS,
    bootstrap_recovery_ci,
    compute_recovery,
    sensitivity_sweep,
)


@pytest.fixture(scope="module")
def result(cfg):
    return compute_recovery(cfg)


def test_recoverable_never_exceeds_allocated(result):
    assert (
        result.units["recoverable_workstations"] <= result.units["allocated_workstations"]
    ).all()


def test_recoverable_is_floored_at_zero_per_unit(result):
    """Floored per unit, not on the total: an over-sized team must not be able
    to cancel out an under-sized one."""
    assert (result.units["recoverable_workstations"] >= 0).all()


def test_undersized_units_recover_nothing(result):
    undersized = result.units[result.units["is_undersized"]]
    assert not undersized.empty, "no under-sized units; the floor is untested"
    assert (undersized["recoverable_workstations"] == 0).all()


def test_units_without_enough_history_are_not_resized(cfg):
    strict = compute_recovery(cfg)
    thin = strict.units[~strict.units["has_sufficient_evidence"]]
    assert (thin["recoverable_workstations"] == 0).all()


def test_higher_percentile_recovers_less(cfg):
    """Monotonicity. If this breaks, the sizing logic has an inversion in it
    and every sensitivity conclusion is unsafe."""
    low = compute_recovery(cfg, percentile=0.75).totals["recoverable_workstations"]
    high = compute_recovery(cfg, percentile=0.99).totals["recoverable_workstations"]
    assert high < low


def test_larger_buffer_recovers_less(cfg):
    none = compute_recovery(cfg, buffer=0.0).totals["recoverable_workstations"]
    wide = compute_recovery(cfg, buffer=0.20).totals["recoverable_workstations"]
    assert wide < none


def test_sensitivity_sweep_covers_the_configured_point(cfg):
    sweep = sensitivity_sweep(cfg)
    match = sweep[
        (sweep["percentile"] == cfg.capacity.demand_percentile)
        & (sweep["buffer"] == cfg.capacity.buffer)
    ]
    assert len(match) == 1


def test_bootstrap_interval_contains_the_point_estimate(result):
    point, low, high = bootstrap_recovery_ci(result.units, iterations=500)
    assert low <= point <= high
    assert high > low


def test_behavioural_opportunity_is_not_added_to_capacity(result, cfg):
    """The deliberate departure from the brief. A no-show consumes no desk, so
    their absence is already inside the demand distribution; adding a fraction
    of it again double counts in the flattering direction."""
    assert "behavioural_desks" not in result.units.columns
    assert result.behavioural["behavioural_desks"].sum() > 0


def test_both_estimators_are_available(cfg):
    for estimator in ESTIMATORS:
        assert compute_recovery(cfg, estimator=estimator).totals["estimator"] == estimator


def test_unknown_estimator_is_rejected(cfg):
    with pytest.raises(ValueError, match="estimator must be one of"):
        compute_recovery(cfg, estimator="vibes")
