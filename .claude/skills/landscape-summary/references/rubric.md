# Landscape summary rubric

Every rule below is implemented in `scripts/summarize_landscape.py` and parameterised by
`references/rules.json`. Edit the JSON to change behaviour; matching is case-insensitive
and whitespace-trimmed. Rule output is a *projection* for curator review, not a
determination; the agent review step (bottom) can override it with a recorded reason.

## Grouping
- One output row per (`DataType`, `DatasetProcessingLevel`). Category matching is case-insensitive
  ("Clinical data" and "clinical data" merge; the most common spelling is displayed); blank category becomes
  `Unknown`. `DataType` is the category name, plus " (<group>)" when the row's assay (after aliases, matched on the
  alphanumeric-lowercase key) is listed in `assay_resolution` (default groups `single-cell`: single-cell RNA-seq,
  single cell ATAC-seq, scCGI-seq; `single-nucleus`: single-nucleus RNA-seq, single-nucleus ATAC-seq; `spatial`:
  spatial transcriptomics), e.g.
  "gene expression (single-cell)". Other rows (bulk RNA-seq) keep the plain category. Split rows stay separate from the
  bulk row even at the same level. Every rule below (sensitivity, repository, caps, exemptions, controlled/open
  categories, format conflicts) keys on the underlying `DatasetCategory`, never the DataType label.
- `ProcessingLevel` is a single value, matched case-insensitively to `processing_level_order`
  (Level 1, Level 2, Level 3, Level 4, Auxiliary, Metadata, Not Applicable, Unspecified). A blank level becomes
  `Unspecified` with the ReviewFlag "N row(s) with no DatasetProcessingLevel".
- All per-row logic (sensitivity, repository, mixed-sensitivity flag, format conflicts, NeedsInput, flags)
  runs per (DataType, level) row. `review_packet.json` has one entry per row.
- CSV headers may be property names (`DatasetCategory`) or titles (`Dataset Category`); unknown
  columns (e.g. `ROW_ID`) are ignored.
- `CategoryStudyCount` = distinct `StudyName` declaring the DataType (the possibly split label) at any level; `StudyCount` = distinct
  `StudyName` for this (category, level). `DatasetCount` = distinct `DatasetGroup` (falling back to
  `DatasetName` when the group is blank), scoped per study. `RowCount` counts every row;
  derived datasets (`DerivedFrom`) and rows repeated across inputs are not de-duplicated.

## EarliestAvailableDate
Earliest `UploadByDate` among the rows of the output row, ISO `YYYY-MM-DD`. Accepted inputs (surrounding whitespace
tolerated): `YYYY-MM-DD` (a trailing time part such as `T10:20:30Z` or ` 10:20` is dropped), `YYYY/MM/DD`,
`MM/DD/YYYY` (US order, so `03/07/2025` is 7 March), and 13-digit epoch milliseconds (UTC). Blank values are ignored;
non-blank unparseable values (including impossible dates and day-first dates) are ignored and add the ReviewFlag
"N row(s) with unparseable UploadByDate". No parseable dates -> blank.

## Preferred file format
`references/canonical_formats.csv` (Assay, Category, ProcessingLevel, PreferredFileFormat, Rationale; each row keys on
an assay or a category, never both). Every value must be a `DatasetFileFormat` enum value of NAMhub-Landscape
(`references/file_format_enum.json`, checked by tests). ProcessingLevel is `Any` or one level. Several values are
"; "-separated only when genuinely co-canonical.
Lookup per assay in the row (assay matched on the alphanumeric key, category case-insensitively on the underlying
category), first hit wins: (Assay, Level) -> (Assay, Any) -> (Category, Level) -> (Category, Any). Rows with no
specific assay, or with Unknown/blank assays, also use the category steps. `PreferredFileFormat` = union of the
results, de-duplicated, sorted, "; "-joined.
- No result for an assay (or the category): ReviewFlag "No canonical format for: <assays or category>".
- If declared `FileFormats` share no value (case-insensitive) with `PreferredFileFormat`: ReviewFlag
  "Declared formats don't include preferred format (<preferred>)". Not applied to Auxiliary or Not Applicable rows
  (nor when no preferred format resolved).

## Compact outputs
`landscape_summary_compact.csv/.md`: DataType, ProcessingLevel, Assays, FileFormats, PreferredFileFormat,
RecommendedRepository, Priority, CategoryStudyCount, StudyCount, DatasetCount, Species, EarliestAvailableDate,
ExpectedDataVolume (last), in the full CSV's row order. In the full CSV `ExpectedDataVolume` follows `EarliestAvailableDate`
(blank without `--old-manifests`; `--compact-from` tolerates reviewed CSVs that lack it). The md is a plain GitHub table with "|" escaped as "\|". `--compact-from REVIEWED_CSV --out-dir DIR`
regenerates them from a reviewed CSV.

## Metadata types
The mapping (`--mapping`, default `<repo>/landscape_assay_schema_candidates.csv`) is long-format: one row per
(Assay, ProcessingLevel, candidate schema). Columns: Assay, ProcessingLevel (`Any` or `Level 1`..`Level 4`),
Namespace (HTAN2, MC2, NF, ADKP, ELITE, ALS), Organization, SchemaName, SchemaVersion, SchemaURI,
IsPreferred, IsFallback (Yes/No), MatchBasis, Rationale. Each (Assay, ProcessingLevel) group has exactly one
preferred row.

Lookup, per assay in a summary row with processing level L (after aliases/unspecified handling): the assay key is
lowercased with every non-alphanumeric character removed, so "in silico synthesis" matches "Insilicosynthesis" and
"single cell ATAC-seq" matches "Single-cell ATAC-seq", but "single-nucleus ATAC-seq" stays unmapped (no fuzzy
matching). If the assay has rows with ProcessingLevel == L, the preferred schema comes from those and its `Any`
rows are listed as non-preferred candidates; otherwise its `Any` rows are used as-is.
Auxiliary, Metadata, Not Applicable and Unspecified always use `Any`. Each assay resolves independently, so one
row can mix level-specific and `Any` schemas.

- `MetadataTypes` = the preferred candidate(s), formatted `Namespace:SchemaName vVersion`, suffixed
  `(fallback)` when `IsFallback` is Yes; de-duplicated and sorted.
- `MetadataCandidates` (right after `MetadataTypes`) = every non-preferred candidate for the row's assays,
  same format (no fallback suffix), joined with "; ", de-duplicated (a schema already shown as preferred is not
  repeated), sorted by Namespace then name. These are alternatives for curator review.
- Backward compatibility: a mapping with a `DCC` column and no `Namespace` column (old format: Assay, DCC,
  SchemaName, SchemaURL, IsFallback, Rationale) is read as ProcessingLevel `Any`, Namespace = DCC, always
  preferred, no version (formatted `Namespace:SchemaName`, no " v").

`assay_aliases` (a map of assay -> canonical name, keys matched on the same key, e.g. `PharmocokineticADMEassay` ->
`pharmacokinetic ADME assay`) is applied first, before schema mapping and display de-duplication. Assay display
values within a row are de-duplicated on the same key. Assays with no candidates at L or `Any` are listed in
`UnmappedAssays` and raise a review flag. Assays listed in `unspecified_assay_values` (default `Unknown`) and blank
assays are not looked up and not listed as unmapped; instead one flag "N row(s) with no specific assay
(Unknown/blank); set DatasetAssay" is added.

## Placeholder/test rows
Rows where any of DatasetName, DatasetDescription or Comments matches (case-insensitive `re.search`)
a regex in `placeholder_patterns` (default: "test" / "initial test" alone) are excluded from every
aggregate (counts, studies, tiers, priority). They are listed in `excluded_rows.csv`
(LandscapeId, StudyName, DatasetName, Reason) and the count is printed in the CLI summary line.

## Format/category conflicts
`format_category_conflicts` lists `{formats, categories, message}` entries (default: sequencing formats on
image/mask image/volume categories). A matching row keeps its format in `FileFormats` but the format
is treated as blank for sensitivity tier and repository, and a ReviewFlag
"Format conflict: N row(s) (<LandscapeIds>): <message>; format ignored for sensitivity/repository" is added.

## EstimatedFileCount
Per row: `Files per sample` = samples x ratio; `Samples per file` = ceil(samples / ratio);
`Total files` = ratio. Rows with missing/non-numeric inputs (or an unknown unit) are skipped
and counted in a review flag. The values are summed.

## Sensitivity (`ProjectedSensitivity`)
Tiers: Open < Registered < Controlled. A row group's tier is the maximum over its rows, then the processing-level gradient (below) may lower it. Per row,
first match wins:
1. Code/model row -> **Open** regardless of species, including blank/Unknown (no unknown-species
   flag). A row is code/model if its format is in `code_model_formats` (Github Repo, docker image,
   Python/R/MATLAB/bash script, Jupyter notebook, rmd, js, ...) or its category is in
   `code_model_categories` ("model") and its format is blank. Switch off with `code_model_always_open`.
1b. Category in `open_categories` (default "data index": documentation/metadata, no sample data) ->
   **Open** regardless of species.
2. Species in `human_species` (Homo sapiens):
   - category in `controlled_categories`, or format in `identifiable_formats` AND processing
     level in `identifiable_processing_levels` (Level 1/2) -> **Controlled**
   - otherwise -> **Registered**
   - Rows whose assay is in `computational_assays` (default "in silico synthesis": model outputs, not
     participant records) skip the controlled-category rule; the rationale then reads
     "human, computational assay (controlled-category rule not applied)". The identifiable-format
     rule still applies.
3. Species blank/Unknown -> **Registered** plus a review flag.
4. Any other species -> **Open**. "Mus musculus (humanized)" counts as non-human.

### Processing-level gradient
Processed data is less likely to identify participants, so tier caps apply by level:
- `level_max_tier` (default `{"Level 3": "Registered", "Level 4": "Registered"}`, i.e. processed human-derived data stops at Registered); unlisted levels are uncapped.
- `level_cap_exempt_categories` (clinical, demographics, epidemiological data, survey data, audio transcript,
  text data, germline/annotated germline/genomic/structural/copy number variants) are persistently
  identifiable; caps never apply to them.
- After the rules above give a row's tier, if its level has a cap, the category is not exempt and the tier is
  higher than the cap, it is lowered to the cap. The rationale says "<old> lowered to <new> by <level> cap" and
  the repository is chosen from the capped tier. The cap only lowers, so Open rules (code/model, open
  categories, non-human) are unaffected.
- Every lowering adds a NeedsInput entry "N row(s) lowered <old> -> <new> by <level> processing-level cap;
  confirm processed data is not re-identifiable". Rows already at or below the cap get no entry.

`SensitivityRationale` lists, per tier, how many rows fired which rule.

## Repository
`repository_rules` are evaluated in order per row; the first rule matching wins. A rule matches
when its `sensitivity` list (if present) contains the row's tier AND (if `categories` and/or
`formats` are present) the row's category or format is listed. No match -> `fallback_repository`, chosen by tier:
Open -> Synapse (open), Registered -> Synapse (registered access), Controlled -> Synapse
(controlled access); a Registered row is never labelled "Synapse (open)". `RecommendedRepository` is the most frequent per-row result among the rows whose tier equals
the category's `ProjectedSensitivity` (ties go to the earliest rule), so the recommendation never
contradicts the sensitivity. Repositories from the other rows are `OtherRepositories`. When rows
span more than one tier a `Mixed sensitivity` review flag is added and `RepositoryRationale`
keeps the per-row breakdown. The rationale notes
that NAMHub also hosts metadata/landscape in Synapse whenever the recommendation is external.

## NeedsInput
Separate from data-quality `ReviewFlags`: decisions that need curator input ("; "-joined, blank if none).
Evaluated per (category, level) over its rows after exclusions:
- `public_archive_repositories` (GEO, SRA, PRIDE, MetaboLights, BioImage Archive, IDR): if
  `RecommendedRepository` is one and `ProjectedSensitivity` is Registered -> "<repo> is fully public;
  confirm donor consent allows open release, otherwise Synapse (registered access)".
- `needs_input_patterns`: list of `{pattern, message, tiers?, code_model_only?}`. The regex
  (case-insensitive) is searched in DatasetName, DatasetDescription, ContextOfUse and Comments. `tiers`
  restricts the pattern to categories whose `ProjectedSensitivity` is listed; `code_model_only` restricts
  it to code/model rows. One entry per matched pattern: "<message> (N row(s), e.g. <LandscapeId>:
  "...snippet of at most 100 characters around the first match...")". Defaults cover consent/access
  terms, "de-identified" on Controlled categories, and participant-related words in code/model rows.

## Priority
Breadth across studies per DataType only (`priority` in rules.json; the single-cell/single-nucleus split gets its
own breadth); all levels of a DataType share its Priority. With T = total distinct studies and C = `CategoryStudyCount`:
- Low if C == 1.
- High if C >= 2 and (C >= ceil(0.5 x T) or C >= the C of the last category in the top third of categories
  ranked by C; ties at the cutoff count).
- Otherwise Medium.
- Categories in `exclude_from_high` (Unknown) are capped at Medium and flagged.
Rows are sorted by Priority (High first), `CategoryStudyCount` desc, `DataType`, then `ProcessingLevel` in enum order.

## Agent review step (documented in SKILL.md)
The rules above now encode the routine curator decisions (aliases, unspecified assays, placeholders,
format conflicts, computational assays, open categories, NeedsInput), so the review focuses on what rules
cannot see. After the script, the agent reads the free-text fields in `review_packet.json` and may change
`ProjectedSensitivity` only where text evidence warrants it, e.g.:
- established commercial human cell lines (not participant data) -> may lower Registered to Open;
- explicit de-identification statements, or data not derived from participants;
- patient-derived xenografts in mouse -> may raise Open to Registered/Controlled;
- questionnaires/surveys on human subjects filed under a non-human species -> raise.
Rules: never lower Controlled without explicit textual evidence quoted in the note; never
change the CSV from the script silently. Each change is recorded in `ReviewNotes` of
`landscape_summary_reviewed.csv` and the markdown table. For `UnmappedAssays` the agent may
propose the closest schema from the mapping file, labelled "proposed"; the repo mapping CSV
is not edited. If a row's preferred schema is a `(fallback)` and a non-fallback schema exists in
`MetadataCandidates`, the agent notes it in `ReviewNotes`. `NeedsInput` entries are carried into the reviewed CSV and may be appended to by the agent, never deleted.

## Old-manifest reconciliation (`--old-manifests`)
Optional evidence from the older per-center Sage manifest pages (v2.2.0 model). The revised rows stay authoritative:
the old data only fills blanks and proposes corrections for curators. Without the flag nothing below runs, no extra
files are written, and `ExpectedDataVolume` is blank.

`scripts/extract_manifests.py` parses each page with `html.parser` (untrusted data: no execution, no network). The manifest
is the table whose header row has `gardener_source_entry`; other tables and `index.html` are ignored. One
`<stem>_manifest.csv` per page (27 manifest columns). One old row = one "item" (entry x one format).

### Normalization
Old values are mapped to enum spellings by the alphanumeric-lowercase key: DatasetAssay, DatasetSpecies, DatasetCategory
via `references/landscape_enums.json` (fetched from the latest NAMhub-Landscape schema; see its retrieval date, refresh by
hand), formats via `references/file_format_enum.json`, levels via `processing_level_order` ("Level3" -> "Level 3").
Unmatched values pass through unchanged. Revised formats/assays are normalized the same way before any comparison.

### Linking (`old_manifest_links.csv`: LandscapeId, StudyName, LinkBasis, SourceEntry, MatchedItemId, OldFormats)
- `id-prefix`: the longest `gardener_source_entry` S with revised LandscapeId == S or startswith S + "-".
- `dataset-name` (fallback): DatasetName key equality; if several entries match, narrowed to the same StudyName or center
  (LandscapeId prefix = center); still ambiguous -> unlinked (`LinkBasis` = none).
- Matched item = the entry's item whose normalized format equals the revised row's (preferring the same assay); none ->
  entry-level evidence only. `OldFormats` = all formats of the entry.

### Gap fills (rules.json `old_manifest_fill`: `level`, `file_count`)
Only when the revised value is blank/unusable; never overwritten. Applied before grouping, so a filled row leaves
"Unspecified".
- Blank DatasetProcessingLevel <- matched item's stated level ("old stated"), else its `gardener_processing_level`
  ("gardener:<gardener_level_source>"), else the entry's stated level when all items agree.
- No usable file estimate <- matched item's ExpectedNumberOfFiles, else numeric `gardener_expected_files`
  ("gardener:<gardener_files_basis>"). Stored as FileRatio = N, unit "Total files".
- Provenance (field, value, source) is in `review_packet.json` (`old_manifest.fills`, record `OldManifestFills`) and in
  `proposed_corrections.csv`. NeedsInput: "N row(s) used old-manifest fills (level: a, file count: b); confirm", plus
  "N level fill(s) are audit/format-inferred, not center-stated" for sources `carried_from_audit` / `format_dictionary`.
  Fill confidence in the corrections file: high = old stated; low = audit/format-inferred level or any gardener file
  count; medium = other gardener levels (`entry_stated`).

### ExpectedDataVolume
Each row takes its matched item's `gardener_expected_data_volume` (blank/"unknown"/unlisted ignored; falls back to the
entry's items if all known bands agree). The summary row shows the largest band in rules.json `volume_band_order`
(under 1 GB ... PBs); blank if none. `review_packet.json` has the band distribution (`old_manifest.volume_bands`).

### Row-level plausibility (`row_issues.csv`, rules in `references/format_level_rules.json`)
Columns: LandscapeId, StudyName, DataType, ProcessingLevel, Assay, Format, Issue, Origin, OldStatedLevel,
OldGroundedLevel, OldFormats. Evaluated after fills, on the revised row's assay family (`sequencing_assays`,
`imaging_assays`, `mass_spec_assays`; keys on the alphanumeric key):
- "sequencing Level 1 with non-raw format": level 1 and the format not in `level1_allowed_formats_for_sequencing`.
- "format implies <level> but stated <level>": `format_implied_level_for_sequencing` (blank/Unspecified levels skipped).
- "<format family> format on <assay label> assay": `disallowed_format_families` (sequencing x image formats, imaging x
  sequencing formats, mass spec x image formats).
A row can have several issues (Level 1 + bam gives the first two). Origin: "inherited from old entry" (an old item of
the entry has the same format and, with its stated level, gives the same issue), "introduced in revision" (the old entry
exists but lacks this format/level combination), "no old entry" (unlinked). Count goes to ReviewFlags
("N row issue(s): see row_issues.csv").

### Proposed corrections (`proposed_corrections.csv`; never applied)
Columns: LandscapeId, StudyName, DatasetName, Assay, Field, CurrentValue, ProposedValue, Evidence (<=200 chars), Source,
Confidence, Rationale.
- Itemization: old Comments/DatasetDescription segments "<assay label> (<n> <format>, ...)" are parsed (labels in
  `itemization_assay_labels`; truncated text parsed as far as it goes, an unrecognised cut-off last item dropped;
  duplicate formats summed and noted in Rationale). For a revised row with assay A, format F where the entry itemizes A:
  F listed -> `FileCount` (high; when it differs from the row's estimate or none) and, for sequencing assays,
  `DatasetProcessingLevel` = implied level (high; when it differs from stated). F not listed -> judged over the
  *scope* = all revised rows linked to the same old source entry with the same assay (any DatasetGroup, so a derived
  dataset in its own group does not hide raw formats present elsewhere):
  - row has `DerivedFrom` set -> no proposal (derived products are legitimately absent from raw itemizations);
  - none of A's itemized formats appear in the scope -> `DatasetFileFormat` = the itemized formats (medium, replace);
  - otherwise -> `DatasetFileFormat` = "review: not in old itemization (old lists <formats>)" (low; a review prompt,
    not a replacement).
  Itemized formats absent across the whole scope -> `MissingRow` "<format> (<n> files, implied <level>)" (medium, once
  per old entry/assay/format), attached to the first row of the scope by LandscapeId whose `DerivedFrom` is blank
  (never to derived rows).
- Level conflict without itemization of the row's assay: stated level differs from the matched item's grounded level
  -> `DatasetProcessingLevel`; low if `format_dictionary`, medium if `entry_stated` (`carried_from_audit` ignored).
- `ExistingDeposit` (medium): accessions in old text (GSE, SRP/SRR/SRX, PRJNA, phs, PXD, MTBLS, EMPIAR/S-BIAD,
  PMID, bare "SRA") plus a NeedsInput "already deposited: ...; consider linking instead of re-uploading".
- The gap fills above are listed too so curators can confirm them.

### Description loss (`description_loss.csv`)
Columns: LandscapeId, StudyName, OldDescription, OldComments (matched item, else the entry's longest),
NewDescription, Similarity (difflib ratio of the two descriptions, 2 dp), MissingTerms, CountConflicts. Only rows with
MissingTerms or CountConflicts are written. MissingTerms = salient tokens of old description+comments absent (case-
insensitive) from the new description+comments: rsIDs, accessions (as above), gene/marker-like tokens (`[A-Z][A-Z0-9-]{2,}`, trailing "-" stripped, tokens then shorter than 3 characters dropped (e.g. "NF-" from "NF-κB"),
minus `description_stoplist` and format names), and numbers attached to cohort words ("30 patients", "n=24",
"Years 1-3"; a cohort number counts as present if the same number appears with any cohort noun). CountConflicts =
cohort numbers for the same noun class that are disjoint between old and new (classes in `cohort_nouns`: patients/
donors/participants/subjects/individuals are one class; samples, lines, animals separate). Count goes to ReviewFlags
("N row(s) lost description content: see description_loss.csv").

## Package README and run_info.json
Every normal run also writes `run_info.json` (timestamp, input/row/excluded/study/output counts, whether old manifests
were used and how many rows linked, mapping file name/row count/namespace versions, rules/format file names, enum fetch
notes, script path, the command with `<INPUT>`, `<OUT_DIR>`, `<OLD_MANIFESTS>` placeholders, and a snapshot of the rule
values the README quotes) and `README.md` (generated by `scripts/package_readme.py`). The README holds no landscape
content (no study/dataset names, descriptions or ids): only counts, file and column descriptions (`COLUMN_DESCRIPTIONS`,
one entry per output column), method and provenance. `--readme-only --out-dir DIR` rebuilds it from `run_info.json`
and the files present in DIR (error if `run_info.json` is missing); run it after the review step adds
`landscape_summary_reviewed.csv` / `landscape_summary.md`.
