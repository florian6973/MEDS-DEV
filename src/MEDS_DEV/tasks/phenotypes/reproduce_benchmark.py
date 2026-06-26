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
    """Standard concept ids of a concept set (frozen snapshot) from includedConcepts.csv."""
    with zipfile.ZipFile(zip_path) as z:
        rows = csv.DictReader(io.StringIO(z.read("includedConcepts.csv").decode("utf-8-sig")))
        return [int(r["Concept ID"]) for r in rows if int(r["Concept Set ID"]) == cs_id]


def seed_specs(zip_path: str, cs_id: int) -> list[tuple[int, bool]]:
    """(concept_id, is_excluded) seeds of a concept set, from conceptSetExpression.csv."""
    with zipfile.ZipFile(zip_path) as z:
        rows = csv.DictReader(io.StringIO(z.read("conceptSetExpression.csv").decode("utf-8-sig")))
        return [(int(r["Concept ID"]), r["Exclude"].strip().lower() == "true")
                for r in rows if int(r["Concept Set ID"]) == cs_id]


def load_concept_ancestor(omop: str):
    """Lazy concept_ancestor (parquet dir or *concept_ancestor*.csv) with columns anc/desc; None if absent."""
    pq = [f for f in glob.glob(os.path.join(omop, "concept_ancestor", "**", "*.parquet"), recursive=True)
          if not os.path.basename(f).startswith(".")]
    if pq:
        lf = pl.scan_parquet(pq)
    else:
        csvs = [f for f in glob.glob(os.path.join(omop, "*.csv")) if "concept_ancestor" in os.path.basename(f).lower()]
        if not csvs:
            return None
        lf = pl.scan_csv(csvs[0], infer_schema_length=0)
    cols = {c.lower(): c for c in lf.collect_schema().names()}
    return lf.select(pl.col(cols["ancestor_concept_id"]).cast(pl.Int64, strict=False).alias("anc"),
                     pl.col(cols["descendant_concept_id"]).cast(pl.Int64, strict=False).alias("desc"))


def load(omop: str, table: str, cols: list[str], subjects: list[int]) -> pl.DataFrame:
    """Scan an OMOP table, keep only the requested subjects + available columns."""
    fs = glob.glob(os.path.join(omop, table, "**", "*.parquet"), recursive=True)
    fs = [f for f in fs if not os.path.basename(f).startswith(".")]  # skip hidden .crc files
    if not fs:
        raise SystemExit(f"[load] no parquet files under {os.path.join(omop, table)!r} "
                         f"(searched recursively). Check the table directory name/path.")
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
    ap.add_argument("--subjects-from", default=None,
                    help="parquet file or dir of shards (MEDS or ATLAS) to take the subject set from "
                         "(reads subject_id/person_id/patient_id). Use the MEDS tuning shards to "
                         "reproduce the WHOLE tuning split for an apples-to-apples ACES compare. "
                         "Default: subjects = the --atlas cohort's subjects (the downsampled sample).")
    ap.add_argument("--case-zip", required=True)
    ap.add_argument("--risk-zip", required=True)
    ap.add_argument("--output", required=True, help="reproduced cohort parquet")
    # gate toggles -- knock out one benchmark visit-gate at a time to measure its share of the
    # ACES-vs-benchmark gap (run each, watch subject/point count climb toward the ACES cohort).
    ap.add_argument("--no-depth-gate", action="store_true",
                    help="drop the #10 lower bound (vd >= earliest obs_start + 2yr)")
    ap.add_argument("--no-cohort-end", action="store_true",
                    help="drop the #10 upper bound (vd < end of obs period containing entry)")
    ap.add_argument("--no-recent-gate", action="store_true",
                    help="drop the #9 recent condition/drug requirement (ACES uses looser _ANY_EVENT)")
    ap.add_argument("--depth-anchor", choices=["obs_start", "first_visit"], default="obs_start",
                    help="depth-gate reference: obs_start (observation_period start = the benchmark) "
                         "or first_visit (min visit date = the MEDS-expressible proxy ACES can use)")
    args = ap.parse_args()

    atlas = pl.read_parquet(args.atlas)
    if args.subjects_from:
        sp = args.subjects_from
        files = ([f for f in glob.glob(os.path.join(sp, "**", "*.parquet"), recursive=True) if ".logs" not in f]
                 if os.path.isdir(sp) else [sp])
        if not files:
            raise SystemExit(f"--subjects-from matched no parquet under {sp!r}")
        names = pl.scan_parquet(files[0]).collect_schema().names()
        col = next((c for c in ("subject_id", "person_id", "patient_id") if c in names), None)
        if col is None:
            raise SystemExit(f"--subjects-from {sp!r}: no subject id column in {names}")
        subjects = (pl.scan_parquet(files).select(pl.col(col).cast(pl.Int64, strict=False))
                    .drop_nulls().unique().collect().to_series().to_list())
        print(f"subjects (from {sp}, col '{col}'): {len(subjects):,}")
    else:
        subjects = atlas["subject_id"].cast(pl.Int64).unique().to_list()
        print(f"subjects (from ATLAS sample): {len(subjects):,}")

    # Concept ids: re-resolve seed + descendants via the dataset's OWN concept_ancestor. The zip's
    # includedConcepts is a frozen snapshot that lags the live vocabulary (verified: it missed the
    # at-risk entry concept for the residual subjects). Fall back to the snapshot if no concept_ancestor.
    specs = {"IHD": (args.risk_zip, 3), "DIFF": (args.risk_zip, 1), "EMB": (args.risk_zip, 4),
             "ACUTE": (args.risk_zip, 0), "AMI": (args.case_zip, 3), "VISIT": (args.case_zip, 2)}
    ca = load_concept_ancestor(args.omop)
    if ca is not None:
        seedmap = {k: seed_specs(*v) for k, v in specs.items()}
        all_seeds = list({c for s in seedmap.values() for c, _ in s})
        g = ca.filter(pl.col("anc").is_in(all_seeds)).group_by("anc").agg(pl.col("desc")).collect()
        desc_by = {a: set(ds) for a, ds in zip(g["anc"].to_list(), g["desc"].to_list())}

        def resolve(k):
            inc = {c for c, ex in seedmap[k] if not ex}
            exc = {c for c, ex in seedmap[k] if ex}
            expand = lambda ids: set(ids) | {d for i in ids for d in desc_by.get(i, set())}
            return list(expand(inc) - expand(exc))

        IHD, DIFF, EMB, ACUTE_YL, AMI, VISIT_IPER = (resolve(k) for k in ("IHD", "DIFF", "EMB", "ACUTE", "AMI", "VISIT"))
        print("concept sets re-resolved from seeds via concept_ancestor")
    else:
        IHD = included_concept_ids(args.risk_zip, 3); DIFF = included_concept_ids(args.risk_zip, 1)
        EMB = included_concept_ids(args.risk_zip, 4); ACUTE_YL = included_concept_ids(args.risk_zip, 0)
        AMI = included_concept_ids(args.case_zip, 3); VISIT_IPER = included_concept_ids(args.case_zip, 2)
        print("concept sets from zip includedConcepts snapshot (no concept_ancestor found)")
    direct = list(set(IHD) | set(DIFF))
    print(f"concept ids: IHD {len(IHD)} | DIFF {len(DIFF)} | EMB {len(EMB)} | AMI {len(AMI)} | VISIT {len(VISIT_IPER)}")

    cond = load(args.omop, "condition_occurrence",
                ["condition_concept_id", "condition_start_date"], subjects)
    cond = cond.with_columns(pl.col("condition_concept_id").cast(pl.Int64), d("condition_start_date").alias("cd"))
    visit = load(args.omop, "visit_occurrence",
                 ["visit_concept_id", "visit_start_date", "visit_end_date", "visit_start_datetime"], subjects)
    dtcol = "visit_start_datetime" if "visit_start_datetime" in visit.columns else "visit_start_date"
    vend = "visit_end_date" if "visit_end_date" in visit.columns else "visit_start_date"
    # NB: harmonized stores condition *_datetime as midnight (real time lost), so the case "during a
    # visit" CONTAINMENT is done at DATE granularity (datetime there would drop AMIs whose only
    # inpatient/ER visit starts after 00:00 -- verified: 44 FN). The case-EXCLUSION label, however,
    # compares case_start_date to visit_start_DATETIME (vt) exactly as the SQL -- visit times are reliable.
    visit = visit.with_columns(
        pl.col("visit_concept_id").cast(pl.Int64), d("visit_start_date").alias("vd"), d(vend).alias("ved"),
        pl.col(dtcol).cast(pl.Utf8).str.to_datetime(strict=False).alias("vt"))
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
    # "during" = the AMI's DATE falls inside the CS2 visit's [start_date, end_date] interval (ATLAS's
    # AdditionalCriteria, at date granularity -- see note above on lost condition times).
    vip = visit.filter(pl.col("visit_concept_id").is_in(VISIT_IPER)).select("person_id", "vd", "ved")
    amivis = (cond.filter(pl.col("condition_concept_id").is_in(AMI)).select("person_id", pl.col("cd").alias("ami_d"))
              .join(vip, on="person_id", how="inner")
              .filter((pl.col("vd") <= pl.col("ami_d")) & (pl.col("ami_d") <= pl.col("ved")))
              .select("person_id", pl.col("ami_d").alias("case_d")).unique())
    # cohort interval [AMI, AMI+7] padded 180 -> merge if gap <= 187
    cases = collapse_eras(amivis, 187) if amivis.height else pl.DataFrame(schema={"person_id": pl.Int64, "case_start": pl.Date})
    print(f"case subjects: {cases['person_id'].n_unique() if cases.height else 0:,}")

    # ---- visit windowing + labels ----
    fv = visit.group_by("person_id").agg(pl.col("vd").min().alias("fv"))  # first-visit proxy for obs-start
    Vsrc = visit.join(entry, on="person_id", how="inner").join(fv, on="person_id", how="left")
    vfilt = pl.col("vd") >= pl.col("entry")
    if not args.no_cohort_end:
        vfilt = vfilt & (pl.col("vd") < pl.col("cohort_end"))
    if not args.no_depth_gate:
        anchor = pl.col("fv") if args.depth_anchor == "first_visit" else pl.col("ostart_min")
        vfilt = vfilt & (pl.col("vd") >= anchor + pl.duration(days=365 * MIN_OBS_YEARS))
    V = Vsrc.filter(vfilt).select("person_id", "vd", "vt").unique()
    if not args.no_recent_gate:
        cd = pl.concat([cond.select("person_id", "cd"), drug.select("person_id", "cd")]).drop_nulls().sort(["person_id", "cd"])
        V = V.sort(["person_id", "vd"]).join_asof(cd.rename({"cd": "cdd"}), left_on="vd", right_on="cdd",
                                                  by="person_id", strategy="backward")
        V = V.filter(((pl.col("vd") - pl.col("cdd")).dt.total_days() <= 365 * MIN_OBS_YEARS).fill_null(False))
    gates = [g for g, off in [("depth", args.no_depth_gate), ("cohort_end", args.no_cohort_end),
                              ("recent", args.no_recent_gate)] if not off]
    print(f"active visit gates: {gates or ['(none)']}")
    # labels via case exclusion (one row per visit x case era; drop -1, then max). The SQL compares
    # case_start_DATE to visit_start_DATETIME, so a same-day case (case_start midnight < visit time)
    # excludes the visit -- we replicate that by casting case_start to a midnight datetime vs vt.
    V = V.join(cases, on="person_id", how="left").with_columns(
        pl.col("case_start").cast(pl.Datetime("us")).alias("cs_dt")).with_columns(
        pl.when(pl.col("case_start").is_null()).then(0)
        .when(pl.col("cs_dt") < pl.col("vt")).then(-1)
        .when(pl.col("cs_dt") <= pl.col("vt") + pl.duration(days=365 * PREDICTION_YEARS)).then(1)
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
