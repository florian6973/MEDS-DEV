# Phenotype tasks

Each task is a **dataset-independent** ACES config (windows + relational predicates); the concept
sets are supplied **per dataset** as a predicates file that ACES merges over the task's `???`.

## The two AMI cohorts

The reAIM-Lab benchmark defines AMI **two different ways** — they are *different cohorts*, not the
same task in two code schemes, so each is its own file:

| Task | Source | Cohort |
| --- | --- | --- |
| [`ami_1.yaml`](ami_1.yaml) | ATLAS (`ami_case` / `ami_at_risk` JSON + SQL) | at-risk on a first IHD/differential dx → **incident AMI _during an inpatient/ER encounter_** within 1yr. **Visit-anchored, first-AMI.** |
| [`ami_2.yaml`](ami_2.yaml) | `cohort_extract.py` (imperative walk) | at-risk on an IHD code **OR ≥2 clinical risk factors** → **AMI within 1yr** at **every discharge**. **Discharge-anchored, all discharges.** |

Both require the [`interval_logic`](https://github.com/florian6973/ACES/tree/interval_logic) ACES fork
(`ami_1` for `during`, `ami_2` for `has_any`). `ami_1` is validated on CUMC/OMOP, `ami_2` on MIMIC-IV
— see [`validation/AMI_TASK_REPORT.md`](validation/AMI_TASK_REPORT.md) for the full fidelity analysis,
including which clinical definition to prefer.

## MASLD

[`masld_1.yaml`](masld_1.yaml) is the chronic-liver analogue of `ami_1` from the same ATLAS benchmark:
at-risk on a first metabolic risk-factor dx → **MASLD diagnosis within 1yr** at each visit. Simpler
than `ami_1` — a **plain** case (no encounter restriction) and a single at-risk set (no `has_any`).
Validated on CUMC/OMOP: ACES translation **exact** (`0/0`); the only substantive fidelity choice is
`concept_id` vs `source` coding (the standard build cannot reproduce the ICD-Only case set exactly).
See [`validation/MASLD_TASK_REPORT.md`](validation/MASLD_TASK_REPORT.md). Needs the same `interval_logic`
fork (for `during` on the obs-period cohort-end).

## Run a task

```bash
meds-dev-task task=phenotypes/<ami_1|ami_2> \
  dataset_predicates_path=src/MEDS_DEV/tasks/phenotypes/predicates/<DATASET>/<file>.yaml \
  ...
```

- **Task** (`ami_1.yaml` / `ami_2.yaml`) — windows + relational predicates; base predicates are `???`.
- **Predicates** (`predicates/<DATASET>/<task>.yaml`) — the concept sets resolved into that dataset's
  coding scheme. ACES merges these over the task's `???`. Shipped:
  - `predicates/CUMC/ami_1_concept_id.yaml`, `predicates/CUMC/ami_1_source_approx.yaml`
  - `predicates/CUMC/masld_1_concept_id_strict.yaml`
  - `predicates/MIMIC-IV/ami_2.yaml`

## Add a dataset

Resolve each base predicate into the dataset's codes and write `predicates/<DATASET>/<task>.yaml`.

- `ami_1` base predicates: `ami`, `ip_er_start/end`, `any_visit`, `risk_entry`, `obs_period_start/end`,
  `condition_or_drug`.
- `ami_2` base predicates: `ami_at_risk`, `crf`, `ami_case`, `hospital_discharge`, `ed_only_discharge`,
  `clinical_event`.

- **Coding scheme matters.** Standard-concept-id builds (OMOP-MEDS) use `<vocab>//<concept_id>//start`;
  source-code builds (MIMIC, the v3_mapped CUMC) use e.g. `DIAGNOSIS//ICD//10//I2119`. The *same*
  clinical concept is a different token in each, so a predicates file is **build-specific and the
  schemes are not interchangeable**. Smoke-test every predicate against the MEDS (0 matches = wrong
  scheme). Diagnosis matching is **prefix** (`startswith`): a parent stub (`I25`, `438`) covers its
  children, so `ami_2` regexes are prefix-pruned (regenerate via `validation/aces/build_predicates.py`).
- **Build requirements (each cohort needs the MEDS to carry specific events):**
  - `ami_1` needs **visit start+end** and **observation_period start+end** (for the `during` encounter
    + cohort-end + depth windows). OMOP-MEDS emits them only after two ETL config edits
    (`OMOP_MEDS/configs/pre_MEDS.yaml` visit `reference_cols` → add `visit_concept_id`;
    `event_configs.yaml` visit/obs `time:` → the `*_date` columns) — else they are **silently dropped**.
  - `ami_2` needs the build to (a) **not anchor on a spurious birth event** — `observation_start` =
    first non-`MEDS_BIRTH` event; if the build sits a timed `MEDS_BIRTH` decades before the first
    clinical event, the at-risk **2-yr burn-in** can't be anchored declaratively; and (b) emit a
    discharge event for **non-admitted ED** stays (`transfers.outtime where hadm_id is null`) — the
    standard MIMIC→MEDS build drops these (~29% of points), so `ed_only_discharge` matches nothing and
    `ami_2` runs hospital-only. Both are documented MIMIC→MEDS deltas (see the report).

> `ami_1`/`ami_2` require the ACES `interval_logic` fork (`during` / `has_any`).
