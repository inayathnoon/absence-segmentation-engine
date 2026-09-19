# 4. Two labels are inferred, and both are deliberately conservative

**Status:** accepted · **Date:** 2025-06-29

## Context

Ten of the twelve employee-day labels have a source record behind them: a leave
request, a travel booking, a holiday calendar, a hire date, a tap. Two do not.

- **P8, shift pattern offset.** The HR roster publishes the *team's* office-day
  policy, not an individual's variation from it. Someone who works Wednesday to
  Friday on a Monday-to-Wednesday team is absent on Monday for a planned
  reason, and nothing in the source data says so.
- **O2, unreported absence.** A pattern, not an event. One missed day is an O1;
  a run of them is something a manager should be told about.

Both therefore have to be inferred from attendance itself, and both have a
threshold nobody can derive from first principles.

## Decision

**Direction of the error is chosen deliberately.** Both rules are tuned to
under-fire rather than over-fire, because a false P8 or a false O2 moves waste
out of the optimizable bucket and makes the estate look better than it is. An
analysis that flatters the estate is worse than one that misses some of it.

**P8** needs two separate inferences, not one: that the person works a
different set of days from their team *at all*, and that this particular
weekday is not one they work. With only the first, the rule excuses every
absence that person ever has.

**O2** fires only when the window count clears an absolute minimum *and* the
window rate exceeds the team's own baseline by a multiple. An absolute
threshold alone is meaningless without a reference attendance rate: at 60%
attendance the average person misses four of every ten office days by chance,
and "three misses in ten" labels most of a team as an unreported absence.

The multiple is set from a published precision/recall sweep, not by taste. On
the demo profile it is 1.8, the F1 maximum. The curve is in
`docs/img/o2_calibration.png`.

## Consequences, measured

Against planted causes, both rules are the weakest in the taxonomy, and the
reasons are structural rather than fixable by tuning.

- **P8 recall is 0.45 on three-day teams and 0.04 on four-day teams.** Shifting
  a four-day pattern leaves it overlapping the original on three of its four
  days, and on a five-day policy the shifted pattern is identical. There is no
  evidence in badge data that separates those cases, and a test asserts the
  five-day case is identical by construction so the claim stays tied to code.
- **O2 recall is 0.49 at precision 0.27** over a 28-day window. This is a
  lagging classifier and 28 days is close to its floor; it should improve
  markedly on the 180-day profile.

Both are reported in the results table rather than hidden, because a taxonomy
that cannot say which of its buckets it is bad at is not a measurement.
