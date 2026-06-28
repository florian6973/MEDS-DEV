#!/usr/bin/env python3
"""Porting diagnostics for the AMI task on a new MEDS dataset (see PORTING.md).

Automates the *dataset-agnostic* parts of porting -- the parts that need no vocabulary judgment:

  1. SCHEME INVENTORY    -- vocab prefixes + the start/end (interval) convention, so you know the
                            token shape before writing any predicate.
  2. SUPPORTABILITY      -- which windows this dataset can express, from the presence of visit
                            discharge events (#2 `during` encounter) and observation periods
                            (R4 depth / R5 cohort-end). Tells you which approximations you're forced
                            into BEFORE you start.
  3. SMOKE TEST          -- given a candidate predicates/task yaml, the match count of each base
                            predicate. A ZERO means the predicate is in the wrong coding scheme
                            (the failure that silently empties the cohort).

It does NOT resolve concept sets -- that needs a vocabulary and a per-scheme emit (concept_id vs
source code), so it stays in reproduce_benchmark.py:resolve / build_ami_task.py.

Usage:
    python port_check.py --meds <BUILD>/data/tuning
    python port_check.py --meds <BUILD>/data/tuning --predicates predicates/<DATASET>/ami_full.yaml
"""

from __future__ import annotations

import argparse
import glob
import os

import polars as pl
import yaml

BASE_PREDICATES = ["ami", "ip_er_start", "ip_er_end", "any_visit", "condition_or_drug",
                   "cs13", "cs4", "obs_period_start", "obs_period_end"]


def read_codes(meds_dir: str) -> pl.DataFrame:
    fs = [f for f in glob.glob(os.path.join(meds_dir, "**", "*.parquet"), recursive=True) if ".logs" not in f]
    if not fs:
        raise SystemExit(f"[meds] no parquet under {meds_dir!r}")
    return pl.read_parquet(fs, columns=["code"])


def inventory(m: pl.DataFrame) -> None:
    print("=" * 72 + "\nSCHEME INVENTORY\n" + "=" * 72)
    voc = (m.with_columns(pl.col("code").str.split("//").list.first().alias("v"))
           .group_by("v").len().sort("len", descending=True))
    print("top vocab prefixes (token before first '//'):")
    for r in voc.head(25).iter_rows(named=True):
        print(f"  {r['len']:>13,}  {r['v']}")
    n, ns, ne = m.height, m.filter(pl.col("code").str.contains("//start$")).height, m.filter(pl.col("code").str.contains("//end$")).height
    print(f"\ncodes: {n:,} | ending //start: {ns:,} | ending //end: {ne:,}")
    print("  interval (start/end) convention:",
          "YES" if ne > 0 else "NO  (no //end events -> `during` cannot be reconstructed)")
    print("sample distinct codes:")
    for c in m.select("code").unique().head(12)["code"].to_list():
        print("   ", c)


def supportability(m: pl.DataFrame) -> None:
    print("\n" + "=" * 72 + "\nSUPPORTABILITY  (which windows this dataset can express -- heuristic)\n" + "=" * 72)
    cnt = lambda rx: m.filter(pl.col("code").str.contains(rx)).height
    end = cnt("//end$")
    visit = cnt("(?i)(visit|admission|encounter|ed_reg|hospital)")
    iper_end = cnt("(?i)(visit//(ip|er)//end|discharge|hospital_discharge)")
    obs_s = cnt("(?i)(obs.{0,12}period.{0,12}//start|observation_period.{0,20}start)")
    obs_e = cnt("(?i)(obs.{0,12}period.{0,12}//end|observation_period.{0,20}end)")
    cond = cnt("(?i)^(snomed|icd|condition|ciel|nebraska|meddra)")
    drug = cnt("(?i)^(rxnorm|ndc|drug)")

    def line(name: str, ok: bool, note: str) -> None:
        print(f"  [{'OK ' if ok else 'NO '}] {name:30s} {note}")

    line("#2 encounter (during)", iper_end > 0 or (end > 0 and visit > 0),
         f"discharge-ish events: {iper_end:,} | any //end: {end:,}  (need admission AND discharge)")
    line("R4 depth / R5 cohort-end", obs_s > 0 and obs_e > 0,
         f"obs-period start/end: {obs_s:,}/{obs_e:,}  (absent -> first-event depth proxy, drop cohort-end)")
    line("#9 recent activity", cond > 0 or drug > 0, f"condition-ish: {cond:,} | drug-ish: {drug:,}")
    line("trigger (any_visit)", visit > 0, f"visit-ish events: {visit:,}")
    print("\n  (regex heuristics -- confirm against the inventory above before trusting them.)")


def smoke_test(m: pl.DataFrame, pred_file: str) -> None:
    print("\n" + "=" * 72 + "\nSMOKE TEST  (candidate predicates: per-predicate match count)\n" + "=" * 72)
    doc = yaml.safe_load(open(pred_file))
    preds = doc.get("predicates", doc)
    any_zero = False
    for name in BASE_PREDICATES:
        if name not in preds:
            print(f"  [skip] {name:20s} not in {pred_file}")
            continue
        spec = preds[name].get("code", preds[name])
        if "regex" in spec:
            c = m.filter(pl.col("code").str.contains(spec["regex"])).height
        elif "any" in spec:
            c = m.filter(pl.col("code").is_in(spec["any"])).height
        else:
            print(f"  [??]   {name:20s} not a plain code predicate (during/within/expr derived elsewhere)")
            continue
        if c == 0:
            any_zero = True
            print(f"  [ZERO] {name:20s} {c:>13,}  <-- matches nothing: wrong coding scheme?")
        else:
            print(f"  [OK ]  {name:20s} {c:>13,}")
    if any_zero:
        print("\n  ! At least one predicate matched ZERO events -> re-resolve into this dataset's scheme.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meds", required=True, help="MEDS shard dir (e.g. <build>/data/tuning)")
    ap.add_argument("--predicates", default=None, help="candidate predicates/task yaml to smoke-test")
    args = ap.parse_args()
    m = read_codes(args.meds)
    inventory(m)
    supportability(m)
    if args.predicates:
        smoke_test(m, args.predicates)
    print("\nNext (PORTING.md): resolve the concept sets into the scheme above "
          "(reproduce_benchmark.py:resolve for concept_id, build_ami_task.py for source codes),\n"
          "write predicates/<DATASET>/ami_full.yaml, then re-run with --predicates to smoke-test.")


if __name__ == "__main__":
    main()
