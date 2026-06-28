# AMI full-benchmark validation harness

This directory holds the scripts and task that **reproduce the reAIM-Lab AMI benchmark exactly** and
prove the ACES translation is faithful. The story (and the complete fidelity/drift ledger) is in
[`../AMI_TASK_REPORT.md`](../AMI_TASK_REPORT.md), "Full-benchmark validation" section.

Three views of the *same* CUMC patients are compared, exact `(subject, second)`:

```
   ACES task (ami_full.yaml, interval_logic fork)  ──┐
   reproduce_meds.py   (MEDS reference)            ──┼──  cmp1()  ──>  0/0 = exact translation
   reproduce_benchmark.py (raw OMOP, = the SQL)    ──┘                 residual = $H<->MEDS drift
```

- **`reproduce_benchmark` (all defaults) == original ATLAS** is the *live-cohort* check — the OMOP
  Python reproduces the published cohort exactly (**~100%**); this is the ground truth everything
  else is measured against.
- **`ACES == reproduce_meds`** is the *translation* check (must be `0/0` — the YAML and the
  hand-written reference are the same logic).
- **`reproduce_meds(mode=r0) == reproduce_benchmark`** is the *fidelity* check (residual ≈ 0.066%
  ETL drift — AMI concept remap + unmapped-`concept_id 0` drops).
- **`reproduce_meds(aces) vs reproduce_meds(r0)`** is the *logic-gap* check — both on the **same**
  MEDS data, so it isolates the pure logic/definitional differences (CS4 corroboration + multi-AMI
  quirk + ERA + cohort-end) with **zero data drift**.

## Files

| file | what |
| --- | --- |
| [`ami_full.yaml`](ami_full.yaml) | the full-benchmark ACES task (`during`/`has_any`/`within`); base predicates `???` |
| [`reproduce_meds.py`](reproduce_meds.py) | MEDS-side reference cohort (presets `aces` / `r0`) |
| [`../reproduce_benchmark.py`](../reproduce_benchmark.py) | OMOP-side reference (the SQL ladder) + `cmp1` / `load_cohort_file` |
| [`run_validation.sh`](run_validation.sh) | one-shot: ACES vs `reproduce_meds` vs `reproduce_benchmark` |
| [`MAPPING.md`](MAPPING.md) | criterion-by-criterion benchmark→ACES mapping + every issue hit |

## 0. Build the OMOP-MEDS dataset (keep visit-end + observation periods)

The validation needs a MEDS built **directly from the harmonized CUMC OMOP (`$H`)** with concept-id
codes (`<vocab>//<concept_id>//start|end`) so ACES matches the *same* standard concept ids the OMOP
reference does. Out of the box, [OMOP-MEDS](https://pypi.org/project/OMOP-MEDS/) **silently drops all
visits** (and times observation periods wrong) on `$H`, because `$H` nulls `visit_source_concept_id`
and the visit/obs *datetimes*. Two config edits fix it:

1. **`OMOP_MEDS/configs/pre_MEDS.yaml`** — `visit_occurrence` blocks (5.3 and 5.4):
   `reference_cols: ["visit_source_concept_id"]` → `["visit_concept_id", "visit_source_concept_id"]`
   (the source concept is null for all 58.6M visits → null preferred concept → row dropped).
2. **`OMOP_MEDS/configs/event_configs.yaml`** — `visit` / `visit_end` (and confirm
   `observation_period` start/end use the *date* columns):
   `time: col(visit_start_datetime)` / `col(visit_end_datetime)` →
   `col(visit_start_date)` / `col(visit_end_date)` (the harmonized OMOP nulls the datetimes; dates
   are populated; a null event time is dropped).

Then build:

```bash
OMOP_MEDS raw_input_dir=$H root_output_dir=$OUT do_download=False   # [limit_subjects=N] to smoke-test
```

Verify visits **and** obs periods emit (both ends): you should see `Visit//{IP,ER,OP}//{start,end}`
and `Obs Period Type//.../{start,end}`. Because visits are timed at `visit_start_date` (midnight),
**all comparisons are at date granularity** (the OMOP side forces the same — see
`reproduce_benchmark.py`'s `vt`). This build is described in the `omop-meds-cumc-config-fixes` note.

## 1. Generate the concept-id task + predicates (CUMC)

The base predicates of `ami_full.yaml` (`ami`, `ip_er_start/end`, `cs13`, `cs4`, `smoking`,
`obs_period_start/end`, `condition_or_drug`, `any_visit`) are **concept-id regexes** resolved from
the ATLAS zips through the dataset's own `concept_ancestor` — the *same* resolution
`reproduce_benchmark.py` does (`resolve()` there; the zip `includedConcepts.csv` is a stale
snapshot, so re-resolve from seeds). The resolver emits, per concept set, a regex
`^[^/]+//(<id1>|<id2>|…)//start$`. Drop the resulting block into
`predicates/CUMC/ami_full.yaml` (concept-id format, **not** the `v3_mapped` `ami.yaml` next to it,
which is a different build/scheme).

> The on-server generator (`gen_omeds_task.py` + the `during`/obs patch) produces this directly; if
> you want it versioned, copy it here. The resolution logic is small and already lives in
> `reproduce_benchmark.py` (`load_concept_ancestor` + `resolve`), so the generator is a thin wrapper
> that prints the regexes instead of the id lists.

`condition_or_drug` is **widened** (per the drift analysis): match any condition/drug representation,
not just standard-vocab `//start` —
`^(SNOMED|RxNorm|RxNorm Extension|OMOP Extension|OMOP_condition_era)//.*//(start|end)$`.

## 2. Run

```bash
H=/path/to/ohdsi_cumc_deid_..._harmonized
ATLAS=/path/.../task_labels/phenotype_sample/AMI/tuning.parquet
ZIPS=/path/.../cohort_concept_sets
MEDS=$OUT/MEDS_cohort/data/tuning
PRED=predicates/CUMC/ami_full.yaml

# (a) OMOP reference = the benchmark SQL (R5 = full minus the #2 encounter is case-mode=encounter)
python ../reproduce_benchmark.py --omop "$H" --atlas "$ATLAS" --subjects-from "$MEDS" \
  --case-zip "$ZIPS/AMI Case.zip" --risk-zip "$ZIPS/AMI at Risk.zip" \
  --depth-anchor obs_start --case-mode encounter --output omop_r0.parquet

# (b) MEDS reference, both presets
python reproduce_meds.py --meds "$MEDS" --predicates "$PRED" --mode aces --output meds_aces.parquet
python reproduce_meds.py --meds "$MEDS" --predicates "$PRED" --mode r0   --output meds_r0.parquet

# (c) ACES on the task (interval_logic fork venv, meds>=0.4)
aces-cli cohort_dir=. cohort_name=ami_full data.standard=meds data.path=$MEDS_FLAT \
  output_filepath=aces_full.parquet     # (point data.path at the flattened MEDS the fork expects)
```

## 3. Compare (identical `cmp1` on every pair)

```bash
python -c "
import polars as pl
from reproduce_benchmark import cmp1, load_cohort_file as L
Dt = lambda p: L(p).with_columns(pl.col('prediction_time').dt.truncate('1d'))   # date granularity
print('live cohort  OMOP-default == ATLAS  :', cmp1(Dt('omop_live.parquet'), Dt('$ATLAS')))
print('translation  ACES == MEDS-ref(aces):', cmp1(L('meds_aces.parquet'), L('aces_full.parquet')))
print('fidelity     MEDS-ref(r0) == OMOP   :', cmp1(L('omop_r0.parquet'),  L('meds_r0.parquet')))
print('logic gap    MEDS-ref(r0) vs (aces) :', cmp1(L('meds_r0.parquet'),  L('meds_aces.parquet')))
"
```

The **live-cohort** match comes from the all-defaults run (full benchmark, ATLAS subjects):
`python ../reproduce_benchmark.py --omop "$H" --atlas "$ATLAS" --case-zip … --risk-zip … --output omop_live.parquet`.
Compare it at **date granularity** (ATLAS is day-resolution; the reproduction may carry
`visit_start_datetime`) — that's the validated exact match (`pts exact = the ATLAS sample, onlyB 0,
100%`). ATLAS is a `phenotype_sample` downsample, so `onlyA` (the unsampled full cohort) is large by
design; the proof is `onlyB ≈ 0` + label agreement ~100%.

With `ami_full.yaml` on the **live** CS4≥1 (the shipped default), the **logic gap** (`MEDS-ref(r0)`
vs `(aces)`) collapses to just the multi-AMI quirk + ERA + cohort-end (~26k); it is ~290k only if you
set the **published** CS4≥2 (`--cs4-min 2`), where the CS4 corroboration dominates — a definitional
choice, not drift.

Expected: translation **`0/0`**; fidelity **~99.985%**, residual attributable to the AMI concept
remap + unmapped-`concept_id 0` drops (see `MAPPING.md` and the report's drift ledger). The
**multi-AMI quirk** (≈26,539 pts) appears only if you compare `mode=aces` (first-AMI) against the
SQL (`last`): it is the one inexpressible logic gap, an SQL artifact we do not ship.

`run_validation.sh` wraps all of the above.
