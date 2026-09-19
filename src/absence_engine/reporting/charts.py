"""Static charts for the README, written to docs/img/.

Rendered by a script rather than screenshotted from a dashboard, so the README
renders without anyone running the pipeline and every chart is reproducible
from the seed.

Colour follows a validated categorical palette with a fixed slot order, a
single hue for magnitude, and a diverging pair with a neutral midpoint for
signed error. Every chart carries direct value labels, so nothing depends on
colour alone.
"""

from __future__ import annotations

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from ..capacity.grading import grade, o2_calibration  # noqa: E402
from ..capacity.model import compute_recovery, sensitivity_sweep  # noqa: E402
from ..config import IMG_DIR, WAREHOUSE_PATH, Config, load_config  # noqa: E402

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
SEQUENTIAL = ["#e8f0fa", "#cfe0f5", "#9ec2ea", "#6ba3e0", "#2a78d6", "#1a4d8a"]
INK, INK_MUTED, SURFACE, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#d8d8d4"

BLUES = LinearSegmentedColormap.from_list("blues", SEQUENTIAL)
# Diverging: two hues with a neutral grey midpoint, never a rainbow.
DIVERGING = LinearSegmentedColormap.from_list(
    "err", ["#1a4d8a", "#9ec2ea", "#eeeeec", "#f0a98c", "#b8422e"]
)


def _style(ax, title: str, subtitle: str = "") -> None:
    ax.set_facecolor(SURFACE)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.tick_params(colors=INK_MUTED, labelsize=9)
    ax.set_axisbelow(True)
    ax.set_title(title, fontsize=13, color=INK, pad=30 if subtitle else 10, loc="left")
    if subtitle:
        # Placed below the title, not on top of it. imshow axes report a
        # different height than bar axes, so a shared offset collides on one
        # of them; the padding is set to clear the taller case.
        ax.text(
            0, 1.035, subtitle, transform=ax.transAxes, fontsize=9.5, color=INK_MUTED, va="bottom"
        )


def _save(fig, name: str):
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    path = IMG_DIR / name
    fig.savefig(path, dpi=150, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return path


# --- 1. The waterfall ------------------------------------------------------


def chart_waterfall(cfg: Config):
    """Delivered desks down to recoverable, one step at a time.

    A waterfall rather than a bar chart because the argument is the sequence:
    each step is a different question, and the distance between two of them is
    the entire subject of the repo.
    """
    result = compute_recovery(cfg)
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        row = con.execute(
            "select sum(delivered_workstations) from main_marts.dim_workplace"
        ).fetchone()
        delivered = (row[0] if row else 0) or 0
    finally:
        con.close()

    units = result.units
    # The "Required" step has to be shown. Without it the recoverable slice
    # does not reconcile against anything on the chart: it is allocated minus
    # required, and required is peak-day demand plus the buffer, floored per
    # unit so an under-sized team cannot offset an over-sized one.
    steps = [
        ("Delivered", int(delivered)),
        ("Allocated", int(units["allocated_workstations"].sum())),
        ("Required\n(peak + buffer)", int(units["required_workstations"].sum())),
        ("Peak-day demand", int(round(units["percentile_demand"].sum()))),
        ("Mean demand", int(round(units["mean_demand"].sum()))),
    ]
    recoverable = int(units["recoverable_workstations"].sum())

    fig, ax = plt.subplots(figsize=(10, 5))
    xs = range(len(steps))
    values = [v for _, v in steps]
    ax.bar(xs, values, color=SEQUENTIAL[1:6], width=0.62, edgecolor=SURFACE, linewidth=2)
    for x, (_, value) in zip(xs, steps, strict=True):
        ax.text(
            x, value + max(values) * 0.015, f"{value:,}", ha="center", fontsize=10, color=INK_MUTED
        )

    # The recoverable slice, drawn on the allocated bar it comes out of.
    ax.bar(
        [1],
        [recoverable],
        bottom=[steps[1][1] - recoverable],
        width=0.62,
        color=SERIES[1],
        edgecolor=SURFACE,
        linewidth=2,
        label=f"Recoverable: {recoverable:,} desks (allocated - required)",
    )
    ax.set_xticks(list(xs))
    ax.set_xticklabels([label for label, _ in steps])
    ax.set_ylabel("workstations", color=INK_MUTED, fontsize=9.5)
    ax.grid(axis="y", color=GRID, alpha=0.6, linewidth=0.8)
    _style(
        ax,
        "From desks built to desks recoverable",
        "Peak-day demand is the sizing input. The gap between allocated and "
        "peak-day demand is what can be handed back.",
    )
    ax.legend(frameon=False, fontsize=9.5, loc="upper right", labelcolor=INK_MUTED)
    return _save(fig, "recovery_waterfall.png")


# --- 2. Planned versus optimizable over time -------------------------------


def chart_planned_vs_optimizable(cfg: Config):
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    try:
        frame = con.execute(
            """
            select local_date, label_class, sum(employee_days) as employee_days
            from main_marts.fct_absence_segmentation_daily
            group by 1, 2 order by 1
            """
        ).df()
    finally:
        con.close()

    pivot = frame.pivot(index="local_date", columns="label_class", values="employee_days").fillna(0)
    order = [c for c in ["attended", "planned", "optimizable"] if c in pivot.columns]
    pivot = pivot[order]
    labels = {
        "attended": "Attended",
        "planned": "Planned absence (not actionable)",
        "optimizable": "Optimizable absence",
    }

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.stackplot(
        pd.to_datetime(pivot.index),
        [pivot[c] for c in order],
        labels=[labels[c] for c in order],
        colors=[SERIES[2], SERIES[0], SERIES[1]],
        edgecolor=SURFACE,
        linewidth=1.8,
    )
    ax.set_ylabel("employee-days", color=INK_MUTED, fontsize=9.5)
    ax.set_xlim(pd.to_datetime(pivot.index).min(), pd.to_datetime(pivot.index).max())
    ax.grid(axis="y", color=GRID, alpha=0.6, linewidth=0.8)
    _style(
        ax,
        "Planned against optimizable absence, every day",
        "An empty desk is only waste when the absence was avoidable. The blue "
        "band is absence no decision follows from.",
    )
    ax.legend(
        frameon=False,
        fontsize=9.5,
        loc="upper center",
        ncol=3,
        bbox_to_anchor=(0.5, -0.16),
        labelcolor=INK_MUTED,
    )
    fig.autofmt_xdate(rotation=30, ha="right")
    return _save(fig, "planned_vs_optimizable.png")


# --- 3. Sensitivity heatmap ------------------------------------------------


def chart_sensitivity(cfg: Config):
    """Recovery rate across the percentile and buffer grid.

    The chart that shows the answer is a choice. Presenting a single savings
    number without this behind it is presenting an opinion with a decimal
    point on it.
    """
    sweep = sensitivity_sweep(cfg)
    grid = sweep.pivot(index="percentile", columns="buffer", values="recovery_rate")

    fig, ax = plt.subplots(figsize=(9, 5))
    image = ax.imshow(grid.to_numpy(), cmap=BLUES, aspect="auto", origin="lower")
    ax.set_xticks(range(len(grid.columns)))
    ax.set_xticklabels([f"{b:.0%}" for b in grid.columns])
    ax.set_yticks(range(len(grid.index)))
    ax.set_yticklabels([f"P{int(p * 100)}" for p in grid.index])
    ax.set_xlabel("planning buffer", color=INK_MUTED, fontsize=9.5)
    ax.set_ylabel("demand percentile", color=INK_MUTED, fontsize=9.5)

    for i in range(len(grid.index)):
        for j in range(len(grid.columns)):
            value = grid.to_numpy()[i, j]
            ax.text(
                j,
                i,
                f"{value:.1%}",
                ha="center",
                va="center",
                fontsize=9,
                color="white" if value > grid.to_numpy().mean() else INK,
            )

    chosen = (
        list(grid.index).index(cfg.capacity.demand_percentile),
        list(grid.columns).index(cfg.capacity.buffer),
    )
    ax.add_patch(
        plt.Rectangle(
            (chosen[1] - 0.5, chosen[0] - 0.5), 1, 1, fill=False, edgecolor=SERIES[1], linewidth=3
        )
    )
    fig.colorbar(image, ax=ax, label="recovery rate")
    _style(
        ax,
        "Recovery rate by sizing percentile and buffer",
        f"The configured setting (P{int(cfg.capacity.demand_percentile * 100)}, "
        f"{cfg.capacity.buffer:.0%} buffer) is outlined. The same estate yields "
        "very different savings.",
    )
    return _save(fig, "sensitivity_heatmap.png")


# --- 4. City opportunity, planted vs recovered -----------------------------


def chart_city_opportunity(cfg: Config):
    result = grade(cfg)
    frame = result.recovery_by_city
    frame = frame[frame["estimator"] == "parametric"].sort_values("planted", ascending=False)
    frame = frame.head(12)

    fig, ax = plt.subplots(figsize=(10, 5.4))
    x = np.arange(len(frame))
    width = 0.38
    ax.bar(
        x - width / 2,
        frame["planted"],
        width,
        label="Planted (simulator)",
        color=SERIES[0],
        edgecolor=SURFACE,
        linewidth=2,
    )
    ax.bar(
        x + width / 2,
        frame["recovered"],
        width,
        label="Recovered (pipeline)",
        color=SERIES[1],
        edgecolor=SURFACE,
        linewidth=2,
    )
    top = max(frame["planted"].max(), frame["recovered"].max())
    for i, row in enumerate(frame.itertuples(index=False)):
        ax.text(
            i - width / 2,
            row.planted + top * 0.02,
            f"{int(row.planted)}",
            ha="center",
            fontsize=8.5,
            color=INK_MUTED,
        )
        ax.text(
            i + width / 2,
            row.recovered + top * 0.02,
            f"{int(row.recovered)}",
            ha="center",
            fontsize=8.5,
            color=INK_MUTED,
        )

    ax.set_xticks(x)
    ax.set_xticklabels(frame["city"], rotation=30, ha="right")
    ax.set_ylabel("recoverable workstations", color=INK_MUTED, fontsize=9.5)
    ax.grid(axis="y", color=GRID, alpha=0.6, linewidth=0.8)
    _style(
        ax,
        "Recoverable capacity by city: planted against recovered",
        "Parametric estimator. City-level error is larger than the estate "
        "total, which is what aggregation is for.",
    )
    ax.legend(frameon=False, fontsize=9.5, loc="upper right", labelcolor=INK_MUTED)
    return _save(fig, "city_opportunity.png")


# --- 5. O2 calibration -----------------------------------------------------


def chart_o2_calibration(cfg: Config):
    sweep = o2_calibration(cfg)
    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.plot(
        sweep["baseline_multiple"],
        sweep["precision"],
        marker="o",
        markersize=8,
        linewidth=2,
        color=SERIES[0],
        label="Precision",
    )
    ax.plot(
        sweep["baseline_multiple"],
        sweep["recall"],
        marker="s",
        markersize=8,
        linewidth=2,
        color=SERIES[1],
        label="Recall",
    )
    ax.plot(
        sweep["baseline_multiple"],
        sweep["f1"],
        marker="^",
        markersize=8,
        linewidth=2,
        color=SERIES[2],
        label="F1",
    )

    best = sweep.loc[sweep["f1"].idxmax()]
    ax.axvline(best["baseline_multiple"], color=INK_MUTED, linestyle=":", linewidth=1.4)
    ax.text(
        best["baseline_multiple"],
        0.83,
        f"  configured: {best['baseline_multiple']}",
        fontsize=9,
        color=INK,
    )
    ax.set_xlabel(
        "O2 threshold, as a multiple of the team's own no-show rate", color=INK_MUTED, fontsize=9.5
    )
    ax.set_ylim(0, 0.9)
    ax.grid(axis="y", color=GRID, alpha=0.6, linewidth=0.8)
    _style(
        ax,
        "Choosing the unreported-absence threshold",
        "The only rule with a threshold nobody can derive. Moving it trades "
        "precision against recall directly, so the curve is published.",
    )
    ax.legend(frameon=False, fontsize=9.5, labelcolor=INK_MUTED)
    return _save(fig, "o2_calibration.png")


# --- 6. Confusion matrix ---------------------------------------------------


def chart_confusion(cfg: Config):
    result = grade(cfg)
    confusion = result.confusion
    # Row-normalised: the question is "of the days that were truly X, where did
    # they go", and raw counts across buckets of wildly different size answer
    # a different question badly.
    normalised = confusion.div(confusion.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)

    fig, ax = plt.subplots(figsize=(9.5, 7.5))
    image = ax.imshow(normalised.to_numpy(), cmap=BLUES, vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(confusion.columns)))
    ax.set_xticklabels(confusion.columns, fontsize=9)
    ax.set_yticks(range(len(confusion.index)))
    ax.set_yticklabels(confusion.index, fontsize=9)
    ax.set_xlabel("recovered label", color=INK_MUTED, fontsize=9.5)
    ax.set_ylabel("planted label", color=INK_MUTED, fontsize=9.5)

    values = normalised.to_numpy()
    for i in range(values.shape[0]):
        for j in range(values.shape[1]):
            if values[i, j] >= 0.005:
                ax.text(
                    j,
                    i,
                    f"{values[i, j]:.2f}",
                    ha="center",
                    va="center",
                    fontsize=7.5,
                    color="white" if values[i, j] > 0.55 else INK,
                )
    fig.colorbar(image, ax=ax, label="share of planted label")
    _style(
        ax,
        "Where every employee-day went",
        f"Row-normalised. Label accuracy {result.overall_accuracy:.1%}; "
        f"planned-vs-optimizable class accuracy {result.class_accuracy:.1%}.",
    )
    return _save(fig, "confusion_matrix.png")


def render_all(cfg: Config | None = None) -> list:
    cfg = cfg or load_config()
    return [
        chart_waterfall(cfg),
        chart_planned_vs_optimizable(cfg),
        chart_sensitivity(cfg),
        chart_city_opportunity(cfg),
        chart_o2_calibration(cfg),
        chart_confusion(cfg),
    ]


if __name__ == "__main__":  # pragma: no cover
    for path in render_all():
        print(f"  wrote {path.relative_to(path.parents[2])}")
