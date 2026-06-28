#!/usr/bin/env bash
# One-shot AMI full-benchmark validation: ACES task vs MEDS reference vs OMOP reference.
#
#   ACES == reproduce_meds(aces)   -> translation check   (expect 0/0)
#   reproduce_meds(r0) == reproduce_benchmark  -> fidelity (expect ~99.985%, residual = $H<->MEDS drift)
#
# Run on the server (the OMOP $H + the OMOP-MEDS build are there). Edit the paths, then:
#   bash run_validation.sh
# Prereqs: an interval_logic-fork ACES venv (meds>=0.4) on PATH as `aces-cli`; the concept-id
# predicates at $PRED (see README.md §1); the OMOP-MEDS build (README.md §0).
set -euo pipefail

# --- paths (EDIT) ---------------------------------------------------------------------------------
H=${H:?set H to the harmonized OMOP dir}
ZIPS=${ZIPS:?set ZIPS to the cohort_concept_sets dir}
ATLAS=${ATLAS:?set ATLAS to the AMI tuning.parquet}
MEDS=${MEDS:?set MEDS to <build>/MEDS_cohort/data/tuning}
MEDS_FLAT=${MEDS_FLAT:-$MEDS}          # the flattened MEDS path the ACES fork reads
PRED=${PRED:-predicates/CUMC/ami_full.yaml}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-/tmp/ami_validation}; mkdir -p "$OUT"

# --- (a) OMOP reference (the benchmark SQL), full minus encounter is case-mode -------------------
python "$HERE/../reproduce_benchmark.py" --omop "$H" --atlas "$ATLAS" --subjects-from "$MEDS" \
  --case-zip "$ZIPS/AMI Case.zip" --risk-zip "$ZIPS/AMI at Risk.zip" \
  --depth-anchor obs_start --case-mode encounter --output "$OUT/omop_r0.parquet"

# --- (b) MEDS reference, both presets ------------------------------------------------------------
python "$HERE/reproduce_meds.py" --meds "$MEDS" --predicates "$HERE/$PRED" --mode aces --output "$OUT/meds_aces.parquet"
python "$HERE/reproduce_meds.py" --meds "$MEDS" --predicates "$HERE/$PRED" --mode r0   --output "$OUT/meds_r0.parquet"

# --- (c) ACES on the task ------------------------------------------------------------------------
aces-cli cohort_dir="$HERE" cohort_name=ami_full data.standard=meds data.path="$MEDS_FLAT" \
  output_filepath="$OUT/aces_full.parquet"

# --- (d) compare (identical cmp1 on every pair) --------------------------------------------------
cd "$HERE/.."
python -c "
from reproduce_benchmark import cmp1, load_cohort_file as L
O='$OUT'
print('=== translation: ACES == MEDS-ref(aces)  -> expect 0/0 ===')
print(cmp1(L(O+'/meds_aces.parquet'), L(O+'/aces_full.parquet')))
print('=== fidelity:    MEDS-ref(r0) == OMOP(R0) -> residual = \$H<->MEDS drift ===')
print(cmp1(L(O+'/omop_r0.parquet'),  L(O+'/meds_r0.parquet')))
"
