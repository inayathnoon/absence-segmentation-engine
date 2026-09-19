"""Properties of the simulator the rest of the repo relies on."""

from __future__ import annotations

import pytest

from absence_engine.config import load_config
from absence_engine.gen.estate import generate_estate
from absence_engine.gen.org import generate_org
from absence_engine.gen.rng import beta_around, lognormal_from_mean, substream


@pytest.fixture(scope="module")
def small_cfg():
    return load_config(profile="demo")


@pytest.fixture(scope="module")
def estate(small_cfg):
    return generate_estate(small_cfg)


def test_substreams_are_independent(small_cfg):
    a1 = substream(small_cfg.seed, "attendance").random(40)
    a2 = substream(small_cfg.seed, "attendance").random(40)
    b = substream(small_cfg.seed, "leave").random(40)
    assert (a1 == a2).all()
    assert not (a1 == b).all()


def test_lognormal_hits_the_arithmetic_mean():
    draws = lognormal_from_mean(substream(1, "t"), 540.0, 0.22, 200_000)
    assert draws.mean() == pytest.approx(540.0, rel=0.02)


def test_beta_around_hits_its_target_mean():
    draws = beta_around(substream(1, "t"), 0.64, 11.0, 100_000)
    assert draws.mean() == pytest.approx(0.64, abs=0.01)


def test_generation_is_reproducible(small_cfg):
    assert generate_estate(small_cfg).equals(generate_estate(small_cfg))


def test_shift_offset_differs_except_where_it_cannot(small_cfg, estate):
    """P8 only exists where the shifted pattern differs from the team's.

    On a five-day policy it cannot: every weekday is already an office day, so
    shifting the pattern produces the same set. That is not a generator bug, it
    is the reason P8 is unrecoverable for those teams, and asserting it here
    keeps the claim in the README tied to the code.
    """
    employees, _ = generate_org(small_cfg, estate)
    offset = employees[employees["has_shift_offset"]]
    assert not offset.empty

    identical, differing = 0, 0
    for own, team in zip(
        offset["scheduled_weekdays"], offset["team_scheduled_weekdays"], strict=True
    ):
        if set(own) == set(team):
            assert len(set(team)) == 5, "only a five-day policy can absorb the shift"
            identical += 1
        else:
            differing += 1
    assert differing > 0 and identical > 0


def test_non_offset_employees_match_their_team(small_cfg, estate):
    employees, _ = generate_org(small_cfg, estate)
    regular = employees[~employees["has_shift_offset"]]
    assert all(
        set(own) == set(team)
        for own, team in zip(
            regular["scheduled_weekdays"], regular["team_scheduled_weekdays"], strict=True
        )
    )


def test_seating_shares_sum_to_one_per_team(small_cfg, estate):
    _, seating = generate_org(small_cfg, estate)
    totals = seating.groupby("dept_l4")["seat_share"].sum()
    assert totals.between(0.999, 1.001).all()


def test_truth_columns_never_reach_the_published_allocation():
    """The integrity rule: if a column would let the warehouse cheat, it must
    not be in data/raw."""
    from absence_engine.gen.allocation import PUBLISHED_COLUMNS

    assert not [c for c in PUBLISHED_COLUMNS if c.startswith("truth_")]
