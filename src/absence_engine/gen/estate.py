"""Cities, workplaces, floors and their cost base."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from .rng import lognormal_from_mean, substream


def generate_estate(cfg: Config) -> pd.DataFrame:
    """One row per floor: the unit space is actually managed in."""
    rng = substream(cfg.seed, "estate")
    cities = cfg.cities
    n_workplaces = cfg.profile.n_workplaces

    # Every city gets one workplace; the rest go to the larger cities.
    weights = 1.0 / np.arange(1, len(cities) + 1) ** 0.6
    weights = weights / weights.sum()
    extra = rng.multinomial(max(n_workplaces - len(cities), 0), weights)
    per_city = {c.name: 1 + int(extra[i]) for i, c in enumerate(cities)}

    total_desks = cfg.profile.n_employees * cfg.estate.workstations_per_employee
    floor_cfg = cfg.estate.floors_per_workplace
    cost_cfg = cfg.estate.cost_per_workstation_month

    rows: list[dict] = []
    seq = 0
    for city in cities:
        for _ in range(per_city[city.name]):
            seq += 1
            code = f"WP{seq:03d}"
            n_floors = int(rng.integers(floor_cfg["min"], floor_cfg["max"] + 1))
            cost = float(
                lognormal_from_mean(rng, cost_cfg["mean"] * city.cost_index, cost_cfg["sigma"], 1)[
                    0
                ]
            )
            for floor in range(1, n_floors + 1):
                rows.append(
                    {
                        "workplace_code": code,
                        "workplace_name": f"{city.name} {seq}",
                        "city": city.name,
                        "country": city.country,
                        "region": city.region,
                        "timezone": city.tz,
                        "floor": floor,
                        "cost_per_workstation_month": round(cost, 2),
                    }
                )

    frame = pd.DataFrame(rows)
    # Delivered desks are apportioned across floors, then scaled so the estate
    # totals the configured desks-per-employee. Sizing the estate from
    # headcount rather than from an absolute mean keeps both profiles
    # plausible without a second set of numbers in the config.
    shares = rng.dirichlet(np.full(len(frame), 6.0))
    delivered = np.maximum(12, np.round(shares * total_desks).astype(int))
    frame["delivered_workstations"] = delivered
    frame["free_sharing_workstations"] = np.maximum(
        1, np.round(delivered * cfg.estate.free_sharing_share).astype(int)
    )
    return frame
