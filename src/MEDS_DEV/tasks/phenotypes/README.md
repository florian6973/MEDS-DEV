# Phenotype tasks

Each task is a **dataset-independent** ACES config (windows + relational predicates); the concept
sets are supplied **per dataset** as a predicates file that ACES merges over the task's `???`.

## Run a task

```bash
meds-dev-task task=phenotypes/<task> \
  dataset_predicates_path=src/MEDS_DEV/tasks/phenotypes/predicates/<DATASET>/<task>.yaml \
  ...
```

- **Task** (e.g. [`ami.yaml`](ami.yaml)) — the windows + relational predicates. Base predicates are
  `???`.
- **Predicates** (`predicates/<DATASET>/<task>.yaml`) — the concept sets resolved into that
  dataset's coding scheme. ACES merges these over the task's `???`.

## Add a dataset

Resolve each base predicate into the dataset's codes and write `predicates/<DATASET>/<task>.yaml`.
For `ami` the base predicates are: `ami`, `ip_er_start/end`, `any_visit`, `risk_entry`,
`obs_period_start/end`, `condition_or_drug`.

- **Coding scheme matters.** Standard-concept-id builds (OMOP-MEDS) use `<vocab>//<concept_id>//start`;
  source-code builds (MIMIC) use e.g. `DIAGNOSIS//ICD//10//I2119`. Resolve into the dataset's scheme,
  re-resolved from seeds through `concept_ancestor` (not the stale ATLAS snapshot).
- **Drop unsupported windows.** No visit-end events → drop the `during` encounter restriction; no
  observation_period → drop `sufficient_history` (depth) + the cohort-end trigger.
- **Smoke-test** every predicate against the MEDS (0 matches = wrong scheme).

> `ami.yaml` requires the ACES `interval_logic` fork (for `during`). The validation harness, the
> design spec, and the porting guide live in `validation/` (not committed) — see its `README.md`,
> `SPEC.md`, and `PORTING.md`.
