"""Role definitions and the scoping that goes with them.

Scoping happens here, in the query layer, and not in the dashboard. A UI that
hides rows it has already fetched is not access control - it is a layout
choice that happens to look like one, and the first person to export the
underlying frame gets everything.

Each role also declares what it is allowed to *see*, not just which rows:
a department lead sees named teams because they manage them, while a regional
lead sees aggregates because individual attendance is not theirs to inspect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import duckdb
import pandas as pd

from ..config import WAREHOUSE_PATH


class Role(StrEnum):
    EXECUTIVE = "executive"
    REGIONAL_LEAD = "regional_lead"
    DEPARTMENT_LEAD = "department_lead"
    SPACE_PLANNER = "space_planner"


@dataclass(frozen=True)
class RoleScope:
    role: Role
    title: str
    question: str
    # Columns this role may see. Anything else is dropped before the frame
    # leaves this module.
    visible_columns: tuple[str, ...]
    # Column the scope value filters on, if any.
    scope_column: str | None = None
    # Lowest level of detail the role may drill to.
    max_detail: str = "city"
    metrics: tuple[str, ...] = field(default_factory=tuple)


SCOPES: dict[Role, RoleScope] = {
    Role.EXECUTIVE: RoleScope(
        role=Role.EXECUTIVE,
        title="Executive",
        question="How much capacity and money is on the table, and where?",
        visible_columns=(
            "city",
            "region",
            "allocated_workstations",
            "required_workstations",
            "recoverable_workstations",
            "monthly_saving",
            "recovery_rate",
        ),
        scope_column=None,
        max_detail="city",
        metrics=("recovery_rate", "monthly_saving", "recoverable_workstations"),
    ),
    Role.REGIONAL_LEAD: RoleScope(
        role=Role.REGIONAL_LEAD,
        title="Regional lead",
        question="Which of my cities are worst, and is it planned or avoidable?",
        visible_columns=(
            "city",
            "region",
            "workplace_code",
            "allocated_workstations",
            "required_workstations",
            "recoverable_workstations",
            "monthly_saving",
            "recovery_rate",
            "planned_share",
            "optimizable_share",
        ),
        scope_column="region",
        max_detail="workplace_code",
        metrics=("recovery_rate", "optimizable_share", "monthly_saving"),
    ),
    Role.DEPARTMENT_LEAD: RoleScope(
        role=Role.DEPARTMENT_LEAD,
        title="Department lead",
        question="How are my own teams using the desks they hold?",
        visible_columns=(
            "dept_l1",
            "dept_l2",
            "dept_l3",
            "dept_l4",
            "workplace_code",
            "floor",
            "headcount",
            "allocated_workstations",
            "required_workstations",
            "recoverable_workstations",
            "o1_no_show",
            "o2_unreported",
            "o3_partial",
            "o4_booked_not_used",
            "recovery_rate",
        ),
        scope_column="dept_l1",
        max_detail="dept_l4",
        metrics=("o1_no_show", "o2_unreported", "o4_booked_not_used"),
    ),
    Role.SPACE_PLANNER: RoleScope(
        role=Role.SPACE_PLANNER,
        title="Space planner",
        question="Which units should be resized, by how much, and what is the risk?",
        visible_columns=(
            "dept_l4",
            "workplace_code",
            "floor",
            "city",
            "region",
            "headcount",
            "allocated_workstations",
            "required_workstations",
            "recoverable_workstations",
            "percentile_demand",
            "peak_demand",
            "mean_demand",
            "weeks_observed",
            "has_sufficient_evidence",
            "is_undersized",
            "monthly_saving",
            "recovery_rate",
        ),
        scope_column="city",
        max_detail="dept_l4",
        metrics=("recoverable_workstations", "is_undersized", "weeks_observed"),
    ),
}


class ScopeError(PermissionError):
    """Raised when a role asks for something its scope does not cover."""


def scope_values(role: Role) -> list[str]:
    """The values this role can be scoped to, read from the warehouse."""
    spec = SCOPES[role]
    if spec.scope_column is None:
        return []
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        column = spec.scope_column
        source = (
            "main_marts.dim_employee" if column.startswith("dept") else "main_marts.dim_workplace"
        )
        rows = con.execute(
            f"select distinct {column} from {source} where {column} is not null order by 1"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def apply_scope(
    frame: pd.DataFrame, role: Role, scope_value: str | None, strict: bool = True
) -> pd.DataFrame:
    """Filter and project a frame for a role. The only way frames leave here."""
    spec = SCOPES[role]

    if spec.scope_column is not None:
        if scope_value is None:
            if strict:
                raise ScopeError(
                    f"{spec.title} must be scoped to a {spec.scope_column}; an unscoped view "
                    "of this role would show the whole estate."
                )
        elif spec.scope_column in frame.columns:
            frame = frame[frame[spec.scope_column] == scope_value]
        elif strict:
            raise ScopeError(
                f"{spec.title} is scoped by {spec.scope_column}, which this dataset does not "
                "carry, so the scope cannot be enforced and the request is refused."
            )

    keep = [c for c in spec.visible_columns if c in frame.columns]
    missing = [c for c in frame.columns if c not in keep]
    # Dropping rather than hiding: a column not returned cannot be exported.
    return frame[keep].copy() if keep else frame.iloc[:, :0].copy() if missing else frame
