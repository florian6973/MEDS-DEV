#!/usr/bin/env python3
"""Compare an ACES-extracted task cohort against the original ATLAS/SQL benchmark cohort.

Both sides are MEDS label tables: ``subject_id``, ``prediction_time``, ``boolean_value`` (plus
the unused integer/float/categorical columns). This script loads the ACES output shards and the
(merged) ATLAS split parquets, runs sanity checks that catch the three things most likely to make
the comparison meaningless, then matches prediction points and reports cohort/label agreement.

The three sanity checks (see AMI_TASK_REPORT.md "Compare" section):
  1. subject_id identity  -- ATLAS was built in OMOP (person_id); MEDS uses subject_id. If the id
     spaces don't overlap, every downstream number is garbage, so we fail loudly.
  2. prediction_time alignment -- ACES anchors to the MEDS admission/ED event time; ATLAS to OMOP
     visit_start_datetime. Same event, two ETLs -> possible granularity/shift mismatch. We report
     the nearest-match time-delta distribution and a day-resolution flag, and offer --tolerance.
  3. trigger multiplicity -- ACES may fire on ED *and* inpatient admissions of one stay; ATLAS may
     collapse to one visit. We compare per-subject prediction-point counts.

Usage:
    python compare_aces_atlas.py \
        --aces  /path/to/ami_aces_out \
        --atlas /path/to/atlas/train.parquet /path/to/atlas/tuning.parquet /path/to/atlas/held_out.parquet \
        --tolerance 0s \
        --out /path/to/discordances   # optional: dump mismatching rows as parquet

``--atlas`` accepts files and/or directories (directories are globbed for ``*.parquet``).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import polars as pl

# canonical -> accepted source names (first match wins)
COL_ALIASES = {
    "subject_id": ["subject_id", "patient_id", "person_id"],
    "prediction_time": ["prediction_time", "index_timestamp", "timestamp"],
    "boolean_value": ["boolean_value", "label", "boolean_label"],
}


def parse_tolerance_ns(s: str) -> int:
    """Parse a tolerance string into nanoseconds. Bare numbers are seconds.

    >>> parse_tolerance_ns("0")
    0
    >>> parse_tolerance_ns("1d") == 24 * 3600 * 10**9
    True
    >>> parse_tolerance_ns("30m") == 30 * 60 * 10**9
    True
    """
    s = s.strip().lower()
    m = re.fullmatch(r"(\d+)\s*(ns|us|ms|s|m|h|d)?", s)
    if not m:
        raise argparse.ArgumentTypeError(f"Bad tolerance: {s!r} (try 0s, 500ms, 12h, 1d)")
    n = int(m.group(1))
    unit = m.group(2) or "s"
    factor = {
        "ns": 1,
        "us": 1_000,
        "ms": 1_000_000,
        "s": 1_000_000_000,
        "m": 60 * 10**9,
        "h": 3600 * 10**9,
        "d": 86400 * 10**9,
    }[unit]
    return n * factor


def collect_parquets(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(f for f in p.rglob("*.parquet") if ".logs" not in f.parts)
        elif p.exists():
            files.append(p)
        else:
            raise SystemExit(f"Path does not exist: {p}")
    if not files:
        raise SystemExit(f"No parquet files found under: {paths}")
    return files


def normalize_one(df: pl.DataFrame) -> pl.DataFrame | None:
    """Reduce one parquet to the 3 canonical columns, or None if any is missing.

    ACES writes only a ``subject_id`` column for empty shards (no prediction points); those are
    returned as None and skipped, keeping schemas consistent for concat.
    """
    rename = {}
    for canonical, candidates in COL_ALIASES.items():
        found = next((c for c in candidates if c in df.columns), None)
        if found is None:
            return None
        rename[found] = canonical
    return df.rename(rename).select(
        pl.col("subject_id").cast(pl.Int64, strict=False),
        pl.col("prediction_time").cast(pl.Datetime("ns"), strict=False),
        pl.col("boolean_value").cast(pl.Boolean, strict=False),
    )


def load_normalized(paths: list[Path], side: str) -> pl.DataFrame:
    """Load parquets, normalize each to canonical columns, concat; skip empty/incompatible shards."""
    files = collect_parquets(paths)
    frames, skipped = [], 0
    for f in files:
        norm = normalize_one(pl.read_parquet(f))
        if norm is None:
            skipped += 1
        else:
            frames.append(norm)
    if not frames:
        raise SystemExit(
            f"[{side}] no file had subject_id/prediction_time/boolean_value columns "
            f"(checked {len(files)} file(s))"
        )
    df = pl.concat(frames, how="vertical_relaxed")
    print(f"[{side}] {len(files)} file(s) ({skipped} empty/skipped), {df.height:,} rows")
    return df


def h(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def fmt_pct(num: int, den: int) -> str:
    return f"{num:,} ({100 * num / den:.1f}%)" if den else f"{num:,} (n/a)"


def sanity_checks(aces: pl.DataFrame, atlas: pl.DataFrame) -> set[int]:
    h("SANITY CHECKS")

    # null ids/times from failed casts
    for side, df in [("ACES", aces), ("ATLAS", atlas)]:
        n_bad_id = df["subject_id"].null_count()
        n_bad_t = df["prediction_time"].null_count()
        if n_bad_id or n_bad_t:
            print(f"  ! [{side}] {n_bad_id} null subject_id, {n_bad_t} null prediction_time after cast")

    a_subj = set(aces["subject_id"].drop_nulls().to_list())
    b_subj = set(atlas["subject_id"].drop_nulls().to_list())
    inter = a_subj & b_subj
    union = a_subj | b_subj

    print("\n[1] subject_id identity")
    print(f"    ACES subjects:  {len(a_subj):,}")
    print(f"    ATLAS subjects: {len(b_subj):,}")
    print(f"    shared:         {fmt_pct(len(inter), len(union))}  (Jaccard)")
    print(f"    only ACES:      {len(a_subj - b_subj):,}")
    print(f"    only ATLAS:     {len(b_subj - a_subj):,}")
    if not inter:
        print("    !! NO SHARED subject_ids -- id spaces do not align (OMOP person_id vs MEDS")
        print("       subject_id?). Comparison below is meaningless until this is fixed.")
    elif len(inter) / max(1, len(union)) < 0.5:
        print("    ! Low subject overlap (<50%). Check id mapping before trusting label metrics.")

    print("\n[2] prediction_time alignment")
    for side, df in [("ACES", aces), ("ATLAS", atlas)]:
        t = df["prediction_time"].drop_nulls()
        frac_midnight = (
            t.dt.hour().eq(0) & t.dt.minute().eq(0) & t.dt.second().eq(0)
        ).mean() if t.len() else None
        res = "day-resolution" if (frac_midnight is not None and frac_midnight > 0.99) else "sub-day"
        print(f"    [{side}] range {t.min()} .. {t.max()} | {res} "
              f"({'all midnight' if res == 'day-resolution' else 'has time-of-day'})")

    print("\n[3] trigger multiplicity (prediction points per subject)")
    for side, df in [("ACES", aces), ("ATLAS", atlas)]:
        counts = df.group_by("subject_id").len()["len"]
        dups = df.height - df.select(["subject_id", "prediction_time"]).unique().height
        print(f"    [{side}] points/subject: mean {counts.mean():.2f}, "
              f"median {counts.median():.0f}, max {counts.max()} | "
              f"duplicate (subject,time) rows: {dups:,}")

    return inter


def time_delta_diagnostic(aces: pl.DataFrame, atlas: pl.DataFrame) -> None:
    """For each ACES point, nearest ATLAS point of the same subject; report |Δt| distribution."""
    h("TIME-DELTA DIAGNOSTIC (nearest ATLAS point per ACES point, shared subjects)")
    atlas2 = atlas.rename({"prediction_time": "atlas_time", "boolean_value": "atlas_label"}).sort("atlas_time")
    aces2 = aces.sort("prediction_time")
    joined = aces2.join_asof(
        atlas2, left_on="prediction_time", right_on="atlas_time", by="subject_id", strategy="nearest"
    ).with_columns((pl.col("prediction_time") - pl.col("atlas_time")).dt.total_nanoseconds().alias("dt_ns"))
    dt = joined["dt_ns"].drop_nulls()
    if dt.len() == 0:
        print("    no shared-subject points to compare.")
        return joined
    absdt = dt.abs()
    to_s = lambda ns: f"{ns / 1e9:,.1f}s"
    print(f"    matched (same subject): {dt.len():,}")
    print(f"    |dt| exact (0):  {fmt_pct((absdt == 0).sum(), absdt.len())}")
    print(f"    |dt| <= 1 day:   {fmt_pct((absdt <= 86400 * 10**9).sum(), absdt.len())}")
    print(f"    |dt| p50 {to_s(absdt.median())} | p90 {to_s(absdt.quantile(0.9))} | max {to_s(absdt.max())}")
    if (absdt == 0).sum() / absdt.len() < 0.5 and absdt.median() > 0:
        print("    ! Exact timestamps rarely match -> use --tolerance (e.g. 1d) for label comparison.")
    return joined


def match_and_compare(joined: pl.DataFrame, atlas: pl.DataFrame, tol_ns: int, out: Path | None) -> None:
    h(f"PREDICTION-POINT MATCHING  (tolerance = {tol_ns / 1e9:g}s)")
    joined = joined.with_columns(pl.col("dt_ns").abs().alias("abs_dt"))
    matched = joined.filter(pl.col("abs_dt").is_not_null() & (pl.col("abs_dt") <= tol_ns))
    only_aces = joined.filter(pl.col("abs_dt").is_null() | (pl.col("abs_dt") > tol_ns))

    n_aces = joined.height
    print(f"    ACES points:            {n_aces:,}")
    print(f"    matched to ATLAS:       {fmt_pct(matched.height, n_aces)}")
    print(f"    unmatched (only ACES):  {only_aces.height:,}")

    # reverse direction: ATLAS points with no ACES point within tolerance
    atlas_keyed = atlas.with_columns(
        (pl.col("subject_id").cast(pl.Utf8) + "|" + pl.col("prediction_time").cast(pl.Utf8)).alias("k")
    )
    matched_atlas_keys = set(
        matched.with_columns(
            (pl.col("subject_id").cast(pl.Utf8) + "|" + pl.col("atlas_time").cast(pl.Utf8)).alias("k")
        )["k"].to_list()
    )
    only_atlas = atlas_keyed.filter(~pl.col("k").is_in(list(matched_atlas_keys)))
    print(f"    unmatched (only ATLAS): {only_atlas.height:,}")

    if matched.height:
        h("LABEL AGREEMENT (on matched points)")
        cm = (
            matched.group_by(["boolean_value", "atlas_label"])
            .len()
            .sort(["boolean_value", "atlas_label"])
        )
        # 2x2 grid
        def cell(a, b):
            r = cm.filter((pl.col("boolean_value") == a) & (pl.col("atlas_label") == b))
            return int(r["len"][0]) if r.height else 0
        tt, tf, ft, ff = cell(True, True), cell(True, False), cell(False, True), cell(False, False)
        agree, total = tt + ff, tt + tf + ft + ff
        print("                          ATLAS=1     ATLAS=0")
        print(f"        ACES=1           {tt:>9,}   {tf:>9,}")
        print(f"        ACES=0           {ft:>9,}   {ff:>9,}")
        print(f"\n    agreement:    {fmt_pct(agree, total)}")
        print(f"    ACES prevalence:  {fmt_pct(tt + tf, total)}")
        print(f"    ATLAS prevalence: {fmt_pct(tt + ft, total)}")
        if cm["len"].sum() != total:
            print(f"    ! {cm['len'].sum() - total} matched rows had a null label (excluded above)")

    if out:
        out.mkdir(parents=True, exist_ok=True)
        disagree = matched.filter(pl.col("boolean_value") != pl.col("atlas_label"))
        disagree.write_parquet(out / "label_disagreements.parquet")
        only_aces.write_parquet(out / "only_in_aces.parquet")
        only_atlas.drop("k").write_parquet(out / "only_in_atlas.parquet")
        print(f"\n    wrote disagreements + unmatched rows to {out}")

    h("INTERPRETATION POINTERS (map differences back to fidelity gaps)")
    print("    - extra ACES subjects/points  -> likely #6 over-inclusion (broad risk_entry) or")
    print("                                     #9 _ANY_EVENT looser gate / #10 no obs-period gate.")
    print("    - missing (only-ATLAS) points -> likely #1 trigger narrowed to inpatient/ER, or")
    print("                                     time misalignment (raise --tolerance).")
    print("    - label flips on matched pts  -> likely #2 outcome not encounter-restricted, or")
    print("                                     horizon-edge / first-AMI boundary differences.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aces", nargs="+", required=True, type=Path, help="ACES output dir(s)/file(s)")
    ap.add_argument("--atlas", nargs="+", required=True, type=Path, help="ATLAS split parquet(s)/dir(s)")
    ap.add_argument("--tolerance", type=parse_tolerance_ns, default=0,
                    help="max |Δt| to call two prediction points the same (e.g. 0s, 1d). Default 0.")
    ap.add_argument("--out", type=Path, default=None, help="optional dir to dump mismatching rows")
    args = ap.parse_args()

    h("LOADING")
    aces = load_normalized(args.aces, "ACES")
    atlas = load_normalized(args.atlas, "ATLAS")

    inter = sanity_checks(aces, atlas)
    if not inter:
        print("\nStopping: no shared subjects -> fix id mapping first.")
        sys.exit(2)

    joined = time_delta_diagnostic(aces, atlas)
    match_and_compare(joined, atlas, args.tolerance, args.out)


if __name__ == "__main__":
    main()
