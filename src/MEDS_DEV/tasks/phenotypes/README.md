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
  source-code builds (MIMIC, the v3_mapped CUMC) use e.g. `ICD10CM/I21.19`, `CIEL/…`. The *same*
  clinical concept is a different token in each (concept_id `319039` vs its `ICD10CM`/`SNOMED`-source
  codes), so a predicates file is **build-specific and the schemes are not interchangeable**. Resolve
  into the dataset's scheme, re-resolved from seeds through `concept_ancestor` (not the stale ATLAS
  snapshot). Name one file per build, e.g. `predicates/CUMC/ami_concept_id.yaml` (OMOP-MEDS) and
  `predicates/CUMC/ami_source.yaml` (v3_mapped); point `dataset_predicates_path` at the matching one.
- **The build must keep visit-end + observation periods.** The full task's `during` (encounter +
  cohort-end) and depth gate need the MEDS to emit **visit start AND end** and **observation_period
  start AND end**, retained from OMOP. OMOP-MEDS emits them only after two ETL config edits
  (`OMOP_MEDS/configs/pre_MEDS.yaml` visit `reference_cols` → add `visit_concept_id`;
  `event_configs.yaml` visit/obs `time:` → the `*_date` columns) — else visits/obs are **silently
  dropped**. A build without visit-end / obs-period can only run the **approximation** (drop the
  encounter restriction, the depth gate, and the cohort-end trigger).
- **Smoke-test** every predicate against the MEDS (0 matches = wrong scheme).

> `ami.yaml` requires the ACES `interval_logic` fork (for `during`). 