#!/usr/bin/env python3
"""Extract the Sage manifest table from old per-center HTML pages into CSV.

Usage:
  extract_manifests.py INPUT [INPUT ...] --out-dir DIR

INPUT is an .html file or a directory of them (index.html is ignored). The manifest is the <table> whose
header row contains `gardener_source_entry`; one `<stem>_manifest.csv` is written per page with the exact
MANIFEST_COLUMNS header. Stdlib only. The HTML is treated as untrusted data: it is parsed with
html.parser, never executed, and nothing is fetched.

Importable: load_old_manifests(paths) -> list of dicts (MANIFEST_COLUMNS + "Center"), from .html or
extracted .csv files / directories.
"""
from __future__ import annotations

import argparse
import csv
import sys
from html.parser import HTMLParser
from pathlib import Path

MANIFEST_COLUMNS = [
    "LandscapeId", "Comments", "StudyAim", "StudyName", "DatasetName", "ContextOfUse", "DatasetAssay",
    "UploadByDate", "DatasetSpecies", "DatasetCategory", "DatasetDescription", "DatasetFileFormats",
    "ApplicableStandards", "DataContributionLead", "ExpectedNumberOfFiles", "DatasetProcessingLevel",
    "ExpectedNumberOfSamples", "gardener_source_entry", "gardener_processing_level", "gardener_level_source",
    "gardener_level_gap", "gardener_expected_files", "gardener_files_basis", "gardener_files_gap",
    "gardener_expected_data_volume", "gardener_volume_basis", "gardener_volume_gap",
]
ANCHOR = "gardener_source_entry"


class _TableParser(HTMLParser):
    """Collects every <table> as a list of rows of cell texts (nested tables handled with a stack)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[dict] = []
        self._skip = 0

    def _close_cell(self):
        t = self._stack[-1]
        if t["cell"] is not None:
            if t["row"] is not None:
                t["row"].append(" ".join("".join(t["cell"]).split()))
            t["cell"] = None

    def _close_row(self):
        self._close_cell()
        t = self._stack[-1]
        if t["row"] is not None:
            t["rows"].append(t["row"])
            t["row"] = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None})
        elif not self._stack:
            return
        elif tag == "tr":
            self._close_row()
            self._stack[-1]["row"] = []
        elif tag in ("td", "th"):
            self._close_cell()
            t = self._stack[-1]
            if t["row"] is None:
                t["row"] = []
            t["cell"] = []
        elif tag in ("br", "p", "li", "div") and self._stack[-1]["cell"] is not None:
            self._stack[-1]["cell"].append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "table" and self._stack:
            self._close_row()
            self.tables.append(self._stack.pop()["rows"])
        elif self._stack and tag == "tr":
            self._close_row()
        elif self._stack and tag in ("td", "th"):
            self._close_cell()

    def handle_data(self, data):
        if self._skip or not self._stack:
            return
        if self._stack[-1]["cell"] is not None:
            self._stack[-1]["cell"].append(data)

    def close(self):
        super().close()
        while self._stack:
            self._close_row()
            self.tables.append(self._stack.pop()["rows"])


def parse_manifest_html(text: str) -> list[dict]:
    """Rows (dicts keyed by MANIFEST_COLUMNS) of the manifest table; [] if the page has none."""
    p = _TableParser()
    p.feed(text)
    p.close()
    for rows in p.tables:
        for i, row in enumerate(rows):
            if ANCHOR in row:
                header = row
                out = []
                for r in rows[i + 1:]:
                    if not any(c.strip() for c in r):
                        continue
                    d = {h: (r[j] if j < len(r) else "") for j, h in enumerate(header)}
                    out.append({c: d.get(c, "") for c in MANIFEST_COLUMNS})
                return out
    return []


def center_of(path: Path) -> str:
    stem = path.stem.upper()
    return stem[: -len("_MANIFEST")] if stem.endswith("_MANIFEST") else stem


def _expand(paths) -> list[Path]:
    files: list[Path] = []
    for i in paths:
        p = Path(i)
        if p.is_dir():
            html = [f for f in sorted(p.glob("*.html")) if f.stem.lower() != "index"]
            stems = {f.stem.upper() for f in html}
            files.extend(html)
            files.extend(f for f in sorted(p.glob("*.csv")) if center_of(f) not in stems)
        elif p.is_file():
            files.append(p)
        else:
            raise SystemExit(f"Input not found: {i}")
    return files


def load_old_manifests(paths) -> list[dict]:
    """Rows from .html pages or extracted .csv files (files or directories); each dict has the
    MANIFEST_COLUMNS plus "Center" (file stem upper-cased, minus a trailing _MANIFEST)."""
    rows: list[dict] = []
    for f in _expand(paths):
        if f.suffix.lower() in (".html", ".htm"):
            parsed = parse_manifest_html(f.read_text(encoding="utf-8", errors="replace"))
        elif f.suffix.lower() == ".csv":
            with open(f, newline="", encoding="utf-8-sig") as fh:
                parsed = [{c: (r.get(c) or "").strip() for c in MANIFEST_COLUMNS} for r in csv.DictReader(fh)]
        else:
            continue
        for r in parsed:
            r["Center"] = center_of(f)
            rows.append(r)
    return rows


def write_manifest_csv(rows, path: Path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", metavar="INPUT", help=".html files or directories")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in _expand(a.inputs):
        if f.suffix.lower() not in (".html", ".htm"):
            continue
        rows = parse_manifest_html(f.read_text(encoding="utf-8", errors="replace"))
        if not rows:
            print(f"warning: no manifest table in {f.name}", file=sys.stderr)
            continue
        write_manifest_csv(rows, out / f"{f.stem}_manifest.csv")
        print(f"{f.name}: {len(rows)} rows")
        n += 1
    if not n:
        raise SystemExit("No manifest tables extracted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
