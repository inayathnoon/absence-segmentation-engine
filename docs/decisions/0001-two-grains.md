# 1. The taxonomy has two grains, not one

**Status:** accepted · **Date:** 2025-06-29

## Context

The brief lists fifteen absence categories in one numbered list, P1-P8 and
O1-O7, and asks that "every employee-day gets exactly one label".

Seven of them are not properties of an employee-day.

- P1-P8 and O1-O4 describe **a person on a day**: they were on leave, they
  travelled, they did not show up, they booked a desk and never used it.
- O5 (allocation exceeds schedule), O6 (structural oversupply) and O7 (stale
  allocation) describe **a team's desks over a period**. No individual day is
  "structurally oversupplied"; the unit is, across weeks.

Forcing all fifteen into one grain means either inventing a rule that attributes
structural oversupply to particular people on particular days - which is
arbitrary, and produces a number nobody can act on - or abandoning the "exactly
one label" guarantee.

## Decision

Two cascades, at two grains.

| Cascade | Grain | Labels | Model |
|---|---|---|---|
| Employee-day | `(emp_id, local_date)` | P1-P8, O1-O4, ATTENDED | `int_employee_day` |
| Allocation excess | `(dept_l4, workplace, floor)` | O5, O6, O7 | `capacity/model.py` |

Every employee-day still gets exactly one label, and that guarantee is tested.
The allocation cascade produces the recoverable capacity, which is the number
that drives a decision.

## Consequences

The two grains meet in `fct_allocation_unit_daily`, where employee-day labels
are aggregated to the unit a desk decision is made at. That join is the whole
bridge from "why was this person absent" to "how many desks can we hand back".

It also means the O1/O4 behavioural opportunity cannot simply be added to the
structural recoverable number, because they live at different grains and
measure overlapping things - see ADR 3.

Reporting waste per building would have been simpler and is useless for action:
nobody can hand back half a floor of desks that belong to four different teams.
