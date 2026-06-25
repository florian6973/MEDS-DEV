#!/usr/bin/env python3
"""Diagnose ACES-vs-ATLAS misalignment by inspecting the MEDS data at each cohort's prediction times.

The comparison (compare_aces_atlas.py) can show near-zero agreement for a structural reason rather
than a logic reason: the ATLAS cohort is built on OMOP MIMIC while ACES runs on MEDS MIMIC. If the
two use different subject identifiers (OMOP ``person_id`` vs MEDS ``subject_id``) or different MIMIC
date-shifts, then matching on ``(subject_id, prediction_time)`` compares unrelated rows.

For each cohort (ATLAS, and ACES if given) this checks whether its ``(subject_id, prediction_time)``
rows actually correspond to events in the MEDS data:

  * subject presence: how many of the cohort's subjects exist at all in the MEDS shards.
  * time-space: distance from each prediction time to the nearest MEDS admission of that subject
    (~0 -> aligned; ~years -> different time/id space).
  * code-at-prediction-time: for a sample of prediction points, the MEDS code(s) sitting at that
    exact timestamp (this is the key check the user asked for -- printed for BOTH cohorts). ACES
    times should land exactly on an admission code; if ATLAS times land on nothing, the OMOP and
    MEDS datasets are in different time spaces.

Usage:
    python diagnose_alignment.py \
        --meds  /data/.../MEDS_cohort/data/held_out \
        --atlas /data/.../phenotype_task_clean/ami/held_out.parquet \
        --aces  /data/.../phenotype_task_aces/ami/held_out \
        --n 8
"""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

ADMISSION_REGEX = "^(HOSPITAL_ADMISSION|ED_REGISTRATION)//"
SUBJ_ALIASES = ["subject_id", "person_id", "patient_id"]
TIME_ALIASES = ["prediction_time", "index_timestamp", "timestamp"]
LABEL_ALIASES = ["boolean_value", "label"]


def collect_parquets(p: Path) -> list[Path]:
    if p.is_dir():
        return sorted(f for f in p.rglob("*.parquet") if ".logs" not in f.parts)
    return [p]


def load_cohort(p: Path) -> pl.DataFrame:
    df = pl.concat([pl.read_parquet(f) for f in collect_parquets(p)], how="vertical_relaxed")
    s = next(c for c in SUBJ_ALIASES if c in df.columns)
    t = next(c for c in TIME_ALIASES if c in df.columns)
    lbl = next((c for c in LABEL_ALIASES if c in df.columns), None)
    cols = [pl.col(s).cast(pl.Int64).alias("subject_id"),
            pl.col(t).cast(pl.Datetime("ns")).alias("prediction_time")]
    cols.append((pl.col(lbl).cast(pl.Boolean) if lbl else pl.lit(None, pl.Boolean)).alias("label"))
    return df.select(cols).drop_nulls(["subject_id", "prediction_time"])


def codes_at(meds: pl.DataFrame, subject: int, t, window_days: int = 1):
    """Return (status, delta_days, codes) for the MEDS events nearest to time ``t`` for ``subject``."""
    m = meds.filter(pl.col("subject_id") == subject)
    if m.height == 0:
        return "absent", None, []
    exact = m.filter(pl.col("time") == t)
    if exact.height:
        return "exact", 0.0, exact["code"].to_list()
    m = m.with_columns(((pl.col("time") - t).dt.total_nanoseconds() / 1e9 / 86400).alias("d"))
    win = m.filter(pl.col("d").abs() <= window_days)
    if win.height:
        return f"<={window_days}d", float(win["d"].abs().min()), win.sort(pl.col("d").abs())["code"].head(4).to_list()
    nearest = m.sort(pl.col("d").abs()).head(1)
    return "nearest", float(nearest["d"][0]), nearest["code"].to_list()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meds", required=True, type=Path, help="dir of MEDS shards (e.g. .../data/held_out)")
    ap.add_argument("--atlas", required=True, type=Path, help="ATLAS split parquet")
    ap.add_argument("--aces", type=Path, default=None, help="ACES output dir/parquet (optional)")
    ap.add_argument("--n", type=int, default=8, help="prediction points to dump per cohort")
    ap.add_argument("--admission-regex", default=ADMISSION_REGEX)
    args = ap.parse_args()

    cohorts = {"ATLAS": load_cohort(args.atlas)}
    if args.aces:
        cohorts["ACES"] = load_cohort(args.aces)

    all_subs = pl.concat([c["subject_id"] for c in cohorts.values()]).unique()
    meds = (
        pl.scan_parquet(str(args.meds / "**" / "*.parquet"))
        .filter(pl.col("subject_id").cast(pl.Int64).is_in(all_subs.implode()))
        .select(pl.col("subject_id").cast(pl.Int64), pl.col("time").cast(pl.Datetime("ns")), pl.col("code"))
        .collect()
    )
    present = set(meds["subject_id"].unique().to_list())
    adm = (meds.filter(pl.col("code").str.contains(args.admission_regex))
           .select("subject_id", pl.col("time").alias("adm_time")).sort("adm_time"))

    for name, coh in cohorts.items():
        subs = coh["subject_id"].unique()
        in_meds = [s for s in subs.to_list() if s in present]
        print("=" * 78)
        print(f"{name}: SUBJECT PRESENCE")
        print("=" * 78)
        print(f"  subjects: {subs.len():,} | present in MEDS: {len(in_meds):,} "
              f"({100 * len(in_meds) / max(1, subs.len()):.1f}%)")
        if not in_meds:
            print("  !! none present -> id spaces differ (need an OMOP person_id <-> MEDS subject_id crosswalk)")
            continue

        # time-space: nearest admission
        shared = coh.filter(pl.col("subject_id").is_in(pl.Series(in_meds).implode())).sort("prediction_time")
        near = shared.join_asof(adm, left_on="prediction_time", right_on="adm_time",
                                by="subject_id", strategy="nearest").with_columns(
            ((pl.col("prediction_time") - pl.col("adm_time")).dt.total_nanoseconds() / 1e9 / 86400).abs().alias("d"))
        d = near["d"].drop_nulls()
        if d.len():
            print(f"  |pred_time - nearest MEDS admission|: within 1d {100*(d<=1).mean():.1f}% | "
                  f"within 7d {100*(d<=7).mean():.1f}% | median {d.median():,.1f}d | max {d.max():,.1f}d")

        # code-at-prediction-time (the key check, for THIS cohort)
        print(f"\n  CODES AT {name} PREDICTION TIMES (sample of {args.n}):")
        sample = shared.unique(subset=["subject_id", "prediction_time"]).sort(["subject_id", "prediction_time"]).head(args.n)
        for r in sample.iter_rows(named=True):
            status, dd, codes = codes_at(meds, r["subject_id"], r["prediction_time"])
            delta = "" if dd in (0.0, None) else f" (off {dd:+.1f}d)"
            shown = ", ".join(codes[:4]) + (" ..." if len(codes) > 4 else "") if codes else "(no events)"
            print(f"    subj {r['subject_id']} @ {r['prediction_time']} label={r['label']} "
                  f"-> [{status}{delta}] {shown}")
        print()


if __name__ == "__main__":
    main()
