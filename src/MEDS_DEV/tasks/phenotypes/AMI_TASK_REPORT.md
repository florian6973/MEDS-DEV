# AMI ACES Task — Derivation & Fidelity Report

This documents how [`ami.yaml`](ami.yaml) is derived from the reAIM-Lab
[`ehr_foundation_model_benchmark`](https://github.com/reAIM-Lab/ehr_foundation_model_benchmark/tree/main/src/ehr_foundation_model_benchmark/phenotypes)
AMI cohorts, and **every place the ACES translation is not bit-exact** with the original
benchmark. It is a first draft for review.

## Source artifacts

The benchmark defines AMI in **three** pieces; the prediction task is the combination of all three:

| Artifact | Role |
| --- | --- |
| `ami_case.json` (ATLAS) | **outcome** cohort — an Acute MI *during an inpatient/ER visit* |
| `ami_at_risk.json` (ATLAS) | **eligibility** cohort — first ischemic-heart-disease / differential-for-AMI / AMI-embedding diagnosis, no AMI in prior 7d |
| `case_control_query.sql` + `cohort_pos_neg_query.sql` | **prediction task** — per-visit labels combining the two cohorts |
| `cohort_concept_sets/*.zip` | ATLAS-resolved code lists for each concept set (descendants + exclusions baked in) |

## Three anchors (the key mental model)

These are **distinct** events; conflating them is the main translation hazard:

| Anchor | What it is | ACES role |
| --- | --- | --- |
| **At-risk index** | first risk dx (IHD / differential / CS4-correlated), no AMI in 7d | opens the eligibility window → look-back `has` constraint |
| **Prediction time** | *each qualifying visit* in that window | the `trigger` + `index_timestamp` |
| **Case index** | each Acute-MI-during-inpatient/ER | checked in the `target` window → the label |

One patient ⇒ **one** at-risk index, **many** prediction times (one per visit), and the case index
determines each visit's label. The benchmark scores *every* eligible visit, not just cohort entry.

## SQL → ACES mapping (what `ami.yaml` encodes)

| Benchmark logic | ACES element |
| --- | --- |
| `prediction_time = visit_start` | `trigger: inpatient_or_er_visit`, `recent_activity.index_timestamp: end` |
| at-risk eligibility (entered the population) | `at_risk_entry` window, `has: risk_entry (1, None)` |
| `case_start < visit` exclusion (+ "no AMI in 7d") | `no_prior_ami` window, `has: ami (None, 0)` |
| `≥1 condition/drug in prior 2yr` | `recent_activity` window, `has: _ANY_EVENT (1, None)` |
| `case_start ≤ visit + 1yr` ⇒ positive | `target` window `(0, +365d]`, `label: ami` |

## Per-item decisions

| # | Benchmark logic | Decision | Why |
| --- | --- | --- | --- |
| 1 | Index/outcome = Acute MI (Case CS3) | **Kept** — `ami` predicate, codes inlined | core outcome |
| 2 | AMI must occur *during* inpatient/ER (Case CS2) | **Approximated** (see below) | ACES can't bind two events by interval |
| 3 | End strategy (index + 7d) | **Dropped** | unused by the labeling SQL |
| 4 | Collapse 180d (case ERA) | **Dropped** | target only needs AMI *presence* in the year |
| 5 | At-risk index = "First" | **Re-mapped** — trigger is the visit, entry → look-back gate | prediction is per-visit, not at entry |
| 6 | Correlated CS4 (≥2 prior **or** +smoking) | **CS4 dropped** — `risk_entry` = CS1∪CS3 (see below) | ACES can't OR count-thresholds; uncorroborated CS4 → ~13× over-generation |
| 7 | No AMI within 7 days (at-risk inclusion) | **Subsumed** by `no_prior_ami` | "no AMI before visit" is stricter & covers it |
| 8 | Collapse pad 0 (risk ERA) | **Dropped** | negligible |
| 9 | ≥1 condition/drug in prior 2yr | **Loosened** — `recent_activity` with `_ANY_EVENT` (see below) | avoids dataset-specific condition/drug predicates |
| 10 | ≥2 years of history (obs-period) | **Dropped** (see below) | weakest-fidelity approximation; MEDS has no obs-period |
| — | Trigger granularity (all visits) | **Narrowed** to inpatient/ER (see below) | per decision; chosen `???` predicates are `er_visit`/`inpatient_visit` |

## Fidelity gaps (read before using results)

These cannot be expressed exactly in ACES (single trigger, window-anchored, no inter-event
"during", no OR across count-thresholds). Each shifts the cohort relative to the benchmark:

**#2 — outcome not restricted to inpatient/ER.** The benchmark counts an AMI only if its code
falls *inside* an inpatient/ER encounter (a true acute event vs. an outpatient follow-up,
problem-list carry-forward, or rule-out mention). ACES windows anchor to the trigger and can only
*count* events in the target year — they can't test that the future AMI sits inside a specific
encounter interval. **`ami` therefore defaults to the raw Acute-MI concept set.** On hospital-only
datasets (MIMIC-IV) this is ~equivalent since diagnoses there are inpatient; on claims/OMOP data
it **over-counts positives**. A dataset MAY override `ami` to an inpatient/ER-restricted predicate.

**#6 — at-risk entry: CS4 dropped.** ATLAS enters the at-risk cohort on the *first* of `IHD (CS3)`,
`differential (CS1)`, or `CS4 ("ten closest embeddings") WITH corroboration ((≥2 prior) OR (+smoking))`.
ACES `has` is conjunctive and can't OR count-thresholds, so the corroboration is inexpressible.
Admitting CS4 at ≥1 (the first attempt) was **catastrophic**: since
`min(CS1,CS3,CS4_uncorroborated) ≤ min(CS1,CS3,CS4_corroborated)`, a single uncorroborated CS4 code
opened the at-risk window a **median ~1.6 yr too early** (p90 7.7 yr; 58% of subjects >1 yr early),
inflating prediction points **~13×** (70 vs 5 per subject) on CUMC — validated against the benchmark.
So `risk_entry` is now **CS1 ∪ CS3 only** (differential + ischemic heart disease); CS4 is excluded.
Trade-off: patients whose *only* at-risk qualifier is a corroborated CS4 (no IHD/differential ever)
are missed — expected to be few, since CS4 overlaps IHD/differential heavily.

**#9 — recent-activity gate loosened to `_ANY_EVENT`.** The SQL requires ≥1 `condition_occurrence`
**or** `drug_exposure` in the prior 2yr. We use ACES's built-in `_ANY_EVENT` (any record) instead,
so a patient whose only events in the window are labs/measurements/visits still passes. This is a
**looser** "non-empty history" gate, chosen to avoid two dataset-specific condition/drug predicates.
To restore exact fidelity, define `condition`/`drug` predicates per dataset and use
`condition_or_drug: or(condition, drug)` in the window instead.

**#1 — trigger narrowed to inpatient/ER visits.** The SQL triggers on **all** `visit_occurrence`
rows (any visit type). We trigger on `inpatient_or_er_visit = or(er_visit, inpatient_visit)` instead.
On hospital-only datasets (MIMIC-IV, which has no outpatient encounters) this **coincides** with
"all visits." On datasets with outpatient visits it is a **deviation**: outpatient visits are no
longer prediction points, so the sample set is smaller and shifted toward acute encounters. To
restore "all visits," supply a single `visit` predicate and trigger on it.

**#10 — ≥2yr history dropped.** The SQL requires `visit ≥ observation_period_start + 2yr`
(continuous coverage). MEDS has **no observation-period concept** — only events — so the only proxy
is "an event exists ≥2yr ago," which (a) passes patients with one stale event and a coverage gap,
and (b) drops patients enrolled-but-quiet. We dropped this gate entirely; `recent_activity` still
guarantees real data in the last 2yr. Net effect: a **slightly larger, noisier** cohort at the
2-year boundary. If a dataset emits an observation-period marker, re-add it as a window.

**Outcome = "first AMI."** `no_prior_ami` (no AMI before the visit) + `target` (AMI in next year)
together = first-AMI-after-prediction, matching the SQL's `case_start < visit` exclusion. Collapse
settings (#4/#8) are therefore irrelevant. AMI exactly at the trigger is counted positive
(`no_prior_ami` end-exclusive, `target` start-inclusive), matching the SQL boundary.

## Dataset-specific predicates (`???`)

These vary by MEDS ETL and must be supplied per dataset (or in the dataset `predicates.yaml`):

- `er_visit` — an emergency-room visit start.
- `inpatient_visit` — an inpatient (hospital) visit start.
  Together (`inpatient_or_er_visit = or(er_visit, inpatient_visit)`) they **define the prediction
  points** (the trigger). For MIMIC-IV: `er_visit → ED_REGISTRATION//.*`,
  `inpatient_visit → HOSPITAL_ADMISSION//.*` (both already in
  [`datasets/MIMIC-IV/predicates.yaml`](../../datasets/MIMIC-IV/predicates.yaml) under the names
  `ED_registration` / `hospital_admission`).

**Concept-set codes are dataset-specific in practice.** The task inlines `ami`/`risk_entry` in
ATLAS `VOCAB//CODE` form (e.g. `ICD10CM//I21.19`), but **MEDS datasets encode codes differently**,
so each dataset must **override** these predicates in its `predicates.yaml` (ACES merges dataset
predicates *over* task ones). Example: MIMIC-IV MEDS writes diagnoses as
`DIAGNOSIS//ICD//{9,10}//<code-without-dots>` (e.g. `DIAGNOSIS//ICD//10//I2119`), and has no
SNOMED/CIEL codes at all — so the ATLAS-format task codes match **nothing**, and a first run produced
an empty cohort (every trigger dropped at `at_risk_entry` because `risk_entry` matched zero events).
The MIMIC override is generated with `build_ami_task.py --emit dataset-predicates --format mimic`
(keeps ICD-9/10 only, strips dots → 65 `ami` + 1624 `risk_entry` codes) and appended to
[`datasets/MIMIC-IV/predicates.yaml`](../../datasets/MIMIC-IV/predicates.yaml).

The recent-activity gate uses the built-in `_ANY_EVENT` (no predicate to define). The concept-set
predicates `ami` and `risk_entry` in the **task** are **ATLAS-format** (standard + source
`VOCABULARY//CODE` across SNOMED/ICD9CM/ICD10CM/…), so they are inlined directly in the task. This
is a deliberate departure from the usual MEDS-DEV `???`-per-dataset pattern, because the codes come
from fixed ATLAS concept sets, not from a dataset's coding choices. (Open question for review:
inline vs. companion predicate file vs. dataset `???`.)

## Regenerating

```bash
python build_ami_task.py \
  --case-zip "<repo>/cohort_concept_sets/AMI Case.zip" \
  --risk-zip "<repo>/cohort_concept_sets/AMI at Risk.zip" \
  --output ami.yaml
```

`ami` = AMI Case CS3 (422 codes); `risk_entry` = AMI at Risk CS1 ∪ CS3 ∪ CS4 (6311 codes). Code
lists are resolved from the ATLAS zips per the contract in
[`resolve_predicates.py`](resolve_predicates.py).

## ACES validation

The task **loads cleanly** under `aces.config.TaskExtractorConfig` (es-aces 0.7.3), with the `???`
predicates stubbed to dummy codes: trigger `inpatient_or_er_visit`, windows
`at_risk_entry / no_prior_ami / recent_activity / target`, label window `target`, index_timestamp
window `recent_activity`. One ACES rule was enforced during this check — *exactly one* window
endpoint may reference the other — so `recent_activity` is `start: end - 730d` (not `trigger - 730d`)
and `target` is `end: start + 365d` (not `trigger + 365d`). This validates window/predicate/`expr`
syntax only, not extraction against real data.

**`code` list format (caught only at evaluation):** a code *list* must be written
`code: { any: [...] }`, never a bare `code: [...]`. ACES compiles `any` to
`pl.col("code").is_in(...)`; a bare list falls through to `pl.col("code") == <list>` and crashes
at `.collect()` with `cannot cast List type (inner: 'String', to: 'String')`. `TaskExtractorConfig.load`
does **not** catch this (it doesn't evaluate the expression) — only a real/synthetic data run does.
The generator emits the `any:` wrapper; we verify by evaluating each predicate against a tiny MEDS
frame.

**`code` list format (caught only at evaluation):** a code *list* must be written
`code: { any: [...] }`, never a bare `code: [...]`. ACES compiles `any` to
`pl.col("code").is_in(...)`; a bare list falls through to `pl.col("code") == <list>` and crashes
at `.collect()` with `cannot cast List type (inner: 'String', to: 'String')`. `TaskExtractorConfig.load`
does **not** catch this (it doesn't evaluate the expression) — only a real/synthetic data run does.
The generator emits the `any:` wrapper; we verify by evaluating each predicate against a tiny MEDS
frame.

## Open items for next iteration

- Run full extraction against a real MIMIC-IV MEDS shard (syntax is validated; data run is not).
- Decide the inline-codes convention (above).
- Optional faithful refinements: inpatient/ER `ami` override (#2), exact condition/drug gate (#9),
  obs-period gate (#10), or "all visits" trigger (#1).

---

# Like-for-like validation on OMOP-MEDS (concept-id build)

> Added after the end-to-end validation campaign. The sections above describe the **shipped**
> `ami.yaml` (inpatient/ER trigger, looser gates). This section describes a separate experiment that
> proves the **ACES logic itself is exact** by removing every confound. Some earlier per-item notes
> (e.g. CS4 in/out, the #9/#10 approximations) describe the shipped task and should be reconciled
> against the current `ami.yaml` on the next edit — they are not re-derived here.

## Why a dedicated build

Comparing ACES on the CUMC `v3_mapped` MEDS against the Python reproduction conflated **ACES logic**
with **build/code-scheme differences** (`v3_mapped` is built by a different ETL than the OMOP the
Python reads). To isolate ACES, we built a clean MEDS **directly from the same harmonized OMOP
(`$H`)** with [OMOP-MEDS](https://pypi.org/project/OMOP-MEDS/): codes are
`<vocab>//<concept_id>//start|end`, so ACES matches the **same standard `concept_id`s** the Python
matches — the code-vs-concept divergence disappears.

A dedicated **floor task** (generated by `gen_omeds_task.py`, not the shipped `ami.yaml`) mirrors the
ladder's R0 exactly: trigger on **every visit** (`any_visit`), `at_risk_entry` (CS1∪CS3∪CS4),
`no_prior_ami`, a 2-yr `sufficient_history` depth gate anchored at the first `clinical_event`, and a
365-day `ami` `target`. Predicates use a vocab-agnostic concept-id regex
(`^[^/]+//(<ids>)//start$`) for `risk_entry`/`ami` and a vocab regex for `any_visit`/`clinical_event`.

## Result (full tuning split: 72,939 subjects, 1.96M points)

```
R0 vs ACES: shared 72,109 / onlyA 830 / onlyB 0
            pts exact 1,901,069 / onlyA 64,406 / onlyB 0 | agree 100.000%
            confusion: A1B1 21,249  A1B0 0  A0B1 0  A0B0 1,879,820
```

- **`onlyB 0`** — ACES never produces a point outside the benchmark floor (strict subset).
- **100% label agreement on 1.9M matched points; positives exact** (no `A1B0`/`A0B1`).
- ⇒ **ACES expresses the benchmark floor exactly** on a like-for-like OMOP. (On a 20-subject build
  the match is literally `96/96 pts, 0/0`.)

## The 3.3% `onlyA`, fully decomposed (0 unexplained)

The 830 `onlyA` subjects split into two mechanisms, both data/benchmark properties — **not ACES bugs**:

- **458 — depth divergence (`first_event` proxy on denser source).** R0's depth anchor is
  `min(condition, drug, visit)` over **raw `$H`**, including **unmapped (`concept_id = 0`) drugs the
  OMOP→MEDS ETL drops** (verified: a `$H` drug precedes MEDS's first `clinical_event` for ~462 of
  these subjects; median gap ~1,910 days). MEDS's first clinical event is therefore later, so ACES's
  2-yr gate is stricter than R0's. Artifact of the *first-event proxy* reading raw OMOP vs sparser
  MEDS; does not arise at the true benchmark anchor (`obs_start`).

- **380 — multi-AMI exclusion semantics.** The benchmark SQL drops `(visit, case)` pairs where
  `case_start < visit` and then takes `MAX(boolean_value)` over survivors, so a visit **between two
  AMIs survives** (the later AMI rescues it). ACES's `no_prior_ami` strictly excludes any visit after
  any AMI. Verified: all 380 have a `$H` AMI-set condition before the kept visit (i.e. a later AMI
  rescues it in the SQL). Only multi-AMI patients differ; the SQL's behavior there is arguably a quirk.

## Full-benchmark irreducible gap (R6)

```
R6 vs ACES: agree 99.774% | confusion A1B1 16,957  A1B0 0  A0B1 4,246  A0B0 1,858,126
```

The 4,246 `A0B1` are the **encounter restriction (#2)**: the benchmark requires the AMI to occur
during an inpatient/ER encounter; ACES cannot bind a future event to an encounter interval.
Together with observation-period **membership** (R2) and the observation-period **depth bound**
(R4/R5, approximated by first-clinical-event), these remain the documented irreducible
approximations — they need `observation_period` / visit intervals MEDS does not carry.

## Reproducing the build (OMOP-MEDS config fixes for harmonized CUMC `$H`)

Two source-specific fixes were required before visits would emit (in `$H`, `visit_source_concept_id`
and the visit *datetimes* are null):

1. **`OMOP_MEDS/configs/pre_MEDS.yaml`**, `visit_occurrence` (5.3 and 5.4):
   `reference_cols: ["visit_source_concept_id"]` → `["visit_concept_id", "visit_source_concept_id"]`
   — `visit_source_concept_id` is null for all 58.6M visits, so the preferred-concept join (hence the
   code) was null and every visit was dropped.
2. **`OMOP_MEDS/configs/event_configs.yaml`**, `visit`/`visit_end`:
   `time: col(visit_start_datetime)` / `col(visit_end_datetime)` →
   `col(visit_start_date)` / `col(visit_end_date)` — the harmonized OMOP nulls the visit *datetimes*
   (dates are populated), and a null event time is dropped.

Because the build times visits at `visit_start_date` (midnight), the Python reproduction must force
the visit time to `visit_start_date` for an exact-second compare (raw `$H` visit datetimes otherwise
never match MEDS midnight). ACES (`es-aces`) is run from an isolated venv pinned to
`es-aces==0.6.1` + `meds==0.3.3` (the ETL env keeps an older `meds` without `LabelSchema` for
OMOP-MEDS / MEDS_transforms compatibility).

## Bottom line

- Python reproduction ≡ ATLAS: **100%**.
- ACES floor ≡ benchmark floor on like-for-like OMOP: **exact** (labels 100%, positives exact,
  `onlyB 0`).
- Residual `onlyA` (3.3%) is **entirely** OMOP→MEDS ETL density (unmapped early events) + a multi-AMI
  SQL quirk — not ACES error.
- Irreducible full-benchmark gaps: **#2 encounter**, **observation-period membership**, and the
  **observation-period depth bound** (approximated). *(Superseded below — these are now closed.)*

---

# Full-benchmark validation with ACES relational predicates (`interval_logic`)

> Supersedes the "irreducible gaps" line above. The three gaps it named (#2 encounter,
> obs-membership, obs-depth/cohort-end) were closed by adding three principled, additive constructs
> to ACES — specified in [`aces_relational_predicates_spec.md`](aces_relational_predicates_spec.md)
> and [`aces_windows_pos_spec.md`](aces_windows_pos_spec.md), implemented on the
> [`interval_logic`](https://github.com/florian6973/ACES/tree/interval_logic) fork. Every rung below
> was validated **ACES-YAML ≡ MEDS-Python = `0/0`** (exact translation, to the row), then the
> MEDS-Python reference was made **byte-faithful to the benchmark SQL** (`reproduce_benchmark.py` /
> `repro_date.py`) to attribute the remaining `$H↔MEDS` residual exactly.

## The three new ACES constructs (what made the benchmark expressible)

- **`during`** — a relational predicate: a point inside a reconstructed open/close pair-interval, via
  the **net-open count** `admitted(τ)=#{opens ≤ τ}−#{closes < τ}` (closed-`both`), which is *exact*
  for "event inside ≥1 interval" regardless of overlap/nesting. Expresses **#2** (`ami_encounter =
  during(ami, ip_er_start, ip_er_end)`) — the encounter restriction — uniformly for the **target**,
  **no-prior-AMI**, and **multi-AMI** counting. Also expresses **cohort-end** as a trigger gate
  (`visit_in_obs = during(any_visit, obs_start, obs_end, closed=left)`).
- **`has_any`** — a disjunctive window constraint (OR of `has`-blocks). Expresses **#6**'s at-risk
  entry `CS1∪CS3 ∨ ≥2·CS4 ∨ CS4-near-smoking` (the OR over count-thresholds the conjunctive `has`
  couldn't).
- **`within`** — a relational predicate: a point inside a fixed offset window of another event
  (`cs4_smk = within(cs4, of: smoking, ±)`), the smoking corroboration arm of **#6**.

## Clean config (concept-id build; widened recent predicate)

Every predicate/window below was validated `0/0`. Concept-id lists abbreviated (`<…>`); the generator
emits the full `^[^/]+//(<ids>)//start$` regexes. `condition_or_drug` is **widened** per the drift
analysis (it counts condition-eras and interval-ends R0's "any cond/drug" includes). Stale windows
(CS0, `obs_membership`) are omitted; the smoking arm is kept but is a dead branch on this data.

```yaml
predicates:
  any_visit:        { code: { regex: "^(Visit|NUCC|CMS Place of Service|OMOP_visit_occurrence)//.*//start$" } }
  ip_er_start:      { code: { any: [Visit//IP//start, Visit//ER//start, "<…CS2 admits…>"] } }
  ip_er_end:        { code: { any: [Visit//IP//end,   Visit//ER//end,   "<…CS2 discharges…>"] } }
  ami:              { code: { regex: "^[^/]+//(<AMI CS3 ids>)//start$" } }
  ami_encounter:    { during: { event: ami, opens: ip_er_start, closes: ip_er_end, closed: both } }   # #2
  condition_or_drug:{ code: { regex: "^(SNOMED|RxNorm|RxNorm Extension|OMOP Extension|OMOP_condition_era)//.*//(start|end)$" } }  # widened (#9)
  cs13:             { code: { regex: "^[^/]+//(<CS1 ∪ CS3 ids>)//start$" } }
  cs4:              { code: { regex: "^[^/]+//(<CS4 ids>)//start$" } }
  smoking:          { code: { regex: "^[^/]+//(<smoking ids>)//start$" } }
  cs4_smk:          { within: { event: cs4, of: smoking, before: 36500d, after: 36500d } }             # #6
  obs_period_start: { code: { regex: "^Obs Period Type//.*//start$" } }
  obs_period_end:   { code: { regex: "^Obs Period Type//.*//end$" } }
  visit_in_obs:     { during: { event: any_visit, opens: obs_period_start, closes: obs_period_end, closed: left } }  # R5

trigger: visit_in_obs            # all visits, restricted to inside an observation period (cohort-end)
windows:
  at_risk_entry:    { start: null, end: trigger, index_timestamp: end,
                      has_any: [ {cs13: (1,None)}, {cs4: (2,None)}, {cs4_smk: (1,None)} ] }            # #6
  no_prior_ami:     { start: null, end: trigger, end_inclusive: False, has: { ami_encounter: (None, 0) } }  # first-AMI
  sufficient_history:{ start: null, end: trigger - 730d, has: { obs_period_start: (1, None) } }         # R4 depth
  recent_activity:  { start: end - 730d, end: trigger, has: { condition_or_drug: (1, None) } }          # #9
  target:           { start: trigger, end: start + 365d, label: ami_encounter }                         # #2 outcome
```

## Validation ladder (each rung: ACES ≡ MEDS-Python, exact)

| rung | benchmark feature | construct | ACES ≡ MEDS-Python |
| --- | --- | --- | --- |
| #2 | encounter outcome / no-prior / target | `during` | **`0/0`** |
| #9 | recent cond/drug (2-yr) | window count | **`0/0`** |
| #6 | corroborated CS4 entry | `has_any` + `within` | **`0/0`** |
| R2 | observation-period membership | window count | **`0/0`** (no-op — all subjects are members) |
| R4 | obs-start depth (vs first-event proxy) | window count | **`0/0`** |
| R5 | cohort-end (visit < entry's obs-end) | `during` trigger | **`0/0`** |

So the **entire expressible benchmark reproduces declaratively**. Two things ACES still cannot
express, both *deliberate*: the **multi-AMI quirk** (below) and the *first-entry-anchored* **CS0
7-day exclusion** — and CS0 is **stale anyway** (0 subjects: its acute-MI concepts are ⊂ CS3 (IHD),
so an acute MI *is* the at-risk entry, never 1–7 days before it).

## Stale / no-op constraints (expressible but useless on this data)

- **CS0 7-day entry exclusion** — 0 effect (acute-MI ⊂ risk-entry concepts; also subsumed by
  `no_prior_ami`).
- **obs-membership (R2)** — 0 effect (every subject has an `observation_period`; implied by R4 depth).
- **smoking arm of #6 (`within`)** — 0 effect (smoking concepts are Observation-domain, absent from
  the condition-matched stream). The `within` construct is validated, but yields nothing here.

## The one inexpressible logic gap — the multi-AMI quirk (quantified)

The SQL drops `(visit, case)` pairs with `case < visit` then `MAX`es, so a visit *between two AMIs*
survives (drop only after the **last** AMI). ACES `no_prior_ami` excludes after the **first** AMI.
Isolated on identical MEDS data (quirk-ON Python vs ACES, same corroboration):

```
onlyA 26,539 pts / 156 subj | onlyB 0 | shared 100% agree
```

So the quirk = **26,539 pts / 156 subjects** — strictly additive (it only *keeps* between-AMI
visits), and an arguable SQL artifact we choose **not** to ship.

## Residual `$H↔MEDS` data drift, attributed to 0 unexplained

With the MEDS-Python made byte-faithful to R0 (port of `collapse_eras` (187-day ERA), the
`coalesce(cohort_end, oend_max)` obs-interval cohort-end, and the `b≠−1`/`MAX` case logic), the
live-cohort comparison (CS4≥1) is **99.985%** with every differing point named:

```
R0-faithful Python  vs  R0 R5:  onlyA 677 / onlyB 585 | label A1B0 299 / A0B1 4
```

| residual bucket | pts | mechanism | kind |
| --- | --- | --- | --- |
| label `A1B0/A0B1` | 303 | AMI source→standard remap | **drift** |
| `onlyA g_keepq` | 92 | MEDS-side extra AMI era (remap) | **drift** |
| `onlyA g_recent` (absent even widened) | 320 | unmapped `concept_id 0` cond/drug dropped by ETL | **drift** |
| `onlyB` subject-level | 204 | risk/membership concept remap | **drift** |
| `onlyB` visit-level | 381 | `$H` extra AMI era (remap) | **drift** |
| `onlyA g_recent` (widenable) | 265 | recent-predicate *breadth* (eras/ends/`RxNorm Extension`) | **fixable, not drift** |
| risk-entry / obs-depth / cohort-end | 0 | — | **exact** |

→ **irreducible drift = 1,300 pts (0.066%)** from exactly **two ETL mechanisms** — *AMI concept
remap* (776: label + both keepq buckets) and *unmapped-`concept_id 0` drops* (320 recent + 204
risk) — plus **265 pts (0.013%) that are a predicate-definition choice**, closable by widening
`condition_or_drug` (R0's "any cond/drug" counts condition-eras and interval-ends my
`SNOMED|RxNorm|OMOP Extension//…//start` predicate missed). The shipped concept-id config should use
the **widened** recent predicate.

## Bottom line (full benchmark)

- The **entire expressible benchmark** reproduces declaratively in ACES (`during`/`has_any`/`within`
  + window counts), validated **`0/0`** against an independent MEDS-Python reference at every rung.
- The **#2 encounter, obs-membership, and obs-depth/cohort-end gaps are closed** (not approximated).
- The only logic ACES can't capture is the **multi-AMI quirk** (26,539 pts — an SQL artifact) and
  the **R5 multi-obs-window edge** (a single-obs-period exactness; multi-period patients over-include).
- Everything else is **`$H↔MEDS` ETL drift ≈ 0.066%** (AMI remap + concept-0 drops), with a further
  **0.013%** that is a closable recent-predicate breadth choice. Risk-entry, depth, cohort-end are
  **exact**.
