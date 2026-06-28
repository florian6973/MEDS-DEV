# Porting the AMI task to a new MEDS dataset

The task logic is **dataset-independent**; only the *base predicates* change. This is the full
porting model — what to do, in order, and how to decide the unavoidable approximations.

## Fixed vs per-dataset

| | where | changes per dataset? |
| --- | --- | --- |
| relational predicates (`ami_encounter`, `visit_in_obs` = `during`), windows, trigger | [`ami_full.yaml`](ami_full.yaml) | **no** — the logic is fixed |
| the 9 base `code` predicates (`ami`, `ip_er_start/end`, `any_visit`, `cs13`, `cs4`, `obs_period_start/end`, `condition_or_drug`) | `predicates/<DATASET>/ami_full.yaml` | **yes** — this is the entire porting surface |

So porting = **resolve the concept sets into the new dataset's coding scheme**, drop the windows the
dataset can't support, and validate.

## Checklist

### 1. Inventory the MEDS coding scheme *first*
Dump the vocabularies and token shape before anything else:
```python
import polars as pl, glob
m = pl.read_parquet(glob.glob("<MEDS>/data/tuning/**/*.parquet", recursive=True))
m.select(pl.col("code").str.split("//").list.first().alias("vocab")).group_by("vocab").len().sort("len", descending=True)
m.filter(pl.col("code").str.contains("//(start|end)$")).select("code").head(20)   # interval convention?
```
This is step 1 because a predicate that matches **0** events silently empties the cohort (the MIMIC
bug: ATLAS-format codes matched nothing). `port_check.py` automates this (below).

### 2. Pick the resolution path — concept_id vs source code
- **Standard-concept-id MEDS** (OMOP-MEDS, our CUMC build): resolve the ATLAS sets → standard
  `concept_id`s via `concept_ancestor` → `^<vocab>//(<ids>)//start$`. Logic is in
  `reproduce_benchmark.py` (`load_concept_ancestor` + `resolve`).
- **Source-code MEDS** (MIMIC: `DIAGNOSIS//ICD//10//I2119`): resolve → **source** codes in the
  dataset's token format (strip dots, pick ICD9/10), mapping standard→source via
  `concept_relationship` / `concept.concept_code`. This is what `build_ami_task.py --format mimic` does.

### 3. Re-resolve from seeds; mind the vocabulary *version*
- **Don't** trust the zip's `includedConcepts.csv` — a frozen snapshot that lags the live vocabulary
  (it missed our entry concept). Resolve the **seed** concepts through the dataset's *own*
  `concept_ancestor`.
- Version skew cuts both ways: a newer ICD10/SNOMED code in the data won't be in an older ancestor
  table (missed); resolved codes may be absent from the data. Check the dataset's vocab version
  against the resolver's.

### 4. Map each predicate; let the dataset's *contents* decide the approximations
| predicate | needs | if missing |
| --- | --- | --- |
| `ami` / `cs13` / `cs4` | condition concept sets | (core — must resolve) |
| `ip_er_start` / `ip_er_end` | inpatient/ER **admission AND discharge** events | **no discharge ⇒ no `during` encounter (#2)** → fall back to raw `ami` |
| `obs_period_start` / `obs_period_end` | an observation_period | **absent ⇒ drop `sufficient_history` (R4) + cohort-end (R5)** → first-clinical-event depth proxy, documented |
| `condition_or_drug` | the vocabs representing conditions+drugs *here* | widen to match (we needed `RxNorm Extension` + `OMOP_condition_era` + interval-`end`s) |
| `any_visit` | the trigger universe | (core) |

### 5. Handle vocab coverage / skew
- The data may use vocabs the seed-resolution didn't reach (`CIEL` / `Nebraska Lexicon` / `MedDRA` in
  v3_mapped CUMC) → resolve the full standard→source map or the cohort under-enters.
- Cover the ICD9↔ICD10 transition.
- Accept a small irreducible miss: standard concepts with **no representation** in the dataset's
  vocab are invisible (our 228 FN = the AMI remap). Quantify it; don't chase it.

### 6. Validate by what's available
- **Smoke-test** every predicate: `>0` matches on a sample (0 ⇒ wrong scheme). `port_check.py`.
- **OMOP source exists** → `reproduce_benchmark.py` (OMOP) + `reproduce_meds.py` (MEDS) +
  `run_validation.sh` → prevalence / recall / PPV / drift, as in `AMI_TASK_REPORT.md`.
- **MEDS-only** (no OMOP) → no fidelity anchor, but **ACES ≡ `reproduce_meds` is dataset-agnostic
  `0/0`** (validates the translation), plus prevalence + per-predicate match-count sanity.

### 7. Pre-flight gotchas we actually hit
- null `visit_source_concept_id` → use `visit_concept_id` (OMOP-MEDS `pre_MEDS.yaml`).
- null visit datetimes → `visit_start_date` (and compare at date; see `--prediction-time`).
- stale `includedConcepts` snapshot → re-resolve via `concept_ancestor`.
- recent-gate too narrow → widen `condition_or_drug`.

## Mental model

> inventory the scheme → resolve the concept sets into it (re-resolved, version-aware) → drop the
> windows the dataset can't support → smoke-test every predicate → validate against OMOP if you have
> it, else translation-only.

## Tooling

- `port_check.py` — **automates the dataset-agnostic diagnostics**: scheme inventory, a
  *supportability report* (which windows the dataset can express, from visit-end / obs-period
  presence), and a smoke-test of a candidate predicates file (per-predicate match counts). Run it
  first on any new MEDS; it tells you the scheme and which approximations you're forced into.
- Concept-set **resolution** (step 2) reuses `reproduce_benchmark.py:resolve` (concept_id) or
  `build_ami_task.py` (source code) — the part that needs a vocabulary and a per-scheme emit, so it's
  kept in those resolvers rather than guessed.
