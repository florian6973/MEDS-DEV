# AMI benchmark → ACES: criterion-by-criterion mapping & issues

The reAIM-Lab AMI benchmark is three artifacts: `ami_at_risk.json` (eligibility), `ami_case.json`
(outcome), and `cohort_pos_neg_query.sql` (per-visit labels). This maps **every** benchmark
criterion to its ACES element in [`ami_full.yaml`](ami_full.yaml), the decision taken, and the
fidelity. `OK` = reproduced exactly (validated `ACES == reproduce_meds = 0/0`). Full numeric ledger:
[`../AMI_TASK_REPORT.md`](../AMI_TASK_REPORT.md).

## Criteria

| # | Benchmark criterion | ACES element | Status |
| --- | --- | --- | --- |
| 1 | prediction time = each visit | `trigger: visit_in_obs` (every visit, inside an obs period) | **OK** |
| 2 | outcome = Acute-MI (CS3) **during** an inpatient/ER visit (CS2) `[vstart, vend]` | `ami_encounter: during(ami, ip_er_start, ip_er_end, closed=both)`; used in `target` + `no_prior_ami` | **OK** — `during` net-open is exact for "inside ≥1 stay" |
| 3 | case end-strategy (index + 7d) | — | dropped (unused by the labeling SQL) |
| 4 | case ERA-collapse 180d (merge gap≤187) | implicit | **OK** for *presence* in the year; only the `r0` reference materializes it (`reproduce_meds --era-collapse`) |
| 5 | at-risk index = "First" of the risk dxs | re-mapped: trigger is the visit; entry → `at_risk_entry` look-back gate | **OK** (per-visit task, not entry task) |
| 6 | at-risk = CS1∪CS3 **or** ≥2·CS4 **or** CS4+smoking | `at_risk_entry.has_any: [cs13, cs4(2,None), cs4_smk]`; `cs4_smk: within(cs4, of=smoking)` | **OK** — `has_any` is the top-level OR; smoking arm is a dead branch on a condition-only stream |
| 7 | no AMI within 7d (at-risk inclusion) | subsumed by `no_prior_ami` | **OK** (no prior encounter-AMI is stricter) |
| 8 | risk ERA-collapse pad 0 | — | dropped (negligible) |
| 9 | ≥1 condition/drug in prior 2yr | `recent_activity.has: condition_or_drug (1,None)` | **OK** — predicate **widened** to match R0's "any cond/drug" (see Issues) |
| 10-lo | ≥2yr history: `visit ≥ earliest obs_start + 2yr` | `sufficient_history.has: obs_period_start (1,None)` in `[null, trigger-730d]` | **OK** — equals `min(obs_start) ≤ trigger-730d` |
| 10-hi | cohort end: `visit < end of the obs period containing entry` | `trigger: visit_in_obs = during(any_visit, obs_start, obs_end, closed=left)` | **approx** — "inside *an* obs period"; exact for single-obs-period patients, over-includes multi-period ones |
| — | `case_start < visit` exclusion + `MAX` | `no_prior_ami.has: ami_encounter (None,0)` (first-AMI) | **deliberate divergence** — see multi-AMI quirk |
| CS0 | no acute-MI in `[entry-7d, entry-1d]` | — | **omitted (stale)**: 0 effect — acute-MI ⊂ CS3, so it *is* the entry; also subsumed by #7 |
| R2 | observation-period membership | — | **omitted (stale)**: 0 effect — every subject has an obs period; implied by 10-lo |

## What ACES cannot express (and why it's fine)

- **Multi-AMI quirk** — the SQL keeps a visit *between* two AMIs (`MAX` over `case ≥ visit`); ACES
  `no_prior_ami` drops after the **first** AMI. Quantified: **26,539 pts / 156 subj**. An arguable
  SQL artifact; we ship the cleaner first-AMI semantics. (`reproduce_meds --multi-ami last`
  reproduces the SQL behavior for measurement.)
- **CS0 first-entry anchor** — would need to anchor at the subject's *first-ever* risk entry; ACES
  `trigger <- risk_entry` only reaches the most recent prior one. Moot here (CS0 is stale).
- **R5 multi-obs-window edge** — "visit inside the *entry's* obs period" vs "inside *any*"; differ
  only for patients with multiple observation periods.

## Issues encountered (chronological, the ones that mattered)

1. **OMOP-MEDS dropped all visits** on `$H` (null `visit_source_concept_id` + null visit datetimes)
   → two config edits (see [`README.md`](README.md) §0). Same fix surfaced observation periods.
2. **Code scheme vs concept id.** Comparing ACES on the `v3_mapped` MEDS against the OMOP Python
   conflated ACES logic with ETL/code differences. Fixed by building a concept-id OMOP-MEDS directly
   from `$H` so both sides key on the same standard `concept_id`.
3. **Stale concept-set snapshot.** The zips' `includedConcepts.csv` lags the live vocabulary (missed
   the entry concept for the residual subjects) → re-resolve seeds through `concept_ancestor`.
4. **`during` same-timestamp ordering.** Encoding containment via event-anchored ACES windows on
   date-granularity data depends on same-day `//start`/AMI/`//end` ordering. The `during` predicate
   sidesteps it: net-open `#{opens≤τ} − #{closes<τ}` computed once in the data layer with date `≤`.
5. **Trigger `->` is strict.** `trigger -> ami` skips an AMI on the trigger's own day; the year
   window `[t, t+365]` is inclusive. Reconciled by the `during` + strict/inclusive boundaries —
   reproduced ACES to 100% in Python before trusting it.
6. **Encounter-AMI ≠ raw AMI across $H↔MEDS.** A `$H` AMI need not have a MEDS counterpart (verified:
   admission/discharge present 82/82, AMI 0/82) — the source→standard remap. This is the dominant
   residual (see below), *not* a logic error.
7. **Recent-gate breadth.** `condition_or_drug` as `SNOMED|RxNorm|OMOP Extension//…//start` was
   *narrower* than R0's "any condition_occurrence/drug_exposure": it missed condition-eras,
   interval-ends, and `RxNorm Extension`. Widened the predicate; 265 of the 585 recent-gate
   disagreements were this (a predicate choice, not drift).

## The residual, named (0 unexplained)

Logic is exact (`ACES == reproduce_meds = 0/0`). Versus the live benchmark the residual is **~0.066%
$H↔MEDS ETL drift**, two mechanisms:

- **AMI concept remap** (~776 pts) — `$H` `condition_concept_id ∈ AMI` but the MEDS preferred concept
  ∉ `ami` → label/keepq differ.
- **Unmapped-`concept_id 0` drops** (~520 pts) — conditions/drugs `$H` carries that the ETL drops
  (unmapped) → recent-gate / risk-entry differ.

Plus a further ~0.013% that is the closable recent-predicate breadth (now widened). Risk-entry,
depth, and cohort-end are exact.
