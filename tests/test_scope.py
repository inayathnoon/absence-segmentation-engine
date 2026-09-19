"""Role scoping. The tests here are about what does NOT come back."""

from __future__ import annotations

import pandas as pd
import pytest

from absence_engine.reporting.scope import SCOPES, Role, ScopeError, apply_scope


@pytest.fixture
def frame():
    return pd.DataFrame(
        {
            "city": ["Corvane", "Corvane", "Fennmark"],
            "region": ["EMEA", "EMEA", "APAC"],
            "dept_l1": ["Engineering", "Product", "Engineering"],
            "dept_l4": ["A", "B", "C"],
            "workplace_code": ["WP001", "WP001", "WP002"],
            "floor": [1, 2, 3],
            "headcount": [10, 20, 30],
            "allocated_workstations": [10, 20, 30],
            "required_workstations": [8, 18, 28],
            "recoverable_workstations": [2, 2, 2],
            "monthly_saving": [100.0, 200.0, 300.0],
            "recovery_rate": [0.2, 0.1, 0.07],
            "attendance_propensity": [0.6, 0.7, 0.8],
        }
    )


def test_scoped_role_filters_rows(frame):
    scoped = apply_scope(frame, Role.DEPARTMENT_LEAD, "Engineering")
    assert set(scoped["dept_l1"]) == {"Engineering"}
    assert len(scoped) == 2


def test_columns_outside_the_remit_are_dropped_not_hidden(frame):
    """A column that is not returned cannot be exported. Hiding it in the UI
    is a layout choice that looks like access control."""
    scoped = apply_scope(frame, Role.EXECUTIVE, None)
    assert "attendance_propensity" not in scoped.columns
    assert "dept_l4" not in scoped.columns


def test_executive_never_sees_team_detail(frame):
    scoped = apply_scope(frame, Role.EXECUTIVE, None)
    assert not [c for c in scoped.columns if c.startswith("dept")]


def test_unscoped_request_for_a_scoped_role_is_refused(frame):
    with pytest.raises(ScopeError, match="must be scoped"):
        apply_scope(frame, Role.REGIONAL_LEAD, None, strict=True)


def test_missing_scope_column_is_refused_rather_than_ignored(frame):
    """Silently returning everything because the filter column is absent is the
    worst possible failure mode for an access rule."""
    with pytest.raises(ScopeError, match="cannot be enforced"):
        apply_scope(frame.drop(columns=["region"]), Role.REGIONAL_LEAD, "EMEA", strict=True)


def test_every_role_declares_its_visible_columns():
    for role, spec in SCOPES.items():
        assert spec.visible_columns, role
        assert spec.question.endswith("?"), role
