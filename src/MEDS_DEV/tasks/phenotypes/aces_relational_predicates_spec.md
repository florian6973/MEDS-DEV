# Proposal: relational predicates (`during`, `within`) + boolean constraints (`has_any`) for ACES

## Summary

ACES today expresses **monadic predicates** (properties of one event) and **counting quantifiers over
temporal windows** (`has: P (a,b)` = "the count of P in the window is in `[a,b]`"). It cannot express a
predicate defined by a **relation between events** (e.g. "an AMI that occurs *during* an inpatient
stay"), nor a **disjunction of constraints** within a window. This proposal adds, minimally:

1. **Relational predicates** — a new predicate kind that defines a predicate from a temporal relation
   to other events, materialized **once** as an ordinary (monadic) predicate column:
   - **`during`** — a point inside a **pair-interval** reconstructed from open/close events
     (`AMI during [ip_er_start … ip_er_end]`),
   - **`within`** — a point inside a **fixed offset window of another event**
     (`CS4 within [−365d, +365d] of smoking`).
2. **`has_any`** — a **disjunction** of constraint blocks within a window (the OR that today's
   conjunctive `has` cannot express).

Together these let a config express the AMI benchmark's `#2` (encounter outcome) and `#6` (correlated
CS4 entry) **declaratively**, with no preprocessing side-script.

## Motivation (the expressiveness ladder)

- **Predicates** are monadic, quantifier-free: `P(e)` (incl. `expr: and/or/not` over the *same* event).
- **Windows** are counting quantifiers with a **monadic body**: `∃/∀ e ∈ Window(t): P(e)`.
- **`#2` needs** `∃ AMI a, ∃ stay s : a ∈ year ∧ during(a, s)` — two existentials and a **binary
  relation**, which cannot sit inside a monadic window body.
- **The fix is to Skolemize the inner `∃` into a monadic predicate**: `ami_enc(a) := ∃ s: during(a,s)`,
  computed once → then the window's `∃` handles the rest. That is exactly a relational predicate.
- **`#6` additionally needs** the top-level constraint to be a **disjunction** (`CS1∪CS3 ∨ ≥2·CS4 ∨
  CS4-near-smoking`) — that is `has_any`.

`during`/`within` are both "point inside an interval anchored to other event(s)" (Allen *during* for a
point), but the interval is sourced differently — **a reconstructed open/close pair** vs **a fixed
offset around one event** — so they are separate constructors.

## Feature 1 — relational predicates

A new predicate kind, alongside `code:` (plain) and `expr:` (boolean). Each yields a **0/1 predicate
column per event**, usable anywhere a predicate is (any window's `has`, `label`, triggers, `expr`).

### 1a. `during` — point in a reconstructed pair-interval

```yaml
predicates:
  ami_enc:
    during:
      event:  ami            # restrict THIS predicate's events
      opens:  ip_er_start    # interval-open boundary predicate
      closes: ip_er_end      # interval-close boundary predicate
      closed: both           # inclusivity of [opens, closes]: both | left | right | none  (default both)
```

Semantics: `ami_enc(e)` is true iff `event(e)` holds **and** `e.time` lies inside an open
`[opens … closes]` interval, reconstructed via the **net-open count** —
`admitted(τ) = #{opens with time ≤ τ} − #{closes with time < τ} > 0` (the `≤`/`<` edges follow
`closed`). This is exact for **non-overlapping** stays; for nested/overlapping stays it is the
"currently admitted" semantics, which must be **documented** (a stated, deterministic rule — *not*
threaded through window inclusivity).

### 1b. `within` — point in a fixed offset window of another event

```yaml
predicates:
  cs4_smk:
    within:
      event:  cs4            # restrict THIS predicate's events
      of:     smoking        # the corroborating predicate
      before: 365d           # look this far back  from each `event` occurrence
      after:  365d           # ... and this far forward   (either may be 0 for one-sided)
```

Semantics: `cs4_smk(e)` is true iff `event(e)` holds **and** `∃ σ: of(σ) ∧ σ ∈ [e.time − before, e.time
+ after]`. No interval reconstruction — it is a temporal proximity (range) join, so it has **no
overlap/pairing ambiguity** (this is the key difference from `during`).

### Shared properties

- The output is a **plain monadic predicate** — so `has: ami_enc (1, None)` (∃ encounter-AMI in a
  window), `has: ami_enc (None, 0)` (no prior encounter-AMI), `label: ami_enc`, etc. all just work.
- This single mechanism fixes `#2`'s **target**, its **no-prior-AMI**, *and* the **multi-AMI** behavior
  uniformly (the count is over *all* encounter-AMIs in the window, no single anchor).

## Feature 2 — `has_any` (disjunctive window constraint)

Today a window's `has` is a **conjunction** — every listed constraint must hold. Add `has_any`: a
**disjunction** of constraint blocks, where each block is itself an ordinary (conjunctive) `has`:

```yaml
at_risk_entry:
  start: null
  end: trigger
  has_any:                          # OR of blocks; each block is an AND of leaf constraints
    - { cs13:    (1, None) }        # CS1 ∪ CS3
    - { cs4:     (2, None) }        # ≥2 CS4  (counting quantifier — already legal today)
    - { cs4_smk: (1, None) }        # CS4 corroborated by smoking
```

The window is satisfied iff **at least one** block holds. `has` (conjunction) is unchanged, and may not
be combined with `has_any` on the same window. This is the single missing connective — the top-level
OR — that `#6` needs; nesting and negation (full boolean trees) are left out of scope for now.

## Companion addition worth doing alongside (principled, small)

- **Let `expr:` reference derived predicates** — then the **complement** is free, e.g.
  `ami_outpatient: expr: and(ami, not(ami_enc))`. No new operator, just allow `expr` over relational
  predicate columns. (Closes "event NOT during an interval" without a dedicated `not_during`.)

## Out of scope (the natural endgame, deliberately deferred)

- **Full boolean constraint trees** (nesting + `not`) — `has_any` adds the one connective `#6` needs
  (top-level OR); arbitrary `all`/`any`/`not` nesting is a later generalization.
- **`by:` explicit interval pairing** — using a data link field (e.g. `visit_occurrence_id`) to pair
  open/close exactly and skip reconstruction. Deferred; `during` uses the net-open rule for now.
- **Interval-valued predicates + the full Allen algebra** (`overlaps`, `contains`, `meets`, `starts`,
  `finishes`, `equals`, …) for **interval–interval** relations (e.g. "two overlapping stays"). `during`
  and `within` give *point*-in-interval, which is the 80/20 for phenotyping; first-class interval
  predicates are a larger, separate lift. Mark as future.
- **Numeric/value aggregations** (`max value > x`, `sum`, …) — a different axis (value predicates), not
  part of the relational family.
- **Cross-window disjunction** ("qualify via window-tree A *or* tree B") — `has_any` covers
  within-window OR, which is what `#6` needs; alternative *trees* would be a bigger feature.

## Worked examples

**`#2` — AMI benchmark outcome (encounter), faithfully:**
```yaml
predicates:
  ami_enc: { during: { event: ami, opens: ip_er_start, closes: ip_er_end, closed: both } }
windows:
  no_prior_ami: { start: null, end: trigger, has: { ami_enc: (None, 0) } }   # no prior ENCOUNTER-AMI
  target:       { start: trigger, end: start + 365d, has: { ami_enc: (1, None) } }  # encounter-AMI in year
```
Bounded to the year, no anchor, multi-AMI-correct, no out-of-window leakage.

**`#6` — correlated CS4 entry:**
```yaml
predicates:
  cs13:    { expr: or(cs1, cs3) }
  cs4_smk: { within: { event: cs4, of: smoking, before: 365d, after: 365d } }
windows:
  at_risk_entry:
    start: null
    end: trigger
    has_any:
      - { cs13:    (1, None) }
      - { cs4:     (2, None) }
      - { cs4_smk: (1, None) }
```

## Implementation notes

- Relational predicates are computed in the **predicates stage**, before windowing, as extra columns on
  the `(subject, timestamp)` predicate frame — `within` via a range/asof self-join on `of`-events;
  `during` via the net-open cumulative count. After that the existing window engine is unchanged.
- `has_any` is a change in the **constraint-evaluation** layer only (evaluate each block over the
  per-trigger window counts, then OR the results) — windows themselves are unchanged.

## Caveats

- `during` reconstructs intervals from unpaired open/close events, so it needs **one explicit,
  documented overlap rule** (net-open) and a stated `closed` convention. The difference from the
  event-bound-window approach is decisive: the rule is applied **once, deterministically, in plain
  data-frame ops with date `≤`** — not implicitly via per-window ±µs inclusivity.
- `within` has no such ambiguity (pure proximity).

## Backward compatibility

All additive. `during`/`within` are new predicate kinds; `has_any` is a new optional window field
(`has` unchanged). Existing configs are unaffected.
