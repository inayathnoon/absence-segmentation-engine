"""The day loop: attendance decisions, badge taps, desk bookings, and truth.

For every employee-day the simulator knows *why* the day turned out the way it
did, because it decided. That cause is written to ``data/truth/`` and never to
``data/raw/``. The pipeline re-derives a label from evidence alone, and the two
are compared as a confusion matrix.

That comparison is the point of the repo. A segmentation framework that cannot
be graded is an opinion with SQL attached.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from ..config import Config
from .rng import lognormal_from_mean, substream

# Truth causes. The pipeline's own label vocabulary is defined in the dbt
# cascade and uses the same codes, so the confusion matrix is square.
PLANNED = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]
OPTIMIZABLE = ["O1", "O2", "O3", "O4"]
ATTENDED = "ATTENDED"

WORKDAY_HOURS = {"mean": 8.2, "sigma": 0.34}
ARRIVAL_HOUR = {"mean": 9.2, "sigma": 0.9}


@dataclass
class TruthTally:
    """Realised counts per truth cause, accumulated as days are generated.

    The config plants hazards, not shares; the share each bucket ends up with
    is an outcome of those hazards interacting. Recording the realised counts
    means the README compares recovered against what was actually simulated
    rather than against an input that was never quite achieved.
    """

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, labels: np.ndarray) -> None:
        unique, counts = np.unique(labels, return_counts=True)
        for label, count in zip(unique, counts, strict=True):
            self.counts[str(label)] = self.counts.get(str(label), 0) + int(count)

    def shares(self) -> dict[str, float]:
        total = sum(self.counts.values())
        return {k: round(v / total, 5) for k, v in sorted(self.counts.items())} if total else {}


class DayGenerator:
    """Holds the per-employee lookups the day loop needs."""

    def __init__(self, cfg: Config, employees: pd.DataFrame, estate: pd.DataFrame) -> None:
        self.cfg = cfg
        self.emp = employees.reset_index(drop=True)
        self.rng = substream(cfg.seed, "attendance")
        self.tally = TruthTally()
        self.booking_rows: list[dict] = []

        n = len(self.emp)
        self.index = {e: i for i, e in enumerate(self.emp["emp_id"])}
        self.hire = self.emp["hire_date"].to_numpy()
        self.term = self.emp["term_date"].to_numpy()
        self.propensity = self.emp["attendance_propensity"].to_numpy()
        self.chronic = self.emp["is_chronic_no_show"].to_numpy()
        self.free_sharing = self.emp["is_free_sharing"].to_numpy()
        self.shift_offset = self.emp["has_shift_offset"].to_numpy()
        self.country = self.emp["city"].map(
            {c.name: c.country for c in cfg.cities}
        ).to_numpy()
        self.timezone = self.emp["city"].map({c.name: c.tz for c in cfg.cities}).to_numpy()

        self.personal = np.zeros((n, 7), dtype=bool)
        self.team = np.zeros((n, 7), dtype=bool)
        for i, (own, team) in enumerate(
            zip(self.emp["scheduled_weekdays"], self.emp["team_scheduled_weekdays"], strict=True)
        ):
            for d in own:
                self.personal[i, d] = True
            for d in team:
                self.team[i, d] = True

    def _mask_from(self, keys) -> np.ndarray:
        mask = np.zeros(len(self.emp), dtype=bool)
        idx = [self.index[e] for e in keys if e in self.index]
        if idx:
            mask[idx] = True
        return mask

    def generate_day(
        self,
        day: date,
        leave_today: set[str],
        travel_today: set[str],
        assignment_today: set[str],
        holiday_countries: set[str],
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Return (taps, truth) for one calendar day."""
        cfg = self.cfg
        weekday = day.weekday()
        n = len(self.emp)

        post_exit = np.array([t is not None and day > t for t in self.term])
        pre_hire = np.array([day < h for h in self.hire])
        on_holiday = np.isin(self.country, list(holiday_countries))
        on_leave = self._mask_from(leave_today)
        on_travel = self._mask_from(travel_today)
        on_assignment = self._mask_from(assignment_today)

        scheduled = self.personal[:, weekday] & (weekday < 5)
        team_day = self.team[:, weekday] & (weekday < 5)

        # Attendance decision, made only for people who could attend at all.
        could_attend = (
            ~post_exit & ~pre_hire & ~on_holiday & ~on_leave & ~on_travel & ~on_assignment
        )
        p_attend = np.where(scheduled, self.propensity, cfg.planted.off_schedule_attendance_rate)
        attends = could_attend & (self.rng.random(n) < p_attend)

        # Desk bookings, for the free-sharing population on scheduled days.
        # Booking is decided independently of attendance, then a share of
        # booked attendees are flipped to no-shows - which is what makes O4 a
        # distinct, evidenced bucket rather than an inference.
        booked = could_attend & scheduled & self.free_sharing
        booked &= self.rng.random(n) < cfg.planted.booking_rate_free_sharing
        flipped = booked & attends & (self.rng.random(n) < cfg.planted.booking_no_show_share)
        attends = attends & ~flipped

        self._record_bookings(day, booked, attends)

        # Partial days: a token visit that occupies a desk for under the
        # threshold and should not be counted as a full day of demand.
        hours = np.maximum(
            0.25,
            lognormal_from_mean(self.rng, WORKDAY_HOURS["mean"], WORKDAY_HOURS["sigma"], n),
        )
        partial = attends & (self.rng.random(n) < cfg.planted.partial_day_share)
        partial_cfg = cfg.planted.partial_day_hours
        hours = np.where(
            partial,
            np.minimum(
                partial_cfg["max"],
                lognormal_from_mean(self.rng, partial_cfg["mean"], partial_cfg["sigma"], n),
            ),
            hours,
        )

        labels = self._label(
            post_exit=post_exit,
            pre_hire=pre_hire,
            on_holiday=on_holiday,
            on_leave=on_leave,
            on_travel=on_travel,
            on_assignment=on_assignment,
            scheduled=scheduled,
            team_day=team_day,
            booked_not_used=booked & ~attends,
            attends=attends,
            partial=partial,
        )
        self.tally.add(labels)

        truth = pd.DataFrame(
            {
                "emp_id": self.emp["emp_id"].to_numpy(),
                "local_date": day,
                "truth_label": labels,
                "truth_attended": attends,
                "truth_hours": np.where(attends, hours.round(2), 0.0),
            }
        )
        return self._taps(day, attends, hours), truth

    # --- labelling ---------------------------------------------------------

    def _label(self, **m) -> np.ndarray:
        """The generative cause of each employee-day, resolved in one pass.

        Order matters and differs from the brief's numbering in two places,
        both documented in docs/decisions/0002-precedence.md: employment status
        (P6/P7) is evaluated first because nothing else can be true of someone
        who does not work here, and a public holiday (P2) beats approved leave
        (P1) because a day the office is shut is not leave anybody spent.
        """
        n = len(self.emp)
        labels = np.full(n, ATTENDED, dtype=object)

        # Applied last-to-first so that earlier rules overwrite later ones.
        #
        # The schedule labels are gated on *not attending*. Someone who comes
        # in on a day they were not expected has attended; calling that day
        # "not a scheduled office day" would hide real attendance inside a
        # planned bucket and understate demand.
        labels = np.where(m["partial"], "O3", labels)
        labels = np.where(
            m["scheduled"] & ~m["attends"] & ~m["booked_not_used"],
            np.where(self.chronic, "O2", "O1"),
            labels,
        )
        # O4 is defined by evidence, not by intent: a desk booked and never
        # used is a booked-and-unused desk whether or not the person had meant
        # to come. Labelling only the ones the simulator deliberately flipped
        # would make the truth narrower than any rule could ever be, and the
        # pipeline would be marked down for being right.
        labels = np.where(m["booked_not_used"], "O4", labels)
        labels = np.where(~m["attends"] & ~m["scheduled"] & ~m["team_day"], "P5", labels)
        labels = np.where(
            ~m["attends"] & ~m["scheduled"] & m["team_day"] & self.shift_offset, "P8", labels
        )
        labels = np.where(m["on_assignment"], "P4", labels)
        labels = np.where(m["on_travel"], "P3", labels)
        labels = np.where(m["on_leave"], "P1", labels)
        labels = np.where(m["on_holiday"], "P2", labels)
        labels = np.where(m["pre_hire"], "P6", labels)
        labels = np.where(m["post_exit"], "P7", labels)
        return labels

    # --- evidence ----------------------------------------------------------

    def _record_bookings(self, day: date, booked: np.ndarray, attends: np.ndarray) -> None:
        idx = np.flatnonzero(booked)
        if idx.size == 0:
            return
        created = datetime.combine(day - timedelta(days=1), time(16, 30))
        for i in idx:
            self.booking_rows.append(
                {
                    "booking_id": f"DB{len(self.booking_rows):09d}",
                    "emp_id": self.emp["emp_id"].iloc[i],
                    "workplace_code": self.emp["workplace_code"].iloc[i],
                    "floor": int(self.emp["floor"].iloc[i]),
                    "booking_date": day,
                    "created_ts": created,
                    "status": "confirmed",
                }
            )

    def _taps(self, day: date, attends: np.ndarray, hours: np.ndarray) -> pd.DataFrame:
        """Two taps per attended day, in and out, stored in UTC.

        Deliberately simple: this repo's subject is what an empty desk means,
        not badge-reader forensics. Dwell time comes from the in/out pair,
        which is all the partial-day rule needs.
        """
        idx = np.flatnonzero(attends)
        if idx.size == 0:
            return pd.DataFrame(columns=_TAP_COLUMNS)

        arrival = np.clip(
            self.rng.normal(ARRIVAL_HOUR["mean"], ARRIVAL_HOUR["sigma"], len(idx)), 5.5, 20.0
        )
        dwell = hours[idx]
        emp = self.emp.iloc[idx]

        rows = pd.DataFrame(
            {
                "emp_id": np.repeat(emp["emp_id"].to_numpy(), 2),
                "workplace_code": np.repeat(emp["workplace_code"].to_numpy(), 2),
                "floor": np.repeat(emp["floor"].to_numpy(), 2),
                "direction": np.tile(["in", "out"], len(idx)),
                "local_hour": np.empty(2 * len(idx)),
                "timezone": np.repeat(self.timezone[idx], 2),
            }
        )
        rows.loc[0::2, "local_hour"] = arrival
        rows.loc[1::2, "local_hour"] = arrival + dwell

        midnight = datetime.combine(day, time(0, 0))
        local = pd.to_datetime([midnight + timedelta(hours=float(h)) for h in rows["local_hour"]])
        utc = pd.Series(pd.NaT, index=rows.index, dtype="datetime64[ns]")
        for tz, group in rows.groupby("timezone").groups.items():
            utc.loc[group] = (
                local[rows.index.get_indexer(group)]
                .tz_localize(tz, ambiguous=True, nonexistent="shift_forward")
                .tz_convert("UTC")
                .tz_localize(None)
            )
        rows["tap_ts_utc"] = utc
        rows["local_date"] = day
        return rows[_TAP_COLUMNS]


_TAP_COLUMNS = ["emp_id", "workplace_code", "floor", "tap_ts_utc", "direction", "local_date"]
