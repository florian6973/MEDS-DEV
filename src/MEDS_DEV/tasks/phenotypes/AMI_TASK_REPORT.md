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
