# 2. Precedence, and where it departs from the brief's numbering

**Status:** accepted · **Date:** 2025-06-29

## Context

An employee-day can satisfy several conditions at once. Someone can have
approved leave covering a day they had also booked a desk for, in a week they
had already left the company. Without a declared order, two teams resolve it
differently and both are defensible.

The brief numbers its categories P1-P8 then O1-O7 and says first match wins.
Taken literally, that ordering produces wrong or unreachable buckets in three
places.

## Decision

The cascade runs in this order. Departures from the brief's numbering are
marked, each with its reason.

| # | Label | Note |
|---|---|---|
| 1 | P7 post exit | **Moved up.** Nothing else can be true of someone who had left. |
| 2 | P6 not yet onboarded | **Moved up.** Same reasoning. |
| 3 | P2 public holiday | **Moved above P1.** A day the office is shut is not leave anybody spent. |
| 4 | P1 approved leave | |
| 5 | P3 business travel | |
| 6 | P4 on assignment | |
| 7 | O3 / ATTENDED | **Moved above P5/P8.** See below. |
| 8 | P8 shift pattern offset | Inferred; see ADR 4. |
| 9 | P5 not a scheduled office day | |
| 10 | O4 desk booked, never used | **Moved above O1.** Strictly more specific evidence. |
| 11 | O2 unreported absence | Pattern rule; see ADR 4. |
| 12 | O1 no-show on a scheduled day | |

**Attendance before the schedule rules.** Somebody who came in on a day they
were not expected has attended. Filing that day under "not a scheduled office
day" hides real desk demand inside a planned bucket, and the capacity model
then sizes the floor too small.

**O4 before O1.** A booked desk that was never used is a no-show with a
receipt. Under the brief's literal ordering O1 fires first and O4 is
unreachable - the bucket exists in the taxonomy and never contains anything.

## Consequences

Every row stores `label_reason`, the sentence describing the rule that fired,
so the cascade can be audited from the data rather than by reading the SQL.

The order is asserted by tests, not just documented: employment status beating
everything, holiday beating leave, attendance beating the schedule rules, and
O4 requiring an actual booking.
