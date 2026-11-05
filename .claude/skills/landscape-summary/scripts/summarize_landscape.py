#!/usr/bin/env python3
"""Summarize NAMHub Landscape RecordSet CSV exports: one row per (DataType, DatasetProcessingLevel).

DataType is the DatasetCategory, with a " (<group>)" suffix for assays in rules.json `assay_resolution`
(single-cell / single-nucleus). Rules always key on the underlying DatasetCategory.

Usage:
  summarize_landscape.py INPUT [INPUT ...] --out-dir DIR [--mapping PATH] [--rules PATH] [--formats PATH]
                       [--old-manifests PATH [PATH ...]] [--format-level-rules PATH]
  summarize_landscape.py --compact-from REVIEWED_CSV --out-dir DIR
  summarize_landscape.py --readme-only --out-dir DIR     (rebuild README.md from DIR/run_info.json and the files in DIR)

Stdlib only (Python 3.10+). Rules live in ../references/rules.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from collections import Counter, OrderedDict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import old_manifest  # noqa: E402
import package_readme  # noqa: E402
from package_readme import COLUMN_DESCRIPTIONS  # noqa: E402,F401
SKILL_DIR = SCRIPT_DIR.parent
REPO_ROOT = SKILL_DIR.parents[2]  # <repo>/.claude/skills/landscape-summary
SCRIPT_REL = (SCRIPT_DIR / "summarize_landscape.py").relative_to(REPO_ROOT).as_posix()
DEFAULT_RULES = SKILL_DIR / "references" / "rules.json"
DEFAULT_MAPPING = REPO_ROOT / "landscape_assay_schema_candidates.csv"
DEFAULT_FORMATS = SKILL_DIR / "references" / "canonical_formats.csv"
DEFAULT_FORMAT_LEVEL_RULES = SKILL_DIR / "references" / "format_level_rules.json"
DEFAULT_ENUMS = SKILL_DIR / "references" / "landscape_enums.json"
DEFAULT_FORMAT_ENUM = SKILL_DIR / "references" / "file_format_enum.json"

# property name -> title (both are accepted as CSV headers)
PROPERTIES = OrderedDict([
    ("LandscapeId", "Landscape_id"), ("StudyName", "Study Name"), ("StudyAim", "Study Aim"),
    ("DatasetName", "Dataset Name"), ("DatasetGroup", "Dataset Group"), ("DerivedFrom", "Derived From"),
    ("DatasetDescription", "Dataset Description"), ("DatasetAssay", "Dataset Assay"),
    ("DatasetCategory", "Dataset Category"), ("DatasetSpecies", "Dataset Species"),
    ("DatasetFileFormat", "Dataset File Format"), ("DatasetProcessingLevel", "Dataset Processing Level"),
    ("ExpectedNumberOfSamples", "Expected Number Of Samples"), ("FileRatio", "File Ratio"),
    ("FileRatioUnit", "File Ratio Unit"), ("UploadByDate", "Upload By Date"),
    ("ContextOfUse", "Context Of Use"), ("ApplicableStandards", "Applicable Standards"),
    ("DataContributionLead", "Data Contribution Lead"), ("Comments", "Comments"),
])

OUTPUT_COLUMNS = [
    "DataType", "ProcessingLevel", "Assays", "FileFormats", "PreferredFileFormat", "MetadataTypes", "MetadataCandidates",
    "UnmappedAssays", "ProjectedSensitivity", "SensitivityRationale", "RecommendedRepository", "OtherRepositories",
    "RepositoryRationale", "Priority", "CategoryStudyCount", "StudyCount", "DatasetCount", "RowCount",
    "EstimatedFileCount", "EarliestAvailableDate", "ExpectedDataVolume", "Species", "ReviewFlags", "NeedsInput",
]
COMPACT_COLUMNS = [
    "DataType", "ProcessingLevel", "Assays", "FileFormats", "PreferredFileFormat", "RecommendedRepository",
    "Priority", "CategoryStudyCount", "StudyCount", "DatasetCount", "Species", "EarliestAvailableDate",
    "ExpectedDataVolume",
]
NO_FORMAT_CHECK_LEVELS = ("Auxiliary", "Not Applicable")
EXCLUDED_COLUMNS = ["LandscapeId", "StudyName", "DatasetName", "Reason"]
PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
SEP = "; "


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


HEADER_LOOKUP = {}
for _p, _t in PROPERTIES.items():
    HEADER_LOOKUP[_key(_p)] = _p
    HEADER_LOOKUP[_key(_t)] = _p


def norm_header(h: str) -> str | None:
    """Map a CSV header (property name or title) to a property name; None if unknown."""
    return HEADER_LOOKUP.get(_key(h.lstrip("﻿")))


def cf(s: str) -> str:
    return " ".join(s.split()).casefold()


def clean(s) -> str:
    return " ".join((s or "").split())


def parse_list(s: str) -> list[str]:
    """ContextOfUse may be a JSON list string or plain text."""
    s = (s or "").strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            v = json.loads(s)
            if isinstance(v, list):
                return [clean(str(x)) for x in v if clean(str(x))]
        except ValueError:
            pass
    return [clean(s)]


def to_num(s):
    try:
        v = float(str(s).strip())
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def canon(values, keyfn=cf) -> list[str]:
    """Dedupe by keyfn (default case-insensitive); display the most common spelling (ties -> lowercase-preferring max)."""
    groups: dict[str, Counter] = {}
    for v in values:
        v = clean(v)
        if v:
            groups.setdefault(keyfn(v), Counter())[v] += 1
    out = [max(c.items(), key=lambda kv: (kv[1], kv[0]))[0] for c in groups.values()]
    return sorted(out, key=str.casefold)


# ---------------------------------------------------------------- loading
def find_csvs(inputs) -> list[Path]:
    files: list[Path] = []
    for i in inputs:
        p = Path(i)
        if p.is_dir():
            files.extend(sorted(p.glob("*.csv")))
        elif p.is_file():
            files.append(p)
        else:
            raise SystemExit(f"Input not found: {i}")
    if not files:
        raise SystemExit("No CSV inputs found")
    return files


def load_rows(files) -> list[dict]:
    rows = []
    for f in files:
        with open(f, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            colmap = {h: norm_header(h) for h in (reader.fieldnames or [])}
            for raw in reader:
                row = {p: "" for p in PROPERTIES}
                for h, v in raw.items():
                    p = colmap.get(h)
                    if p:
                        row[p] = (v or "").strip()
                if not any(row.values()):
                    continue
                row["_source"] = f.name
                rows.append(row)
    return rows


def _yes(s) -> bool:
    return cf(s or "") in ("yes", "true", "y")


ANY_LEVEL = "Any"
LEVEL_SPECIFIC = ("Level 1", "Level 2", "Level 3", "Level 4")


def load_mapping(path) -> dict[str, list[dict]]:
    """Assay key -> list of candidate dicts {Level, Namespace, SchemaName, Version, IsPreferred, IsFallback}.

    New long format: Assay, ProcessingLevel, Namespace, SchemaName, SchemaVersion, IsPreferred, IsFallback, ...
    Old format (DCC column, no Namespace): every row is Level "Any", Namespace = DCC, preferred, no version.
    """
    m: dict[str, list[dict]] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        old = "DCC" in (reader.fieldnames or []) and "Namespace" not in (reader.fieldnames or [])
        for r in reader:
            a = _key(r.get("Assay", ""))  # lowercase, alphanumerics only
            if not a:
                continue
            if old:
                cand = {"Level": ANY_LEVEL, "Namespace": clean(r.get("DCC")), "Version": "", "IsPreferred": True}
            else:
                lv = clean(r.get("ProcessingLevel")) or ANY_LEVEL
                lv = next((o for o in (ANY_LEVEL,) + LEVEL_SPECIFIC if cf(o) == cf(lv)), lv)
                cand = {"Level": lv, "Namespace": clean(r.get("Namespace")),
                        "Version": clean(r.get("SchemaVersion")), "IsPreferred": _yes(r.get("IsPreferred"))}
            cand["SchemaName"] = clean(r.get("SchemaName"))
            cand["IsFallback"] = _yes(r.get("IsFallback"))
            m.setdefault(a, []).append(cand)
    return m


def candidate_label(c) -> str:
    ver = f" v{c['Version']}" if c["Version"] else ""
    return f"{c['Namespace']}:{c['SchemaName']}{ver}"


def candidates_for(cands, level) -> list[dict]:
    """Rows for the processing level plus the 'Any' rows.

    When level-specific rows exist (Level 1-4 only), the preferred schema comes from them and the
    'Any' rows are kept as non-preferred candidates; otherwise the 'Any' rows are used as-is.
    """
    any_rows = [c for c in cands if cf(c["Level"]) == cf(ANY_LEVEL)]
    if level in LEVEL_SPECIFIC:
        specific = [c for c in cands if cf(c["Level"]) == cf(level)]
        if specific:
            return specific + [dict(c, IsPreferred=False) for c in any_rows]
    return any_rows


# ---------------------------------------------------------------- dates
def parse_date(s):
    """ISO date string (YYYY-MM-DD) or None. Accepts YYYY-MM-DD (optionally + time), YYYY/MM/DD,
    MM/DD/YYYY (US order) and 13-digit epoch milliseconds (UTC)."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        if re.fullmatch(r"\d{13}", s):
            return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc).date().isoformat()
        m = re.fullmatch(r"(\d{4})([-/])(\d{1,2})\2(\d{1,2})(?:[T ]\S.*)?", s)
        if m:
            y, _, mo, d = m.groups()
            return datetime(int(y), int(mo), int(d)).date().isoformat()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if m:
            mo, d, y = m.groups()
            return datetime(int(y), int(mo), int(d)).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None
    return None


# ---------------------------------------------------------------- canonical formats
def load_formats(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{"Assay": _key(r.get("Assay") or ""), "Category": cf(r.get("Category") or ""),
                 "Level": clean(r.get("ProcessingLevel")) or ANY_LEVEL,
                 "Formats": [clean(x) for x in (r.get("PreferredFileFormat") or "").split(";") if clean(x)]}
                for r in csv.DictReader(fh)]


def lookup_format(table, assay_key, category, level):
    """First hit of (Assay, Level) -> (Assay, Any) -> (Category, Level) -> (Category, Any); [] if none.
    assay_key may be '' to skip the assay steps."""
    cat = cf(category)
    steps = []
    if assay_key:
        steps += [(assay_key, "", level), (assay_key, "", ANY_LEVEL)]
    steps += [("", cat, level), ("", cat, ANY_LEVEL)]
    for a, c, lv in steps:
        for e in table:
            if e["Assay"] == a and e["Category"] == c and cf(e["Level"]) == cf(lv):
                return e["Formats"]
    return []


# ---------------------------------------------------------------- rules
def assay_group(row, rules):
    """assay_resolution group name for the (aliased) row's assay, or ''."""
    k = _key(row["DatasetAssay"])
    for g, assays in rules.get("assay_resolution", {}).items():
        if k and k in {_key(a) for a in assays}:
            return g
    return ""


def row_category(row) -> str:
    return clean(row["DatasetCategory"]) or "Unknown"


def is_code_model(row, rules) -> bool:
    """Code/model row: code format, or model category with a blank format."""
    fmt = cf(row["DatasetFileFormat"])
    if fmt:
        return fmt in {cf(x) for x in rules["code_model_formats"]}
    return cf(row_category(row)) in {cf(c) for c in rules.get("code_model_categories", [])}


UNSPECIFIED_LEVEL = "Unspecified"


def row_level(row, rules) -> str:
    """Canonical processing level: enum spelling if known, 'Unspecified' if blank."""
    lv = clean(row["DatasetProcessingLevel"])
    if not lv:
        return UNSPECIFIED_LEVEL
    return next((o for o in rules["processing_level_order"] if cf(o) == cf(lv)), lv)


def level_sort_key(level, rules):
    order = rules["processing_level_order"]
    return (order.index(level) if level in order else len(order), level.casefold())


def apply_level_cap(tier, row, level, rules) -> str:
    """Tier after the processing-level cap (only ever lowers; exempt categories untouched)."""
    caps = {cf(k): v for k, v in rules.get("level_max_tier", {}).items()}
    cap = caps.get(cf(level))
    if not cap or cf(row_category(row)) in _set(rules, "level_cap_exempt_categories"):
        return tier
    tiers = rules["sensitivity_tiers"]
    return cap if tiers.index(tier) > tiers.index(cap) else tier


def _set(rules, key) -> set[str]:
    return {cf(x) for x in rules.get(key, [])}


def apply_alias(row, rules) -> dict:
    """Return the row with DatasetAssay replaced by its configured alias (keyed on _key)."""
    aliases = {_key(k): v for k, v in rules.get("assay_aliases", {}).items()}
    new = aliases.get(_key(row["DatasetAssay"]))
    return {**row, "DatasetAssay": new} if new else row


def placeholder_reason(row, rules) -> str | None:
    """Reason string if any DatasetName/DatasetDescription/Comments matches a placeholder pattern."""
    for pat in rules.get("placeholder_patterns", []):
        for field in ("DatasetName", "DatasetDescription", "Comments"):
            if re.search(pat, row[field] or "", re.IGNORECASE):
                return f"placeholder/test row: {field} matches /{pat}/"
    return None


def format_conflict(row, rules) -> str | None:
    """Message of the first format_category_conflicts entry matching the row, else None."""
    fmt, cat = cf(row["DatasetFileFormat"]), cf(row_category(row))
    for c in rules.get("format_category_conflicts", []):
        if fmt and fmt in {cf(x) for x in c["formats"]} and cat in {cf(x) for x in c["categories"]}:
            return c["message"]
    return None


def row_tier(row, rules) -> tuple[str, str]:
    """Return (tier, reason) for one row."""
    species = cf(row["DatasetSpecies"])
    fmt = cf(row["DatasetFileFormat"])
    cat = cf(row_category(row))
    level = cf(row["DatasetProcessingLevel"])
    human = species in {cf(s) for s in rules["human_species"]}
    if rules.get("code_model_always_open", True) and is_code_model(row, rules):
        return "Open", "code/model row (no participant data)"
    if cat in _set(rules, "open_categories"):
        return "Open", "open category (documentation/metadata, no sample data)"
    if human:
        computational = _key(row["DatasetAssay"]) in {_key(a) for a in rules.get("computational_assays", [])}
        if cat in {cf(c) for c in rules["controlled_categories"]} and not computational:
            return "Controlled", "human + controlled category"
        if fmt in {cf(x) for x in rules["identifiable_formats"]} and \
                level in {cf(x) for x in rules["identifiable_processing_levels"]}:
            return "Controlled", "human + identifiable format at Level 1/2"
        if computational and cat in {cf(c) for c in rules["controlled_categories"]}:
            return "Registered", "human, computational assay (controlled-category rule not applied)"
        return "Registered", "human, other data"
    if not species or species == "unknown":
        return "Registered", "unknown species (treated as Registered)"
    return "Open", "non-human species"


def rule_matches(rule, row, tier) -> bool:
    if "sensitivity" in rule and tier not in rule["sensitivity"]:
        return False
    cats, fmts = rule.get("categories"), rule.get("formats")
    if cats is None and fmts is None:
        return True
    hit = False
    if cats and cf(row_category(row)) in {cf(c) for c in cats}:
        hit = True
    if fmts and cf(row["DatasetFileFormat"]) in {cf(f) for f in fmts}:
        hit = True
    return hit


def row_repository(row, tier, rules) -> tuple[int, str]:
    for i, rule in enumerate(rules["repository_rules"]):
        if rule_matches(rule, row, tier):
            return i, rule["repository"]
    return len(rules["repository_rules"]), fallback_for(tier, rules)["repository"]


def fallback_for(tier, rules) -> dict:
    fb = rules["fallback_repository"]
    return fb[tier] if tier in fb else fb


def file_estimate(row):
    n, r = to_num(row["ExpectedNumberOfSamples"]), to_num(row["FileRatio"])
    unit = cf(row["FileRatioUnit"])
    if unit == "total files":
        return r
    if n is None or r is None:
        return None
    if unit == "files per sample":
        return n * r
    if unit == "samples per file":
        return math.ceil(n / r) if r > 0 else None
    return None


def fmt_num(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


def assign_priority(study_counts: dict[str, int], total_studies: int, rules, base=None) -> tuple[dict, dict]:
    """Return ({data type: priority}, {data type: extra flag}). `base` maps a data type label to its
    underlying category (for exclude_from_high); defaults to the label itself."""
    base = base or {}
    pr = rules["priority"]
    thresh = math.ceil(pr["high_fraction_of_studies"] * total_studies)
    ranked = sorted(study_counts.values(), reverse=True)
    top_n = max(1, math.ceil(pr["top_fraction_of_categories"] * len(ranked) - 1e-9)) if ranked else 0
    cutoff = ranked[top_n - 1] if ranked else 0
    excl = {cf(x) for x in pr["exclude_from_high"]}
    out, flags = {}, {}
    for cat, n in study_counts.items():
        if n <= pr["low_if_study_count_equals"]:
            out[cat] = "Low"
            continue
        high = n >= pr["min_studies_for_high"] and (n >= thresh or n >= cutoff)
        if high and cf(base.get(cat, cat)) in excl:
            out[cat] = "Medium"
            flags[cat] = f"'{cat}' would rank High by study breadth but is excluded from High; clean up category"
        else:
            out[cat] = "High" if high else "Medium"
    return out, flags


def _snippet(text: str, m: re.Match, width: int = 100) -> str:
    extra = max(0, width - (m.end() - m.start()))
    start = max(0, m.start() - extra // 2)
    end = min(len(text), start + max(width, m.end() - start))
    end = min(end, start + width)
    return "\u2026" + text[start:end] + "\u2026"


def needs_input(rows, effs, top, rec, rules) -> list[str]:
    """Curator-decision items for one category (rows already exclusion-filtered)."""
    out = []
    if top == "Registered" and cf(rec) in _set(rules, "public_archive_repositories"):
        out.append(f"{rec} is fully public; confirm donor consent allows open release, "
                   "otherwise Synapse (registered access)")
    for np_ in rules.get("needs_input_patterns", []):
        if "tiers" in np_ and top not in np_["tiers"]:
            continue
        rx = re.compile(np_["pattern"], re.IGNORECASE)
        hits, first = 0, None
        for r, e in zip(rows, effs):
            if np_.get("code_model_only") and not is_code_model(e, rules):
                continue
            texts = [r["DatasetName"], r["DatasetDescription"], " ".join(parse_list(r["ContextOfUse"])), r["Comments"]]
            for t in texts:
                m = rx.search(t or "")
                if m:
                    hits += 1
                    if first is None:
                        first = (r["LandscapeId"], _snippet(t, m))
                    break
        if hits:
            out.append(f'{np_["message"]} ({hits} row(s), e.g. {first[0]}: "{first[1]}")')
    return out


# ---------------------------------------------------------------- summarize
def summarize(rows, mapping, rules, excluded=None, formats=None, oldm=None):
    """Aggregate rows. Placeholder rows are dropped; if `excluded` is a list they are appended to it
    as {LandscapeId, StudyName, DatasetName, Reason} dicts. `formats` is the canonical-format table
    (load_formats); None skips PreferredFileFormat. `oldm` is an old_manifest.OldManifest (rows already
    passed through old.apply_fills) or None."""
    kept = []
    for r in rows:
        why = placeholder_reason(r, rules)
        if why:
            if excluded is not None:
                excluded.append({"LandscapeId": r["LandscapeId"], "StudyName": r["StudyName"],
                                 "DatasetName": r["DatasetName"], "Reason": why})
        else:
            kept.append(apply_alias(r, rules))
    rows = kept
    total_studies = len({clean(r["StudyName"]) for r in rows if clean(r["StudyName"])})
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(cf(row_category(r)), []).append(r)
    groups: dict[tuple[str, str, str], list[dict]] = {}
    cat_names: dict[str, str] = {}
    type_studies: dict[str, set] = {}
    type_base: dict[str, str] = {}
    for ck, crs in by_cat.items():
        name = "Unknown" if ck == "unknown" else canon([row_category(r) for r in crs])[0]
        cat_names[ck] = name
        for r in crs:
            grp = assay_group(r, rules)
            label = f"{name} ({grp})" if grp else name
            type_base[label] = name
            if clean(r["StudyName"]):
                type_studies.setdefault(label, set()).add(clean(r["StudyName"]))
            else:
                type_studies.setdefault(label, set())
            groups.setdefault((ck, label, row_level(r, rules)), []).append(r)
    cat_studies = {k: len(v) for k, v in type_studies.items()}

    drafts = {}
    for (ck, dt, level), rs in groups.items():
        cat = cat_names[ck]
        flags = []
        if level == UNSPECIFIED_LEVEL:
            flags.append(f"{len(rs)} row(s) with no DatasetProcessingLevel")
        studies = {clean(r["StudyName"]) for r in rs if clean(r["StudyName"])}
        datasets = {(clean(r["StudyName"]), clean(r["DatasetGroup"]) or clean(r["DatasetName"]))
                    for r in rs if clean(r["DatasetGroup"]) or clean(r["DatasetName"])}
        # assays / metadata
        assays = canon([r["DatasetAssay"] for r in rs], _key)
        unspecified = {_key(x) for x in rules.get("unspecified_assay_values", [])} | {""}
        n_unspec = sum(1 for r in rs if _key(r["DatasetAssay"]) in unspecified)
        schemas, others, unmapped = set(), {}, []
        for a in assays:
            if _key(a) in unspecified:
                continue
            cs = candidates_for(mapping.get(_key(a), []), level)
            if not cs:
                unmapped.append(a)
                continue
            for c in cs:
                if c["IsPreferred"]:
                    schemas.add(candidate_label(c) + (" (fallback)" if c["IsFallback"] else ""))
                else:
                    others[candidate_label(c)] = (cf(c["Namespace"]), cf(c["SchemaName"]), candidate_label(c))
        pref_labels = {x.removesuffix(" (fallback)") for x in schemas}
        # A schema preferred outright for one assay isn't also listed as a fallback for another.
        schemas = {x for x in schemas if not (x.endswith(" (fallback)") and x.removesuffix(" (fallback)") in schemas)}
        candidates = sorted((l for l in others if l not in pref_labels), key=lambda l: others[l])
        if unmapped:
            flags.append("Unmapped assays: " + ", ".join(unmapped))
        if n_unspec:
            flags.append(f"{n_unspec} row(s) with no specific assay (Unknown/blank); set DatasetAssay")
        # file count
        total, skipped = 0.0, 0
        for r in rs:
            e = file_estimate(r)
            if e is None:
                skipped += 1
            else:
                total += e
        if skipped:
            flags.append(f"{skipped} row(s) skipped in EstimatedFileCount (missing/non-numeric)")
        # earliest upload-by date
        dates, bad = [], 0
        for r in rs:
            if clean(r["UploadByDate"]):
                d_ = parse_date(r["UploadByDate"])
                if d_:
                    dates.append(d_)
                else:
                    bad += 1
        if bad:
            flags.append(f"{bad} row(s) with unparseable UploadByDate")
        earliest = min(dates) if dates else ""
        # canonical formats
        declared = canon([r["DatasetFileFormat"] for r in rs])
        preferred = ""
        if formats is not None:
            prefs, missing = set(), []
            # Code/model rows take the code format (the "model" category entry), not their assay's data format.
            code_rows = [r for r in rs if is_code_model(r, rules)]
            data_rows = [r for r in rs if not is_code_model(r, rules)]
            if code_rows:
                prefs.update(lookup_format(formats, "", "model", level) or [])
            data_assays = canon([r["DatasetAssay"] for r in data_rows], _key)
            specific = [a for a in data_assays if _key(a) not in unspecified]
            for a in specific:
                got = lookup_format(formats, _key(a), cat, level)
                if got:
                    prefs.update(got)
                else:
                    missing.append(a)
            if data_rows and (not specific or any(_key(r["DatasetAssay"]) in unspecified for r in data_rows)):
                got = lookup_format(formats, "", cat, level)
                if got:
                    prefs.update(got)
                else:
                    missing.append(cat)
            preferred = SEP.join(sorted(prefs, key=str.casefold))
            if missing:
                flags.append("No canonical format for: " + ", ".join(missing))
            if prefs and level not in NO_FORMAT_CHECK_LEVELS and \
                    not ({cf(x) for x in declared} & {cf(x) for x in prefs}):
                flags.append(f"Declared formats don't include preferred format ({preferred})")
        # sensitivity
        tiers = rules["sensitivity_tiers"]
        # rows whose format conflicts with their category: format ignored for tier/repository
        conflicts: dict[str, list[str]] = {}
        effs = []
        for r in rs:
            msg = format_conflict(r, rules)
            if msg:
                conflicts.setdefault(msg, []).append(r["LandscapeId"] or "no id")
                effs.append({**r, "DatasetFileFormat": ""})
            else:
                effs.append(r)
        for msg, ids in conflicts.items():
            flags.append(f"Format conflict: {len(ids)} row(s) ({', '.join(ids)}): {msg}; "
                         "format ignored for sensitivity/repository")
        row_info, lowered = [], Counter()
        for e in effs:
            t, why = row_tier(e, rules)
            t2 = apply_level_cap(t, e, level, rules)
            if t2 != t:
                lowered[(t, t2)] += 1
                why = f"{why}; {t} lowered to {t2} by {level} cap"
            row_info.append((t2, why))
        top = max((t for t, _ in row_info), key=tiers.index)
        reasons: dict[str, Counter] = {}
        for t, why in row_info:
            reasons.setdefault(t, Counter())[why] += 1
        parts = [f"{t}: " + ", ".join(f"{n} row(s) {w}" for w, n in sorted(reasons[t].items()))
                 for t in sorted(reasons, key=tiers.index, reverse=True)]
        rationale = f"Max over rows = {top}. " + " | ".join(parts)
        n_unk = sum(1 for _, w in row_info if w.startswith("unknown species"))
        if n_unk:
            flags.append(f"{n_unk} row(s) with unknown species (treated as Registered)")
        # repository
        votes = Counter()      # all rows
        top_votes = Counter()  # rows at the category's tier
        first_idx: dict[str, int] = {}
        for r, (t, _) in zip(effs, row_info):
            idx, repo = row_repository(r, t, rules)
            votes[repo] += 1
            if t == top:
                top_votes[repo] += 1
            first_idx[repo] = min(first_idx.get(repo, idx), idx)
        ranked_top = sorted(top_votes, key=lambda x: (-top_votes[x], first_idx[x]))
        rec = ranked_top[0]
        others = sorted((x for x in votes if x != rec), key=lambda x: (-votes[x], first_idx[x]))
        ranked = [rec] + others
        notes = {x["repository"]: x["note"] for x in rules["repository_rules"]}
        for t in tiers:
            fb = fallback_for(t, rules)
            notes[fb["repository"]] = fb["note"]
        rr = "; ".join(f"{votes[x]} row(s) -> {x} ({notes.get(x, '')})" for x in ranked)
        tier_counts = Counter(t for t, _ in row_info)
        if len(tier_counts) > 1:
            mix = " / ".join(f"{tier_counts[t]} {t}" for t in sorted(tier_counts, key=tiers.index, reverse=True))
            flags.append(f"Mixed sensitivity: {mix} row(s); recommendation follows the {top} rows")
            rr = f"Recommendation follows the {top} rows ({top_votes[rec]} row(s) -> {rec}). All rows: " + rr
        if not any(rec.startswith(p) for p in rules["external_repository_prefixes_internal"]):
            rr += ". NAMHub also hosts metadata/landscape in Synapse."
        ni = needs_input(rs, effs, top, rec, rules)
        for (old, new), n in sorted(lowered.items(), key=lambda kv: tiers.index(kv[0][0])):
            ni.append(f"{n} row(s) lowered {old} -> {new} by {level} processing-level cap; "
                      "confirm processed data is not re-identifiable")
        if cf(cat) == "unknown":
            flags.append("Category 'Unknown': assign a real DatasetCategory")
        vol, extra = "", None
        if oldm is not None:
            extra = oldm.group_extras(rs, dt, level)
            flags += extra["flags"]
            ni += extra["needs_input"]
            vol = extra["volume"]
        drafts[(dt, level)] = {
            "DataType": dt, "ProcessingLevel": level, "Assays": SEP.join(assays),
            "FileFormats": SEP.join(declared), "PreferredFileFormat": preferred,
            "MetadataTypes": SEP.join(sorted(schemas, key=str.casefold)),
            "MetadataCandidates": SEP.join(candidates),
            "UnmappedAssays": SEP.join(unmapped), "ProjectedSensitivity": top,
            "SensitivityRationale": rationale, "RecommendedRepository": rec,
            "OtherRepositories": SEP.join(others), "RepositoryRationale": rr,
            "NeedsInput": SEP.join(ni),
            "Priority": "", "CategoryStudyCount": cat_studies[dt], "StudyCount": len(studies), "DatasetCount": len(datasets),
            "RowCount": len(rs), "EstimatedFileCount": fmt_num(total), "EarliestAvailableDate": earliest,
            "ExpectedDataVolume": vol, "_old": extra,
            "Species": SEP.join(canon([r["DatasetSpecies"] or "Unknown" for r in rs])),
            "_flags": flags, "_rows": rs,
        }
    prio, pflags = assign_priority(cat_studies, total_studies, rules, type_base)
    result = []
    for (dt, level), d in drafts.items():
        d["Priority"] = prio[dt]
        if dt in pflags:
            d["_flags"].append(pflags[dt])
        d["ReviewFlags"] = SEP.join(d["_flags"])
        result.append(d)
    result.sort(key=lambda d: (PRIORITY_ORDER[d["Priority"]], -d["CategoryStudyCount"], d["DataType"].casefold(),
                               level_sort_key(d["ProcessingLevel"], rules)))
    return result, total_studies


def write_compact(rows, out_dir: Path):
    """Write landscape_summary_compact.csv/.md from dict rows (must have COMPACT_COLUMNS keys)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "landscape_summary_compact.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COMPACT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    def cell(v):
        return " ".join(str(v if v is not None else "").split()).replace("|", "\\|")
    lines = ["| " + " | ".join(COMPACT_COLUMNS) + " |", "|" + "|".join(["---"] * len(COMPACT_COLUMNS)) + "|"]
    lines += ["| " + " | ".join(cell(r.get(c, "")) for c in COMPACT_COLUMNS) + " |" for r in rows]
    (out_dir / "landscape_summary_compact.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def compact_from_csv(path, out_dir: Path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in COMPACT_COLUMNS if c != "ExpectedDataVolume" and c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path} is missing columns: {', '.join(missing)}")
        rows = list(reader)
    write_compact(rows, out_dir)
    return len(rows)


def packet_record(r) -> dict:
    rec = {
        "SourceFile": r["_source"], "LandscapeId": r["LandscapeId"], "StudyName": r["StudyName"],
        "DatasetName": r["DatasetName"], "DatasetDescription": r["DatasetDescription"],
        "ContextOfUse": parse_list(r["ContextOfUse"]), "Comments": r["Comments"],
        "ApplicableStandards": r["ApplicableStandards"], "Species": r["DatasetSpecies"],
        "Assay": r["DatasetAssay"], "FileFormat": r["DatasetFileFormat"],
        "ProcessingLevel": r["DatasetProcessingLevel"],
    }
    if "_fills" in r:
        rec["OldManifestFills"] = r["_fills"]
    return rec


def write_outputs(result, total_studies, out_dir: Path, excluded=None, old=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "excluded_rows.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=EXCLUDED_COLUMNS)
        w.writeheader()
        w.writerows(excluded or [])
    with open(out_dir / "landscape_summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(result)
    write_compact(result, out_dir)
    packet = {
        "total_studies": total_studies,
        "data_types": [
            {"row": {c: d[c] for c in OUTPUT_COLUMNS}, "records": [packet_record(r) for r in d["_rows"]]}
            for d in result
        ],
    }
    if old is not None:
        for entry, d in zip(packet["data_types"], result):
            entry["old_manifest"] = {"fills": d["_old"]["fills"], "volume_bands": d["_old"]["volume_bands"]}
        old.write_reports(out_dir, result)
    (out_dir / "review_packet.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")


def _command_line(a) -> str:
    """The command with absolute paths replaced by placeholders (no local paths in the package)."""
    parts = ["python3", SCRIPT_REL, "<INPUT>", "--out-dir", "<OUT_DIR>"]
    if a.old_manifests:
        parts += ["--old-manifests", "<OLD_MANIFESTS>"]
    for flag, val, default in (("--mapping", a.mapping, DEFAULT_MAPPING), ("--rules", a.rules, DEFAULT_RULES),
                               ("--formats", a.formats, DEFAULT_FORMATS),
                               ("--format-level-rules", a.format_level_rules, DEFAULT_FORMAT_LEVEL_RULES)):
        if Path(val).resolve() != Path(default).resolve():
            parts += [flag, Path(val).name]
    return " ".join(parts)


def write_run_info(a, rules, mapping, rows, files, excluded, total, result, old, out_dir: Path):
    info = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input_file_count": len(files),
        "input_row_count": len(rows),
        "excluded_row_count": len(excluded),
        "study_count": total,
        "output_row_count": len(result),
        "old_manifests": {"used": old is not None,
                          "linked_row_count": sum(1 for r in rows if r.get("_link")) if old is not None else 0},
        "mapping": {"file": Path(a.mapping).name, "row_count": package_readme.mapping_row_count(a.mapping),
                    "versions": package_readme.mapping_versions(mapping)},
        "config_files": {"rules": Path(a.rules).name, "canonical_formats": Path(a.formats).name,
                         "format_level_rules": Path(a.format_level_rules).name},
        "enum_notes": package_readme.enum_notes(SKILL_DIR / "references"),
        "script": SCRIPT_REL,
        "command": _command_line(a),
        "rules_snapshot": package_readme.rules_snapshot(rules),
    }
    (out_dir / "run_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", metavar="INPUT", help="CSV files or directories of CSVs")
    ap.add_argument("--compact-from", metavar="REVIEWED_CSV",
                    help="only rewrite the compact csv/md in --out-dir from a reviewed summary CSV")
    ap.add_argument("--formats", default=str(DEFAULT_FORMATS))
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--mapping", default=str(DEFAULT_MAPPING))
    ap.add_argument("--rules", default=str(DEFAULT_RULES))
    ap.add_argument("--old-manifests", nargs="+", metavar="PATH",
                    help="old Sage manifest .html pages or extracted .csv files/directories (reconciliation evidence)")
    ap.add_argument("--format-level-rules", default=str(DEFAULT_FORMAT_LEVEL_RULES))
    ap.add_argument("--readme-only", action="store_true",
                    help="only rebuild README.md in --out-dir from run_info.json and the files present there")
    a = ap.parse_args(argv)
    if a.readme_only:
        out = Path(a.out_dir)
        if not (out / "run_info.json").is_file():
            ap.error(f"{out / 'run_info.json'} not found; --readme-only needs a folder written by a normal run")
        package_readme.write_readme(out, OUTPUT_COLUMNS, COMPACT_COLUMNS)
        print(f"README.md -> {a.out_dir}")
        return 0
    if a.compact_from:
        n = compact_from_csv(a.compact_from, Path(a.out_dir))
        print(f"{n} rows -> {a.out_dir}/landscape_summary_compact.csv/.md")
        return 0
    if not a.inputs:
        ap.error("INPUT is required (or use --compact-from / --readme-only)")
    rules = json.loads(Path(a.rules).read_text(encoding="utf-8"))
    mapping = load_mapping(a.mapping)
    rows = load_rows(find_csvs(a.inputs))
    excluded: list[dict] = []
    old = None
    if a.old_manifests:
        old = old_manifest.build(a.old_manifests, rules, a.format_level_rules, DEFAULT_ENUMS, DEFAULT_FORMAT_ENUM,
                                 file_estimate)
        rows = old.apply_fills(rows)
    result, total = summarize(rows, mapping, rules, excluded, load_formats(a.formats), old)
    out_dir = Path(a.out_dir)
    write_outputs(result, total, out_dir, excluded, old)
    write_run_info(a, rules, mapping, rows, find_csvs(a.inputs), excluded, total, result, old, out_dir)
    package_readme.write_readme(out_dir, OUTPUT_COLUMNS, COMPACT_COLUMNS)
    print(f"{len(rows)} rows, {len(excluded)} excluded, {total} studies, {len(result)} data type/level rows -> {a.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
