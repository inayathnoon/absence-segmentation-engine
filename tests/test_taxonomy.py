"""The rule cascade, asserted against the warehouse it produced."""

from __future__ import annotations

from conftest import scalar

PLANNED = ("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8")
OPTIMIZABLE = ("O1", "O2", "O3", "O4")


def test_every_employee_day_has_exactly_one_label(con):
    total = scalar(con, "select count(*) from main_intermediate.int_employee_day")
    labelled = scalar(
        con, "select count(*) from main_intermediate.int_employee_day where label is not null"
    )
    assert total == labelled > 0


def test_label_grain_is_unique(con):
    assert (
        scalar(
            con,
            "select count(*) from (select emp_id, local_date from "
            "main_intermediate.int_employee_day group by 1,2 having count(*) > 1)",
        )
        == 0
    )


def test_label_class_matches_its_label(con):
    assert (
        scalar(
            con,
            f"""
        select count(*) from main_intermediate.int_employee_day
        where (label in {OPTIMIZABLE} and label_class != 'optimizable')
           or (label in {PLANNED} and label_class != 'planned')
           or (label = 'ATTENDED' and label_class != 'attended')
        """,
        )
        == 0
    )


def test_precedence_employment_status_beats_everything(con):
    """Nothing can be true of somebody who had left or had not started."""
    assert (
        scalar(
            con,
            """
        select count(*) from main_intermediate.int_employee_day
        where (term_date is not null and local_date > term_date and label != 'P7')
           or (local_date < hire_date and label != 'P6')
        """,
        )
        == 0
    )


def test_precedence_holiday_beats_leave(con):
    """P2 beats P1 - a day the office is shut is not leave anybody spent.

    Employment status still beats both: somebody who had already left, or had
    not started, is P7 or P6 whatever the calendar says.
    """
    assert (
        scalar(
            con,
            "select count(*) from main_intermediate.int_employee_day "
            "where is_public_holiday and is_on_leave and label not in ('P2', 'P6', 'P7')",
        )
        == 0
    )


def test_attendance_beats_the_schedule_rules(con):
    """Somebody who came in on a day they were not expected has attended.
    Filing that as 'not a scheduled office day' hides real demand."""
    assert (
        scalar(
            con,
            "select count(*) from main_intermediate.int_employee_day "
            "where attended and label in ('P5', 'P8')",
        )
        == 0
    )


def test_o3_only_fires_below_the_threshold(con, cfg):
    assert (
        scalar(
            con,
            "select count(*) from main_intermediate.int_employee_day "
            "where label = 'O3' and hours_present >= ?",
            [cfg.taxonomy.partial_day_hours_threshold],
        )
        == 0
    )


def test_o4_requires_a_booking(con):
    assert (
        scalar(
            con,
            "select count(*) from main_intermediate.int_employee_day "
            "where label = 'O4' and not has_desk_booking",
        )
        == 0
    )


def test_o2_requires_the_window_and_the_baseline(con, cfg):
    """Both conditions, not either. An absolute count alone labels most of a
    60%-attendance team as an unreported absence."""
    assert (
        scalar(
            con,
            """
        select count(*) from main_intermediate.int_employee_day
        where label = 'O2'
          and (no_shows_in_window < ?
               or no_show_rate_in_window < ? * team_no_show_rate)
        """,
            [
                cfg.taxonomy.unreported_absence_min_no_shows,
                cfg.taxonomy.unreported_absence_baseline_multiple,
            ],
        )
        == 0
    )


def test_optimizable_labels_only_occur_on_office_days(con):
    assert (
        scalar(
            con,
            "select count(*) from main_intermediate.int_employee_day "
            "where label in ('O1','O2','O4') and not is_team_office_day",
        )
        == 0
    )


def test_overnight_shift_is_one_business_day(con):
    """Without the anchor rule the first day is a zero-hour visit the partial-
    day rule reports as waste, and the second is a visit nobody made."""
    crossing = scalar(
        con,
        "select count(*) from main_staging.stg_badge_taps where business_date != local_date",
    )
    assert crossing > 0, "no midnight-crossing taps were generated; the rule is untested"
    assert (
        scalar(
            con,
            "select count(*) from main_staging.stg_badge_taps "
            "where direction = 'out' and business_date > local_date",
        )
        == 0
    )


def test_leave_days_are_deduplicated(con):
    assert (
        scalar(
            con,
            "select count(*) from (select emp_id, leave_date from main_intermediate.int_leave_days "
            "group by 1,2 having count(*) > 1)",
        )
        == 0
    )
