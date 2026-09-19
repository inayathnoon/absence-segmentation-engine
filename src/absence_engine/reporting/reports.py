"""Weekly per-role reports, rendered to HTML and PDF.

HTML because it is what people actually open, PDF because it is what gets
attached to a paper for a committee.

The PDF is produced with matplotlib's multi-page writer rather than an HTML-to-
PDF engine. That is a deliberate trade: WeasyPrint or a headless browser would
give a better-looking document, at the cost of a heavyweight dependency (and,
for the browser, a binary this repo cannot assume is installed). Everything
that matters - the numbers, the tables, the charts - is in both.

Reports are written to out/reports/{role}/{date}/ and regenerating a date
overwrites it, so the job is idempotent by construction.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

from ..capacity.model import compute_recovery  # noqa: E402
from ..config import REPORTS_DIR, Config, load_config  # noqa: E402
from .scope import SCOPES, Role, apply_scope  # noqa: E402

SYNTHETIC_NOTICE = (
    "All data in this report is programmatically generated. It contains no "
    "proprietary, confidential or personal data, and no real operational figures."
)


@dataclass
class ReportArtifacts:
    role: Role
    report_date: date
    html_path: Path
    pdf_path: Path
    rows: int


def _role_frame(role: Role, scope_value: str | None, cfg: Config) -> pd.DataFrame:
    """Build the frame a role is entitled to, at the grain it works in."""
    result = compute_recovery(cfg)
    units = result.units

    if role is Role.EXECUTIVE:
        frame = result.by_city.copy()
        frame["region"] = frame["city"].map(
            units.drop_duplicates("city").set_index("city")["region"]
        )
    elif role is Role.REGIONAL_LEAD:
        frame = (
            units.groupby(["region", "city", "workplace_code"], as_index=False)
            .agg(
                allocated_workstations=("allocated_workstations", "sum"),
                required_workstations=("required_workstations", "sum"),
                recoverable_workstations=("recoverable_workstations", "sum"),
                monthly_saving=("monthly_saving", "sum"),
                o1_no_show=("o1_no_show", "sum"),
                o2_unreported=("o2_unreported", "sum"),
            )
        )
        frame["recovery_rate"] = (
            frame["recoverable_workstations"] / frame["allocated_workstations"]
        ).round(4)
        optimizable = units.groupby("workplace_code")[
            ["o1_no_show", "o2_unreported", "o3_partial", "o4_booked_not_used"]
        ].sum().sum(axis=1)
        frame["optimizable_share"] = (
            frame["workplace_code"].map(optimizable) / frame["allocated_workstations"]
        ).round(3)
        frame["planned_share"] = (1 - frame["optimizable_share"]).round(3)
    elif role is Role.DEPARTMENT_LEAD:
        frame = units.copy()
    else:
        frame = units.copy()

    return apply_scope(frame, role, scope_value, strict=False)


def _headline(role: Role, frame: pd.DataFrame) -> list[tuple[str, str]]:
    def total(column: str) -> float:
        return float(frame[column].sum()) if column in frame.columns else 0.0

    if role is Role.SPACE_PLANNER:
        resizable = int(frame["recoverable_workstations"].gt(0).sum()) if len(frame) else 0
        undersized = int(frame["is_undersized"].sum()) if "is_undersized" in frame else 0
        return [
            ("Units in scope", f"{len(frame):,}"),
            ("Units to resize down", f"{resizable:,}"),
            ("Units under-sized", f"{undersized:,}"),
            ("Desks recoverable", f"{total('recoverable_workstations'):,.0f}"),
        ]
    if role is Role.DEPARTMENT_LEAD:
        return [
            ("Teams in scope", f"{len(frame):,}"),
            ("No-show days", f"{total('o1_no_show'):,.0f}"),
            ("Unreported absence days", f"{total('o2_unreported'):,.0f}"),
            ("Booked and unused", f"{total('o4_booked_not_used'):,.0f}"),
        ]
    return [
        ("Desks allocated", f"{total('allocated_workstations'):,.0f}"),
        ("Desks required", f"{total('required_workstations'):,.0f}"),
        ("Desks recoverable", f"{total('recoverable_workstations'):,.0f}"),
        ("Monthly saving", f"{total('monthly_saving'):,.0f}"),
    ]


def _render_html(
    role: Role, scope_value: str | None, report_date: date, frame: pd.DataFrame, cfg: Config
) -> str:
    spec = SCOPES[role]
    cards = "".join(
        f'<div class="card"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(value)}</div></div>'
        for label, value in _headline(role, frame)
    )
    table = frame.head(60).to_html(index=False, border=0, classes="data", float_format="%.3f")
    scope_line = (
        f"Scoped to {spec.scope_column} = <strong>{html.escape(str(scope_value))}</strong>"
        if scope_value
        else "Whole estate"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(spec.title)} report - {report_date.isoformat()}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; padding: 32px; font: 14px/1.5 -apple-system, BlinkMacSystemFont,
         "Segoe UI", Roboto, sans-serif; color: #0b0b0b; background: #fcfcfb; }}
  header {{ border-bottom: 2px solid #d8d8d4; padding-bottom: 16px; margin-bottom: 24px; }}
  h1 {{ margin: 0 0 4px; font-size: 22px; }}
  .question {{ color: #52514e; font-size: 15px; margin: 0 0 8px; }}
  .meta {{ color: #52514e; font-size: 12.5px; }}
  .notice {{ background: #eef3fa; border-left: 3px solid #2a78d6; padding: 10px 14px;
             margin: 18px 0; font-size: 12.5px; color: #33404f; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 14px; margin: 20px 0 26px; }}
  .card {{ flex: 1 1 180px; border: 1px solid #d8d8d4; border-radius: 8px; padding: 14px 16px;
           background: #fff; }}
  .card .label {{ font-size: 12px; color: #52514e; text-transform: uppercase;
                  letter-spacing: .04em; }}
  .card .value {{ font-size: 24px; font-weight: 600; margin-top: 6px; }}
  table.data {{ border-collapse: collapse; width: 100%; font-size: 12.5px; }}
  table.data th {{ text-align: left; background: #eef0f2; padding: 8px 10px;
                   border-bottom: 1px solid #d8d8d4; }}
  table.data td {{ padding: 7px 10px; border-bottom: 1px solid #ececea; }}
  table.data tr:hover td {{ background: #f6f8fb; }}
  footer {{ margin-top: 28px; color: #52514e; font-size: 12px; }}
</style></head><body>
<header>
  <h1>{html.escape(spec.title)} &mdash; week ending {report_date.isoformat()}</h1>
  <p class="question">{html.escape(spec.question)}</p>
  <p class="meta">{scope_line} &middot; profile {html.escape(cfg.profile_name)} &middot;
     seed {cfg.seed} &middot; sized at P{int(cfg.capacity.demand_percentile * 100)}
     with a {cfg.capacity.buffer:.0%} buffer</p>
</header>
<div class="notice">{SYNTHETIC_NOTICE}</div>
<div class="cards">{cards}</div>
<h2 style="font-size:16px;">Detail</h2>
{table}
<footer>Generated by absence-segmentation-engine. Showing up to 60 rows of
{len(frame):,}. Every figure is a property of the simulator.</footer>
</body></html>"""


def _render_pdf(
    role: Role, scope_value: str | None, report_date: date, frame: pd.DataFrame, path: Path
) -> None:
    spec = SCOPES[role]
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.07, 0.94, f"{spec.title} — week ending {report_date.isoformat()}",
                 fontsize=17, fontweight="bold")
        fig.text(0.07, 0.915, spec.question, fontsize=11, color="#52514e")
        scope_line = (
            f"Scoped to {spec.scope_column} = {scope_value}" if scope_value else "Whole estate"
        )
        fig.text(0.07, 0.895, scope_line, fontsize=9.5, color="#52514e")
        fig.text(0.07, 0.86, SYNTHETIC_NOTICE, fontsize=8.5, color="#33404f", wrap=True)

        y = 0.80
        for label, value in _headline(role, frame):
            fig.text(0.07, y, label, fontsize=9.5, color="#52514e")
            fig.text(0.07, y - 0.028, value, fontsize=18, fontweight="bold")
            y -= 0.075

        if not frame.empty:
            axis = fig.add_axes([0.07, 0.08, 0.86, 0.38])
            axis.axis("off")
            preview = frame.head(18)
            table = axis.table(
                cellText=preview.round(3).astype(str).to_numpy(),
                colLabels=list(preview.columns),
                loc="upper center",
                cellLoc="left",
            )
            table.auto_set_font_size(False)
            table.set_fontsize(5.6)
            table.scale(1, 1.25)
        pdf.savefig(fig)
        plt.close(fig)


def generate_report(
    role: Role,
    report_date: date,
    scope_value: str | None = None,
    cfg: Config | None = None,
) -> ReportArtifacts:
    cfg = cfg or load_config()
    frame = _role_frame(role, scope_value, cfg)

    folder = REPORTS_DIR / role.value / report_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    stem = scope_value.replace(" ", "_").lower() if scope_value else "all"

    html_path = folder / f"{stem}.html"
    pdf_path = folder / f"{stem}.pdf"
    html_path.write_text(_render_html(role, scope_value, report_date, frame, cfg))
    _render_pdf(role, scope_value, report_date, frame, pdf_path)
    return ReportArtifacts(role, report_date, html_path, pdf_path, len(frame))


def generate_weekly_reports(
    report_date: date | None = None, cfg: Config | None = None
) -> list[ReportArtifacts]:
    """One report per role, plus one per scope value for the scoped roles."""
    cfg = cfg or load_config()
    report_date = report_date or cfg.end_date

    from .scope import scope_values

    artifacts: list[ReportArtifacts] = []
    for role in Role:
        values = scope_values(role)
        if not values:
            artifacts.append(generate_report(role, report_date, None, cfg))
            continue
        # Cap the fan-out: a department lead report per L1 is four files, not
        # four hundred. The scope machinery is identical either way.
        for value in values[:8]:
            artifacts.append(generate_report(role, report_date, value, cfg))
    return artifacts


if __name__ == "__main__":  # pragma: no cover
    for artifact in generate_weekly_reports():
        print(f"  {artifact.role.value:16s} {artifact.rows:>5} rows -> {artifact.html_path}")
