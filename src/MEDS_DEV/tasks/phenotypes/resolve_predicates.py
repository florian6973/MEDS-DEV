#!/usr/bin/env python3
"""Resolve OHDSI/ATLAS cohort concept sets into ACES-style predicate code lists.

The reAIM-Lab ``ehr_foundation_model_benchmark`` ships each phenotype as:

* a **cohort definition** JSON (e.g. ``ami_case.json``) whose ``ConceptSets``
  block names every concept set the cohort logic references, and
* a **concept-set** zip (e.g. ``AMI Case.zip``) that contains the *resolved*
  codes for those concept sets after descendant/mapping expansion:

  - ``includedConcepts.csv`` — the standard OMOP concepts (mostly SNOMED) that
    the expression resolves to (descendants included).
  - ``mappedConcepts.csv`` — the source-vocabulary codes (ICD9CM, ICD10CM,
    CIEL, ...) that map to those standard concepts.

This script joins the two on ``Concept Set ID`` and emits, for every concept
set referenced by the definition, the list of ``VOCABULARY//CONCEPT_CODE``
strings ready to drop into an ACES ``predicates`` block. Which vocabularies you
keep depends on how your MEDS dataset codes events: keep ``standard`` if events
carry OMOP/SNOMED concept codes, keep ``source`` (or ``all``) if they carry
raw ICD/etc. codes.

Resolution contract (important):
    The zip MUST be ATLAS's *resolved* export, kept in sync with the definition
    JSON. ``includedConcepts.csv`` and ``mappedConcepts.csv`` already have the
    expression's ``isExcluded`` / ``includeDescendants`` flags applied by ATLAS
    (e.g. for AMI, "Old myocardial infarction" + its descendants are already
    removed). This script therefore does NOT evaluate those flags itself: it
    reads the definition JSON only to learn *which* concept sets (by id) the
    cohort references, and trusts the two resolved CSVs for membership. The
    third member, ``conceptSetExpression.csv``, still carries the un-applied
    ``Exclude=true`` seeds, so it is intentionally NOT read. Consequences:
      * Edit a concept set in the JSON without re-exporting the zip and this
        output goes stale -- the script trusts the zip, not the JSON.
      * A zip-free resolver is impossible locally: applying descendants/
        exclusions needs the OMOP ``CONCEPT_ANCESTOR`` table, for which the zip
        is the substitute.

Usage:
    python resolve_predicates.py \
        --definition cohort_definitions/ami_case.json \
        --concept-sets "cohort_concept_sets/AMI Case.zip" \
        --codes all \
        --output ami_case_predicates.yaml
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path

INCLUDED_CSV = "includedConcepts.csv"
MAPPED_CSV = "mappedConcepts.csv"


def slugify(name: str) -> str:
    """Turn a concept-set name into a YAML-friendly predicate key.

    >>> slugify("[LEGEND HTN] Acute myocardial Infarction")
    'acute_myocardial_infarction'
    >>> slugify("Differential for AMI [yl]")
    'differential_for_ami'
    """
    name = re.sub(r"\[[^\]]*\]", " ", name)  # drop bracketed tags like [yl], [LEGEND HTN]
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    return name.strip("_")


def read_zip_csv(zf: zipfile.ZipFile, member: str) -> list[dict[str, str]]:
    """Read a UTF-8-BOM CSV member from a concept-set zip into dict rows."""
    text = zf.read(member).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def resolve(definition_path: Path, zip_path: Path, codes: str) -> dict[int, dict]:
    """Resolve every concept set in ``definition_path`` to its code list.

    ``codes`` selects which rows to keep: ``standard`` (includedConcepts only),
    ``source`` (mappedConcepts, excluding rows already in includedConcepts), or
    ``all`` (union of both). Returns a mapping keyed by concept-set id.
    """
    definition = json.loads(definition_path.read_text())
    concept_sets = {cs["id"]: cs["name"] for cs in definition["ConceptSets"]}

    with zipfile.ZipFile(zip_path) as zf:
        included = read_zip_csv(zf, INCLUDED_CSV)
        mapped = read_zip_csv(zf, MAPPED_CSV)

    # group codes by concept-set id; track standard ids to subtract from source
    std_codes: dict[int, set[tuple[str, str]]] = defaultdict(set)
    src_codes: dict[int, set[tuple[str, str]]] = defaultdict(set)
    for row in included:
        cid = int(row["Concept Set ID"])
        std_codes[cid].add((row["Vocabulary"], row["Concept Code"]))
    for row in mapped:
        cid = int(row["Concept Set ID"])
        src_codes[cid].add((row["Vocabulary"], row["Concept Code"]))

    out: dict[int, dict] = {}
    for cid, name in concept_sets.items():
        std = std_codes.get(cid, set())
        src = src_codes.get(cid, set())
        if codes == "standard":
            chosen = std
        elif codes == "source":
            chosen = src - std  # source-only codes (mapped that aren't standard)
        else:  # all
            chosen = std | src
        formatted = sorted(f"{vocab}//{code}" for vocab, code in chosen)
        out[cid] = {
            "name": name,
            "key": slugify(name),
            "n_standard": len(std),
            "n_source": len(src - std),
            "codes": formatted,
        }
    return out


def to_aces_yaml(resolved: dict[int, dict]) -> str:
    """Render resolved concept sets as an ACES ``predicates`` YAML block."""
    lines = ["predicates:"]
    for cid in sorted(resolved):
        cs = resolved[cid]
        lines.append(
            f"  # concept set {cid}: {cs['name']} "
            f"({cs['n_standard']} standard + {cs['n_source']} source codes)"
        )
        lines.append(f"  {cs['key']}:")
        lines.append("    code:")
        for c in cs["codes"]:
            lines.append(f'      - "{c}"')
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--definition", required=True, type=Path, help="cohort definition JSON")
    p.add_argument("--concept-sets", required=True, type=Path, help="concept-set zip")
    p.add_argument(
        "--codes",
        choices=["standard", "source", "all"],
        default="all",
        help="which vocabularies to emit (default: all)",
    )
    p.add_argument("--output", type=Path, help="write ACES predicates YAML here (else stdout)")
    args = p.parse_args()

    resolved = resolve(args.definition, args.concept_sets, args.codes)

    print("Resolved concept sets:")
    for cid in sorted(resolved):
        cs = resolved[cid]
        print(
            f"  [{cid}] {cs['name']!r} -> {cs['key']}: "
            f"{len(cs['codes'])} codes ({cs['n_standard']} standard, {cs['n_source']} source)"
        )

    yaml_text = to_aces_yaml(resolved)
    if args.output:
        args.output.write_text(yaml_text)
        print(f"\nWrote {args.output}")
    else:
        print("\n" + yaml_text)


if __name__ == "__main__":
    main()
