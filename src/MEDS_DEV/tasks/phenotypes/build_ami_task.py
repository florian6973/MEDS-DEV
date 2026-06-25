#!/usr/bin/env python3
"""Generate the AMI ACES task YAML (``ami.yaml``) from the reAIM-Lab cohort artifacts.

This assembles the *Layer-3* prediction task (see ``AMI_TASK_REPORT.md``): trigger on
every visit, gate on at-risk eligibility + recent activity, exclude post-AMI visits, and
label a first AMI within one year. Concept-set-derived predicates (``ami``, ``risk_entry``)
are resolved straight from the ATLAS concept-set zips via the same contract documented in
``resolve_predicates.py`` (the zip is the resolved export; exclusions/descendants are
already baked into ``includedConcepts.csv`` / ``mappedConcepts.csv``).

Predicate -> concept-set mapping:
    ami         = AMI Case zip, CS 3  ([LEGEND HTN] Acute myocardial Infarction)   # outcome + exclusion
    risk_entry  = AMI at Risk zip, CS 1 u CS 3 u CS 4                              # at-risk eligibility (over-inclusion)

The ``???`` predicates (visit / condition / drug / their unions) are genuinely
dataset-specific and are left for each dataset's ``predicates.yaml`` to fill.

Usage:
    python build_ami_task.py \
        --case-zip "<repo>/cohort_concept_sets/AMI Case.zip" \
        --risk-zip "<repo>/cohort_concept_sets/AMI at Risk.zip" \
        --output ami.yaml
"""

from __future__ import annotations

import argparse
import csv
import io
import zipfile
from pathlib import Path

PREDICTION_YEARS = 1  # outcome horizon
LOOKBACK_YEARS = 2  # recent-activity window (the >=2yr history depth gate, #10, was dropped)


def resolve_codes(zip_path: Path, concept_set_ids: set[int]) -> list[str]:
    """Return the sorted ``VOCABULARY//CODE`` list for the given concept-set ids.

    Reads only the resolved CSVs (``includedConcepts`` + ``mappedConcepts``), so ATLAS's
    descendant expansion and ``isExcluded`` removals are already applied. The union across
    the requested ids is taken (used for ``risk_entry`` = CS1 u CS3 u CS4).
    """
    codes: set[str] = set()
    with zipfile.ZipFile(zip_path) as zf:
        for member in ("includedConcepts.csv", "mappedConcepts.csv"):
            rows = csv.DictReader(io.StringIO(zf.read(member).decode("utf-8-sig")))
            for r in rows:
                if int(r["Concept Set ID"]) in concept_set_ids:
                    codes.add(f"{r['Vocabulary']}//{r['Concept Code']}")
    return sorted(codes)


def code_block(codes: list[str], indent: str = "      ") -> str:
    """Render a YAML block-style code list.

    NB: the caller nests this under ``code: { any: [...] }``. ACES requires the ``any`` wrapper
    for a list of codes (it compiles to ``pl.col("code").is_in(...)``); a bare YAML list under
    ``code:`` is NOT supported and fails at evaluation with a List-type cast error.
    """
    return "\n".join(f'{indent}- "{c}"' for c in codes)


def build_yaml(ami_codes: list[str], risk_codes: list[str]) -> str:
    lookback_days = LOOKBACK_YEARS * 365
    horizon_days = PREDICTION_YEARS * 365
    return f"""\
metadata:
  description: >-
    Acute Myocardial Infarction (AMI) risk prediction, translated from the reAIM-Lab
    ehr_foundation_model_benchmark ATLAS cohorts (ami_case.json + ami_at_risk.json) and
    their case/control SQL. At every patient visit (the prediction time), given all data
    up to that visit, predict whether the patient has their FIRST AMI within the next
    {PREDICTION_YEARS} year(s). A visit is scored only if (1) the patient has already entered the
    "at risk" population (an ischemic-heart-disease / differential-for-AMI / AMI-embedding
    diagnosis has occurred), (2) there is at least one condition or drug event in the prior
    {LOOKBACK_YEARS} years, and (3) no AMI has occurred yet. See AMI_TASK_REPORT.md for the full
    derivation and the documented fidelity gaps (encounter restriction, at-risk correlation,
    observation-period approximation).
  links:
    - "https://github.com/reAIM-Lab/ehr_foundation_model_benchmark/tree/main/src/ehr_foundation_model_benchmark/phenotypes"
  contacts:
    - name: "Florent Pollet"
      github_username: "FlorentPollet"
  supported_datasets:
    - MIMIC-IV

# ---------------------------------------------------------------------------
# Trigger = every inpatient/ER visit (a prediction time). index_timestamp = that visit.
# NOTE: the benchmark SQL triggers on ALL visit_occurrence rows; we restrict to inpatient/ER
# (er_visit OR inpatient_visit). On hospital-only datasets (MIMIC-IV) this coincides with
# "all visits"; on datasets with outpatient encounters it is a deviation -- see report #1.
# ---------------------------------------------------------------------------
trigger: inpatient_or_er_visit

windows:
  # At-risk eligibility: the patient must already have entered the at-risk population.
  # (Over-inclusion approximation of the ATLAS correlated CS4 criterion -- see report #6.)
  at_risk_entry:
    start: null
    end: trigger
    start_inclusive: True
    end_inclusive: True
    has:
      risk_entry: (1, None)
  # Exclusion: no AMI may have occurred at or before the prediction visit. This implements
  # the SQL `case_start < visit` exclusion AND subsumes the "no AMI within 7 days" rule.
  no_prior_ami:
    start: null
    end: trigger
    start_inclusive: True
    end_inclusive: False
    has:
      ami: (None, 0)
  # Recent activity: >=1 event in the prior {LOOKBACK_YEARS} years. The benchmark requires a
  # condition OR drug specifically; we use the built-in _ANY_EVENT (any record), a looser
  # "non-empty history" gate that avoids dataset-specific condition/drug predicates -- see
  # report #9/Q3. Also carries the index_timestamp (= the visit = prediction time).
  recent_activity:
    start: end - {lookback_days}d
    end: trigger
    start_inclusive: True
    end_inclusive: True
    index_timestamp: end
    has:
      _ANY_EVENT: (1, None)
  # Outcome: first AMI within the {PREDICTION_YEARS}-year horizon -> positive label.
  target:
    start: trigger
    end: start + {horizon_days}d
    start_inclusive: True
    end_inclusive: True
    label: ami

# ---------------------------------------------------------------------------
# Predicates
#
#   `???` = genuinely dataset-specific (visit encoding varies per MEDS ETL);
#           each dataset's predicates.yaml must supply these.
#   `ami` / `risk_entry` = dataset-agnostic concept-set code lists, inlined from the ATLAS
#           concept sets (regenerate with build_ami_task.py).
# ---------------------------------------------------------------------------
predicates:
  # --- dataset-specific structural predicates ---
  # The trigger is an inpatient OR ER visit. Each dataset must supply both visit predicates
  # (e.g. for MIMIC-IV: er_visit -> ED_REGISTRATION, inpatient_visit -> HOSPITAL_ADMISSION).
  er_visit: ???           # an emergency-room visit start
  inpatient_visit: ???    # an inpatient (hospital) visit start
  inpatient_or_er_visit:
    expr: or(er_visit, inpatient_visit)

  # --- outcome predicate (AMI Case, concept set 3) ---
  # NOTE (#2): the ATLAS case requires this AMI to occur DURING an inpatient/ER encounter.
  # ACES cannot bind the future AMI to an encounter interval, so this defaults to the raw
  # Acute-MI concept set. On hospital-only datasets (e.g. MIMIC-IV) that is already ~inpatient;
  # a dataset MAY override `ami` to restrict to inpatient/ER-recorded AMI.
  ami:
    code:
      any:
{code_block(ami_codes, indent="        ")}

  # --- at-risk eligibility predicate (AMI at Risk, CS1 u CS3 u CS4: differential / ischemic
  #     heart disease / AMI embeddings). Over-inclusion: the CS4 "ten closest embeddings" set
  #     is admitted at >=1 occurrence, dropping the ATLAS "(>=2 prior) OR (>=1 + smoking)"
  #     correlation, which ACES cannot express (see report #6). ---
  risk_entry:
    code:
      any:
{code_block(risk_codes, indent="        ")}
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--case-zip", required=True, type=Path, help="AMI Case concept-set zip")
    p.add_argument("--risk-zip", required=True, type=Path, help="AMI at Risk concept-set zip")
    p.add_argument("--output", required=True, type=Path, help="ami.yaml output path")
    args = p.parse_args()

    ami_codes = resolve_codes(args.case_zip, {3})
    risk_codes = resolve_codes(args.risk_zip, {1, 3, 4})
    print(f"ami: {len(ami_codes)} codes | risk_entry: {len(risk_codes)} codes")

    args.output.write_text(build_yaml(ami_codes, risk_codes))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
