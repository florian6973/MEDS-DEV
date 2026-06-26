#!/usr/bin/env python3
"""Generate the AMI ACES task YAML (``ami.yaml``) from the reAIM-Lab cohort artifacts.

This assembles the *Layer-3* prediction task (see ``AMI_TASK_REPORT.md``): trigger on
every visit, gate on at-risk eligibility + recent activity, exclude post-AMI visits, and
label a first AMI within one year. The concept-set-derived predicates (``ami``, ``risk_entry``)
read their ``VOCABULARY//CODE`` lists from ``ami.txt`` / ``risk_entry.txt`` -- the concept sets
**re-resolved from seeds via the dataset's ``concept_ancestor``**. The zip's
``includedConcepts.csv`` is a *frozen snapshot* that lags the live vocabulary (it misses ~48
concepts; verified against the exact OMOP reproduction in ``reproduce_benchmark.py``), so we
no longer trust it. Regenerate the code files with the re-resolve command in that report.

Predicate -> concept-set mapping:
    ami         = AMI Case zip, CS 3  ([LEGEND HTN] Acute myocardial Infarction)   # outcome + exclusion
    risk_entry  = AMI at Risk zip, CS 1 u CS 3 u CS 4                              # at-risk eligibility (over-inclusion)

The ``???`` predicates (visit / condition / drug / their unions) are genuinely
dataset-specific and are left for each dataset's ``predicates.yaml`` to fill.

Usage:
    python build_ami_task.py --emit task --format atlas --output ami.yaml
    python build_ami_task.py --emit dataset-predicates --format cumc --output predicates/CUMC/ami.yaml
    # --ami-codes / --risk-codes default to ami.txt / risk_entry.txt next to this script

NOTE: ``resolve_codes`` (zip reader) is retained only for reference/provenance; the generator
no longer uses it -- the live resolution is in ami.txt / risk_entry.txt.
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


def to_mimic_meds(code: str) -> str | None:
    """Translate an ATLAS ``VOCAB//CODE`` to the MIMIC-IV MEDS code, or None to drop it.

    MIMIC-IV MEDS writes diagnoses as ``DIAGNOSIS//ICD//{9,10}//<code-without-dots>``
    (e.g. ATLAS ``ICD10CM//I21.19`` -> ``DIAGNOSIS//ICD//10//I2119``). Non-ICD vocabularies
    (SNOMED/CIEL/MedDRA/...) do not appear in MIMIC diagnoses and are dropped.

    >>> to_mimic_meds("ICD10CM//I21.19")
    'DIAGNOSIS//ICD//10//I2119'
    >>> to_mimic_meds("ICD9CM//410.72")
    'DIAGNOSIS//ICD//9//41072'
    >>> to_mimic_meds("SNOMED//22298006") is None
    True
    """
    vocab, _, raw = code.partition("//")
    raw = raw.replace(".", "")
    if vocab == "ICD10CM":
        return f"DIAGNOSIS//ICD//10//{raw}"
    if vocab == "ICD9CM":
        return f"DIAGNOSIS//ICD//9//{raw}"
    return None


def to_cumc_meds(code: str) -> str:
    """Translate an ATLAS ``VOCAB//CODE`` to the CUMC OMOP-MEDS code (single-slash, dots kept).

    CUMC MEDS writes ``VOCAB/CODE`` (e.g. ATLAS ``ICD10CM//I21.29`` -> ``ICD10CM/I21.29``,
    ``SNOMED//22298006`` -> ``SNOMED/22298006``). All vocabularies are kept (CUMC has SNOMED).

    >>> to_cumc_meds("ICD10CM//I21.29")
    'ICD10CM/I21.29'
    >>> to_cumc_meds("SNOMED//22298006")
    'SNOMED/22298006'
    """
    return code.replace("//", "/", 1)


def transform(codes: list[str], fmt: str) -> list[str]:
    """Apply a dataset code-format transform; ``atlas`` is identity."""
    if fmt == "atlas":
        return codes
    if fmt == "mimic":
        return sorted({c for c in (to_mimic_meds(x) for x in codes) if c})
    if fmt == "cumc":
        return sorted({to_cumc_meds(x) for x in codes})
    raise ValueError(f"unknown format: {fmt}")


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
  # Carries index_timestamp (= the trigger/visit = prediction time). It MUST live on a
  # `start: null` window: ACES reads index from `start_summary.timestamp_at_end`, which only
  # equals the trigger when start is unbounded. On a bounded-start window (e.g. recent_activity
  # `start: end - 730d`) it wrongly resolves to `trigger - 730d`.
  at_risk_entry:
    start: null
    end: trigger
    start_inclusive: True
    end_inclusive: True
    index_timestamp: end
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
#   `ami` / `risk_entry` = concept-set code lists in ATLAS `VOCAB//CODE` form (e.g.
#           ICD10CM//I21.19, SNOMED//...). A dataset whose MEDS uses a different code format
#           MUST override these in its predicates.yaml (ACES merges dataset predicates over
#           task ones). E.g. MIMIC-IV writes `DIAGNOSIS//ICD//10//I2119` -- generate its
#           override with: build_ami_task.py --emit dataset-predicates --format mimic
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

  # --- at-risk eligibility predicate (AMI at Risk, CS1 u CS3 u CS4: differential / ischemic heart
  #     disease / ten closest embeddings). CS4 admitted at >=1 (its ATLAS corroboration is
  #     inexpressible in ACES -> minor over-inclusion, #6). The big over-generation is the missing
  #     observation-period window bound (#10), not this. See report. ---
  risk_entry:
    code:
      any:
{code_block(risk_codes, indent="        ")}
"""


# Dataset-specific visit predicates needed to make the file self-contained (the task leaves
# er_visit/inpatient_visit as ???). Keyed by --format.
DATASET_VISIT_PREDICATES = {
    "mimic": [
        ("er_visit", "^ED_REGISTRATION//.*"),
        ("inpatient_visit", "^HOSPITAL_ADMISSION//.*"),
    ],
    # CUMC OMOP-MEDS. The benchmark triggers on ALL visit_occurrence rows, so we override the
    # task's `inpatient_or_er_visit` trigger to match every visit. CUMC MEDS encodes visits under
    # TWO prefixes -- `Visit/IP|OP|ER|HE|...` AND `CMS Place of Service/NN` (telehealth 02, office
    # 11, inpatient 21, ER 23, ...) -- which are *separate* visits (~1% co-occur), so the trigger
    # must match both or it misses every CMS-POS-coded visit. The er_visit/inpatient_visit entries
    # still resolve the task's ??? (they become unreferenced once the derived trigger is overridden).
    "cumc": [
        ("er_visit", "(?i)^(visit/er|cms place of service/23)"),
        ("inpatient_visit", "(?i)^(visit/ip|cms place of service/21)"),
        ("inpatient_or_er_visit", "(?i)^(visit/|cms place of service/)"),
    ],
}


def build_dataset_predicates(ami_codes: list[str], risk_codes: list[str], fmt: str) -> str:
    """Render a complete, self-contained predicates file for `dataset_predicates_path`.

    Includes the dataset's visit predicates (filling the task's ``er_visit``/``inpatient_visit``
    ``???``) plus the ``ami``/``risk_entry`` concept-set overrides in the dataset's code format.
    Pass it with ``meds-dev-task ... dataset_predicates_path=<this file>``.
    """
    visit = DATASET_VISIT_PREDICATES.get(fmt)
    if visit is None:
        raise SystemExit(f"No visit predicates defined for --format {fmt}; add them to DATASET_VISIT_PREDICATES.")
    visit_block = "\n".join(
        f'  {name}:\n    code: {{ regex: "{rx}" }}' for name, rx in visit
    )
    return f"""\
# AMI phenotype (tasks/phenotypes/ami.yaml) -- self-contained predicates for the '{fmt}' MEDS format.
# Pass via: meds-dev-task ... dataset_predicates_path=<this file>
# Generated by: build_ami_task.py --emit dataset-predicates --format {fmt}
predicates:
{visit_block}
  ami:
    code:
      any:
{code_block(ami_codes, indent="        ")}
  risk_entry:
    code:
      any:
{code_block(risk_codes, indent="        ")}
"""


def main() -> None:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # Code source: the concept sets re-resolved from seeds via the dataset's concept_ancestor
    # (the zip's includedConcepts snapshot is stale -- it misses ~48 live concepts; verified against
    # the exact OMOP reproduction). ami.txt = case CS3; risk_entry.txt = at-risk CS1 u CS3 u CS4.
    p.add_argument("--ami-codes", type=Path, default=here / "ami.txt", help="ami VOCAB//CODE list")
    p.add_argument("--risk-codes", type=Path, default=here / "risk_entry.txt", help="risk_entry VOCAB//CODE list")
    p.add_argument("--output", required=True, type=Path, help="output path")
    p.add_argument("--emit", choices=["task", "dataset-predicates"], default="task",
                   help="'task' = full ami.yaml; 'dataset-predicates' = just ami/risk_entry for a "
                        "dataset predicates.yaml override")
    p.add_argument("--format", choices=["atlas", "mimic", "cumc"], default="atlas",
                   help="code format. 'atlas' = VOCAB//CODE (task default); 'mimic' = "
                        "DIAGNOSIS//ICD//{9,10}//<no-dots>, ICD-only; 'cumc' = VOCAB/CODE "
                        "(single slash, all vocabs incl SNOMED), trigger = all Visit/ codes")
    args = p.parse_args()

    def read_codes(path: Path) -> list[str]:
        return sorted({ln.strip() for ln in path.read_text().splitlines() if ln.strip()})

    ami_codes = transform(read_codes(args.ami_codes), args.format)
    risk_codes = transform(read_codes(args.risk_codes), args.format)
    print(f"[{args.format}] ami: {len(ami_codes)} codes | risk_entry: {len(risk_codes)} codes")

    if args.emit == "task":
        if args.format != "atlas":
            raise SystemExit("--emit task only supports --format atlas (keep the task dataset-agnostic)")
        args.output.write_text(build_yaml(ami_codes, risk_codes))
    else:
        args.output.write_text(build_dataset_predicates(ami_codes, risk_codes, args.format))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
