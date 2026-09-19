"""Typed access to ``conf/sim.yaml``.

The config is loaded once, validated, and passed explicitly. Nothing else in
the repository reads the YAML, and no module carries a threshold of its own -
if a rule needs a number, the number lives here.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "conf" / "sim.yaml"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Profile(_Base):
    n_employees: int
    n_days: int
    n_cities: int
    n_workplaces: int
    end_date: date


class City(_Base):
    name: str
    country: str
    region: str
    tz: str
    cost_index: float


class Estate(_Base):
    cities: list[City]
    floors_per_workplace: dict[str, int]
    workstations_per_employee: float
    free_sharing_share: float
    cost_per_workstation_month: dict[str, float]


class Org(_Base):
    departments_l1: list[str]
    l2_per_l1: dict[str, int]
    l3_per_l2: dict[str, int]
    l4_per_l3: dict[str, int]
    team_size: dict[str, float]
    floors_per_team: dict[str, int]
    employee_type_mix: dict[str, float]
    tenure_days: dict[str, float]
    joiner_share: float
    leaver_share: float


class Planted(_Base):
    scheduled_days_per_week: dict[str, int]
    weekday_weights: list[float]
    attendance_rate_by_region: dict[str, float]
    propensity_concentration: float
    off_schedule_attendance_rate: float
    public_holiday_days_per_country: int
    leave: dict[str, Any]
    travel: dict[str, Any]
    assignment_share: float
    shift_offset_share: float
    chronic_no_show_share: float
    chronic_no_show_attendance_rate: float
    partial_day_share: float
    partial_day_hours: dict[str, float]
    booking_rate_free_sharing: float
    booking_no_show_share: float
    allocation_excess: dict[str, Any]


class Capacity(_Base):
    demand_percentile: float
    buffer: float
    o1_recoverable_fraction: float
    o4_recoverable_fraction: float
    min_weeks_evidence: int
    sensitivity: dict[str, list[float]]


class Taxonomy(_Base):
    partial_day_hours_threshold: float
    unreported_absence_window_days: int
    unreported_absence_min_no_shows: int
    unreported_absence_baseline_multiple: float
    headcount_decline_threshold: float


class DQ(_Base):
    freshness_sla_hours: dict[str, int]
    reconciliation_tolerance: float
    min_label_coverage: float


class Config(_Base):
    seed: int
    profile_name: str
    profile: Profile
    estate: Estate
    org: Org
    planted: Planted
    capacity: Capacity
    taxonomy: Taxonomy
    dq: DQ

    @property
    def start_date(self) -> date:
        return self.profile.end_date - timedelta(days=self.profile.n_days - 1)

    @property
    def end_date(self) -> date:
        return self.profile.end_date

    @property
    def cities(self) -> list[City]:
        """Cities in play, picked round-robin across regions.

        Taking the first N in file order would give a small profile a single
        region, and the role-based dashboards and regional benchmarks would
        then be exercising nothing.
        """
        by_region: dict[str, list[City]] = {}
        for city in self.estate.cities:
            by_region.setdefault(city.region, []).append(city)

        chosen: list[City] = []
        depth = 0
        while len(chosen) < self.profile.n_cities:
            added = False
            for region in sorted(by_region):
                if depth < len(by_region[region]) and len(chosen) < self.profile.n_cities:
                    chosen.append(by_region[region][depth])
                    added = True
            if not added:
                break
            depth += 1
        return chosen


def _coerce_date(value: Any) -> date:
    return value if isinstance(value, date) else datetime.strptime(str(value), "%Y-%m-%d").date()


def load_config(path: str | Path | None = None, profile: str | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text())

    profiles = raw.pop("profiles")
    chosen = profile or os.environ.get("ASE_PROFILE") or raw["profile"]
    if chosen not in profiles:
        raise ValueError(f"Unknown profile {chosen!r}; available: {sorted(profiles)}")

    profile_raw = dict(profiles[chosen])
    profile_raw["end_date"] = _coerce_date(profile_raw["end_date"])

    defined = len(raw["estate"]["cities"])
    if profile_raw["n_cities"] > defined:
        raise ValueError(
            f"Profile {chosen!r} wants {profile_raw['n_cities']} cities but only {defined} "
            "are defined in estate.cities"
        )

    data = dict(raw)
    data["profile"] = Profile(**profile_raw)
    data["profile_name"] = chosen
    return Config(**data)


@lru_cache(maxsize=4)
def get_config(profile: str | None = None) -> Config:
    return load_config(profile=profile)


# --- Filesystem layout -----------------------------------------------------

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
TRUTH_DIR = DATA_DIR / "truth"
WAREHOUSE_PATH = REPO_ROOT / "warehouse" / "local.duckdb"
DBT_DIR = REPO_ROOT / "dbt"
DOCS_DIR = REPO_ROOT / "docs"
IMG_DIR = DOCS_DIR / "img"
OUT_DIR = REPO_ROOT / "out"
REPORTS_DIR = OUT_DIR / "reports"
GROUND_TRUTH = TRUTH_DIR / "ground_truth.json"
TRUTH_LABELS = TRUTH_DIR / "employee_day_truth"
