---
name: landscape-summary
description: Summarize and aggregate NAMHub Landscape RecordSet CSV exports (rows conforming to the Synapse JSON schema NAMhub-Landscape) into a one-row-per-(data type, processing level) summary table across studies (single-cell and single-nucleus assays split from bulk), covering assays, processing levels, file formats, metadata schema types, projected data sensitivity (Open/Registered/Controlled), recommended repository, priority by study breadth, and estimated file counts. Use this whenever the user wants a landscape summary table, to roll up or aggregate landscape CSVs, or asks about data types, processing levels, formats, metadata, sensitivity, repository or priority across landscape studies. Trigger even if the user just hands over one or more landscape CSV exports.
---

# Landscape Summary

Roll up one or more NAMHub Landscape CSV exports into a single table with one row per
(`DataType`, `DatasetProcessingLevel`), where `DataType` is the `DatasetCategory`, suffixed " (single-cell)" / " (single-nucleus)" for assays listed in `assay_resolution`, then review the rule-based projections against the free text.

The script does the deterministic work (grouping, counts, schema mapping, rule-based
sensitivity/repository/priority). You then do the judgment work: read the free-text fields
and correct the projections where the evidence warrants. The rules are in
`references/rules.json`; `references/rubric.md` explains every one.

## When this applies

Inputs are CSV exports of Landscape RecordSets. Headers may be property names
(`DatasetCategory`) or titles (`Dataset Category`); extra columns such as `ROW_ID` are ignored.

## Workflow

1. **Inspect the inputs.** Check each CSV has the Landscape columns (`StudyName`,
   `DatasetCategory`, `DatasetAssay`, `DatasetSpecies`, ...), note row counts, blank
   categories/species, and whether numeric sample/ratio fields are filled. If the user gave a
   directory, all `*.csv` in it are used, so make sure nothing unrelated is in there.

1b. **Optional: old manifests.** If the user has the older per-center Sage manifest HTML pages, they are
   evidence for curators (the revised rows stay authoritative). Either extract them to the scratchpad
   (`python3 .claude/skills/landscape-summary/scripts/extract_manifests.py PAGES... --out-dir SCRATCH/`, one
   `<center>_manifest.csv` per page; `index.html` is ignored) or pass the .html pages / extracted CSVs / directories
   straight to `--old-manifests` in step 2. Never copy manifest or landscape content into the repo.

2. **Run the script** (stdlib-only, Python 3.10+):
   ```bash
   python3 .claude/skills/landscape-summary/scripts/summarize_landscape.py INPUT [INPUT ...] --out-dir OUT/
   ```
   Options: `--mapping PATH` (default: the repo's long-format `landscape_assay_schema_candidates.csv`, one row per assay, processing level and candidate schema; the old `DCC`-column mapping format is still read),
   `--rules PATH` (default: `references/rules.json`), `--formats PATH` (default: `references/canonical_formats.csv`),
   `--old-manifests PATH [PATH ...]` (old manifest .html or extracted .csv, files or directories; enables the
   reconciliation outputs below), `--format-level-rules PATH` (default: `references/format_level_rules.json`). It writes:
   - `landscape_summary.csv` - one row per data type and processing level, rule-based columns plus `ReviewFlags`.
   - `landscape_summary_compact.csv` / `landscape_summary_compact.md` - compact view, generated directly by the script (see Outputs).
   - `excluded_rows.csv` - placeholder/test rows left out of all aggregates.
   - With `--old-manifests` only: `old_manifest_links.csv`, `row_issues.csv`, `proposed_corrections.csv`,
     `description_loss.csv` (see Outputs).
   - `run_info.json` and `README.md` - package provenance (counts, settings, placeholdered command) and a
     plain-language guide for colleagues; the README contains no landscape content.
   - `review_packet.json` - each row (including `MetadataCandidates`) plus the raw free-text fields of its contributing rows
     (StudyName, DatasetName, DatasetDescription, ContextOfUse, Comments, ApplicableStandards,
     Species, Assay, FileFormat, ProcessingLevel).

3. **Agent review of `review_packet.json`.** Aliases, unspecified assays, placeholder rows, format
   conflicts, computational assays, open categories and the `NeedsInput` column are now automatic
   (see rubric.md); focus on what rules cannot see. For each row read the free text and decide
   whether `ProjectedSensitivity` needs adjusting. Overrides still go in `ReviewNotes`. Change it only
   where the evidence warrants:
   - established commercial human cell lines are not participant data (may lower Registered to Open);
   - explicit de-identification or non-participant provenance in the text;
   - patient-derived xenografts in mouse (raise above Open);
   - questionnaires or surveys on human subjects filed under a non-human species (raise).

   Never lower Controlled without explicit textual evidence, quoted in the note. When in doubt
   leave the rule result and mention the doubt in the note. Also:
   - For each `UnmappedAssays` entry, propose the closest schema from
     `landscape_assay_schema_candidates.csv`, labelled "proposed". Do not edit the mapping CSV.
   - `RecommendedRepository` follows only the rows at the row group's max sensitivity tier; a
     `Mixed sensitivity` flag marks groups whose rows span tiers (consider splitting them).
   - Rows lowered by a processing-level cap carry a `NeedsInput` note; confirm the processed data is
     not re-identifiable before accepting the lower tier.
   - If a row's preferred schema (`MetadataTypes`) is a `(fallback)` and a non-fallback candidate exists in
     `MetadataCandidates`, note it in `ReviewNotes`.
   - If sensitivity changes, re-check whether `RecommendedRepository` still makes sense
     (e.g. Controlled human data cannot go to an open archive) and note it.
   - `NeedsInput` is carried into the reviewed CSV unchanged; you may append entries, never delete any.
   - With old manifests: go through `proposed_corrections.csv` row by row and record accept/reject per correction in
     `ReviewNotes` (LandscapeId, Field, decision, reason). Corrections are never applied automatically; apply the
     accepted ones only by editing the reviewed CSV, never the inputs. Low-confidence and audit/format-inferred fills
     need a second look. Use `description_loss.csv` to note content (accessions, rsIDs, markers, cohort sizes) to
     restore in the revised descriptions. Produce a per-center follow-up list from `row_issues.csv` grouped by
     `Origin`: "inherited from old entry" -> ask the center; "introduced in revision" -> fix in the revision;
     "no old entry" -> check the row's provenance. Public deposits (`ExistingDeposit`) may mean linking rather than
     re-uploading.
   - Resolve or restate each `ReviewFlags` item (unknown species, skipped file counts,
     `Unknown` category).

   Write the results as:
   - `landscape_summary_reviewed.csv` - the script's CSV with a `ReviewNotes` column appended
     (every override: old -> new value, evidence, plus proposed schemas; blank if untouched), and
     `NeedsInput` kept in place.
     Change the affected cells in place.
   - Compact files: agent review does not change them unless an override changed one of their columns
     (e.g. `RecommendedRepository`, `Species`). In that case regenerate them from the reviewed CSV:
     `python3 .claude/skills/landscape-summary/scripts/summarize_landscape.py --compact-from OUT/landscape_summary_reviewed.csv --out-dir OUT/`
   - `landscape_summary.md` - a markdown table, one row per data type and level: DataType, ProcessingLevel, Assays,
     FileFormats, MetadataTypes, ProjectedSensitivity, RecommendedRepository, Priority,
     CategoryStudyCount, StudyCount, DatasetCount, EstimatedFileCount, Species, NeedsInput (short) and a short Notes column.
     Leave the long rationale columns out. `MetadataCandidates` is NOT in the md table (keep it compact) but stays
     in the reviewed CSV.

   After writing the reviewed CSV / md (and regenerating compact if needed), refresh the package README so its
   file list is current:
   `python3 .claude/skills/landscape-summary/scripts/summarize_landscape.py --readme-only --out-dir OUT/`

4. **Report to the user.** Show the table (or the md), then list the review flags and
   exactly what you overrode and why, and what was only proposed. Point to the output files.
   Mention that the table is a projection for curator review, not an access determination.

## Outputs (written to `--out-dir`)

- `landscape_summary.csv` - columns: DataType, ProcessingLevel, Assays, FileFormats, PreferredFileFormat,
  MetadataTypes, MetadataCandidates (non-preferred alternative schemas), UnmappedAssays, ProjectedSensitivity, SensitivityRationale,
  RecommendedRepository, OtherRepositories, RepositoryRationale, Priority, CategoryStudyCount,
  StudyCount, DatasetCount, RowCount, EstimatedFileCount, EarliestAvailableDate, ExpectedDataVolume (largest old-manifest volume band; blank
  without `--old-manifests`), Species, ReviewFlags, NeedsInput. Sorted by
  Priority, CategoryStudyCount (desc), DataType, then ProcessingLevel in enum order.
- `landscape_summary_compact.csv` and `landscape_summary_compact.md` (plain GitHub table) - columns exactly: DataType,
  ProcessingLevel, Assays, FileFormats, PreferredFileFormat, RecommendedRepository, Priority, CategoryStudyCount,
  StudyCount, DatasetCount, Species, EarliestAvailableDate, ExpectedDataVolume; same row order as the full CSV. Written by the script;
  `--compact-from REVIEWED_CSV --out-dir DIR` rewrites them from a reviewed CSV.
- `PreferredFileFormat` comes from `references/canonical_formats.csv` (values validated against
  `references/file_format_enum.json`); `EarliestAvailableDate` is the earliest parseable `UploadByDate` (ISO).
- With `--old-manifests` (columns and rules in rubric.md): `old_manifest_links.csv` (revised row -> old entry/item),
  `row_issues.csv` (format/level plausibility issues with Origin), `proposed_corrections.csv` (evidence-backed
  proposals, never applied), `description_loss.csv`. `review_packet.json` then also carries fills and the volume-band
  distribution per row. `NeedsInput`/`ReviewFlags` gain fill, deposit, row-issue and description-loss entries.
- `README.md` and `run_info.json` (every normal run; `--readme-only --out-dir DIR` rebuilds the README from run_info.json
  and the files present). The README is for colleagues receiving the folder: files, columns, method, caveats; no landscape content.
- `excluded_rows.csv`, `review_packet.json` (rows include both new columns), `landscape_summary_reviewed.csv`, `landscape_summary.md` as above.

## Notes

- `EstimatedFileCount` skips rows with missing or non-numeric samples/ratio; the number is in
  the flag. Derived datasets (`DerivedFrom`) are counted as their own rows.
- Assay matching ignores case, spaces and punctuation; sensitivity is capped by
  processing level (Level 3 and 4 -> Registered) except persistently identifiable categories
  (`level_max_tier`, `level_cap_exempt_categories`); code/model rows (code formats, or category model with blank format) are always Open; the
  fallback repository follows the tier (Open / Registered access / Controlled access).
- Priority reflects breadth across studies per DataType only (all levels of a DataType share it; the single-cell split has its own breadth). Sensitivity, repository and other rules always use the underlying category, not importance or data quality.
- To change a rule (controlled categories, repository order, priority thresholds), edit
  `references/rules.json` and rerun; see `references/rubric.md`.
- Tests: `python3 -m pytest .claude/skills/landscape-summary/tests -q`.
