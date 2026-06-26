#!/usr/bin/env python3
"""Reproduce the reAIM-Lab AMI benchmark cohort directly on the OMOP CDM parquets (polars).

Implements, end-to-end, the three pieces the benchmark uses:
  1. at-risk entry  (ami_at_risk.json): first of IHD(CS3) / differential(CS1) / corroborated CS4,
     with the "no Acute-MI(CS0) in the 7d before entry" inclusion rule.
  2. case cohort    (ami_case.json): Acute-MI(CS3) during an inpatient/ER visit(CS2), ERA-collapsed 180d.
  3. visit windowing + labels (cohort_pos_neg_query.sql): visits in [entry, obs_end) and
     >= obs_start + 2yr, with >=1 condition/drug in the prior 2yr, labelled by the case exclusion.

Purpose: compare *reproduced* (this script, on the harmonized OMOP) against the *original ATLAS*
cohort and the *ACES* output. If reproduced ~ ATLAS -> our understanding is complete. If reproduced
~ ACES but != ATLAS -> the original cohort was built on a different (sparser) OMOP visit_occurrence.

Concept ids come from the standard "Concept ID" column of each concept-set zip's includedConcepts.csv
(post descendant/exclusion resolution), matched against condition_concept_id / visit_concept_id.

Exact Circe/SQL semantics implemented:
  - at-risk entry = First of {CS1/CS3 condition; CS4 condition}; minus the "no Acute-MI(CS0) in
    [entry-7d, entry-1d]" inclusion rule. (CS4 is admitted at the FIRST occurrence: the published
    JSON specifies a corroboration but the live benchmark cohort ignores it -- verified empirically.)
  - cohort end = end of the observation period containing the entry (Circe default, no EndStrategy);
    the 2yr lookback compares to the earliest obs-period start.
  - case = Acute-MI(CS3) whose date is inside an inpatient/ER visit(CS2) interval
    [visit_start, visit_end], ERA-collapsed (intervals [AMI, AMI+7] padded 180 -> merge if gap<=187).
  - visit windowing + label exactly per cohort_pos_neg_query.sql.
Only remaining simplification: comparisons are at date (not datetime) granularity, immaterial for the
2-yr / 1-yr windows.

Usage:
    python reproduce_benchmark.py \
        --omop  <...>/ohdsi_cumc_deid_2023q4r3_harmonized \
        --atlas <...>/task_labels/phenotype_sample/AMI/tuning.parquet \
        --case-zip "<...>/AMI Case.zip" --risk-zip "<...>/AMI at Risk.zip" \
        --output <...>/reproduced_tuning.parquet
"""

from __future__ import annotations

import argparse
import csv
import glob
import io
import os
import zipfile

import polars as pl

MIN_OBS_YEARS = 2
PREDICTION_YEARS = 1
# 7 SNOMED smoking-status concepts from the ami_at_risk.json correlated criterion
SMOKING_CONCEPTS = [42709996, 762499, 4298794, 4310250, 37395605, 762498, 4184633]


def included_concept_ids(zip_path: str, cs_id: int) -> list[int]:
    """Standard concept ids of a concept set (post-resolution) from includedConcepts.csv."""
    with zipfile.ZipFile(zip_path) as z:
        rows = csv.DictReader(io.StringIO(z.read("includedConcepts.csv").decode("utf-8-sig")))
        return [int(r["Concept ID"]) for r in rows if int(r["Concept Set ID"]) == cs_id]


def load(omop: str, table: str, cols: list[str], subjects: list[int]) -> pl.DataFrame:
    """Scan an OMOP table, keep only the requested subjects + available columns."""
    fs = glob.glob(os.path.join(omop, table, "*.parquet"))
    lf = pl.scan_parquet(fs).filter(pl.col("person_id").cast(pl.Int64, strict=False).is_in(subjects))
    have = lf.collect_schema().names()
    keep = ["person_id"] + [c for c in cols if c in have]
    return lf.select(keep).with_columns(pl.col("person_id").cast(pl.Int64)).collect()


def d(col: str) -> pl.Expr:
    """Parse an OMOP date/datetime string column to a Date (first 10 chars)."""
    return pl.col(col).cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False)


def collapse_eras(df: pl.DataFrame, gap_days: int) -> pl.DataFrame:
    """ERA-collapse per subject: keep a start whenever the gap from the previous date > gap_days."""
    rows = []
    for r in df.group_by("person_id").agg(pl.col("case_d").sort()).iter_rows(named=True):
        prev = None
        for x in r["case_d"]:
            if prev is None or (x - prev).days > gap_days:
                rows.append((r["person_id"], x))
            prev = x
    return pl.DataFrame(rows, schema=["person_id", "case_start"], orient="row")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--omop", required=True, help="harmonized OMOP CDM dir")
    ap.add_argument("--atlas", required=True, help="original ATLAS split parquet (subject list + compare)")
    ap.add_argument("--case-zip", required=True)
    ap.add_argument("--risk-zip", required=True)
    ap.add_argument("--output", required=True, help="reproduced cohort parquet")
    args = ap.parse_args()

    atlas = pl.read_parquet(args.atlas)
    subjects = atlas["subject_id"].cast(pl.Int64).unique().to_list()
    print(f"subjects (from ATLAS): {len(subjects):,}")

    IHD = included_concept_ids(args.risk_zip, 3)
    DIFF = included_concept_ids(args.risk_zip, 1)
    EMB = included_concept_ids(args.risk_zip, 4)
    ACUTE_YL = included_concept_ids(args.risk_zip, 0)   # no-AMI-7d set
    AMI = included_concept_ids(args.case_zip, 3)        # case Acute MI
    VISIT_IPER = included_concept_ids(args.case_zip, 2)  # inpatient/ER visit concepts
    direct = list(set(IHD) | set(DIFF))
    print(f"concept ids: IHD {len(IHD)} | DIFF {len(DIFF)} | EMB {len(EMB)} | AMI {len(AMI)} | VISIT {len(VISIT_IPER)}")

    cond = load(args.omop, "condition_occurrence",
                ["condition_concept_id", "condition_start_date"], subjects)
    cond = cond.with_columns(pl.col("condition_concept_id").cast(pl.Int64), d("condition_start_date").alias("cd"))
    visit = load(args.omop, "visit_occurrence",
                 ["visit_concept_id", "visit_start_date", "visit_end_date", "visit_start_datetime"], subjects)
    dtcol = "visit_start_datetime" if "visit_start_datetime" in visit.columns else "visit_start_date"
    vend = "visit_end_date" if "visit_end_date" in visit.columns else "visit_start_date"
    visit = visit.with_columns(
        pl.col("visit_concept_id").cast(pl.Int64), d("visit_start_date").alias("vd"), d(vend).alias("ved"),
        pl.col(dtcol).cast(pl.Utf8).str.slice(0, 19).str.to_datetime(strict=False).alias("vt"))
    drug = load(args.omop, "drug_exposure", ["drug_exposure_start_date"], subjects).with_columns(
        d("drug_exposure_start_date").alias("cd"))
    op = load(args.omop, "observation_period",
              ["observation_period_start_date", "observation_period_end_date"], subjects)
    op = op.with_columns(d("observation_period_start_date").alias("ostart"), d("observation_period_end_date").alias("oend"))

    # ---- at-risk entry = First of {CS1/CS3, CS4} ----
    # NB: the published ami_at_risk.json wraps CS4 in a corroboration ("(>=2 prior CS4) OR (CS4 obs
    # + smoking value)"), but the LIVE benchmark cohort enters on the *first* CS4 (uncorroborated) --
    # verified on residual subjects: ATLAS entry lands on the 1st CS4, not the 2nd. So we admit CS4
    # at >=1 to match the cohort that was actually built (this also matches ACES's risk_entry).
    de = (cond.filter(pl.col("condition_concept_id").is_in(direct))
          .group_by("person_id").agg(pl.col("cd").min().alias("e_direct")))
    e_cs4 = (cond.filter(pl.col("condition_concept_id").is_in(EMB))
             .group_by("person_id").agg(pl.col("cd").min().alias("e_cs4")))
    entry = (op.select("person_id").unique()
             .join(de, on="person_id", how="left")
             .join(e_cs4, on="person_id", how="left")
             .with_columns(pl.min_horizontal("e_direct", "e_cs4").alias("entry"))
             .filter(pl.col("entry").is_not_null()))
    # inclusion rule: drop subjects with an Acute-MI (CS0) in [entry-7d, entry-1d]
    acute = cond.filter(pl.col("condition_concept_id").is_in(ACUTE_YL)).select("person_id", pl.col("cd").alias("ad"))
    bad = (entry.join(acute, on="person_id", how="left")
           .filter((pl.col("ad") >= pl.col("entry") - pl.duration(days=7)) & (pl.col("ad") <= pl.col("entry") - pl.duration(days=1)))
           .select("person_id").unique())
    entry = entry.join(bad.with_columns(pl.lit(True).alias("drop")), on="person_id", how="left").filter(pl.col("drop").is_null())
    # window bounds: cohort_end = end of the obs period CONTAINING entry (Circe default end strategy);
    # the 2yr lookback uses the earliest obs-period start (visit qualifies if >= any obs_start + 2yr).
    contain = (entry.select("person_id", "entry").join(op, on="person_id", how="left")
               .filter((pl.col("ostart") <= pl.col("entry")) & (pl.col("entry") <= pl.col("oend")))
               .group_by("person_id").agg(pl.col("oend").min().alias("cohort_end")))
    opmin = op.group_by("person_id").agg(pl.col("ostart").min().alias("ostart_min"), pl.col("oend").max().alias("oend_max"))
    entry = (entry.select("person_id", "entry").join(contain, on="person_id", how="left").join(opmin, on="person_id", how="left")
             .with_columns(pl.coalesce("cohort_end", "oend_max").alias("cohort_end")))
    print(f"at-risk subjects: {entry.height:,}")

    # ---- case cohort: Acute-MI(CS3) during an inpatient/ER visit(CS2), ERA collapse 180+7 ----
    vip = visit.filter(pl.col("visit_concept_id").is_in(VISIT_IPER)).select("person_id", "vd", "ved")
    amivis = (cond.filter(pl.col("condition_concept_id").is_in(AMI)).select("person_id", pl.col("cd").alias("ami_d"))
              .join(vip, on="person_id", how="inner")
              .filter((pl.col("vd") <= pl.col("ami_d")) & (pl.col("ami_d") <= pl.col("ved")))
              .select("person_id", pl.col("ami_d").alias("case_d")).unique())
    # cohort interval [AMI, AMI+7] padded 180 -> merge if gap <= 187
    cases = collapse_eras(amivis, 187) if amivis.height else pl.DataFrame(schema={"person_id": pl.Int64, "case_start": pl.Date})
    print(f"case subjects: {cases['person_id'].n_unique() if cases.height else 0:,}")

    # ---- visit windowing + labels ----
    V = (visit.join(entry, on="person_id", how="inner")
         .filter((pl.col("vd") >= pl.col("entry")) & (pl.col("vd") < pl.col("cohort_end"))
                 & (pl.col("vd") >= pl.col("ostart_min") + pl.duration(days=365 * MIN_OBS_YEARS)))
         .select("person_id", "vd", "vt").unique())
    cd = pl.concat([cond.select("person_id", "cd"), drug.select("person_id", "cd")]).drop_nulls().sort(["person_id", "cd"])
    V = V.sort(["person_id", "vd"]).join_asof(cd.rename({"cd": "cdd"}), left_on="vd", right_on="cdd",
                                              by="person_id", strategy="backward")
    V = V.filter(((pl.col("vd") - pl.col("cdd")).dt.total_days() <= 365 * MIN_OBS_YEARS).fill_null(False))
    # labels via case exclusion (one row per visit x case era; drop -1, then max)
    V = V.join(cases, on="person_id", how="left").with_columns(
        pl.when(pl.col("case_start").is_null()).then(0)
        .when(pl.col("case_start") < pl.col("vd")).then(-1)
        .when(pl.col("case_start") <= pl.col("vd") + pl.duration(days=365 * PREDICTION_YEARS)).then(1)
        .otherwise(0).alias("b"))
    out = (V.filter(pl.col("b") != -1).group_by(["person_id", "vd", "vt"]).agg(pl.col("b").max().alias("boolean_value"))
           .select(pl.col("person_id").alias("subject_id"), pl.col("vt").alias("prediction_time"), "boolean_value"))
    out.write_parquet(args.output)

    n = out["subject_id"].n_unique()
    print(f"\nREPRODUCED: {out.height:,} points | {n:,} subjects | {out.height / max(1, n):.1f} pts/subject")
    print(f"ORIGINAL ATLAS: {atlas.height:,} points | {atlas['subject_id'].n_unique():,} subjects | "
          f"{atlas.height / atlas['subject_id'].n_unique():.1f} pts/subject")
    print(f"wrote {args.output}  ->  compare with compare_aces_atlas.py (--aces {args.output} --atlas {args.atlas})")


if __name__ == "__main__":
    main()
