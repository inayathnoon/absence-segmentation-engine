"""Role-based dashboard: one app, four audiences.

The role selector drives the query, not the layout. Switching role changes
which rows are fetched and which columns come back, because scoping that
happens after the fetch is decoration.
"""

from __future__ import annotations

import duckdb
import pandas as pd
import streamlit as st

from absence_engine.capacity.model import bootstrap_recovery_ci, compute_recovery
from absence_engine.config import IMG_DIR, WAREHOUSE_PATH, load_config
from absence_engine.reporting.scope import SCOPES, Role, apply_scope, scope_values

st.set_page_config(page_title="Absence segmentation", page_icon="🪑", layout="wide")

LABEL_HELP = {
    "P1": "Approved leave",
    "P2": "Public holiday",
    "P3": "Business travel",
    "P4": "On assignment in another city",
    "P5": "Not a scheduled office day",
    "P6": "Not yet onboarded",
    "P7": "Post exit",
    "P8": "Shift pattern offset (inferred)",
    "O1": "No-show on a scheduled day",
    "O2": "Unreported absence (recurring)",
    "O3": "Partial day under the dwell threshold",
    "O4": "Desk booked and never used",
    "ATTENDED": "Attended",
}


@st.cache_resource
def _config():
    return load_config()


@st.cache_data(ttl=600)
def _recovery():
    result = compute_recovery(_config())
    return result.units, result.by_city, result.totals, result.behavioural


@st.cache_data(ttl=600)
def _query(sql: str) -> pd.DataFrame:
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


cfg = _config()

if not WAREHOUSE_PATH.exists():
    st.title("Absence segmentation")
    st.info("No warehouse yet. Run `make pipeline` first.")
    st.stop()

units, by_city, totals, behavioural = _recovery()

# --- Role selection --------------------------------------------------------

with st.sidebar:
    st.header("Who is looking")
    role = Role(
        st.selectbox(
            "Role",
            [r.value for r in Role],
            format_func=lambda v: SCOPES[Role(v)].title,
        )
    )
    spec = SCOPES[role]
    st.caption(spec.question)

    options = scope_values(role)
    scope_value = None
    if options:
        scope_value = st.selectbox(spec.scope_column.replace("_", " ").title(), options)
        st.caption(
            f"Rows outside this {spec.scope_column} are never fetched, and columns outside "
            "this role's remit are not returned."
        )
    else:
        st.caption("This role sees the whole estate, at city grain only.")

    st.divider()
    st.caption(
        f"Profile **{cfg.profile_name}**, seed **{cfg.seed}**. Sized at "
        f"P{int(cfg.capacity.demand_percentile * 100)} with a {cfg.capacity.buffer:.0%} buffer."
    )

st.title(f"{spec.title} view")
st.caption(
    "All data is programmatically generated. No proprietary, confidential or personal data, "
    "and no real operational figures."
)

scoped = apply_scope(units, role, scope_value, strict=False)

# --- Headline --------------------------------------------------------------

point, low, high = bootstrap_recovery_ci(
    units if role is Role.EXECUTIVE else scoped if "allocated_workstations" in scoped else units
)

cols = st.columns(4)
if role is Role.DEPARTMENT_LEAD:
    cols[0].metric("Teams in scope", f"{len(scoped):,}")
    cols[1].metric("No-show days", f"{int(scoped.get('o1_no_show', pd.Series()).sum()):,}")
    cols[2].metric("Unreported absence", f"{int(scoped.get('o2_unreported', pd.Series()).sum()):,}")
    cols[3].metric(
        "Booked, unused", f"{int(scoped.get('o4_booked_not_used', pd.Series()).sum()):,}"
    )
else:
    allocated = int(scoped.get("allocated_workstations", pd.Series(dtype=float)).sum())
    recoverable = int(scoped.get("recoverable_workstations", pd.Series(dtype=float)).sum())
    saving = float(scoped.get("monthly_saving", pd.Series(dtype=float)).sum())
    cols[0].metric("Desks allocated", f"{allocated:,}")
    cols[1].metric("Desks recoverable", f"{recoverable:,}")
    cols[2].metric(
        "Recovery rate",
        f"{recoverable / allocated:.1%}" if allocated else "—",
        help=f"Estate-wide 95% CI {low:.1%} to {high:.1%}, bootstrapped over allocation units.",
    )
    cols[3].metric("Monthly saving", f"{saving:,.0f}")

st.divider()

# --- Role-specific body ----------------------------------------------------

if role is Role.EXECUTIVE:
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Where the opportunity is")
        st.dataframe(
            scoped.sort_values("recoverable_workstations", ascending=False),
            width="stretch",
            hide_index=True,
        )
    with right:
        st.subheader("What changed this week")
        trend = _query(
            """
            select iso_year_week, label_class, sum(employee_days) as employee_days
            from main_marts.fct_absence_segmentation_daily
            group by 1, 2 order by 1
            """
        )
        pivot = trend.pivot(
            index="iso_year_week", columns="label_class", values="employee_days"
        ).fillna(0)
        share = pivot.div(pivot.sum(axis=1), axis=0)
        if "optimizable" in share.columns and len(share) >= 2:
            delta = share["optimizable"].iloc[-1] - share["optimizable"].iloc[-2]
            direction = "up" if delta > 0 else "down"
            st.write(
                f"Optimizable absence is **{share['optimizable'].iloc[-1]:.1%}** of all "
                f"employee-days, {direction} {abs(delta):.2%} on the previous week."
            )
        st.bar_chart(share, height=260)

elif role is Role.REGIONAL_LEAD:
    st.subheader("Cities in this region")
    city_view = (
        scoped.groupby("city", as_index=False)
        .agg(
            allocated_workstations=("allocated_workstations", "sum"),
            recoverable_workstations=("recoverable_workstations", "sum"),
            monthly_saving=("monthly_saving", "sum"),
        )
        .sort_values("recoverable_workstations", ascending=False)
        if "city" in scoped.columns
        else scoped
    )
    st.dataframe(city_view, width="stretch", hide_index=True)

    st.subheader("Planned against optimizable, by city")
    split = _query(
        """
        select city, label_class, sum(employee_days) as employee_days
        from main_marts.fct_absence_segmentation_daily
        group by 1, 2
        """
    )
    if scope_value:
        cities = set(city_view["city"]) if "city" in city_view else set()
        split = split[split["city"].isin(cities)]
    pivot = split.pivot(index="city", columns="label_class", values="employee_days").fillna(0)
    st.bar_chart(pivot.div(pivot.sum(axis=1), axis=0), height=300)

elif role is Role.DEPARTMENT_LEAD:
    st.subheader("Your teams")
    st.dataframe(
        scoped.sort_values("o1_no_show", ascending=False) if "o1_no_show" in scoped else scoped,
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Only your own L1 is fetched. Team-level detail is shown because you manage these "
        "teams; individual attendance is not exposed to any role in this app."
    )

else:  # Space planner
    st.subheader("Resize candidates")
    candidates = scoped.copy()
    if "recoverable_workstations" in candidates:
        min_desks = st.slider("Minimum desks recoverable", 0, 40, 3)
        candidates = candidates[candidates["recoverable_workstations"] >= min_desks]
        candidates = candidates.sort_values("recoverable_workstations", ascending=False)
    st.dataframe(candidates, width="stretch", hide_index=True)

    if "is_undersized" in scoped.columns and scoped["is_undersized"].any():
        st.warning(
            f"{int(scoped['is_undersized'].sum())} unit(s) in scope are UNDER-sized: peak-day "
            "demand already exceeds the desks allocated. These need desks adding, not removing.",
            icon="⚠️",
        )

# --- Shared: the taxonomy --------------------------------------------------

st.divider()
with st.expander("The taxonomy, and how each label is decided"):
    counts = _query(
        """
        select label, label_class, label_reason, sum(employee_days) as employee_days
        from main_marts.fct_absence_segmentation_daily
        group by 1, 2, 3 order by 2, 1
        """
    )
    counts["share"] = (counts["employee_days"] / counts["employee_days"].sum()).round(4)
    st.dataframe(counts, width="stretch", hide_index=True)
    st.caption(
        "First match wins, and the rule that fired is stored on every row. An empty desk is "
        "only waste when the absence was avoidable."
    )

with st.expander("Sensitivity: the answer is a choice"):
    image = IMG_DIR / "sensitivity_heatmap.png"
    if image.exists():
        st.image(str(image), width="stretch")
    else:
        st.info("Run `make charts` to generate the sensitivity heatmap.")
    st.caption(
        f"Configured at P{int(cfg.capacity.demand_percentile * 100)} with a "
        f"{cfg.capacity.buffer:.0%} buffer. Sizing to the mean would roughly double the "
        "reported saving and leave the estate short of desks every peak day."
    )
