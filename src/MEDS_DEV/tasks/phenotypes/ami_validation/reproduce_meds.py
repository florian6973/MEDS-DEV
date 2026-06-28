#!/usr/bin/env python3
"""Reproduce the reAIM-Lab AMI benchmark cohort **on an OMOP-MEDS build** (polars).

This is the MEDS-side twin of ``reproduce_benchmark.py`` (which runs on the raw OMOP CDM). Run both
on the *same* harmonized CUMC patients and compare: where they disagree is **$H<->MEDS data drift**,
not logic. It is also the independent reference the ACES task is validated against
(``ACES-YAML == reproduce_meds = 0/0`` at every rung -- see ``AMI_TASK_REPORT.md``).

It reads a MEDS shard directory and a **resolved concept-id predicates YAML** (the same base
predicates an ACES task reads -- ``ami``, ``ip_er_start/end``, ``cs13``, ``cs4``, ``smoking``,
``any_visit``, ``condition_or_drug``, ``obs_period_start/end``), and builds the per-visit cohort. The
relational predicates (``during`` for the encounter-AMI and the cohort-end, ``within`` for smoking)
are computed here directly (net-open interval reconstruction), matching the ACES ``interval_logic``
fork semantics exactly.

Two presets via ``--mode``:
  * ``aces`` -- mirrors the ACES task: encounter-AMI via ``during``, first-AMI ``no_prior``,
    corroborated CS4 (>=2), obs-start depth, cohort-end = visit inside *any* obs period
    (``during(any_visit, obs_start, obs_end)``). This is what ``ACES == this`` validates.
  * ``r0`` -- byte-faithful to ``reproduce_benchmark.py``'s full benchmark (R5/R6): ERA-collapse 187d,
    last-AMI ``MAX`` semantics, CS4>=1 (the live cohort ignores corroboration), cohort-end = end of
    the obs period *containing the entry*. Use this to attribute the residual vs the OMOP side.

Individual cfg keys override the preset (see ``--help``).

Usage:
    python reproduce_meds.py --meds <MEDS>/data/tuning --predicates predicates/CUMC/ami_full.yaml \
        --mode aces  --output meds_aces.parquet
    python reproduce_meds.py --meds <MEDS>/data/tuning --predicates predicates/CUMC/ami_full.yaml \
        --mode r0    --output meds_r0.parquet
    # then: python -c "from reproduce_benchmark import cmp1, load_cohort_file as L; \
    #                   print(cmp1(L('omop_r0.parquet'), L('meds_aces.parquet')))"
"""

from __future__ import annotations

import argparse
import glob
import os

import polars as pl
import yaml

US = pl.duration(microseconds=1)
DAY = lambda n: pl.duration(days=n)
MIN_OBS_YEARS = 2
PREDICTION_YEARS = 1
ERA_GAP_DAYS = 187

# Base code predicates this reference needs from the predicates YAML. Relational predicates
# (ami_encounter, visit_in_obs, cs4_smk) are derived here, not read.
BASE_PREDICATES = ["ami", "ip_er_start", "ip_er_end", "any_visit", "condition_or_drug",
                   "cs13", "cs4", "smoking", "obs_period_start", "obs_period_end"]

PRESETS = {
    # ACES task semantics (what `ACES == reproduce_meds = 0/0` validates). cs4_min=1 matches the LIVE
    # ami_full.yaml (ATLAS labels ignore the CS4 corroboration); pass --cs4-min 2 for the published variant.
    "aces": dict(case_mode="encounter", cs4_min=1, multi_ami="first", era_collapse=False,
                 cohort_end="during_obs", depth_anchor="obs_start", recent_gate=True),
    # reproduce_benchmark.py full-benchmark semantics (R5/R6) -- the SQL artifacts included.
    "r0":   dict(case_mode="encounter", cs4_min=1, multi_ami="last",  era_collapse=True,
                 cohort_end="entry_obs", depth_anchor="obs_start", recent_gate=True),
}


def load_predicates(path: str) -> dict:
    """Read the resolved concept-id predicates YAML; return {name: code-spec} for the base predicates.

    Accepts either a bare ``{name: {code: ...}}`` mapping or a full ACES task with a ``predicates:``
    block. Each base predicate must be a plain ``code`` predicate (``regex`` or ``any``).
    """
    doc = yaml.safe_load(open(path))
    preds = doc.get("predicates", doc)
    out = {}
    for name in BASE_PREDICATES:
        if name not in preds:
            raise SystemExit(f"[predicates] {path!r} is missing base predicate {name!r}")
        spec = preds[name].get("code", preds[name])
        if "regex" not in spec and "any" not in spec:
            raise SystemExit(f"[predicates] {name!r} must be a plain code predicate (regex|any)")
        out[name] = spec
    return out


def read_meds(meds_dir: str) -> pl.DataFrame:
    fs = [f for f in glob.glob(os.path.join(meds_dir, "**", "*.parquet"), recursive=True)
          if ".logs" not in f]
    if not fs:
        raise SystemExit(f"[meds] no parquet under {meds_dir!r}")
    return pl.read_parquet(fs).with_columns(
        pl.col("subject_id").cast(pl.Int64), pl.col("time").cast(pl.Datetime("us")))


def _flag(spec: dict) -> pl.Expr:
    if "regex" in spec:
        return pl.col("code").str.contains(spec["regex"]).cast(pl.Int64)
    return pl.col("code").is_in(spec["any"]).cast(pl.Int64)


def build_cohort(meds: pl.DataFrame, preds: dict, cfg: dict) -> pl.DataFrame:
    """Per-visit AMI cohort on MEDS, parameterized by cfg (see PRESETS / --help)."""
    # 1. one row per (subject, timestamp) with the per-predicate occurrence counts.
    ev = meds.select("subject_id", "time", *[_flag(preds[p]).alias(p) for p in BASE_PREDICATES])
    G = (ev.group_by("subject_id", "time").agg([pl.col(p).sum() for p in BASE_PREDICATES])
         .rename({"time": "ts"}).sort(["subject_id", "ts"]))

    # 2. encounter-AMI via `during`: AMI inside an open [ip_er_start .. ip_er_end] (net-open, closed=both).
    G = G.with_columns(cip=pl.col("ip_er_start").cum_sum().over("subject_id"),
                       cie=pl.col("ip_er_end").cum_sum().over("subject_id"))
    if cfg["case_mode"] == "encounter":
        admitted = (pl.col("cip") - (pl.col("cie") - pl.col("ip_er_end"))) > 0   # #opens<=t - #closes<t
        G = G.with_columns(ami_enc=pl.when(admitted).then(pl.col("ami")).otherwise(0))
    else:  # any_ami: every AMI is a case (no encounter restriction, no ERA)
        G = G.with_columns(ami_enc=pl.col("ami"))

    # 3. ERA-collapse the case events (r0-faithful only): gap>187d starts a new era, dated at its first.
    if cfg["era_collapse"]:
        a = G.filter(pl.col("ami_enc") > 0).select("subject_id", "ts").sort(["subject_id", "ts"])
        a = a.with_columns(gap=(pl.col("ts") - pl.col("ts").shift(1)).over("subject_id"))
        a = a.with_columns(era=((pl.col("gap").is_null()) | (pl.col("gap") > DAY(ERA_GAP_DAYS)))
                           .cum_sum().over("subject_id"))
        es = (a.group_by("subject_id", "era").agg(pl.col("ts").min().alias("ts"))
              .select("subject_id", "ts").with_columns(case=pl.lit(1, dtype=pl.Int64)))
        G = G.join(es, on=["subject_id", "ts"], how="left").with_columns(pl.col("case").fill_null(0))
    else:
        G = G.with_columns(case=pl.col("ami_enc"))

    # 4. smoking corroboration via `within` (#6): a CS4 with any smoking event within +/- ~inf.
    smk = G.group_by("subject_id").agg((pl.col("smoking").sum() > 0).alias("has_smk"))
    G = G.join(smk, on="subject_id", how="left").with_columns(
        cs4_smk=pl.when(pl.col("has_smk")).then(pl.col("cs4")).otherwise(0))

    # cumulative columns + total cases per subject (for the last-AMI semantics)
    etot = G.group_by("subject_id").agg(pl.col("case").sum().alias("ntot"))
    for c in ["case", "condition_or_drug", "cs13", "cs4", "cs4_smk", "obs_period_start"]:
        G = G.with_columns(pl.col(c).cum_sum().over("subject_id").alias(f"{c}_cum"))

    def cl(T, at, col, out, strict):
        """As-of cumulative lookup: value of {col}_cum at time `at` (strict=open interval)."""
        b = (at - US) if strict else at
        L = T.with_columns(b.alias("_b")).sort(["subject_id", "_b"])
        R = (G.select("subject_id", pl.col("ts").alias("_t"), pl.col(f"{col}_cum").alias("_c"))
             .sort(["subject_id", "_t"]))
        return (L.join_asof(R, left_on="_b", right_on="_t", by="subject_id", strategy="backward")
                .with_columns(pl.col("_c").fill_null(0).alias(out)).drop(["_b", "_t", "_c"]))

    # 5. entry = first risk (CS1/CS3 or CS4); cohort-end depends on mode.
    entry = (G.filter((pl.col("cs13") > 0) | (pl.col("cs4") > 0))
             .group_by("subject_id").agg(pl.col("ts").min().alias("entry")))
    if cfg["cohort_end"] == "entry_obs":
        # exact benchmark: end of the obs period CONTAINING the entry, else coalesce to max obs end.
        os_ = (G.filter(pl.col("obs_period_start") > 0).select("subject_id", pl.col("ts").alias("ostart"))
               .sort(["subject_id", "ostart"]).with_columns(i=pl.int_range(pl.len()).over("subject_id")))
        oe_ = (G.filter(pl.col("obs_period_end") > 0).select("subject_id", pl.col("ts").alias("oend"))
               .sort(["subject_id", "oend"]).with_columns(i=pl.int_range(pl.len()).over("subject_id")))
        iv = os_.join(oe_, on=["subject_id", "i"], how="inner")
        contain = (entry.join(iv, on="subject_id", how="left")
                   .filter((pl.col("ostart") <= pl.col("entry")) & (pl.col("entry") <= pl.col("oend")))
                   .group_by("subject_id").agg(pl.col("oend").min().alias("cohort_end")))
        omax = iv.group_by("subject_id").agg(pl.col("oend").max().alias("oend_max"))
        ce = (entry.join(contain, on="subject_id", how="left").join(omax, on="subject_id", how="left")
              .with_columns(cohort_end=pl.coalesce("cohort_end", "oend_max")))
        trig_filter = None  # apply cohort_end below
    elif cfg["cohort_end"] == "during_obs":
        # ACES task: visit must be inside SOME obs period (during(any_visit, obs_start, obs_end), closed=left).
        G = G.with_columns(cop=pl.col("obs_period_start").cum_sum().over("subject_id"),
                           coe=pl.col("obs_period_end").cum_sum().over("subject_id"))
        G = G.with_columns(obs_adm=(pl.col("cop") - pl.col("coe")) > 0)
        ce = None
        trig_filter = pl.col("obs_adm")
    else:
        ce, trig_filter = None, None

    # 6. triggers = every visit (optionally restricted to in-obs for during_obs cohort-end).
    T = G.filter(pl.col("any_visit") > 0)
    if trig_filter is not None:
        T = T.filter(trig_filter)
    T = T.select("subject_id", pl.col("ts").alias("t")).join(etot, on="subject_id", how="left").with_columns(
        pl.col("ntot").fill_null(0))
    if ce is not None:
        T = T.join(ce.select("subject_id", "cohort_end"), on="subject_id", how="left")
        T = T.filter(pl.col("cohort_end").is_not_null() & (pl.col("t") < pl.col("cohort_end")))

    # 7. gate look-ups.
    for col in ["cs13", "cs4", "cs4_smk", "obs_period_start"]:
        T = cl(T, pl.col("t"), col, f"{col}_le", strict=False)
    T = cl(T, pl.col("t"), "case", "case_lt", strict=True)                 # cases strictly before t
    T = cl(T, pl.col("t") - DAY(365 * MIN_OBS_YEARS), "obs_period_start", "obs730", strict=False)
    T = cl(T, pl.col("t") + DAY(365 * PREDICTION_YEARS), "case", "case_ye", strict=False)
    T = cl(T, pl.col("t"), "condition_or_drug", "cd_le", strict=False)
    T = cl(T, pl.col("t") - DAY(365 * MIN_OBS_YEARS), "condition_or_drug", "cd_b", strict=True)

    # 8. eligibility gates.
    at_risk = (pl.col("cs13_le") >= 1) | (pl.col("cs4_le") >= cfg["cs4_min"]) | (pl.col("cs4_smk_le") >= 1)
    if cfg["multi_ami"] == "first":
        no_prior = pl.col("case_lt") == 0                                  # drop any visit after the FIRST case
    else:  # last: keep unless the visit is after the LAST case (SQL MAX semantics)
        no_prior = (pl.col("ntot") == 0) | (pl.col("case_lt") < pl.col("ntot"))
    eligible = at_risk & no_prior
    if cfg["depth_anchor"] == "obs_start":
        eligible = eligible & (pl.col("obs730") >= 1)
    if cfg["recent_gate"]:
        eligible = eligible & ((pl.col("cd_le") - pl.col("cd_b")) >= 1)
    label = ((pl.col("case_ye") - pl.col("case_lt")) >= 1).cast(pl.Int8)   # case in [t, t+1yr]

    return (T.with_columns(eligible=eligible, boolean_value=label).filter(pl.col("eligible"))
            .select("subject_id", pl.col("t").alias("prediction_time"), "boolean_value"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--meds", required=True, help="MEDS shard dir (e.g. <build>/data/tuning)")
    ap.add_argument("--predicates", required=True, help="resolved concept-id predicates/task YAML")
    ap.add_argument("--mode", choices=list(PRESETS), default="aces", help="preset (see module docstring)")
    ap.add_argument("--output", required=True, help="output cohort parquet")
    # per-key overrides (default None -> take from the preset)
    ap.add_argument("--case-mode", choices=["encounter", "any_ami"], default=None)
    ap.add_argument("--cs4-min", type=int, default=None, help="CS4 corroboration threshold (1=live, 2=#6)")
    ap.add_argument("--multi-ami", choices=["first", "last"], default=None,
                    help="first = ACES no_prior; last = SQL MAX quirk (keeps between-AMI visits)")
    ap.add_argument("--era-collapse", dest="era_collapse", action="store_const", const=True, default=None)
    ap.add_argument("--no-era-collapse", dest="era_collapse", action="store_const", const=False)
    ap.add_argument("--cohort-end", choices=["during_obs", "entry_obs", "none"], default=None)
    ap.add_argument("--depth-anchor", choices=["obs_start", "none"], default=None)
    ap.add_argument("--no-recent-gate", dest="recent_gate", action="store_const", const=False, default=None)
    args = ap.parse_args()

    cfg = dict(PRESETS[args.mode])
    for k in ("case_mode", "cs4_min", "multi_ami", "era_collapse", "cohort_end", "depth_anchor"):
        v = getattr(args, k)
        if v is not None:
            cfg[k] = v
    if args.recent_gate is not None:
        cfg["recent_gate"] = args.recent_gate
    print(f"mode={args.mode} | cfg={cfg}")

    preds = load_predicates(args.predicates)
    meds = read_meds(args.meds)
    out = build_cohort(meds, preds, cfg)
    out.write_parquet(args.output)
    n = out["subject_id"].n_unique()
    print(f"REPRODUCED (MEDS): {out.height:,} points | {n:,} subjects | "
          f"{out.filter(pl.col('boolean_value') == 1).height:,} positives")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
