"""Old-manifest reconciliation for summarize_landscape.py (--old-manifests).

The revised Landscape rows stay authoritative; the older per-center Sage manifest pages are evidence:
linking, gap fills, data-volume bands, row-level plausibility checks, proposed corrections (never applied)
and description-loss detection. See references/rubric.md. Stdlib only.
"""
from __future__ import annotations

import csv
import difflib
import re
from collections import Counter, OrderedDict
from pathlib import Path

from extract_manifests import load_old_manifests

SEP = "; "

LINK_COLUMNS = ["LandscapeId", "StudyName", "LinkBasis", "SourceEntry", "MatchedItemId", "OldFormats"]
ISSUE_COLUMNS = ["LandscapeId", "StudyName", "DataType", "ProcessingLevel", "Assay", "Format", "Issue", "Origin",
                 "OldStatedLevel", "OldGroundedLevel", "OldFormats"]
CORRECTION_COLUMNS = ["LandscapeId", "StudyName", "DatasetName", "Assay", "Field", "CurrentValue", "ProposedValue",
                      "Evidence", "Source", "Confidence", "Rationale"]
LOSS_COLUMNS = ["LandscapeId", "StudyName", "OldDescription", "OldComments", "NewDescription", "Similarity",
                "MissingTerms", "CountConflicts"]

ACCESSION_RX = re.compile(
    r"\b(GSE\d+|SRP\d+|SRR\d+|SRX\d+|PRJNA\d+|phs\d+(?:\.v\d+)?|PXD\d+|MTBLS\d+|EMPIAR-\d+|S-BIAD\d+|"
    r"(?i:PMID):?\s*\d+|SRA)\b")
GENE_RX = re.compile(r"\b[A-Z][A-Z0-9-]{2,}\b")
RSID_RX = re.compile(r"\brs\d+\b", re.I)
NUM_RX = re.compile(r"(?<![\w.])~?(\d[\d,]*)(?!\d)\s+((?:[A-Za-z][\w-]*\s*){1,3})")
N_EQ_RX = re.compile(r"\bn\s*=\s*(\d+)", re.I)
YEARS_RX = re.compile(r"\bYears?\s+(\d+(?:\s*-\s*\d+)?)", re.I)


def _key(s) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def cf(s) -> str:
    return " ".join((s or "").split()).casefold()


def clean(s) -> str:
    return " ".join((s or "").split())


def to_num(s):
    try:
        v = float(str(s).strip().replace(",", ""))
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def fmt_num(v: float) -> str:
    return str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


def accessions(text: str) -> list[str]:
    seen, out = set(), []
    for m in ACCESSION_RX.finditer(text or ""):
        a = re.sub(r"(?i)^pmid:?\s*", "PMID:", m.group(1))
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


class Normalizer:
    """Old spellings -> enum spellings via the alphanumeric-lowercase key; unmatched values pass through."""

    def __init__(self, enums: dict, format_enum: list, levels: list):
        self.maps = {
            "assay": {_key(v): v for v in enums.get("DatasetAssay", [])},
            "species": {_key(v): v for v in enums.get("DatasetSpecies", [])},
            "category": {_key(v): v for v in enums.get("DatasetCategory", [])},
            "format": {_key(v): v for v in format_enum},
            "level": {_key(v): v for v in levels},
        }

    def _n(self, kind, v):
        v = clean(v)
        return self.maps[kind].get(_key(v), v) if v else ""

    def assay(self, v): return self._n("assay", v)
    def species(self, v): return self._n("species", v)
    def category(self, v): return self._n("category", v)
    def fmt(self, v): return self._n("format", v)
    def level(self, v): return self._n("level", v)
    def known_format(self, v): return _key(v) in self.maps["format"]


# ---------------------------------------------------------------- itemization
def _label_patterns(labels: dict):
    pats = []
    for lab, assay in sorted(labels.items(), key=lambda kv: -len(kv[0])):
        toks = [t for t in re.split(r"[^a-z0-9]+", lab.lower()) if t]
        if toks:
            pats.append((re.compile(r"(?<![a-z0-9])" + r"[\s\-_]*".join(map(re.escape, toks)) + r"\s*$"), assay))
    return pats


def parse_itemization(text: str, patterns, norm: Normalizer) -> list[dict]:
    """Segments '<assay label> (<count> <format>, ...)' -> [{assay, formats: OrderedDict, truncated, dup, quote}].

    Tolerates a truncated last segment (no closing paren; an unrecognised last item is dropped). Duplicate
    formats within a segment are summed and recorded in `dup`."""
    out = []
    for m in re.finditer(r"\(([^()]*)(\)|$)", text or ""):
        truncated = m.group(2) == ""
        before = text[max(0, m.start() - 80):m.start()].lower()
        assay = next((a for rx, a in patterns if rx.search(before)), None)
        if not assay:
            continue
        parts = [p for p in re.split(r",\s+|;\s*|\s+and\s+", m.group(1)) if p.strip()]
        formats: OrderedDict = OrderedDict()
        dup = []
        for i, part in enumerate(parts):
            pm = re.match(r"\s*~?(\d[\d,]*)\s+(.+?)\s*$", part)
            if not pm:
                continue
            raw = re.sub(r"(?i)\s+files?$", "", pm.group(2))
            if truncated and i == len(parts) - 1 and not norm.known_format(raw):
                continue
            fmt = norm.fmt(raw) if norm.known_format(raw) else raw.lower()
            n = int(pm.group(1).replace(",", ""))
            if fmt in formats:
                dup.append(fmt)
            formats[fmt] = formats.get(fmt, 0) + n
        if formats:
            q = text[max(0, m.start() - 60):m.end()]
            out.append({"assay": assay, "formats": formats, "truncated": truncated, "dup": dup,
                        "quote": clean(q)[:200]})
    return out


# ---------------------------------------------------------------- description tokens
def cohort_terms(text: str, nouns: dict) -> list[tuple[str, str, str]]:
    """[(display, number, noun class)] for numbers attached to cohort words ('30 patients', 'n=24', 'Years 1-3').
    noun class is '' for n= and Years terms."""
    cls = {w: c for c, ws in nouns.items() for w in ws}
    out = []
    for m in NUM_RX.finditer(text or ""):
        toks = re.findall(r"[A-Za-z][\w-]*", m.group(2))[:3]
        idx = next((i for i, t in enumerate(toks) if t.lower() in cls), None)
        if idx is None:
            continue
        while idx + 1 < len(toks) and toks[idx + 1].lower() in cls:
            idx += 1
        num = m.group(1).replace(",", "")
        out.append((f"{num} {toks[idx].lower()}", num, cls[toks[idx].lower()]))
    for m in N_EQ_RX.finditer(text or ""):
        out.append((f"n={m.group(1)}", m.group(1), ""))
    for m in YEARS_RX.finditer(text or ""):
        rng = re.sub(r"\s+", "", m.group(1))
        out.append((f"Years {rng}", rng, "years"))
    return out


class OldManifest:
    def __init__(self, rows: list[dict], rules: dict, flr: dict, enums: dict, format_enum: list, file_estimate):
        self.rules, self.flr, self.file_estimate = rules, flr, file_estimate
        self.norm = Normalizer(enums, format_enum, rules["processing_level_order"])
        self.fill_cfg = {"level": True, "file_count": True, **rules.get("old_manifest_fill", {})}
        self.band_order = [b for b in rules.get("volume_band_order", [])]
        self.bands = {cf(b): i for i, b in enumerate(self.band_order)}
        self.family_assays = {k: {_key(a) for a in flr.get(k, [])}
                              for k in ("sequencing_assays", "imaging_assays", "mass_spec_assays")}
        self.implied = {_key(k): v for k, v in flr.get("format_implied_level_for_sequencing", {}).items()}
        self.l1_ok = {_key(x) for x in flr.get("level1_allowed_formats_for_sequencing", [])}
        self.patterns = _label_patterns(flr.get("itemization_assay_labels", {}))
        self.stop = {x.upper() for x in flr.get("description_stoplist", [])} | {_key(f).upper() for f in format_enum}
        self.nouns = flr.get("cohort_nouns", {})
        self.items = [self._item(r) for r in rows]
        self.entries: dict[str, list[dict]] = {}
        for it in self.items:
            self.entries.setdefault(it["Entry"].lower(), []).append(it)
        self._itemz: dict[str, dict] = {}

    # ------------------------------------------------------------ items
    def _item(self, r: dict) -> dict:
        n = self.norm
        fmt = clean(r.get("DatasetFileFormats"))
        glevel = n.level(r.get("gardener_processing_level"))
        return {
            "Center": clean(r.get("Center")), "Id": clean(r.get("LandscapeId")),
            "Entry": clean(r.get("gardener_source_entry")) or clean(r.get("LandscapeId")),
            "Study": clean(r.get("StudyName")), "Name": clean(r.get("DatasetName")),
            "Format": n.fmt(fmt), "FormatKey": _key(n.fmt(fmt)),
            "Assay": n.assay(r.get("DatasetAssay")), "Species": n.species(r.get("DatasetSpecies")),
            "Category": n.category(r.get("DatasetCategory")),
            "StatedLevel": n.level(r.get("DatasetProcessingLevel")), "GLevel": glevel,
            "LevelSource": clean(r.get("gardener_level_source")),
            "Files": to_num(r.get("ExpectedNumberOfFiles")), "GFiles": to_num(r.get("gardener_expected_files")),
            "FilesBasis": clean(r.get("gardener_files_basis")),
            "Volume": clean(r.get("gardener_expected_data_volume")),
            "Description": clean(r.get("DatasetDescription")), "Comments": clean(r.get("Comments")),
        }

    # ------------------------------------------------------------ linking
    def link(self, row: dict) -> dict | None:
        rid = clean(row["LandscapeId"]).lower()
        best = None
        for e in self.entries:
            if rid == e or rid.startswith(e + "-"):
                if best is None or len(e) > len(best):
                    best = e
        if best:
            return self._mk_link(row, best, "id-prefix")
        nk = _key(row["DatasetName"])
        if not nk:
            return None
        cands = {}
        for e, its in self.entries.items():
            if any(_key(i["Name"]) == nk for i in its):
                cands[e] = its
        if len(cands) > 1:
            sk = _key(row["StudyName"])
            narrowed = {e: its for e, its in cands.items()
                        if any(_key(i["Study"]) == sk for i in its) or
                        any(rid.startswith(i["Center"].lower() + "-") for i in its)}
            cands = narrowed
        if len(cands) == 1:
            return self._mk_link(row, next(iter(cands)), "dataset-name")
        return None

    def _mk_link(self, row, entry_key, basis) -> dict:
        its = self.entries[entry_key]
        fk = _key(self.norm.fmt(row["DatasetFileFormat"]))
        cand = [i for i in its if fk and i["FormatKey"] == fk]
        ak = _key(self.norm.assay(row["DatasetAssay"]))
        matched = next((i for i in cand if _key(i["Assay"]) == ak), cand[0] if cand else None)
        formats = []
        for i in its:
            if i["Format"] and i["Format"] not in formats:
                formats.append(i["Format"])
        return {"basis": basis, "entry": its[0]["Entry"], "key": entry_key, "items": its, "matched": matched,
                "same_format": cand, "old_formats": SEP.join(formats)}

    def link_rows(self, rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            r = dict(r)
            r["_link"] = self.link(r)
            r["_fills"] = []
            out.append(r)
        return out

    @staticmethod
    def _agree(values):
        vals = {v for v in values if v}
        return next(iter(vals)) if len(vals) == 1 else None

    # ------------------------------------------------------------ fills
    def apply_fills(self, rows: list[dict]) -> list[dict]:
        """Link rows, then fill blank level / unusable file estimate from the old manifest (never overwrite)."""
        rows = self.link_rows(rows)
        for r in rows:
            lk = r["_link"]
            if not lk:
                continue
            m = lk["matched"]
            lv = clean(r["DatasetProcessingLevel"])
            if self.fill_cfg["level"] and (not lv or cf(lv) == "unspecified"):
                val = src = None
                if m and m["StatedLevel"]:
                    val, src = m["StatedLevel"], "old stated"
                elif m and m["GLevel"]:
                    val, src = m["GLevel"], f"gardener:{m['LevelSource'] or 'unknown'}"
                else:
                    agreed = self._agree(i["StatedLevel"] for i in lk["items"])
                    if agreed:
                        val, src = agreed, "old stated"
                if val:
                    r["DatasetProcessingLevel"] = val
                    r["_fills"].append({"LandscapeId": r["LandscapeId"], "Field": "DatasetProcessingLevel",
                                        "Value": val, "Source": src, "OldItem": m["Id"] if m else ""})
            if self.fill_cfg["file_count"] and m and self.file_estimate(r) is None:
                val = src = None
                if m["Files"] is not None:
                    val, src = m["Files"], "old stated"
                elif m["GFiles"] is not None:
                    val, src = m["GFiles"], f"gardener:{m['FilesBasis'] or 'unknown'}"
                if val is not None:
                    r["FileRatio"], r["FileRatioUnit"] = fmt_num(val), "Total files"
                    r["_fills"].append({"LandscapeId": r["LandscapeId"], "Field": "FileCount",
                                        "Value": fmt_num(val), "Source": src, "OldItem": m["Id"]})
        return rows

    # ------------------------------------------------------------ volume
    def row_volume(self, row) -> str:
        lk = row.get("_link")
        if not lk:
            return ""

        def ok(v):
            return cf(v) in self.bands
        m = lk["matched"]
        if m and ok(m["Volume"]):
            return cf(m["Volume"])
        return self._agree(cf(i["Volume"]) for i in lk["items"] if ok(i["Volume"])) or ""

    # ------------------------------------------------------------ row issues
    def family(self, assay_key: str) -> str | None:
        for fam, label in (("sequencing_assays", "sequencing"), ("imaging_assays", "imaging"),
                           ("mass_spec_assays", "mass spectrometry")):
            if assay_key in self.family_assays[fam]:
                return label
        return None

    def issues_for(self, assay: str, fmt: str, level: str) -> list[str]:
        ak, fk = _key(assay), _key(fmt)
        out = []
        if not fk:
            return out
        if self.family(ak) == "sequencing":
            if level == "Level 1" and fk not in self.l1_ok:
                out.append("sequencing Level 1 with non-raw format")
            implied = self.implied.get(fk)
            if implied and level and cf(level) != "unspecified" and implied != level:
                out.append(f"format implies {implied} but stated {level}")
        for rule in self.flr.get("disallowed_format_families", []):
            if ak in self.family_assays.get(rule["assay_family"], set()) and \
                    fk in {_key(f) for f in rule["formats"]}:
                out.append(f"{rule['format_family']} format on {rule['assay_label']} assay")
        return out

    def row_issues(self, row, dt: str, level: str) -> list[dict]:
        assay = self.norm.assay(row["DatasetAssay"])
        fmt = self.norm.fmt(row["DatasetFileFormat"])
        lvl = self.norm.level(level) if level else ""
        lk = row.get("_link")
        res = []
        for issue in self.issues_for(assay, fmt, lvl):
            if not lk:
                origin = "no old entry"
            else:
                origin = "introduced in revision"
                for it in lk["same_format"]:
                    if issue in self.issues_for(it["Assay"] or assay, fmt, it["StatedLevel"]):
                        origin = "inherited from old entry"
                        break
            m = (lk["matched"] or (lk["same_format"][0] if lk["same_format"] else None)) if lk else None
            res.append({"LandscapeId": row["LandscapeId"], "StudyName": row["StudyName"], "DataType": dt,
                        "ProcessingLevel": level, "Assay": assay, "Format": fmt, "Issue": issue, "Origin": origin,
                        "OldStatedLevel": m["StatedLevel"] if m else "", "OldGroundedLevel": m["GLevel"] if m else "",
                        "OldFormats": lk["old_formats"] if lk else ""})
        return res

    # ------------------------------------------------------------ itemization per entry
    def itemization(self, link: dict) -> dict:
        """assay key -> {assay, formats: OrderedDict, notes, quote, source, item}; Comments before Description."""
        k = link["key"]
        if k in self._itemz:
            return self._itemz[k]
        res: dict[str, dict] = {}
        seen = set()
        for field in ("Comments", "Description"):
            for it in link["items"]:
                if not it[field] or (field, it[field]) in seen:
                    continue
                seen.add((field, it[field]))
                for seg in parse_itemization(it[field], self.patterns, self.norm):
                    ak = _key(seg["assay"])
                    rec = res.setdefault(ak, {"assay": seg["assay"], "formats": OrderedDict(), "notes": [],
                                              "quote": seg["quote"], "source": field, "item": it})
                    for f, n in seg["formats"].items():
                        rec["formats"].setdefault(f, n)
                    for d in seg["dup"]:
                        rec["notes"].append(f"duplicate {d} entries summed")
                    if seg["truncated"]:
                        rec["notes"].append("itemization truncated in source")
        self._itemz[k] = res
        return res

    # ------------------------------------------------------------ description loss
    def _terms(self, text: str) -> dict:
        acc = accessions(text)
        accset = {a.upper() for a in acc}
        genes = []
        for g in GENE_RX.findall(text or ""):
            g = g.rstrip("-")
            if len(g) < 3 or g in self.stop or g.upper() in accset or re.fullmatch(r"(?:GSE|SRP|SRR|SRX|PRJNA|PXD|MTBLS)\d+|PHS\d+", g, re.I) \
                    or g in genes:
                continue
            genes.append(g)
        return {"rs": list(dict.fromkeys(m.lower() for m in RSID_RX.findall(text or ""))), "acc": acc,
                "genes": genes, "cohort": cohort_terms(text, self.nouns)}

    @staticmethod
    def _present(tok: str, hay: str) -> bool:
        return re.search(r"(?<![a-z0-9])" + re.escape(tok.lower()) + r"(?![a-z0-9])", hay.lower()) is not None

    def description_loss(self, row) -> dict | None:
        lk = row.get("_link")
        if not lk:
            return None
        if "_dl" in row:
            return row["_dl"]
        its = lk["items"]
        m = lk["matched"] or max(its, key=lambda i: len(i["Description"]))
        old_desc = m["Description"] or max((i["Description"] for i in its), key=len, default="")
        old_com = m["Comments"] or max((i["Comments"] for i in its), key=len, default="")
        new_desc, new_com = clean(row["DatasetDescription"]), clean(row["Comments"])
        old_all, new_all = f"{old_desc} {old_com}", f"{new_desc} {new_com}"
        ot, nt = self._terms(old_all), self._terms(new_all)
        missing = []
        new_acc = {x.upper() for x in nt["acc"]}
        for t in ot["rs"] + ot["acc"] + ot["genes"]:
            if (t.upper() not in new_acc) if t in ot["acc"] else not self._present(t, new_all):
                missing.append(t)
        new_nums = {n for _, n, c in nt["cohort"] if c != "years"}
        new_years = {n for _, n, c in nt["cohort"] if c == "years"}
        for disp, num, cls in ot["cohort"]:
            absent = num not in (new_years if cls == "years" else new_nums)
            if absent and disp not in missing:
                missing.append(disp)
        conflicts = []
        for cls in ("person", "samples", "lines", "animals"):
            o = {n for _, n, c in ot["cohort"] if c == cls}
            n_ = {n for _, n, c in nt["cohort"] if c == cls}
            if o and n_ and not (o & n_):
                conflicts.append(f"{cls}: old {'/'.join(sorted(o, key=int))} vs new {'/'.join(sorted(n_, key=int))}")
        sim = round(difflib.SequenceMatcher(None, old_desc, new_desc).ratio(), 2) if (old_desc or new_desc) else 1.0
        res = {"LandscapeId": row["LandscapeId"], "StudyName": row["StudyName"], "OldDescription": old_desc,
               "OldComments": old_com, "NewDescription": new_desc, "Similarity": f"{sim:.2f}",
               "MissingTerms": SEP.join(missing), "CountConflicts": SEP.join(conflicts)}
        row["_dl"] = res if (missing or conflicts) else None
        return row["_dl"]

    # ------------------------------------------------------------ per-summary-row extras
    def group_extras(self, rs: list[dict], dt: str, level: str) -> dict:
        flags, ni = [], []
        filled = [r for r in rs if r.get("_fills")]
        if filled:
            nl = sum(1 for r in filled if any(f["Field"] == "DatasetProcessingLevel" for f in r["_fills"]))
            nf = sum(1 for r in filled if any(f["Field"] == "FileCount" for f in r["_fills"]))
            ni.append(f"{len(filled)} row(s) used old-manifest fills (level: {nl}, file count: {nf}); confirm")
            inferred = sum(1 for r in rs for f in r["_fills"] if f["Field"] == "DatasetProcessingLevel"
                           and f["Source"] in ("gardener:carried_from_audit", "gardener:format_dictionary"))
            if inferred:
                ni.append(f"{inferred} level fill(s) are audit/format-inferred, not center-stated")
        deps = []
        for r in rs:
            lk = r.get("_link")
            if lk:
                text = " ".join(f"{i['Description']} {i['Comments']}" for i in lk["items"])
                for a in accessions(text):
                    if a not in deps:
                        deps.append(a)
        if deps:
            ni.append(f"already deposited: {', '.join(deps)}; consider linking instead of re-uploading")
        issues = [i for r in rs for i in self.row_issues(r, dt, level)]
        if issues:
            flags.append(f"{len(issues)} row issue(s): see row_issues.csv")
        nloss = sum(1 for r in rs if self.description_loss(r))
        if nloss:
            flags.append(f"{nloss} row(s) lost description content: see description_loss.csv")
        bands = Counter(b for b in (self.row_volume(r) for r in rs) if b)
        top = max(bands, key=lambda b: self.bands[b]) if bands else ""
        vol = self.band_order[self.bands[top]] if top else ""
        return {"flags": flags, "needs_input": ni, "volume": vol, "row_issues": issues,
                "volume_bands": {self.band_order[self.bands[b]]: n for b, n in sorted(bands.items(), key=lambda kv: self.bands[kv[0]])},
                "fills": [f for r in rs for f in r["_fills"]]}

    # ------------------------------------------------------------ corrections
    def corrections(self, rows: list[dict]) -> list[dict]:
        out = []
        seen_missing = set()

        def add(r, field, cur, prop, quote, source, conf, why, assay=None):
            out.append({"LandscapeId": r["LandscapeId"], "StudyName": r["StudyName"], "DatasetName": r["DatasetName"],
                        "Assay": assay if assay is not None else self.norm.assay(r["DatasetAssay"]),
                        "Field": field, "CurrentValue": cur, "ProposedValue": prop, "Evidence": clean(quote)[:200],
                        "Source": source, "Confidence": conf, "Rationale": why})

        for r in rows:
            for f in r.get("_fills", []):
                src = f["Source"]
                inferred = src in ("gardener:carried_from_audit", "gardener:format_dictionary") or \
                    (f["Field"] == "FileCount" and src != "old stated")
                conf = "high" if src == "old stated" else ("low" if inferred else "medium")
                add(r, f["Field"], "", f["Value"], f"old item {f['OldItem']}: {f['Field']} = {f['Value']}",
                    f"old-manifest fill ({src})", conf, "Blank/unusable in the revised row; filled from the old manifest")
            lk = r.get("_link")
            if not lk:
                continue
            assay = self.norm.assay(r["DatasetAssay"])
            ak, fk = _key(assay), _key(self.norm.fmt(r["DatasetFileFormat"]))
            level = self.norm.level(r["DatasetProcessingLevel"]) if clean(r["DatasetProcessingLevel"]) else ""
            itz = self.itemization(lk)
            rec = itz.get(ak)
            scope = [g for g in rows if g.get("_link") and g["_link"]["key"] == lk["key"]
                     and _key(self.norm.assay(g["DatasetAssay"])) == ak]
            where = f"old manifest {lk['items'][0]['Center']} {lk['entry']}"
            if rec:
                src = f"{where} {rec['source']}"
                fmts = {_key(f): (f, n) for f, n in rec["formats"].items()}
                note = ("; " + "; ".join(dict.fromkeys(rec["notes"]))) if rec["notes"] else ""
                if fk in fmts:
                    f, n = fmts[fk]
                    est = self.file_estimate(r)
                    if est is None or abs(est - n) > 1e-9:
                        add(r, "FileCount", fmt_num(est) if est is not None else "", str(n), rec["quote"], src,
                            "high", f"old itemization for {rec['assay']} lists {n} {f}{note}")
                    implied = self.implied.get(fk)
                    if self.family(ak) == "sequencing" and implied and implied != level:
                        add(r, "DatasetProcessingLevel", level, implied, rec["quote"], src, "high",
                            f"format {f} implies {implied}")
                else:
                    scope_have = {_key(self.norm.fmt(g["DatasetFileFormat"])) for g in scope}
                    if clean(r.get("DerivedFrom")):
                        pass  # derived products are legitimately absent from raw itemizations
                    elif not any(_key(f) in scope_have for f in rec["formats"]):
                        add(r, "DatasetFileFormat", self.norm.fmt(r["DatasetFileFormat"]), SEP.join(rec["formats"]),
                            rec["quote"], src, "medium",
                            f"old itemization for {rec['assay']} lists {', '.join(rec['formats'])}, "
                            f"not {self.norm.fmt(r['DatasetFileFormat'])}")
                    else:
                        add(r, "DatasetFileFormat", self.norm.fmt(r["DatasetFileFormat"]),
                            f"review: not in old itemization (old lists {SEP.join(rec['formats'])})",
                            rec["quote"], src, "low",
                            f"old itemization for {rec['assay']} lists {', '.join(rec['formats'])}, "
                            f"not {self.norm.fmt(r['DatasetFileFormat'])}; other itemized formats are present, "
                            "so this may be an added format")
                targets = sorted((g for g in scope if not clean(g.get("DerivedFrom"))),
                                 key=lambda g: g["LandscapeId"])
                scope_have = {_key(self.norm.fmt(g["DatasetFileFormat"])) for g in scope}
                for f, n in rec["formats"].items():
                    gk = (lk["key"], ak, _key(f))
                    if _key(f) not in scope_have and gk not in seen_missing and targets and \
                            targets[0] is r:
                        seen_missing.add(gk)
                        implied = self.implied.get(_key(f)) if self.family(ak) == "sequencing" else None
                        prop = f"{f} ({n} files, implied {implied})" if implied else f"{f} ({n} files)"
                        add(r, "MissingRow", "", prop, rec["quote"], src, "medium",
                            f"old itemization for {rec['assay']} lists {f}; no linked revised row with this assay has it")
            else:
                m = lk["matched"]
                if m and level and m["GLevel"] and m["GLevel"] != level and \
                        m["LevelSource"] in ("format_dictionary", "entry_stated"):
                    conf = "low" if m["LevelSource"] == "format_dictionary" else "medium"
                    add(r, "DatasetProcessingLevel", level, m["GLevel"],
                        f"old item {m['Id']}: gardener_processing_level = {m['GLevel']} ({m['LevelSource']})",
                        f"{where} gardener", conf,
                        f"revised level {level} differs from old grounded level ({m['LevelSource']})")
            text_by_field = [(f, " ".join(dict.fromkeys(i[f] for i in lk["items"] if i[f])))
                             for f in ("Comments", "Description")]
            accs = []
            quote = ""
            for fld, txt in text_by_field:
                for m_ in ACCESSION_RX.finditer(txt):
                    a = re.sub(r"(?i)^pmid:?\s*", "PMID:", m_.group(1))
                    if a not in accs:
                        accs.append(a)
                        quote = quote or txt[max(0, m_.start() - 80):m_.end() + 40]
            if accs:
                add(r, "ExistingDeposit", "", SEP.join(accs), quote, f"{where} text", "medium",
                    "old text references public accession(s); consider linking instead of re-uploading")
        return out

    # ------------------------------------------------------------ reports
    def link_rows_report(self, rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            lk = r.get("_link")
            out.append({"LandscapeId": r["LandscapeId"], "StudyName": r["StudyName"],
                        "LinkBasis": lk["basis"] if lk else "none", "SourceEntry": lk["entry"] if lk else "",
                        "MatchedItemId": lk["matched"]["Id"] if lk and lk["matched"] else "",
                        "OldFormats": lk["old_formats"] if lk else ""})
        return out

    def write_reports(self, out_dir: Path, result: list[dict]):
        rows = [r for d in result for r in d["_rows"]]
        issues = [i for d in result for i in d["_old"]["row_issues"]]
        loss = [x for x in (self.description_loss(r) for r in rows) if x]

        def w(name, cols, data):
            with open(out_dir / name, "w", newline="", encoding="utf-8") as fh:
                wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                wr.writeheader()
                wr.writerows(data)
        w("old_manifest_links.csv", LINK_COLUMNS, self.link_rows_report(rows))
        w("row_issues.csv", ISSUE_COLUMNS, issues)
        w("proposed_corrections.csv", CORRECTION_COLUMNS, self.corrections(rows))
        w("description_loss.csv", LOSS_COLUMNS, loss)


def build(paths, rules, flr_path, enums_path, formats_path, file_estimate) -> OldManifest:
    import json
    rows = load_old_manifests(paths)
    if not rows:
        raise SystemExit("No old-manifest rows found")
    flr = json.loads(Path(flr_path).read_text(encoding="utf-8"))
    enums = json.loads(Path(enums_path).read_text(encoding="utf-8"))
    fmts = json.loads(Path(formats_path).read_text(encoding="utf-8"))["enum"]
    return OldManifest(rows, rules, flr, enums, fmts, file_estimate)
