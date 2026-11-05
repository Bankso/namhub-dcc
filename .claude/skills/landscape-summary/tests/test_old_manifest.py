"""Tests for --old-manifests reconciliation. All fixtures are synthetic (invented ids and text)."""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))
import extract_manifests as em  # noqa: E402
import old_manifest as om  # noqa: E402
import summarize_landscape as sl  # noqa: E402

OLD = HERE / "old_fixtures"
MAPPING = HERE / "fixture_mapping.csv"
RULES = json.loads((SKILL / "references" / "rules.json").read_text())
FLR = json.loads((SKILL / "references" / "format_level_rules.json").read_text())
ENUMS = json.loads((SKILL / "references" / "landscape_enums.json").read_text())
FMT_ENUM = json.loads((SKILL / "references" / "file_format_enum.json").read_text())["enum"]
SCRIPT = SKILL / "scripts" / "summarize_landscape.py"


def rev(i, assay, fmt, level, **k):
    d = {"LandscapeId": i, "StudyName": "Alpha Cardio Study", "DatasetName": "Heart tissue atlas",
         "DatasetGroup": "G1", "DatasetAssay": assay, "DatasetCategory": "gene expression",
         "DatasetSpecies": "Homo sapiens", "DatasetFileFormat": fmt, "DatasetProcessingLevel": level,
         "DatasetDescription": "Heart atlas of 36 donors."}
    d.update(k)
    return d


S36 = {"ExpectedNumberOfSamples": "36", "FileRatio": "1", "FileRatioUnit": "Files per sample"}
SNRNA, WES, CONF, MS, RNA = ("single-nucleus RNA-seq", "whole exome sequencing", "confocal microscopy",
                             "mass spectrometry", "RNA-seq")
REVISED = [
    rev("ALPHA-Cardio-001-a", SNRNA, "fastq", ""),
    rev("ALPHA-Cardio-001-b", SNRNA, "bam", "Level 1", **S36),
    rev("ALPHA-Cardio-001-c", SNRNA, "mtx", "Level 3"),
    rev("ALPHA-Cardio-001-d", WES, "vcf", "Level 3", DatasetCategory="genomic variants"),
    rev("ALPHA-Cardio-001-f", SNRNA, "bam", ""),
    rev("ALPHA-Cardio-002-a", CONF, "tif", "", DatasetName="Imaging set", DatasetCategory="image", DatasetGroup="G2"),
    rev("ALPHA-Cardio-002-b", CONF, "fastq", "Level 1", DatasetName="Imaging set", DatasetCategory="image", DatasetGroup="G2"),
    rev("ALPHA-Cardio-002-c", CONF, "tif", "Level 3", DatasetName="Imaging set", DatasetCategory="image", DatasetGroup="G2",
        **S36),
    rev("ZZ-custom-1", MS, "csv", "Level 3", DatasetName="Orphan metabolite set", DatasetCategory="metabolomics",
        DatasetGroup="G3"),
    rev("BETA-Lung-001-a", MS, "png", "Level 1", StudyName="Beta Lung Study", DatasetName="Lung proteome",
        DatasetCategory="proteomics", DatasetGroup="B1"),
    rev("BETA-Lung-002-a", RNA, "bam", "Level 1", StudyName="Beta Lung Study", DatasetName="Lung RNA", DatasetGroup="B2"),
    rev("BETA-Lung-002-b", RNA, "png", "Level 2", StudyName="Beta Lung Study", DatasetName="Lung RNA", DatasetGroup="B2"),
    rev("BETA-Lung-003-a", RNA, "fastq", "Level 2", StudyName="Beta Lung Study", DatasetName="Lung RNA raw", DatasetGroup="B3"),
    rev("BETA-Lung-004-a", RNA, "bam", "Level 3", StudyName="Beta Lung Study", DatasetName="Lung RNA aligned", DatasetGroup="B4"),
    rev("GAMMA-x-001-a", RNA, "bam", "Level 1", StudyName="Gamma Study", DatasetName="Gamma", DatasetGroup="N1"),
]


def write_revised(path, rows=REVISED):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(sl.PROPERTIES))
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in sl.PROPERTIES})


def read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build():
    return om.build([OLD], RULES, SKILL / "references" / "format_level_rules.json",
                    SKILL / "references" / "landscape_enums.json", SKILL / "references" / "file_format_enum.json",
                    sl.file_estimate)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("old")
    write_revised(d / "revised.csv")
    out = d / "out"
    subprocess.run([sys.executable, str(SCRIPT), str(d / "revised.csv"), "--out-dir", str(out), "--mapping",
                    str(MAPPING), "--old-manifests", str(OLD)], check=True, capture_output=True)
    return out


def by_id(rows, i, **where):
    return [r for r in rows if r["LandscapeId"] == i and all(r[k] == v for k, v in where.items())]


def summary_row(out, dt, level):
    return next(r for r in read(out / "landscape_summary.csv") if r["DataType"] == dt and r["ProcessingLevel"] == level)


# ------------------------------------------------------------ extraction
def test_extract_round_trip_ignores_non_manifest_table_and_index(tmp_path):
    subprocess.run([sys.executable, str(SKILL / "scripts" / "extract_manifests.py"), str(OLD), "--out-dir", str(tmp_path)],
                   check=True, capture_output=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["alpha_manifest.csv", "beta_manifest.csv"]
    rows = read(tmp_path / "alpha_manifest.csv")
    assert list(rows[0]) == em.MANIFEST_COLUMNS
    assert len(rows) == 6 and rows[0]["gardener_source_entry"] == "ALPHA-Cardio-001"
    assert "alert" not in json.dumps(rows)  # script text is not cell content
    html_rows = em.load_old_manifests([OLD])
    csv_rows = em.load_old_manifests([tmp_path])
    assert [r["Center"] for r in html_rows] == [r["Center"] for r in csv_rows]
    assert {r["Center"] for r in html_rows} == {"ALPHA", "BETA"}
    assert [r["LandscapeId"] for r in html_rows] == [r["LandscapeId"] for r in csv_rows]


def test_extract_requires_out_dir():
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "extract_manifests.py"), str(OLD)], capture_output=True)
    assert r.returncode != 0


# ------------------------------------------------------------ normalization
def test_normalization():
    n = om.Normalizer(ENUMS, FMT_ENUM, RULES["processing_level_order"])
    assert n.assay("Confocalmicroscopy") == "confocal microscopy"
    assert n.species("Homosapiens") == "Homo sapiens"
    assert n.category("Geneexpression") == "gene expression"
    assert n.fmt("Tif") == "tif" and n.fmt("BednarrowPeak") == "bed narrowPeak"
    assert n.level("Level3") == "Level 3"
    assert n.assay("Totally new assay") == "Totally new assay"  # passes through
    it = build().items[0]
    assert (it["Assay"], it["Species"], it["Category"], it["Format"], it["StatedLevel"]) == \
        ("single-nucleus RNA-seq", "Homo sapiens", "gene expression", "fastq", "Level 1")


# ------------------------------------------------------------ linking
def test_linking_and_links_csv(run):
    links = read(run / "old_manifest_links.csv")
    assert list(links[0]) == om.LINK_COLUMNS
    a = by_id(links, "ALPHA-Cardio-001-a")[0]
    assert (a["LinkBasis"], a["SourceEntry"], a["MatchedItemId"]) == ("id-prefix", "ALPHA-Cardio-001", "ALPHA-Cardio-D001")
    assert a["OldFormats"] == "fastq; bam; tsv"
    assert by_id(links, "ALPHA-Cardio-001-b")[0]["MatchedItemId"] == "ALPHA-Cardio-D002"
    # entry exists but the format is not in it: entry-level evidence only
    c = by_id(links, "ALPHA-Cardio-001-c")[0]
    assert c["LinkBasis"] == "id-prefix" and c["MatchedItemId"] == ""
    z = by_id(links, "ZZ-custom-1")[0]
    assert (z["LinkBasis"], z["SourceEntry"], z["MatchedItemId"]) == ("dataset-name", "ALPHA-Cardio-003", "ALPHA-Cardio-D006")
    g = by_id(links, "GAMMA-x-001-a")[0]
    assert g["LinkBasis"] == "none" and g["SourceEntry"] == ""


def test_longest_entry_prefix_wins():
    rows = [{**{c: "" for c in em.MANIFEST_COLUMNS}, "Center": "X", "LandscapeId": f"X-D{i}", "gardener_source_entry": e,
             "DatasetFileFormats": "Fastq"} for i, e in enumerate(["X-1", "X-1-sub"])]
    o = om.OldManifest(rows, RULES, FLR, ENUMS, FMT_ENUM, sl.file_estimate)
    r = {"LandscapeId": "X-1-sub-a", "StudyName": "", "DatasetName": "", "DatasetFileFormat": "fastq", "DatasetAssay": ""}
    assert o.link(r)["entry"] == "X-1-sub"


# ------------------------------------------------------------ fills
def test_fills_and_provenance():
    o = build()
    rows = o.apply_fills([{**{p: "" for p in sl.PROPERTIES}, **r} for r in REVISED])
    r = {x["LandscapeId"]: x for x in rows}
    a = r["ALPHA-Cardio-001-a"]
    assert a["DatasetProcessingLevel"] == "Level 1" and a["FileRatio"] == "72" and a["FileRatioUnit"] == "Total files"
    assert [(f["Field"], f["Value"], f["Source"]) for f in a["_fills"]] == \
        [("DatasetProcessingLevel", "Level 1", "old stated"), ("FileCount", "72", "old stated")]
    f = r["ALPHA-Cardio-001-f"]
    assert [(x["Field"], x["Value"], x["Source"]) for x in f["_fills"]] == \
        [("DatasetProcessingLevel", "Level 2", "gardener:carried_from_audit"),
         ("FileCount", "36", "gardener:format_dictionary")]
    # never overwrite: stated level and a usable estimate stay
    b = r["ALPHA-Cardio-001-b"]
    assert b["DatasetProcessingLevel"] == "Level 1" and b["FileRatio"] == "1" and not b["_fills"]
    assert r["ALPHA-Cardio-001-c"]["_fills"] == [] and r["ALPHA-Cardio-001-c"]["DatasetProcessingLevel"] == "Level 3"


def test_entry_level_fallback_only_when_items_agree():
    base = {c: "" for c in em.MANIFEST_COLUMNS}
    def mk(levels):
        rows = [{**base, "Center": "X", "LandscapeId": f"X-D{i}", "gardener_source_entry": "X-1",
                 "DatasetFileFormats": "Fastq", "DatasetProcessingLevel": lv} for i, lv in enumerate(levels)]
        return om.OldManifest(rows, RULES, FLR, ENUMS, FMT_ENUM, sl.file_estimate)
    r = {**{p: "" for p in sl.PROPERTIES}, "LandscapeId": "X-1-a", "DatasetFileFormat": "bam"}
    assert mk(["Level2", "Level2"]).apply_fills([r])[0]["DatasetProcessingLevel"] == "Level 2"
    assert mk(["Level2", "Level3"]).apply_fills([r])[0]["DatasetProcessingLevel"] == ""


def test_fills_config_switches_off():
    rules = {**RULES, "old_manifest_fill": {"level": False, "file_count": False}}
    o = om.build([OLD], rules, SKILL / "references" / "format_level_rules.json", SKILL / "references" / "landscape_enums.json",
                 SKILL / "references" / "file_format_enum.json", sl.file_estimate)
    r = o.apply_fills([{**{p: "" for p in sl.PROPERTIES}, **REVISED[0]}])[0]
    assert r["DatasetProcessingLevel"] == "" and r["_fills"] == []


def test_needs_input_for_fills(run):
    s = summary_row(run, "gene expression (single-nucleus)", "Level 1")
    assert "1 row(s) used old-manifest fills (level: 1, file count: 1); confirm" in s["NeedsInput"]
    assert "level fill(s) are audit/format-inferred" not in s["NeedsInput"]
    s2 = summary_row(run, "gene expression (single-nucleus)", "Level 2")
    assert "1 row(s) used old-manifest fills (level: 1, file count: 1); confirm" in s2["NeedsInput"]
    assert "1 level fill(s) are audit/format-inferred, not center-stated" in s2["NeedsInput"]
    s3 = summary_row(run, "image", "Level 2")  # format_dictionary fill
    assert "1 level fill(s) are audit/format-inferred, not center-stated" in s3["NeedsInput"]
    assert "Unspecified" not in {r["ProcessingLevel"] for r in read(run / "landscape_summary.csv")}


# ------------------------------------------------------------ volume
def test_volume_max_band(run):
    # TBs (fastq item) and hundreds of GB (bam item) share this summary row
    assert summary_row(run, "gene expression (single-nucleus)", "Level 1")["ExpectedDataVolume"] == "TBs"
    assert summary_row(run, "gene expression (single-nucleus)", "Level 2")["ExpectedDataVolume"] == "hundreds of GB"
    # "unknown" and no match are ignored -> falls back to entry-level agreement (tsv item unknown; others differ) -> blank
    assert summary_row(run, "gene expression (single-nucleus)", "Level 3")["ExpectedDataVolume"] == ""
    assert summary_row(run, "metabolomics", "Level 3")["ExpectedDataVolume"] == "under 1 GB"
    assert summary_row(run, "proteomics", "Level 1")["ExpectedDataVolume"] == ""
    packet = json.loads((run / "review_packet.json").read_text())
    dt = next(d for d in packet["data_types"] if d["row"]["DataType"] == "gene expression (single-nucleus)"
              and d["row"]["ProcessingLevel"] == "Level 1")
    assert dt["old_manifest"]["volume_bands"] == {"hundreds of GB": 1, "TBs": 1}
    assert any(f["Field"] == "DatasetProcessingLevel" and f["Source"] == "old stated" for f in dt["old_manifest"]["fills"])
    assert any(rec.get("OldManifestFills") for rec in dt["records"])


def test_volume_band_order_rule():
    assert RULES["volume_band_order"][0] == "under 1 GB" and RULES["volume_band_order"][-1] == "PBs"
    assert "tens of TB" in RULES["volume_band_order"]


# ------------------------------------------------------------ row issues
def test_row_issues_and_origins(run):
    issues = read(run / "row_issues.csv")
    assert list(issues[0]) == om.ISSUE_COLUMNS
    def one(i, issue):
        m = [r for r in issues if r["LandscapeId"] == i and r["Issue"] == issue]
        assert len(m) == 1, (i, issue, [r["Issue"] for r in issues if r["LandscapeId"] == i])
        return m[0]
    a = one("ALPHA-Cardio-001-b", "sequencing Level 1 with non-raw format")
    assert a["Origin"] == "introduced in revision" and a["OldGroundedLevel"] == "Level 2" and a["OldStatedLevel"] == ""
    assert a["DataType"] == "gene expression (single-nucleus)" and a["ProcessingLevel"] == "Level 1"
    assert one("ALPHA-Cardio-001-b", "format implies Level 2 but stated Level 1")["Origin"] == "introduced in revision"
    # old entry has the same format and the same level problem
    inh = one("BETA-Lung-003-a", "format implies Level 1 but stated Level 2")
    assert inh["Origin"] == "inherited from old entry" and inh["OldStatedLevel"] == "Level 2"
    assert one("ALPHA-Cardio-002-b", "sequencing format on imaging assay")["Origin"] == "inherited from old entry"
    # entry exists but never had the format
    assert one("BETA-Lung-002-b", "image format on sequencing assay")["Origin"] == "introduced in revision"
    assert one("BETA-Lung-001-a", "image format on mass spectrometry assay")["Origin"] == "introduced in revision"
    assert one("BETA-Lung-004-a", "format implies Level 2 but stated Level 3")["Origin"] == "introduced in revision"
    n = one("GAMMA-x-001-a", "sequencing Level 1 with non-raw format")
    assert n["Origin"] == "no old entry" and n["OldFormats"] == ""
    assert one("GAMMA-x-001-a", "format implies Level 2 but stated Level 1")["Origin"] == "no old entry"
    assert not [r for r in issues if r["LandscapeId"] == "ALPHA-Cardio-001-a"]  # fastq Level 1 is fine
    s = summary_row(run, "image", "Level 1")
    assert "1 row issue(s): see row_issues.csv" in s["ReviewFlags"]


# ------------------------------------------------------------ itemization
def test_itemization_parse_duplicates_and_truncation():
    o = build()
    p = o.patterns
    segs = om.parse_itemization("all 36 samples snRNA-seq (72 fastq, 36 bam, 36 bam, 1 tsv) and snATACseq (72 fastq, "
                                "36 bam), Whole exome DNA sequencing (36 CRAM, 36 VCF)", p, o.norm)
    assert [s["assay"] for s in segs] == ["single-nucleus RNA-seq", "single-nucleus ATAC-seq", "whole exome sequencing"]
    assert dict(segs[0]["formats"]) == {"fastq": 72, "bam": 72, "tsv": 1} and segs[0]["dup"] == ["bam"]
    assert dict(segs[2]["formats"]) == {"cram": 36, "vcf": 36}
    # truncated mid-string: parse what is there, drop the cut item
    t = om.parse_itemization("snRNA-seq (72 fastq, 36 bam), snATACseq (72 fastq, 36 b", p, o.norm)
    assert len(t) == 2 and t[1]["truncated"] and dict(t[1]["formats"]) == {"fastq": 72}
    t2 = om.parse_itemization("scRNA-seq (10 h5ad, 3 mtx", p, o.norm)
    assert t2[0]["assay"] == "single-cell RNA-seq" and dict(t2[0]["formats"]) == {"h5ad": 10, "mtx": 3}
    assert om.parse_itemization("WES (4 cram)", p, o.norm)[0]["assay"] == "whole exome sequencing"
    assert om.parse_itemization("random words (3 apples)", p, o.norm) == []


# ------------------------------------------------------------ corrections
def corr(run):
    rows = read(run / "proposed_corrections.csv")
    assert list(rows[0]) == om.CORRECTION_COLUMNS
    return rows


def find(rows, i, field, **w):
    m = [r for r in rows if r["LandscapeId"] == i and r["Field"] == field and all(r[k] == v for k, v in w.items())]
    return m


def test_corrections_itemized_format(run):
    rows = corr(run)
    fc = find(rows, "ALPHA-Cardio-001-b", "FileCount")[0]
    assert (fc["CurrentValue"], fc["ProposedValue"], fc["Confidence"]) == ("36", "72", "high")
    assert "duplicate bam entries summed" in fc["Rationale"] and len(fc["Evidence"]) <= 200
    assert "snRNA-seq" in fc["Evidence"] and fc["Source"].startswith("old manifest ALPHA ALPHA-Cardio-001")
    lv = find(rows, "ALPHA-Cardio-001-b", "DatasetProcessingLevel")[0]
    assert (lv["CurrentValue"], lv["ProposedValue"], lv["Confidence"]) == ("Level 1", "Level 2", "high")
    # no file estimate -> propose the itemized count
    assert find(rows, "ALPHA-Cardio-001-d", "FileCount")[0]["ProposedValue"] == "36"
    # filled row now equals itemization (72 fastq): no FileCount proposal
    assert not find(rows, "ALPHA-Cardio-001-a", "FileCount", Source="old manifest ALPHA ALPHA-Cardio-001 Comments")


def test_corrections_format_not_itemized_and_missing_rows(run):
    rows = corr(run)
    f = find(rows, "ALPHA-Cardio-001-c", "DatasetFileFormat")[0]
    # mtx is not itemized, but fastq/bam are present across the linked rows: review prompt, not a replacement
    assert f["ProposedValue"] == "review: not in old itemization (old lists fastq; bam; tsv; h5)"
    assert f["Confidence"] == "low"
    assert f["Rationale"].startswith("old itemization for single-nucleus RNA-seq lists fastq, bam, tsv, h5, not mtx")
    miss = [r for r in rows if r["Field"] == "MissingRow"]
    props = {(r["Assay"], r["ProposedValue"]) for r in miss}
    assert ("whole exome sequencing", "cram (36 files, implied Level 2)") in props
    assert ("single-nucleus RNA-seq", "tsv (1 files)") in props
    assert ("single-nucleus RNA-seq", "h5 (36 files, implied Level 3)") in props
    assert all(r["Confidence"] == "medium" for r in miss)
    assert len(props) == len(miss)  # deduplicated per old entry/assay/format
    # attached to the first non-derived row (by LandscapeId) of the entry/assay scope
    assert {r["LandscapeId"] for r in miss if r["Assay"] == "single-nucleus RNA-seq"} == {"ALPHA-Cardio-001-a"}
    assert not [r for r in miss if r["ProposedValue"].startswith(("fastq", "bam"))
                and r["Assay"] == "single-nucleus RNA-seq"]


def _full(i, assay, fmt, level="Level 3", **k):
    d = {p: "" for p in sl.PROPERTIES}
    d.update(rev(i, assay, fmt, level, **k))
    return d


def _corrections_for(rows):
    o = build()
    rows = o.apply_fills(rows)
    return o.corrections(rows)


def test_derived_row_gets_no_format_or_missing_row_proposals():
    # raw rows cover fastq/bam in another group; the derived h5ad row sorts first and has its own group
    rows = [_full("ALPHA-Cardio-001-0", SNRNA, "h5ad", DatasetGroup="GD", DerivedFrom="G1"),
            _full("ALPHA-Cardio-001-b", SNRNA, "fastq", "Level 1", **S36),
            _full("ALPHA-Cardio-001-c", SNRNA, "bam", "Level 2", **S36)]
    out = _corrections_for(rows)
    assert not [c for c in out if c["LandscapeId"] == "ALPHA-Cardio-001-0" and c["Field"] in ("DatasetFileFormat", "MissingRow")]
    miss = [c for c in out if c["Field"] == "MissingRow"]
    assert miss and all(c["LandscapeId"] == "ALPHA-Cardio-001-b" for c in miss)  # first non-derived row
    # presence is judged across groups: fastq and bam are not proposed as missing
    assert not [c for c in miss if c["ProposedValue"].startswith(("fastq", "bam"))]


def test_format_replacement_only_when_no_itemized_format_present():
    rows = [_full("ALPHA-Cardio-001-x", SNRNA, "mtx")]
    out = _corrections_for(rows)
    f = [c for c in out if c["Field"] == "DatasetFileFormat"][0]
    assert f["ProposedValue"] == "fastq; bam; tsv; h5" and f["Confidence"] == "medium"
    # non-derived rows get MissingRow only for formats absent across the whole scope
    rows.append(_full("ALPHA-Cardio-001-y", SNRNA, "fastq", "Level 1", DatasetGroup="G9"))
    out = _corrections_for(rows)
    f = [c for c in out if c["Field"] == "DatasetFileFormat" and c["LandscapeId"] == "ALPHA-Cardio-001-x"][0]
    assert f["ProposedValue"].startswith("review: not in old itemization") and f["Confidence"] == "low"
    assert not [c for c in out if c["Field"] == "MissingRow" and c["ProposedValue"].startswith("fastq")]


def test_gene_like_tokens_strip_trailing_hyphen():
    o = build()
    genes = o._terms("NF-\u03baB signalling, ABC- and ZNF123, AB-")["genes"]
    assert "NF-" not in genes and "NF" not in genes and "AB" not in genes and "AB-" not in genes
    assert "ABC" in genes and "ZNF123" in genes


def test_corrections_level_conflict_without_itemization(run):
    rows = corr(run)
    low = find(rows, "ALPHA-Cardio-002-c", "DatasetProcessingLevel")[0]
    assert (low["CurrentValue"], low["ProposedValue"], low["Confidence"]) == ("Level 3", "Level 2", "low")
    med = find(rows, "BETA-Lung-004-a", "DatasetProcessingLevel")[0]
    assert (med["ProposedValue"], med["Confidence"]) == ("Level 2", "medium")
    # carried_from_audit grounding is not used for conflicts
    assert not find(rows, "ALPHA-Cardio-001-b", "DatasetProcessingLevel", Confidence="low")


def test_corrections_public_deposit_and_fill_listing(run):
    rows = corr(run)
    d = find(rows, "ALPHA-Cardio-001-a", "ExistingDeposit")[0]
    assert d["ProposedValue"] == "GSE111111; PMID:12345678" and d["Confidence"] == "medium"
    s = summary_row(run, "gene expression (single-nucleus)", "Level 1")
    assert "already deposited: GSE111111, PMID:12345678; consider linking instead of re-uploading" in s["NeedsInput"]
    assert not find(rows, "BETA-Lung-001-a", "ExistingDeposit")
    f1 = find(rows, "ALPHA-Cardio-001-a", "DatasetProcessingLevel", Source="old-manifest fill (old stated)")[0]
    assert (f1["CurrentValue"], f1["ProposedValue"], f1["Confidence"]) == ("", "Level 1", "high")
    f2 = find(rows, "ALPHA-Cardio-001-f", "DatasetProcessingLevel")[0]
    assert f2["Source"] == "old-manifest fill (gardener:carried_from_audit)" and f2["Confidence"] == "low"
    assert find(rows, "ALPHA-Cardio-002-a", "FileCount", Confidence="low")


def test_accession_detection():
    assert om.accessions("see GSE195688, SRP123, SRR9, PRJNA55, phs000123.v2, PXD0001, MTBLS12, EMPIAR-10001, "
                         "S-BIAD5, PMID: 3344 and pmid:9; in SRA") == \
        ["GSE195688", "SRP123", "SRR9", "PRJNA55", "phs000123.v2", "PXD0001", "MTBLS12", "EMPIAR-10001", "S-BIAD5",
         "PMID:3344", "PMID:9", "SRA"]
    assert om.accessions("nothing here, GSEx and PMIDs") == []


# ------------------------------------------------------------ description loss
def test_description_loss(run):
    rows = read(run / "description_loss.csv")
    assert list(rows[0]) == om.LOSS_COLUMNS
    a = by_id(rows, "ALPHA-Cardio-001-a")[0]
    terms = a["MissingTerms"].split("; ")
    for t in ("rs12345", "ZNF123", "GSE111111", "PMID:12345678", "30 patients", "Years 1-3"):
        assert t in terms, t
    assert "36 patient" not in " ".join(terms) and "RNA" not in terms and "CRAM" not in terms
    assert a["CountConflicts"] == "person: old 30 vs new 36"
    assert a["OldDescription"].startswith("Heart tissue atlas from") and a["NewDescription"] == "Heart atlas of 36 donors."
    assert 0 < float(a["Similarity"]) < 1 and len(a["Similarity"].split(".")[1]) == 2
    assert "36 patient samples" in a["OldComments"]
    # unlinked rows and rows without lost content are not written
    assert not by_id(rows, "GAMMA-x-001-a")
    assert not by_id(rows, "BETA-Lung-001-a")
    assert "lost description content: see description_loss.csv" in summary_row(
        run, "gene expression (single-nucleus)", "Level 1")["ReviewFlags"]


def test_description_loss_no_loss_when_terms_kept():
    o = build()
    row = {**{p: "" for p in sl.PROPERTIES}, "LandscapeId": "ALPHA-Cardio-001-a", "DatasetFileFormat": "fastq",
           "DatasetDescription": "Heart tissue atlas from ~30 patients with marker ZNF123 and variant rs12345 across "
                                 "Years 1-3.", "Comments": "GSE111111 PMID: 12345678 SRA, 36 donors"}
    row["_link"] = o.link(row)
    assert o.description_loss(row) is None


# ------------------------------------------------------------ CLI
def test_without_old_manifests_outputs_unchanged(tmp_path):
    write_revised(tmp_path / "revised.csv")
    out = tmp_path / "out"
    subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "revised.csv"), "--out-dir", str(out), "--mapping",
                    str(MAPPING)], check=True, capture_output=True)
    assert sorted(p.name for p in out.iterdir()) == [
        "README.md", "excluded_rows.csv", "landscape_summary.csv", "landscape_summary_compact.csv",
        "landscape_summary_compact.md", "review_packet.json", "run_info.json"]
    rows = read(out / "landscape_summary.csv")
    assert list(rows[0]) == sl.OUTPUT_COLUMNS
    assert all(r["ExpectedDataVolume"] == "" for r in rows)
    assert all("old-manifest" not in r["NeedsInput"] and "row issue" not in r["ReviewFlags"] for r in rows)
    assert "old_manifest" not in json.loads((out / "review_packet.json").read_text())["data_types"][0]


def test_with_old_manifests_writes_extra_files_and_compact_last_column(run):
    names = {p.name for p in run.iterdir()}
    assert {"old_manifest_links.csv", "row_issues.csv", "proposed_corrections.csv", "description_loss.csv"} <= names
    with open(run / "landscape_summary_compact.csv", newline="") as fh:
        header = next(csv.reader(fh))
    assert header[-1] == "ExpectedDataVolume" and header[-2] == "EarliestAvailableDate"
    md = (run / "landscape_summary_compact.md").read_text().splitlines()[0]
    assert md.endswith("| ExpectedDataVolume |")
    full = list(read(run / "landscape_summary.csv")[0])
    assert full[full.index("EarliestAvailableDate") + 1] == "ExpectedDataVolume"


def test_reference_json_loads():
    assert set(ENUMS) >= {"DatasetAssay", "DatasetSpecies", "DatasetCategory"}
    assert FLR["level1_allowed_formats_for_sequencing"] == ["fastq", "sra"]
    assert {"sequencing_assays", "imaging_assays", "mass_spec_assays", "disallowed_format_families",
            "itemization_assay_labels", "description_stoplist"} <= set(FLR)
    assert RULES["old_manifest_fill"] == {"level": True, "file_count": True}
    enum_assays = {sl._key(a) for a in ENUMS["DatasetAssay"]}
    for fam in ("sequencing_assays", "imaging_assays", "mass_spec_assays"):
        assert all(sl._key(a) in enum_assays for a in FLR[fam]), fam
