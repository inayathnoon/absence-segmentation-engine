# 3. The behavioural lever is reported separately, not added to capacity

**Status:** accepted · **Date:** 2025-06-29

## Context

The brief defines recoverable capacity as the portion attributable to O5, O6
and O7, "plus a policy-configurable fraction of O1 and O4".

That addition double counts, in the flattering direction.

## The argument

Recoverable capacity here is `allocated − required`, where `required` is the
90th percentile of **observed desk demand** plus a buffer.

A no-show consumes no desk. Their absence is therefore already inside the
demand distribution that percentile is taken over: it has already pushed the
percentile down, and the desk they did not use has already been counted as
recoverable, once. Adding a fraction of it again counts the same desk twice.

The same is true of a booked-and-unused desk. The booking is a claim on a
desk, not an occupation of one, and demand is measured from taps.

## Decision

Recoverable capacity is structural only: `allocated − required`, floored at
zero per unit.

O1 and O4 are computed, kept, and reported as a **behavioural opportunity**
with the policy fractions visible - and explicitly not added to the capacity
number. `capacity/model.py` returns them in a separate frame, and a test
asserts they never appear in the units frame.

## Why they are still worth reporting

They are a genuine lever, just a different one. Converting a chronic no-show
from an allocated desk to the sharing pool changes *who holds* a desk rather
than how many exist, and the saving is realised only if the behaviour actually
changes. That is a management action with an uncertain payoff, and presenting
it inside a capacity figure - which is a lease decision with a certain one -
would be mixing two very different kinds of number.

## Consequences

The headline saving in this repo is smaller than the brief's formula would
produce. That is the point.
