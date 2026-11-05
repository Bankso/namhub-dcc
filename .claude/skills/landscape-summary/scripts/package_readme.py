"""README.md / run_info.json for a landscape-summary output package (stdlib only).

The README explains the package to data-coordination colleagues. It is built only from counts, file and column
descriptions, method text, rule values and provenance -- never from landscape content (no study, center, dataset
names, descriptions or ids). See references/rubric.md.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

# (file, what it is, who it is for) in display order; only files present in the folder are listed.
FILE_INFO = [
    ("README.md", "This guide.", "Everyone"),
    ("landscape_summary_compact.md", "Shareable overview table (GitHub markdown) with the key columns.", "Everyone"),
    ("landscape_summary_compact.csv", "Same overview as a CSV (opens in a spreadsheet).", "Everyone"),
    ("landscape_summary_reviewed.csv",
     "Curated full table: the full summary after agent review, with ReviewNotes recording every override or proposal.",
     "Curators, data coordinators"),
    ("landscape_summary.md", "Reviewed table as markdown, with a short Notes column.", "Everyone"),
    ("landscape_summary.csv", "Full rule-based summary, one row per data type and processing level, with rationale and flags.",
     "Curators, data coordinators"),
    ("review_packet.json", "Machine-readable summary rows plus the raw free-text fields of their source rows; input to the review step.",
     "Reviewers, developers"),
    ("excluded_rows.csv", "Placeholder/test rows left out of every count, with the reason.", "Data coordinators"),
    ("old_manifest_links.csv", "How each revised row was linked to an entry of the earlier (old) manifests.", "Data coordinators"),
    ("row_issues.csv", "Format/level plausibility issues per row, with whether the issue came from the old entry or the revision.",
     "Data coordinators, center contacts"),
    ("proposed_corrections.csv", "Evidence-backed proposed changes to revised rows. Never applied automatically.",
     "Curators, data coordinators"),
    ("description_loss.csv", "Rows whose revised description lost content that the old description had (accessions, markers, cohort sizes).",
     "Curators"),
    ("run_info.json", "Counts, inputs and settings of the run; used to regenerate this README.", "Developers"),
]

# One description per column of landscape_summary.csv (and landscape_summary_reviewed.csv).
COLUMN_DESCRIPTIONS = {
    "DataType": "Data category (e.g. gene expression, image). Single-cell, single-nucleus and spatial assays get a suffix such as \"(single-cell)\" so they are separate from bulk.",
    "ProcessingLevel": "Processing level of the rows in this group (Level 1-4, Auxiliary, Metadata, Not Applicable or Unspecified).",
    "Assays": "Assays declared in the group.",
    "FileFormats": "File formats declared in the group.",
    "PreferredFileFormat": "Canonical format(s) recommended for this assay and level, from the canonical-formats table.",
    "MetadataTypes": "Preferred metadata schema(s) for the assays at this level, as namespace:schema vN. \"(fallback)\" marks a generic fallback schema.",
    "MetadataCandidates": "Other registry schemas that could describe the assays at this level (non-preferred alternatives).",
    "UnmappedAssays": "Assays with no schema in the mapping file.",
    "ProjectedSensitivity": "Projected access tier: Open, Registered or Controlled (highest tier over the rows).",
    "SensitivityRationale": "How many rows fall in each tier and why (rule that applied, any processing-level cap).",
    "RecommendedRepository": "Suggested repository for the rows at the group's highest tier.",
    "OtherRepositories": "Repositories suggested for the remaining rows of the group.",
    "RepositoryRationale": "Row counts per suggested repository and the rule behind each.",
    "Priority": "High, Medium or Low from breadth across studies for the data type (see CategoryStudyCount).",
    "CategoryStudyCount": "Number of studies that have any rows of this data type (all levels).",
    "StudyCount": "Number of studies with rows in this data type and level.",
    "DatasetCount": "Number of distinct datasets (dataset groups) in this group.",
    "RowCount": "Number of Landscape rows in this group.",
    "EstimatedFileCount": "Estimated total files, summed from samples x file ratio per row; rows without usable numbers are skipped.",
    "EarliestAvailableDate": "Earliest upload-by date declared in the group (ISO date).",
    "ExpectedDataVolume": "Largest data-volume band from the old manifests; blank when no old manifests were used.",
    "Species": "Species declared in the group.",
    "ReviewFlags": "Automatic data-quality observations about the group (see Flags and decisions needing input).",
    "NeedsInput": "Decisions a person has to make or confirm for the group (see Flags and decisions needing input).",
    "ReviewNotes": "Reviewed CSV only: every override (old -> new value, evidence) and proposal made in the review step; blank if untouched.",
}


def rules_snapshot(rules: dict) -> dict:
    """The rule values the README quotes, copied into run_info.json so --readme-only needs no rules file."""
    return {
        "sensitivity_tiers": rules.get("sensitivity_tiers", []),
        "level_max_tier": rules.get("level_max_tier", {}),
        "level_cap_exempt_categories": rules.get("level_cap_exempt_categories", []),
        "identifiable_processing_levels": rules.get("identifiable_processing_levels", []),
        "assay_resolution_groups": sorted(rules.get("assay_resolution", {})),
        "priority": rules.get("priority", {}),
        "repository_rules": [{"repository": r["repository"], "sensitivity": r.get("sensitivity", []),
                              "note": r.get("note", "")} for r in rules.get("repository_rules", [])],
        "fallback_repository": {t: v["repository"] for t, v in rules.get("fallback_repository", {}).items()},
    }


def mapping_versions(mapping: dict) -> list[str]:
    """['MC2 14.1.0', ...] from load_mapping output (namespace + version, version omitted if blank)."""
    out = set()
    for cands in mapping.values():
        for c in cands:
            if c.get("Namespace"):
                out.add(f"{c['Namespace']} {c['Version']}".strip())
    return sorted(out)


def mapping_row_count(path) -> int:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def enum_notes(references_dir: Path) -> dict:
    notes = {}
    for name in ("landscape_enums.json", "file_format_enum.json"):
        p = references_dir / name
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            notes[name] = {"schema": d.get("schema", ""), "retrieved": d.get("retrieved", ""), "note": d.get("_note", "")}
    return notes


def _ml(items) -> str:
    return ", ".join(str(i) for i in items) if items else "none"


def _md(s) -> str:
    return " ".join(str(s).split()).replace("|", "\\|")


def build_readme(info: dict, out_dir: Path, output_columns: list, compact_columns: list) -> str:
    present = {p.name for p in out_dir.iterdir() if p.is_file()} | {"README.md"}
    old = info.get("old_manifests", {})
    used_old = bool(old.get("used"))
    rs = info.get("rules_snapshot", {})
    mp = info.get("mapping", {})
    cf_ = info.get("config_files", {})
    L: list[str] = []
    a = L.append

    a("# Landscape summary package\n")
    a("This folder summarizes the NAMHub Landscape across studies: one row per data type and processing level, "
      "with the file formats, metadata schemas, projected data sensitivity, suggested repository and priority "
      "that follow from the Landscape rows. It is a planning aid for data coordination and curation, produced by rule "
      "from the Landscape exports and reviewed by a curator before sharing.\n")
    a(f"Generated: {info.get('generated_at', 'unknown')}\n")
    a("## Run at a glance\n")
    a(f"- Input files: {info.get('input_file_count', '?')}")
    a(f"- Landscape rows read: {info.get('input_row_count', '?')} ({info.get('excluded_row_count', '?')} excluded as placeholder/test rows)")
    a(f"- Studies: {info.get('study_count', '?')}")
    a(f"- Summary rows (data type x processing level): {info.get('output_row_count', '?')}")
    if used_old:
        a(f"- Old manifests used: yes ({old.get('linked_row_count', '?')} revised rows linked to an old entry)")
    else:
        a("- Old manifests used: no")
    if mp:
        a(f"- Metadata mapping: {mp.get('file', '?')} ({mp.get('row_count', '?')} rows; registry versions: {_ml(mp.get('versions'))})")
    a("")

    a("## Start here\n")
    if "landscape_summary_compact.md" in present or "landscape_summary_compact.csv" in present:
        names = " / ".join(f"`{n}`" for n in ("landscape_summary_compact.md", "landscape_summary_compact.csv") if n in present)
        a(f"- {names} is the shareable overview: one short row per data type and processing level.")
    if "landscape_summary_reviewed.csv" in present:
        a("- `landscape_summary_reviewed.csv` is the curated full table (all columns plus ReviewNotes).")
    elif "landscape_summary.csv" in present:
        a("- `landscape_summary.csv` is the full table; it has not been through the review step yet "
          "(no `landscape_summary_reviewed.csv` in this folder).")
    a("- The other files are supporting detail; the next section says who each is for.\n")

    a("## Files in this package\n")
    a("| File | What it is | Who it's for |")
    a("|---|---|---|")
    for f, what, who in FILE_INFO:
        if f in present:
            a(f"| `{f}` | {_md(what)} | {_md(who)} |")
    a("")

    a("## How to read the summary\n")
    a("- **One row = one data type x one processing level.** Data type is the dataset category. Assays in these groups are "
      f"split from bulk assays with a suffix naming their group ({_ml(rs.get('assay_resolution_groups'))}). "
      "Processing levels run from Level 1 (raw) to Level 4 (most processed), plus Auxiliary, Metadata, Not Applicable and Unspecified.")
    pr = rs.get("priority", {})
    a(f"- **Priority** reflects breadth across studies only: `CategoryStudyCount` is the number of studies with the data type "
      f"(all levels share it). High needs at least {pr.get('min_studies_for_high', '?')} studies and either "
      f"{round(100 * pr.get('high_fraction_of_studies', 0))}% of all studies or a place in the top "
      f"{round(100 * pr.get('top_fraction_of_categories', 0))}% of data types by study count; Low means the data type appears in "
      f"{pr.get('low_if_study_count_equals', '?')} study; the rest are Medium. Data types in the exclusion list "
      f"({_ml(pr.get('exclude_from_high'))}) are capped at Medium. It says nothing about data quality or importance.")
    tiers = rs.get("sensitivity_tiers", [])
    a(f"- **Projected sensitivity** has tiers {' < '.join(tiers) if tiers else 'Open < Registered < Controlled'}: "
      "Open for non-human, code/model and documentation rows; Registered for other human data and unknown species; "
      "Controlled for human clinical-type categories and identifiable formats (for example raw reads) at the identifiable levels "
      f"({_ml(rs.get('identifiable_processing_levels'))}). The group takes the highest tier among its rows.")
    caps = rs.get("level_max_tier", {})
    if caps:
        capt = "; ".join(f"{lv} is capped at {t}" for lv, t in caps.items())
        a(f"  - Processing-level caps: {capt}, because processed data is less identifiable. "
          f"Exempt categories (never capped): {_ml(rs.get('level_cap_exempt_categories'))}. Every capped row is listed in NeedsInput for confirmation.")
    a("- **RecommendedRepository** comes from the first matching rule in order, applied to the rows at the group's highest tier "
      "(the most common repository wins). OtherRepositories and RepositoryRationale show the rest. Rule order:")
    for r in rs.get("repository_rules", []):
        sens = _ml(r["sensitivity"]) if r.get("sensitivity") else "any tier"
        a(f"  - {r['repository']} ({sens}): {r.get('note', '')}")
    fb = rs.get("fallback_repository", {})
    if fb:
        a(f"  - If no rule matches: {', '.join(f'{t} -> {v}' for t, v in fb.items())}.")
    a("- **MetadataTypes vs MetadataCandidates.** Both come from the registry mapping (namespace:schema version). MetadataTypes are the "
      "preferred schemas; at Levels 2-4 the schema made for that stage is preferred first. MetadataCandidates are other schemas "
      "that could also fit.")
    a("- **PreferredFileFormat** is looked up in the canonical-formats table: assay + level, then assay (any level), then data "
      "category + level, then category (any level). Blank means no canonical format is defined.")
    a("- **EarliestAvailableDate** is the earliest declared upload-by date in the group.")
    a("- **ExpectedDataVolume** is the largest data-volume band recorded in the old manifests for the group"
      + ("." if used_old else " (blank here: no old manifests were used)."))
    a("- **EstimatedFileCount** sums files per row (samples x files per sample, samples / samples per file, or total files). "
      "Rows with missing or non-numeric values are skipped and counted in ReviewFlags.\n")

    a("## Column reference\n")
    a("Columns of `landscape_summary.csv`" + (" and `landscape_summary_reviewed.csv`" if "landscape_summary_reviewed.csv" in present else "")
      + ". Columns marked \"yes\" under Compact also appear in the compact files.\n")
    a("| Column | Compact | Meaning |")
    a("|---|---|---|")
    for c in output_columns:
        a(f"| {c} | {'yes' if c in compact_columns else ''} | {_md(COLUMN_DESCRIPTIONS.get(c, ''))} |")
    a(f"| ReviewNotes | | {_md(COLUMN_DESCRIPTIONS['ReviewNotes'])} |")
    a("")

    a("## Flags and decisions needing input\n")
    a("- **ReviewFlags** are automatic observations about data quality: missing processing level, unmapped assays or assays left "
      "as Unknown, rows skipped in the file-count estimate, unparseable dates, no canonical format or declared formats "
      "missing the preferred one, format/category conflicts, unknown species, mixed sensitivity tiers within a group"
      + (", and (with old manifests) row issues and lost description content" if used_old else "") + ".")
    a("- **NeedsInput** lists decisions for a person: a fully public repository recommended for Registered data (confirm consent "
      "allows open release), free text mentioning consent or access terms, text saying data are de-identified, rows lowered by a "
      "processing-level cap, and"
      + (" old-manifest fills to confirm and already-deposited public accessions." if used_old else " (with old manifests) fills and public deposits to confirm."))
    a("- Reviewers may append to NeedsInput but never remove entries.\n")

    if used_old:
        a("## Reconciliation with earlier landscapes\n")
        a("The earlier per-center manifests were used as evidence only. The revised Landscape rows remain authoritative, and "
          "**nothing was changed automatically except gap fills, which are listed for confirmation.**\n")
        a("- **Linking** (`old_manifest_links.csv`): each revised row is linked to an old entry by id prefix, otherwise by "
          "dataset name. Unlinked rows get no old-manifest evidence.")
        a("- **Gap fills**: a blank processing level or an unusable file estimate is filled from the old entry. Every fill carries "
          "its provenance (old stated value, or an audit/format-inferred value) and is listed in `proposed_corrections.csv` and "
          "NeedsInput.")
        a("- **row_issues.csv** flags format/level combinations that look implausible, with an Origin: "
          "\"inherited from old entry\" (the old entry had it too: ask the center), \"introduced in revision\" (fix in the revision) "
          "or \"no old entry\" (check provenance).")
        a("- **proposed_corrections.csv** columns: row id, study, dataset, assay, Field, CurrentValue, ProposedValue, Evidence "
          "(quoted source text), Source, Confidence, Rationale. Fields include file format, file count, processing level, missing rows "
          "(a format the old itemization lists that no revised row has) and existing public deposits. Confidence is **high** for values the old entry states and for counts or levels implied by an itemized format, **medium** for "
          "itemization-based format and missing-row proposals, entry-stated level conflicts, public deposits and fills derived from the old "
          "grounding, and **low** for audit- or format-inferred values and \"review\" prompts (a format not in the old itemization although "
          "other itemized formats are present). "
          "These are proposals only.")
        a("- **description_loss.csv** lists rows whose revised description or comments no longer contain accessions, rsIDs, gene "
          "names or cohort sizes that the old text had, or whose cohort counts conflict.\n")

    a("## Caveats\n")
    a("- This is a projection for curator review, not an access determination.")
    a("- Sensitivity and repository are rule-based from category, format, species and level; they do not read consent terms.")
    a("- Volumes and dates are as declared by the centers.")
    a("- File-count estimates skip rows with missing numbers, so they can understate totals.\n")

    a("## How this was produced\n")
    a("```")
    a(info.get("command", ""))
    a("```")
    a(f"- Script: `{info.get('script', '')}`")
    a(f"- Rules: `{cf_.get('rules', '')}`; canonical formats: `{cf_.get('canonical_formats', '')}`; "
      f"format/level rules: `{cf_.get('format_level_rules', '')}`")
    if mp:
        a(f"- Metadata mapping: `{mp.get('file', '')}` ({mp.get('row_count', '?')} rows; {_ml(mp.get('versions'))})")
    for name, n in info.get("enum_notes", {}).items():
        a(f"- `{name}`: NAMhub-Landscape enum retrieved {n.get('retrieved', '?')}. {n.get('note', '')}")
    a("- After the review step adds `landscape_summary_reviewed.csv` or `landscape_summary.md`, refresh the file list with "
      "`python3 <script> --readme-only --out-dir <OUT_DIR>`.")
    return "\n".join(L) + "\n"


def write_readme(out_dir: Path, output_columns: list, compact_columns: list) -> None:
    info = json.loads((out_dir / "run_info.json").read_text(encoding="utf-8"))
    (out_dir / "README.md").write_text(build_readme(info, out_dir, output_columns, compact_columns), encoding="utf-8")
