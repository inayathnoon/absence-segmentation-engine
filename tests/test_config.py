import pytest
from pydantic import ValidationError

from absence_engine.config import load_config


def test_window_matches_day_count(cfg):
    assert (cfg.end_date - cfg.start_date).days + 1 == cfg.profile.n_days


def test_every_region_is_present_in_every_profile():
    """A profile with one region would leave the regional-lead scoping, the
    peer benchmark and the regional attendance plant all untested."""
    for profile in ("demo", "full"):
        regions = {c.region for c in load_config(profile=profile).cities}
        assert regions == {"AMER", "EMEA", "APAC"}, profile


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="Unknown profile"):
        load_config(profile="nope")


def test_config_is_frozen(cfg):
    with pytest.raises(ValidationError):
        cfg.seed = 1


def test_thresholds_agree_between_config_and_dbt(cfg):
    """A rule that means 2.5 hours in one file and 3.0 in another produces two
    defensible taxonomies and no way to choose between them."""
    import yaml

    from absence_engine.config import DBT_DIR

    dbt_vars = yaml.safe_load((DBT_DIR / "dbt_project.yml").read_text())["vars"]
    assert dbt_vars["partial_day_hours_threshold"] == cfg.taxonomy.partial_day_hours_threshold
    assert dbt_vars["unreported_absence_window_days"] == cfg.taxonomy.unreported_absence_window_days
    assert (
        dbt_vars["unreported_absence_min_no_shows"] == cfg.taxonomy.unreported_absence_min_no_shows
    )
    assert (
        dbt_vars["unreported_absence_baseline_multiple"]
        == cfg.taxonomy.unreported_absence_baseline_multiple
    )
