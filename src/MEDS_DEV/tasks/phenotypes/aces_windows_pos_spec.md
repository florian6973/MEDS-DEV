# Proposal: `windows_pos` — label-defining windows for ACES

## Summary

Add an optional `windows_pos` block to an ACES task config that **replaces `label`**. Instead of
deriving the label by counting a single predicate in one window, the label is determined by whether a
trigger satisfies an *additional* group of windows:

- a trigger that satisfies the **base** `windows` is **in the cohort** (a row is emitted);
- among those, a trigger that *also* satisfies **`windows_pos`** is **positive (label 1)**; otherwise
  it is **negative (label 0)**.

Crucially, `windows_pos` is **non-gating**: failing it sets the label to 0, it does **not** drop the
trigger. This lets a temporally complex *outcome* condition (e.g. "the AMI occurred during an
inpatient/ER encounter") shape the **label** without pruning the cohort or its negatives.

## Motivation

ACES today conflates two roles in a single window list:

1. **cohort gating** — every window's constraint must hold or the trigger is excluded;
2. **labeling** — exactly one window carries `label: <predicate>`, and the label is the count of that
   predicate in that window (binarized).

This breaks down when the **positive class is defined by a temporal condition that should not gate the
cohort**. The motivating case is the AMI benchmark's encounter restriction (`#2`): an AMI counts as a
*case* only if it falls inside an inpatient/ER visit interval `[visit_start, visit_end]`. Expressing
containment requires event-anchored windows (walk to the AMI, then to the surrounding `//start` /
`//end`). But making those windows **mandatory** is wrong:

- a never-AMI visit has no AMI to anchor on → the window can't be built → the trigger is dropped, so
  **every true negative disappears**;
- a visit whose in-year AMI is *not* in an encounter → containment fails → trigger dropped, when it
  should be kept as **label 0**.

The condition belongs to the **label**, not to cohort membership. `windows_pos` is that separation.

## Relationship to `label` (it's a generalization)

The current `label: P` on a target window `W` is exactly the special case

```yaml
windows_pos:
  _label:
    start: <W.start>
    end: <W.end>
    has: { P: (1, None) }   # >=1 occurrence of P in the window  ->  positive
```

So migrating an existing task is mechanical: move the labeling window into `windows_pos` and turn
`label: P` into `has: { P: (1, None) }`. `windows_pos` then allows *more than one* window and full
event-anchored structure, which a single `label` predicate cannot express.

## Config schema

```yaml
trigger: <predicate>

windows:            # BASE windows — cohort gating (unchanged semantics)
  <name>: { start, end, start_inclusive, end_inclusive, index_timestamp?, has? }
  ...

windows_pos:        # NEW — positive-label-defining windows (non-gating)
  <name>: { start, end, start_inclusive, end_inclusive, has? }
  ...

windows_neg:        # NEW (optional) — explicit negative-label-defining windows (non-gating)
  <name>: { start, end, start_inclusive, end_inclusive, has? }
  ...
```

Rules:
- `windows_pos` is **optional**. When absent, ACES behaves exactly as today (label if a `label` field
  is present, otherwise an unlabeled cohort). It is **mutually exclusive** with `label` — a config that
  wants a label uses one mechanism or the other (see Backward compatibility for the full matrix).
- `windows_neg` is **optional** and may only appear alongside `windows_pos`. When omitted, the
  negative class is the implicit complement (`E \ P`). When present, it defines the negative class
  **explicitly**, and eligible triggers matching *neither* are **dropped** (see Semantics).
- `windows_pos` / `windows_neg` windows may reference the `trigger` and any **base** window's
  endpoints (same referencing rules as base windows), plus self-references. They may also reference
  each other *within their own group*.
- `index_timestamp` must live on a **base** window (the prediction time is a property of the cohort,
  not the label). `windows_pos` / `windows_neg` windows may **not** set `index_timestamp`.
- Each group is **conjunctive**: all its windows must be satisfiable for that class. (Disjunctive /
  multi-class is out of scope — see below.)

## Semantics

Let `T` be the triggers, `Wb` the base windows, `Wp` = `windows_pos`, `Wn` = `windows_neg`.

- **Eligible / cohort** `E = { t ∈ T : Wb all satisfiable at t }`.
- **Positive** `P = { t ∈ E : Wp all satisfiable at t (given Wb) }`. `P ⊆ E`.
- **Negative** `N`:
  - **`windows_neg` omitted** → `N = E \ P` (implicit complement). One row per `t ∈ E`;
    `label(t) = 1 if t ∈ P else 0`.
  - **`windows_neg` present** → `N = { t ∈ E : Wn all satisfiable }`. Output rows only for
    `P ∪ N`: `label = 1` on `P`, `0` on `N`. Eligible triggers matching **neither**
    (`E \ (P ∪ N)`) are **dropped** (this is the "exclude the ambiguous middle" case/control pattern).
  - **Conflict** `P ∩ N` (a trigger matches both) is ambiguous → **dropped and counted in a warning**
    (don't silently pick a side); a well-formed config should make `Wp`/`Wn` mutually exclusive.
- **index_timestamp / prediction_time**: from the base window carrying `index_timestamp` — identical
  for negatives and positives.

In the requester's words: *process the normal windows for the negatives (the eligible set), and the
aggregated normal + `windows_pos` for the positives* — with `windows_neg` letting the negative class
be stated explicitly instead of "everything not positive."

## Implementation

### Reference implementation (two passes, ~no engine surgery)

Because `P ⊆ E` and both are ordinary ACES extractions, the feature can be implemented by **calling the
existing extractor twice** and joining on the (subject, prediction_time) key:

```text
E = extract(config with windows = Wb)                 # rows; label := 0
P = extract(config with windows = Wb ∪ Wp)            # positive subset (same trigger/index)
label[(subject, prediction_time)] = 1 for rows in P   # else 0
output = E with label
```

`index_timestamp` is on a base window, so the join key is stable across the passes. This requires
only: (1) schema acceptance of `windows_pos`/`windows_neg`, (2) the multi-pass driver + join,
(3) validation that the label groups carry no `index_timestamp`. No change to the core
window-evaluation logic. With `windows_neg`, run a third pass `Wb ∪ Wn` → `N` and apply the
`P`/`N`/drop/conflict rules above.

### Optimization — restart from the eligible subset (not all triggers)

The naive passes re-extract `Wb ∪ Wp` (and `Wb ∪ Wn`) over **all** triggers `T`, recomputing the base
windows every time. Instead, **run the base pass once to get `E`, then restart the positive/negative
passes from `E`** — restricting triggers to the already-eligible set and evaluating *only* `Wp` / `Wn`
(the base-window constraints are already known to hold, and their resolved boundaries can be cached
from pass 1 and reused as reference points). So:

```text
E, base_ctx = extract(Wb)                      # pass 1: eligibility + cached window resolutions
P = evaluate(Wp) over triggers ∈ E using base_ctx   # only the pos windows, only on E
N = E \ P            (or evaluate(Wn) over E, if windows_neg given)
```

This makes the extra cost proportional to `|E| × |Wp|` rather than `|T| × |Wb ∪ Wp|`, and collapses
toward the single-pass cost while keeping the engine change small (the base extractor is unchanged; the
label groups run as a follow-on over its surviving triggers).

## Worked example — AMI encounter restriction (`#2`)

Base windows (unchanged, validated floor): trigger on every visit; `at_risk_entry`
(`has: risk_entry`); `no_prior_ami` (`has: ami (None,0)` — raw AMI, per the chosen "first-AMI"
semantics); `sufficient_history` (2-yr depth, `has: clinical_event`).

```yaml
windows_pos:
  # the outcome AMI within the prediction year, sitting inside an inpatient/ER interval
  ami_in_year:
    start: trigger
    end: start + 365d
    start_inclusive: True
    end_inclusive: True
    has: { ami: (1, None) }            # there is an AMI in the year
  ami_enc_right:
    start: trigger -> ami              # anchor on that AMI
    end: start -> ip_er_end            # next inpatient/ER discharge
    start_inclusive: True
    end_inclusive: True
    has: { ip_er_start: (None, 0) }    # no NEW admission between the AMI and that discharge
  ami_enc_left:
    start: end <- ip_er_start          # previous inpatient/ER admission
    end: trigger -> ami                # the same AMI
    start_inclusive: True
    end_inclusive: True
    has: { ip_er_end: (None, 0) }      # no discharge between that admission and the AMI
```

A visit with no AMI, or an AMI not contained in an inpatient/ER interval, fails `windows_pos` → **label
0, still emitted**. A visit whose in-year AMI is inside an encounter → **label 1**. The at-risk cohort
(negatives included) is byte-identical to the floor.

## Edge cases & rules

- **No base `index_timestamp`** → error (unchanged from today).
- **`label` and `windows_pos` both present** → config error.
- **`windows_pos` references a predicate/window absent from base** → allowed (it's its own subtree).
- **Empty `windows_pos`** → every eligible trigger is positive (degenerate; warn).
- **Counts vs boolean**: label is boolean (`t ∈ P`). If a future need arises for graded labels, see
  multi-class below.

## Out of scope (future)

- **Disjunctive / multi-class labels.** Each group is all-must-hold. Multi-class would generalize
  `windows_pos`/`windows_neg` to `windows_<classname>` groups with a precedence/conflict rule;
  OR-of-conditions would be alternative groups within a class. Deliberately deferred to keep v1 a
  clean binary generalization of `label` (with `windows_neg` as the one explicit second class).

## Known limitations inherited by *uses* of `windows_pos` (not the feature itself)

When `windows_pos` expresses containment via event-anchored windows on **date-granularity** data:
- **Intra-timestamp ordering.** `//start`, the AMI, and `//end` on the same day share a timestamp;
  containment then depends on es-aces's ordering of same-timestamp events. (A pre-derived
  `AMI_ENC` event sidesteps this by computing `visit_start ≤ AMI ≤ visit_end` once in the data layer.)
- **Multi-event selection.** `trigger -> ami` anchors on the *first* AMI after the trigger; a later
  in-year encounter-AMI could be missed. These are properties of the *encoding*, not of `windows_pos`.

## Backward compatibility

In ACES today, **`label` and `index_timestamp` are already optional** — each "may be specified in
exactly one defined window," and if absent the output simply omits that column (an unlabeled cohort of
window summaries). `windows_pos` joins them as another **optional** top-level field, so it cannot break
any existing config. Labeling behavior is selected purely by which fields are present:

| `label` | `windows_pos` | behavior |
|---------|---------------|----------|
| absent  | absent        | **existing default** — no label column (cohort / feature extraction) |
| present | absent        | **existing** — label = count of the predicate in its window |
| absent  | present       | **new** — label from `windows_pos` (+ optional `windows_neg`) membership |
| present | present       | **config error** (ambiguous) |

So `windows_pos` is purely additive: configs with `label`, or with neither, behave exactly as before.
`index_timestamp` is orthogonal and unchanged — still optional, still on a base window.

Docs confirming the existing optionality: ACES
[technical details](https://eventstreamaces.readthedocs.io/en/latest/technical.html) and
[usage guide](https://eventstreamaces.readthedocs.io/en/latest/usage.html).
