# Specification: translating OHDSI/ATLAS benchmark phenotypes to ACES/MEDS

> Status: design spec / current thinking. Exemplified end-to-end by the AMI task; intended to
> generalize to the other reAIM-Lab `ehr_foundation_model_benchmark` phenotypes. Companion documents:
> [`aces_relational_predicates_spec.md`](../aces_relational_predicates_spec.md),
> [`aces_windows_pos_spec.md`](../aces_windows_pos_spec.md), [`AMI_TASK_REPORT.md`](../AMI_TASK_REPORT.md),
> [`MAPPING.md`](MAPPING.md), [`PORTING.md`](PORTING.md).

## 1. Purpose & scope

Translate a benchmark cohort defined in **OHDSI/ATLAS + SQL** (concept sets, Circe cohort logic, a
per-visit labeling query) into a **declarative ACES task over MEDS** that is *provably faithful* to
the original — and to make every place it is **not** bit-exact an explicit, named, quantified item
rather than a silent approximation.

Two artifacts result per phenotype: a **task** (dataset-independent windows + relational predicates)
and **per-dataset base predicates** (the concept sets in the dataset's coding scheme).

## 2. The three anchors (the core mental model)

Conflating these is the main translation hazard:

| anchor | what it is | ACES role |
| --- | --- | --- |
| **at-risk index** | first qualifying risk event | look-back window (`has`) |
| **prediction time** | *each* qualifying visit | the `trigger` + `index_timestamp` |
| **case index** | each outcome event | checked in the `target` window → label |

One patient → one at-risk index, **many** prediction times, and the case index sets each visit's
label. The benchmark scores *every* eligible visit, not just cohort entry.

## 3. The expressiveness ladder

ACES (stock) expresses **monadic predicates** + **counting quantifiers over temporal windows**
(`has: P (a,b)` = "count of P in window ∈ [a,b]"). Benchmark phenotypes need two things it cannot:

1. a predicate defined by a **relation between events** ("an AMI *during* an inpatient stay") — two
   existentials and a binary (Allen) relation, which cannot sit inside a monadic window body. **Fix:
   Skolemize the inner ∃ into a monadic predicate** computed once → a *relational predicate*.
2. a **disjunction** of window constraints (`CS1∪CS3 ∨ ≥2·CS4 ∨ CS4-near-smoking`) — the top-level
   OR a conjunctive `has` can't express. **Fix:** `has_any`.

The rungs, in order of expressive power: monadic predicate → counting quantifier → event-anchored
Skolem (`->`/`<-`) → **relational predicate** (`during`/`within`) → **disjunctive constraint**
(`has_any`). The relational predicate is the key lift: it turns "∃ stay containing this event" into a
plain 0/1 column the existing window engine consumes unchanged.

## 4. The language extensions (florian6973/ACES `interval_logic` fork)

| construct | meaning | closes |
| --- | --- | --- |
| **`during`** | point inside a reconstructed open/close pair-interval, via net-open count `#{opens≤τ}−#{closes<τ}>0` (exact for "∃ interval containing the event", any overlap) | encounter outcome (#2), cohort-end-as-trigger (R5) |
| **`within`** | point inside a fixed offset window of another event (proximity join, no pairing ambiguity) | smoking-corroborated CS4 (#6) |
| **`has_any`** | disjunction of `has`-blocks within one window | corroborated at-risk entry (#6) |
| **`windows_pos`** (alt.) | label-defining windows that don't gate the cohort | an alternative encoding of #2 (kept for reference; the `during` route is preferred) |

All are **additive** (new predicate kinds / optional window field); stock configs are unaffected. The
output of a relational predicate is an ordinary monadic predicate, usable anywhere (`has`, `label`,
trigger, `expr`). Full design + semantics in the two `aces_*_spec.md` files.

**Deferred (out of scope for v1, documented in the specs):** `by:` explicit interval pairing via a
link field; the full Allen interval–interval algebra (`overlaps`/`contains`/`meets`/…); value
aggregations; cross-window disjunction. `during`/`within` give *point*-in-interval, the 80/20 for
phenotyping.

## 5. Fidelity taxonomy (how we classify every divergence)

Every difference from the original benchmark is sorted into exactly one bucket. This is the
discipline that turns "≈" into an audited number.

| class | meaning | example (AMI) | action |
| --- | --- | --- | --- |
| **exact** | reproduced to the row | #2 encounter, #9 recent, R4 depth, target | ship |
| **approximation** | the construct is close but not identical to the SQL | R5 cohort-end = "visit in *any* obs period" vs "the *entry's*" | ship + document the edge (multi-obs-period) |
| **deliberate choice** | we intentionally diverge from a questionable SQL artifact | first-AMI vs the SQL's multi-AMI `MAX` quirk (26,539 pts) | ship the cleaner one, quantify the gap |
| **published-vs-live** | the JSON spec and the live ATLAS labels disagree | CS4 corroboration (published ≥2) vs live (≥1) | ship the **live** variant (matches the labels); document the published one |
| **stale** | expressible but 0-effect on the data | CS0 7-day exclusion, obs-membership, smoking arm | omit |
| **data drift** | `$H↔MEDS` ETL concept remap / unmapped drops | the 228 remapped AMIs (recall gap) | irreducible; quantify |

## 6. Validation methodology

**Two independent references**, four exact `(subject, time)` comparisons:

```
   ACES task (MEDS)                         reproduce_benchmark.py (OMOP = the SQL)
        │                                            │
        ├── translation: ACES ≡ reproduce_meds ──────┼──> 0/0   (YAML == hand reference; no logic, no data)
        │                                            │
   reproduce_meds.py (MEDS) ── fidelity ─────────────┘   ~$H↔MEDS data drift (logic held fixed)
        │
        └── logic gap: meds(aces) vs meds(r0)             pure logic/definitional gap (data held fixed)

   reproduce_benchmark.py (default) ── live ── original ATLAS   ~100% (OMOP Python ≡ published cohort)
```

- **translation** isolates the YAML (must be `0/0`); **fidelity** isolates the data; **logic gap**
  isolates the definitional choices; **live** anchors the OMOP reference to ground truth.
- **Time granularity is handled at the source, not by truncation.** OMOP & ATLAS keep the true
  `visit_start_datetime` → compared **exact**. The MEDS ETL drops visit times to midnight → the
  OMOP-vs-MEDS arm is generated `--prediction-time date` (deduped per day). *Truncating a per-visit
  datetime table fans out the join (duplicate keys) — never do it.*
- **Headline metrics** (AMI, shipped task vs benchmark on common subjects): same prevalence (~1.3%),
  **PPV ≈ 100%** (no false positives), **recall ≈ 99%** (misses only the remapped AMIs), with the
  ~1.3% cohort gap being the *deliberate* multi-AMI choice, not error.

## 7. Key AMI design decisions (instantiating §5)

- **Outcome = encounter-AMI** via `during(ami, ip_er_start, ip_er_end)` — exact.
- **No-prior = first-AMI** (`has: ami_encounter (None,0)`) — deliberately drops the SQL's
  multi-AMI `MAX` quirk (keeps visits *between* two AMIs); cleaner, quantified at 26,539 pts.
- **At-risk = CS1∪CS3∪CS4 (≥1)** — the live cohort; published corroboration (≥2 / +smoking)
  documented but not shipped (it diverges from the ATLAS labels by ~290k).
- **Concept sets resolved through `concept_ancestor`**, not the stale zip snapshot; prior-MI
  (`1755008`) exclusion is baked into the resolved code list (not a separate predicate).
- **Widened `condition_or_drug`** to match the SQL's "any cond/drug" (eras + interval-ends).
- **Dropped stale windows** (CS0, obs-membership, smoking) — 0-effect on this data.

## 8. Porting model (see PORTING.md)

The **task is dataset-independent**; only the **9 base predicates** change. Porting =
inventory the scheme → resolve concept sets into it (re-resolved, version-aware: concept_id *or*
source code) → drop windows the dataset can't support (no visit-end → no encounter; no obs-period →
no R4/R5) → smoke-test every predicate (0 matches = wrong scheme) → validate (full harness if an OMOP
source exists, translation-only otherwise).

## 9. Automation roadmap

A 3-command pipeline; ~80% mechanical, ~20% surfaced-for-judgment:

1. **`port_check.py`** (built) — scheme inventory + supportability report + predicate smoke-test.
2. **`gen_predicates.py`** (proposed) — unify `reproduce_benchmark.py:resolve` (concept_id) and
   `build_ami_task.py` (source code) into one `--scheme`-parameterized resolver → emit
   `predicates/<DATASET>/<task>.yaml`.
3. **`run_validation.sh`** (built) — the four-cell validation.

The 20% that stays human: which approximations to accept, vocab-skew coverage calls. The tooling's
job is to *surface* these, not auto-decide them (silent auto-decisions are how the empty-cohort and
remap issues arose).

## 10. Open questions & future work

- **Upstream** `during`/`within`/`has_any` into mainline ACES (the fork is the reference impl).
- **Generalize** to the other 10 benchmark phenotypes — reuse §2–§8; each needs only its predicates +
  a supportability pass.
- **Interval-valued predicates** + full Allen algebra (deferred) — needed for interval–interval
  phenotypes (e.g. overlapping stays), beyond the point-in-interval `during`.
- **Build `gen_predicates.py`** and dry-run the pipeline on a second dataset (MIMIC) to confirm the
  porting model holds end-to-end.
- **Time-of-day in MEDS**: the OMOP-MEDS ETL drops visit times to midnight; recovering them would
  remove the only place we must compare at date granularity.
