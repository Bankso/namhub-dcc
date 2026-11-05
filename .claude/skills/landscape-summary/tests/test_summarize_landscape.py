import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))
import summarize_landscape as sl  # noqa: E402

FIX = HERE / "fixtures"
MAPPING = HERE / "fixture_mapping.csv"
DEFAULT_RULES = json.loads((SKILL / "references" / "rules.json").read_text())
RULES = DEFAULT_RULES


@pytest.fixture(scope="module")
def summary():
    rows = sl.load_rows(sl.find_csvs([FIX]))
    result, total = sl.summarize(rows, sl.load_mapping(MAPPING), RULES)
    return {(d["DataType"], d["ProcessingLevel"]): d for d in result}, total, rows


def test_header_normalization():
    assert sl.norm_header("Dataset Category") == "DatasetCategory"
    assert sl.norm_header("DatasetCategory") == "DatasetCategory"
    assert sl.norm_header("Landscape_id") == "LandscapeId"
    assert sl.norm_header("﻿Study Name") == "StudyName"
    assert sl.norm_header("ROW_ID") is None


def test_all_files_loaded_and_title_headers_work(summary):
    _, total, rows = summary
    assert len(rows) == 13
    assert total == 5
    assert any(r["LandscapeId"] == "L6" and r["DatasetCategory"] == "Clinical data" for r in rows)


def test_category_casing_merged(summary):
    s, _, _ = summary
    assert ("clinical data", "Level 3") in s  # most common spelling wins
    assert len({k[0] for k in s if k[0].casefold() == "clinical data"}) == 1
    cd3, cdna = s["clinical data", "Level 3"], s["clinical data", "Not Applicable"]
    assert cd3["RowCount"] == 2 and cdna["RowCount"] == 1
    assert cd3["CategoryStudyCount"] == cdna["CategoryStudyCount"] == 3
    assert cd3["StudyCount"] == 2 and cdna["StudyCount"] == 1


def test_metadata_mapping_case_insensitive_fallback_unmapped(summary):
    s, _, _ = summary
    ge = s["gene expression", "Level 3"]
    # " rna-seq " (lowercase, padded) maps to RNA-seq; Zebra assay does not
    assert ge["MetadataTypes"] == "NF:RNASeqTemplate"
    assert ge["UnmappedAssays"] == "Zebra assay"
    assert "Unmapped assays" in ge["ReviewFlags"]
    assert s["image", "Level 1"]["MetadataTypes"] == "NF:MaterialScienceAssayTemplate (fallback)"
    assert s["proteomics", "Level 1"]["MetadataTypes"] == "NF:MassSpecAssayTemplate"


def test_file_count_all_units_and_skips(summary):
    s, _, _ = summary
    # L1: 10*2 (per sample); L3: ceil(10/5) (samples per file) + 6*3 + 4*1 + ceil(8/4)
    assert s["gene expression", "Level 1"]["EstimatedFileCount"] == "20"
    assert s["gene expression", "Level 3"]["EstimatedFileCount"] == "26"
    assert s["image", "Level 1"]["EstimatedFileCount"] == "3" and s["image", "Level 2"]["EstimatedFileCount"] == "4"
    assert s["clinical data", "Level 3"]["EstimatedFileCount"] == "2"
    assert s["clinical data", "Not Applicable"]["EstimatedFileCount"] == "1"
    prot = s["proteomics", "Level 1"]
    assert prot["EstimatedFileCount"] == "0"
    assert "1 row(s) skipped" in prot["ReviewFlags"]


def test_file_estimate_units():
    base = {"ExpectedNumberOfSamples": "7", "FileRatio": "2", "FileRatioUnit": "Files per sample"}
    assert sl.file_estimate(base) == 14
    assert sl.file_estimate({**base, "FileRatioUnit": "Samples per file"}) == 4
    assert sl.file_estimate({**base, "FileRatioUnit": "Total files"}) == 2
    assert sl.file_estimate({**base, "ExpectedNumberOfSamples": ""}) is None
    assert sl.file_estimate({**base, "FileRatio": "abc"}) is None


def test_dataset_and_study_counts(summary):
    s, _, _ = summary
    ge1, ge3 = s["gene expression", "Level 1"], s["gene expression", "Level 3"]
    assert ge1["StudyCount"] == 1 and ge3["StudyCount"] == 4
    assert ge1["CategoryStudyCount"] == ge3["CategoryStudyCount"] == 4
    # L3: S1 G1, S2 G2, S3 D7, S4 D13
    assert ge3["DatasetCount"] == 4
    assert ge1["RowCount"] == 1 and ge3["RowCount"] == 4


def test_sensitivity_max_over_rows(summary):
    s, _, _ = summary
    ge1, ge3 = s["gene expression", "Level 1"], s["gene expression", "Level 3"]
    assert ge1["ProjectedSensitivity"] == "Controlled"  # human fastq Level 1 row
    assert "Controlled" in ge1["SensitivityRationale"] and "1 row(s)" in ge1["SensitivityRationale"]
    assert ge3["ProjectedSensitivity"] == "Registered"  # max over the Level 3 rows
    assert "unknown species" in ge3["ReviewFlags"]
    assert s["image", "Level 1"]["ProjectedSensitivity"] == "Open"
    assert s["model", "Not Applicable"]["ProjectedSensitivity"] == "Open"
    assert s["Unknown", "Level 3"]["ProjectedSensitivity"] == "Open"


def test_row_tier_rules():
    mk = lambda **k: {**{p: "" for p in sl.PROPERTIES}, **k}
    t = lambda **k: sl.row_tier(mk(**k), RULES)[0]
    assert t(DatasetSpecies="Homo sapiens", DatasetCategory="survey data") == "Controlled"
    assert t(DatasetSpecies="Homo sapiens", DatasetCategory="proteomics", DatasetFileFormat="raw") == "Registered"
    assert t(DatasetSpecies="Homo sapiens", DatasetCategory="x", DatasetFileFormat="bam", DatasetProcessingLevel="Level 2") == "Controlled"
    assert t(DatasetSpecies="Homo sapiens", DatasetCategory="x", DatasetFileFormat="bam", DatasetProcessingLevel="Level 3") == "Registered"
    assert t(DatasetSpecies="Mus musculus (humanized)", DatasetCategory="clinical") == "Open"
    assert t(DatasetSpecies="", DatasetCategory="clinical") == "Registered"
    assert t(DatasetSpecies="Mus musculus", DatasetFileFormat="Github Repo") == "Open"


def test_repository_dominance(summary):
    s, _, _ = summary
    ge1, ge3 = s["gene expression", "Level 1"], s["gene expression", "Level 3"]
    assert ge1["RecommendedRepository"] == "dbGaP" and ge1["OtherRepositories"] == ""
    assert ge3["RecommendedRepository"] == "GEO"  # follows the Registered rows
    assert "Mixed sensitivity: 3 Registered / 1 Open" in ge3["ReviewFlags"]
    assert "4 row(s) -> GEO" in ge3["RepositoryRationale"]
    assert "NAMHub also hosts metadata" in ge3["RepositoryRationale"]
    cd = s["clinical data", "Not Applicable"]  # uncapped level
    assert cd["RecommendedRepository"] == "Synapse (controlled access)"
    assert "NAMHub also hosts" not in cd["RepositoryRationale"]
    # "clinical data" is not on the exempt list, so Level 3 is capped to Registered
    cd3 = s["clinical data", "Level 3"]
    assert cd3["ProjectedSensitivity"] == "Registered"
    assert "2 row(s) lowered Controlled -> Registered by Level 3" in cd3["NeedsInput"]
    assert s["image", "Level 1"]["RecommendedRepository"] == "BioImage Archive"
    assert s["proteomics", "Level 1"]["RecommendedRepository"] == "PRIDE"
    assert s["model", "Not Applicable"]["RecommendedRepository"] == "GitHub + Zenodo"
    assert s["Unknown", "Level 3"]["RecommendedRepository"] == "Synapse (open)"


def test_priority_tiers(summary):
    s, _, _ = summary
    for lv in ("Level 1", "Level 3"):
        assert s["gene expression", lv]["Priority"] == "High"
    assert s["clinical data", "Level 3"]["Priority"] == "High"
    assert s["clinical data", "Not Applicable"]["Priority"] == "High"
    assert s["image", "Level 1"]["Priority"] == s["image", "Level 2"]["Priority"] == "Medium"
    assert s["proteomics", "Level 1"]["Priority"] == "Low"
    assert s["model", "Not Applicable"]["Priority"] == "Low"


def test_priority_unknown_excluded_from_high():
    prio, flags = sl.assign_priority({"Unknown": 4, "a": 4, "b": 1}, 5, RULES)
    assert prio["a"] == "High" and prio["Unknown"] == "Medium" and prio["b"] == "Low"
    assert "Unknown" in flags


def test_cli_outputs_and_column_order(tmp_path):
    out = tmp_path / "out"
    script = SKILL / "scripts" / "summarize_landscape.py"
    subprocess.run([sys.executable, str(script), str(FIX), "--out-dir", str(out), "--mapping", str(MAPPING)],
                   check=True, capture_output=True)
    with open(out / "landscape_summary.csv", newline="") as fh:
        r = csv.reader(fh)
        header = next(r)
        rows = list(r)
    assert header == sl.OUTPUT_COLUMNS
    assert header == ["DataType", "ProcessingLevel", "Assays", "FileFormats", "PreferredFileFormat", "MetadataTypes", "MetadataCandidates", "UnmappedAssays",
                      "ProjectedSensitivity", "SensitivityRationale", "RecommendedRepository", "OtherRepositories",
                      "RepositoryRationale", "Priority", "CategoryStudyCount", "StudyCount", "DatasetCount",
                      "RowCount", "EstimatedFileCount", "EarliestAvailableDate", "ExpectedDataVolume", "Species", "ReviewFlags", "NeedsInput"]
    pri = [row[header.index("Priority")] for row in rows]
    order = {"High": 0, "Medium": 1, "Low": 2}
    assert [order[p] for p in pri] == sorted(order[p] for p in pri)
    packet = json.loads((out / "review_packet.json").read_text())
    assert len(packet["data_types"]) == len(rows)
    rec = packet["data_types"][0]["records"][0]
    assert {"StudyName", "ContextOfUse", "Comments", "ProcessingLevel"} <= set(rec)
    prow = packet["data_types"][0]["row"]
    assert "MetadataCandidates" in prow
    assert prow["DataType"] and prow["ProcessingLevel"]
    # sort: priority, CategoryStudyCount desc
    keys = [(order[r[header.index("Priority")]], -int(r[header.index("CategoryStudyCount")])) for r in rows]
    assert keys == sorted(keys)


def test_context_of_use_parsing():
    assert sl.parse_list('["a","b"]') == ["a", "b"]
    assert sl.parse_list("plain text") == ["plain text"]
    assert sl.parse_list("") == []


def test_sort_order_levels_in_enum_order():
    rows = [_mk(StudyName="S1", DatasetName=f"D{i}", DatasetCategory="x", DatasetProcessingLevel=lv)
            for i, lv in enumerate(["", "Not Applicable", "metadata", "Auxiliary", "Level 4", "level 2", "Level 3", "Level 1"])]
    rows.append(_mk(StudyName="S1", DatasetName="Dz", DatasetCategory="a"))
    rows.append(_mk(StudyName="S2", DatasetName="Dy", DatasetCategory="a", DatasetProcessingLevel="Level 1"))
    result, _ = sl.summarize(rows, {}, RULES)
    assert [(d["DataType"], d["ProcessingLevel"]) for d in result] == [
        ("a", "Level 1"), ("a", "Unspecified"),  # a has 2 studies -> sorts first within Priority
        ("x", "Level 1"), ("x", "Level 2"), ("x", "Level 3"), ("x", "Level 4"),
        ("x", "Auxiliary"), ("x", "Metadata"), ("x", "Not Applicable"), ("x", "Unspecified")]


def test_recommendation_follows_max_tier():
    mk = lambda i, sp, fmt: {**{p: "" for p in sl.PROPERTIES}, "_source": "x", "StudyName": f"S{i}",
                              "DatasetName": f"D{i}", "DatasetCategory": "gene expression",
                              "DatasetSpecies": sp, "DatasetFileFormat": fmt,
                              "DatasetProcessingLevel": "Level 1"}
    rows = [mk(0, "Homo sapiens", "fastq")] + [mk(i, "Mus musculus", "csv") for i in (1, 2, 3)]
    result, _ = sl.summarize(rows, {}, RULES)
    assert len(result) == 1
    d = result[0]
    assert d["ProjectedSensitivity"] == "Controlled"
    assert d["RecommendedRepository"] == "dbGaP"
    assert d["OtherRepositories"] == "GEO"
    assert "Mixed sensitivity: 1 Controlled / 3 Open" in d["ReviewFlags"]


def _mk(**k):
    return {**{p: "" for p in sl.PROPERTIES}, "_source": "x", **k}


def test_assay_matching_ignores_case_spacing_punctuation(tmp_path):
    m = tmp_path / "map.csv"
    m.write_text("Assay,DCC,SchemaName,SchemaURL,IsFallback,Rationale\n"
                 "Insilicosynthesis,NF,SynT,u,No,t\nSingle-cell ATAC-seq,NF,AtacT,u,No,t\n")
    mapping = sl.load_mapping(m)
    rows = [_mk(StudyName="S1", DatasetName="D1", DatasetCategory="x", DatasetAssay="in silico synthesis"),
            _mk(StudyName="S2", DatasetName="D2", DatasetCategory="x", DatasetAssay="In-Silico Synthesis"),
            _mk(StudyName="S3", DatasetName="D3", DatasetCategory="x", DatasetAssay="single cell ATAC/seq"),
            _mk(StudyName="S4", DatasetName="D4", DatasetCategory="x", DatasetAssay="single-nucleus ATAC-seq")]
    res = {d["DataType"]: d for d in sl.summarize(rows, mapping, RULES)[0]}
    # single-cell / single-nucleus assays now split into their own DataType rows
    assert set(res) == {"x", "x (single-cell)", "x (single-nucleus)"}
    assert res["x (single-cell)"]["MetadataTypes"] == "NF:AtacT"
    assert res["x (single-nucleus)"]["UnmappedAssays"] == "single-nucleus ATAC-seq"
    d = res["x"]
    assert d["MetadataTypes"] == "NF:SynT"
    # the two synthesis spellings collapse into one displayed assay
    assert sum("ilico" in a.replace(" ", "").replace("-", "").lower() for a in d["Assays"].split("; ")) == 1


def test_registered_fallback_label():
    t = lambda tier, cat="x": sl.row_repository(_mk(DatasetCategory=cat), tier, RULES)[1]
    assert t("Open") == "Synapse (open)"
    assert t("Registered") == "Synapse (registered access)"
    assert t("Controlled") == "Synapse (controlled access)"
    rows = [_mk(StudyName="S1", DatasetName="D", DatasetCategory="x", DatasetSpecies="Homo sapiens")]
    d = sl.summarize(rows, {}, RULES)[0][0]
    assert d["ProjectedSensitivity"] == "Registered"
    assert d["RecommendedRepository"] == "Synapse (registered access)"
    assert "(open)" not in d["RepositoryRationale"]


@pytest.mark.parametrize("kw", [
    dict(DatasetFileFormat="Github Repo", DatasetSpecies=""),
    dict(DatasetFileFormat="MATLAB script", DatasetSpecies="Unknown"),
    dict(DatasetFileFormat="rmd", DatasetSpecies="Homo sapiens"),
    dict(DatasetCategory="model", DatasetFileFormat="", DatasetSpecies=""),
    dict(DatasetCategory="Model", DatasetFileFormat="", DatasetSpecies="Homo sapiens"),
])
def test_code_rows_are_open_without_unknown_species_flag(kw):
    row = _mk(StudyName="S1", DatasetName="D", **{"DatasetCategory": "tool", **kw})
    assert sl.row_tier(row, RULES)[0] == "Open"
    d = sl.summarize([row], {}, RULES)[0][0]
    assert d["ProjectedSensitivity"] == "Open"
    assert "unknown species" not in d["ReviewFlags"]


def test_code_switch_and_non_code_blank_format():
    row = _mk(DatasetCategory="model", DatasetFileFormat="", DatasetSpecies="")
    assert sl.row_tier(row, {**RULES, "code_model_always_open": False})[0] == "Registered"
    # model category with a non-code format is not a code row
    assert sl.row_tier(_mk(DatasetCategory="model", DatasetFileFormat="csv", DatasetSpecies="Homo sapiens"), RULES)[0] == "Registered"
    for f in ["Github Repo", "docker image", "Python script", "R script", "MATLAB script", "bash script",
              "Jupyter notebook", "rmd", "js"]:
        assert f.casefold() in {x.casefold() for x in DEFAULT_RULES["code_model_formats"]}


def _lv_rows(cat, species, specs):
    """specs: list of (level, format); one study each."""
    return [_mk(StudyName=f"S{i}", DatasetName=f"D{i}", LandscapeId=f"L{i}", DatasetCategory=cat,
                DatasetSpecies=species, DatasetFileFormat=fmt, DatasetProcessingLevel=lv)
            for i, (lv, fmt) in enumerate(specs)]


def test_one_category_three_levels_share_priority_and_category_count():
    rows = _lv_rows("proteomics", "Mus musculus", [("Level 1", "raw"), ("Level 3", "csv"), ("Level 4", "csv")])
    rows.append(_mk(StudyName="S9", DatasetName="D9", DatasetCategory="proteomics", DatasetSpecies="Mus musculus",
                    DatasetProcessingLevel="Level 3"))
    result, _ = sl.summarize(rows, {}, RULES)
    assert [d["ProcessingLevel"] for d in result] == ["Level 1", "Level 3", "Level 4"]
    assert {d["DataType"] for d in result} == {"proteomics"}
    assert {d["Priority"] for d in result} == {"High"}
    assert {d["CategoryStudyCount"] for d in result} == {4}
    assert [d["StudyCount"] for d in result] == [1, 2, 1]
    assert "ProcessingLevels" not in result[0]


def test_level_gradient_gene_expression():
    rows = _lv_rows("gene expression", "Homo sapiens",
                    [("Level 1", "fastq"), ("Level 3", "csv"), ("Level 4", "csv")])
    s = {d["ProcessingLevel"]: d for d in sl.summarize(rows, {}, RULES)[0]}
    l1, l3, l4 = s["Level 1"], s["Level 3"], s["Level 4"]
    assert l1["ProjectedSensitivity"] == "Controlled" and l1["RecommendedRepository"] == "dbGaP"
    # L3 and L4 were already Registered: the cap lowered nothing, so no note
    for d in (l3, l4):
        assert d["ProjectedSensitivity"] == "Registered"
        assert "lowered" not in d["SensitivityRationale"] and "processing-level cap" not in d["NeedsInput"]


def test_cap_lowers_controlled_to_registered_at_level_3_and_4():
    for lv in ("Level 3", "Level 4"):
        rows = _lv_rows("aligned reads", "Homo sapiens", [(lv, "bam")])
        d = sl.summarize(rows, {}, RULES)[0][0]
        assert d["ProjectedSensitivity"] == "Registered"
        assert f"Controlled lowered to Registered by {lv} cap" in d["SensitivityRationale"]
        assert f"1 row(s) lowered Controlled -> Registered by {lv} processing-level cap; " \
               "confirm processed data is not re-identifiable" in d["NeedsInput"]
        assert d["RecommendedRepository"] == "SRA"


def test_cap_exempt_category_stays_controlled():
    rows = _lv_rows("genomic variants", "Homo sapiens", [("Level 4", "vcf")])
    d = sl.summarize(rows, {}, RULES)[0][0]
    assert d["ProjectedSensitivity"] == "Controlled"
    assert "lowered" not in d["SensitivityRationale"] and "processing-level cap" not in d["NeedsInput"]
    # unlisted level (Level 2) is uncapped even for non-exempt categories
    d = sl.summarize(_lv_rows("aligned reads", "Homo sapiens", [("Level 2", "bam")]), {}, RULES)[0][0]
    assert d["ProjectedSensitivity"] == "Controlled" and "lowered" not in d["SensitivityRationale"]


def test_cap_never_raises_code_or_non_human():
    rows = _lv_rows("model", "", [("Level 4", "Python script")]) + \
        _lv_rows("image", "Mus musculus", [("Level 4", "tiff")])
    for d in sl.summarize(rows, {}, RULES)[0]:
        assert d["ProjectedSensitivity"] == "Open" and "lowered" not in d["SensitivityRationale"]


def test_blank_level_is_unspecified_with_flag():
    rows = [_r(1, DatasetProcessingLevel=""), _r(2, DatasetProcessingLevel="  "),
            _r(3, DatasetProcessingLevel="Level 1")]
    s = {d["ProcessingLevel"]: d for d in _one(rows)}
    assert set(s) == {"Level 1", "Unspecified"}
    assert "2 row(s) with no DatasetProcessingLevel" in s["Unspecified"]["ReviewFlags"]
    assert "no DatasetProcessingLevel" not in s["Level 1"]["ReviewFlags"]
    assert s["Unspecified"]["RowCount"] == 2


def test_rules_have_no_split_and_gradient_keys():
    assert "split_raw_processed_categories" not in DEFAULT_RULES
    assert DEFAULT_RULES["level_max_tier"] == {"Level 3": "Registered", "Level 4": "Registered"}
    assert "genomic variants" in DEFAULT_RULES["level_cap_exempt_categories"]


# ---------------------------------------------------------------- curator-decision rules
def _one(rows, rules=DEFAULT_RULES, mapping=None, excluded=None):
    return sl.summarize(rows, mapping or {}, rules, excluded)[0]


def _r(i=1, **k):
    base = dict(StudyName=f"S{i}", DatasetName=f"D{i}", LandscapeId=f"L{i}", DatasetCategory="x")
    return _mk(**{**base, **k})


def test_assay_alias_before_mapping_and_dedup(tmp_path):
    m = tmp_path / "map.csv"
    m.write_text("Assay,DCC,SchemaName,SchemaURL,IsFallback,Rationale\n"
                 "pharmacokinetic ADME assay,NF,PkT,u,No,t\n")
    rows = [_r(1, DatasetAssay="PharmocokineticADMEassay"), _r(2, DatasetAssay="pharmacokinetic ADME assay")]
    d = _one(rows, mapping=sl.load_mapping(m))[0]
    assert d["Assays"] == "pharmacokinetic ADME assay" and d["MetadataTypes"] == "NF:PkT"
    assert d["UnmappedAssays"] == ""


def test_unspecified_assays_flagged_not_unmapped():
    rows = [_r(1, DatasetAssay="Unknown"), _r(2, DatasetAssay=""), _r(3, DatasetAssay="Zebra")]
    d = _one(rows)[0]
    assert d["UnmappedAssays"] == "Zebra"
    assert "2 row(s) with no specific assay (Unknown/blank); set DatasetAssay" in d["ReviewFlags"]


def test_placeholder_rows_excluded(tmp_path):
    rows = [_r(1, DatasetSpecies="Mus musculus"),
            _r(2, DatasetName="Initial Test", DatasetSpecies="Homo sapiens")]
    rows.append(_r(3, DatasetDescription="  TEST ", DatasetSpecies="Homo sapiens", DatasetCategory="clinical"))
    rows.append(_r(4, Comments="test", DatasetSpecies="Homo sapiens"))
    rows.append(_r(5, DatasetName="Testing the assay", DatasetSpecies="Mus musculus"))  # not a placeholder
    exc = []
    res = _one(rows, excluded=exc)
    s = {d["DataType"]: d for d in res}
    assert [e["LandscapeId"] for e in exc] == ["L2", "L3", "L4"]
    assert set(s) == {"x"}  # the clinical placeholder category vanished entirely
    assert s["x"]["RowCount"] == 2 and s["x"]["StudyCount"] == 2
    assert s["x"]["ProjectedSensitivity"] == "Open"  # excluded human rows do not raise the tier
    # CLI writes excluded_rows.csv and prints the count
    inp = tmp_path / "in.csv"
    with open(inp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(sl.PROPERTIES))
        w.writeheader()
        for r in rows:
            w.writerow({p: r[p] for p in sl.PROPERTIES})
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(SKILL / "scripts" / "summarize_landscape.py"), str(inp),
                        "--out-dir", str(out), "--mapping", str(MAPPING)], check=True, capture_output=True, text=True)
    assert "3 excluded" in p.stdout
    with open(out / "excluded_rows.csv", newline="") as fh:
        er = list(csv.DictReader(fh))
    assert [r["LandscapeId"] for r in er] == ["L2", "L3", "L4"]
    assert list(er[0]) == ["LandscapeId", "StudyName", "DatasetName", "Reason"]


def test_format_conflict_ignores_format():
    rows = [_r(1, DatasetCategory="image", DatasetSpecies="Homo sapiens", DatasetFileFormat="fastq",
               DatasetProcessingLevel="Level 1"),
            _r(2, DatasetCategory="image", DatasetSpecies="Homo sapiens", DatasetFileFormat="ome-tiff",
               DatasetProcessingLevel="Level 1")]
    d = _one(rows)[0]
    assert d["ProjectedSensitivity"] == "Registered"  # not Controlled
    assert d["RecommendedRepository"] != "dbGaP"
    assert "fastq" in d["FileFormats"]
    assert ("Format conflict: 1 row(s) (L1): sequencing format on an imaging dataset; "
            "format ignored for sensitivity/repository") in d["ReviewFlags"]
    # same format on a non-imaging category is not a conflict
    d2 = _one([_r(1, DatasetCategory="aligned reads", DatasetSpecies="Homo sapiens", DatasetFileFormat="bam")])[0]
    assert "Format conflict" not in d2["ReviewFlags"] and d2["ProjectedSensitivity"] == "Controlled"


def test_computational_assay_not_controlled():
    base = dict(DatasetCategory="text data", DatasetSpecies="Homo sapiens")
    d = _one([_r(1, DatasetAssay="in silico synthesis", **base)])[0]
    assert d["ProjectedSensitivity"] == "Registered"
    assert "computational assay" in d["SensitivityRationale"]
    d = _one([_r(1, DatasetAssay="survey", **base)])[0]
    assert d["ProjectedSensitivity"] == "Controlled"
    # other rules still apply: code format -> Open
    d = _one([_r(1, DatasetAssay="in silico synthesis", DatasetFileFormat="Python script", **base)])[0]
    assert d["ProjectedSensitivity"] == "Open"


def test_open_category_regardless_of_species():
    for sp in ("Homo sapiens", "", "Mus musculus"):
        d = _one([_r(1, DatasetCategory="Data index", DatasetSpecies=sp)])[0]
        assert d["ProjectedSensitivity"] == "Open"
        assert "unknown species" not in d["ReviewFlags"]


def test_needs_input_public_archive():
    d = _one([_r(1, DatasetCategory="gene expression", DatasetSpecies="Homo sapiens", DatasetFileFormat="csv",
                 DatasetProcessingLevel="Level 3")])[0]
    assert d["RecommendedRepository"] == "GEO" and d["ProjectedSensitivity"] == "Registered"
    assert ("GEO is fully public; confirm donor consent allows open release, "
            "otherwise Synapse (registered access)") in d["NeedsInput"]
    # Open tier or non-public repo: no entry
    d = _one([_r(1, DatasetCategory="gene expression", DatasetSpecies="Mus musculus", DatasetFileFormat="csv")])[0]
    assert d["NeedsInput"] == ""
    d = _one([_r(1, DatasetCategory="x", DatasetSpecies="Homo sapiens")])[0]
    assert d["NeedsInput"] == ""


def test_needs_input_consent_pattern_and_snippet():
    long = "a" * 80 + " requires IRB approval " + "b" * 80
    rows = [_r(1, DatasetDescription="fine"), _r(2, Comments=long), _r(3, ContextOfUse='["Restricted access only"]')]
    ni = _one(rows)[0]["NeedsInput"]
    assert "free text mentions consent/access terms (2 row(s), e.g. L2: \"\u2026" in ni
    snippet = ni.split('e.g. L2: "')[1].rstrip('")')
    assert "IRB" in snippet and len(snippet.strip("\u2026")) <= 100
    assert "\u2026" in snippet


def test_needs_input_deidentified_only_for_controlled():
    txt = dict(DatasetDescription="Data are De-identified before sharing")
    ctrl = _one([_r(1, DatasetCategory="clinical", DatasetSpecies="Homo sapiens", **txt)])[0]
    assert "text says de-identified; Controlled retained, lower only with explicit approval (1 row(s), e.g. L1:" in ctrl["NeedsInput"]
    reg = _one([_r(1, DatasetCategory="x", DatasetSpecies="Homo sapiens", **txt)])[0]
    assert reg["ProjectedSensitivity"] == "Registered" and "de-identified" not in reg["NeedsInput"]


def test_needs_input_code_model_only_gating():
    code = _r(1, DatasetCategory="model", DatasetFileFormat="Python script", DatasetDescription="trained on patient cohort")
    msg = "code/model may embed participant-derived data"
    assert msg in _one([code])[0]["NeedsInput"]
    data = _r(1, DatasetCategory="x", DatasetSpecies="Mus musculus", DatasetDescription="patient-derived xenograft")
    assert msg not in _one([data])[0]["NeedsInput"]
    # a data row in the same category does not count toward the code/model match
    mixed = _one([code, _r(2, DatasetCategory="model", DatasetFileFormat="csv", DatasetSpecies="Mus musculus",
                           DatasetDescription="donor tissue")])[0]
    assert "(1 row(s), e.g. L1:" in mixed["NeedsInput"]


def test_needs_input_entries_joined_and_in_packet(tmp_path):
    rows = [_r(1, DatasetCategory="gene expression", DatasetSpecies="Homo sapiens", DatasetFileFormat="csv",
               DatasetProcessingLevel="Level 3", Comments="needs consent")]
    d = _one(rows)[0]
    assert d["NeedsInput"].count("; ") >= 1 and d["NeedsInput"].startswith("GEO is fully public")
    out = tmp_path / "o"
    sl.write_outputs([d], 1, out)
    pk = json.loads((out / "review_packet.json").read_text())
    assert pk["data_types"][0]["row"]["NeedsInput"] == d["NeedsInput"]


def test_output_column_order_needs_input_after_review_flags():
    assert sl.OUTPUT_COLUMNS[-2:] == ["ReviewFlags", "NeedsInput"]


# ---------------------------------------------------------------- long-format candidate mapping
LONG_HEADER = ("Assay,ProcessingLevel,Namespace,Organization,SchemaName,SchemaVersion,SchemaURI,"
               "IsPreferred,IsFallback,MatchBasis,Rationale\n")


def _long(tmp_path, *rows):
    m = tmp_path / "cand.csv"
    m.write_text(LONG_HEADER + "".join(",".join(r) + "\n" for r in rows))
    return sl.load_mapping(m)


def _c(assay, level, ns, name, ver, pref, fb="No"):
    return (assay, level, ns, "Org", name, ver, f"{ns}-{name}-{ver}", pref, fb, "x", "t")


def _lvl(mapping, level, assay="Foo", category="x"):
    rows = [_r(1, DatasetAssay=assay, DatasetCategory=category, DatasetProcessingLevel=level)]
    return _one(rows, mapping=mapping)[0]


def test_level_specific_preferred_beats_any(tmp_path):
    m = _long(tmp_path,
              _c("Foo", "Any", "MC2", "GenericT", "1.0.0", "Yes"),
              _c("Foo", "Level 1", "HTAN2", "L1T", "1.2.0", "Yes"),
              _c("Foo", "Level 1", "NF", "L1Alt", "2.0.0", "No"))
    d = _lvl(m, "Level 1")
    assert d["MetadataTypes"] == "HTAN2:L1T v1.2.0"
    assert d["MetadataCandidates"] == "MC2:GenericT v1.0.0; NF:L1Alt v2.0.0"  # Any rows demoted to candidates
    assert d["UnmappedAssays"] == ""


def test_falls_back_to_any_without_level_rows(tmp_path):
    m = _long(tmp_path,
              _c("Foo", "Any", "MC2", "GenericT", "1.0.0", "Yes"),
              _c("Foo", "Level 1", "HTAN2", "L1T", "1.2.0", "Yes"))
    d = _lvl(m, "Level 3")
    assert d["MetadataTypes"] == "MC2:GenericT v1.0.0"
    assert d["MetadataCandidates"] == ""


def test_auxiliary_and_other_non_numbered_levels_use_any(tmp_path):
    m = _long(tmp_path,
              _c("Foo", "Any", "MC2", "GenericT", "1.0.0", "Yes"),
              _c("Foo", "Level 1", "HTAN2", "L1T", "1.2.0", "Yes"))
    for lv in ("Auxiliary", "Metadata", "Not Applicable", ""):
        assert _lvl(m, lv)["MetadataTypes"] == "MC2:GenericT v1.0.0"


def test_assays_resolve_independently_per_level(tmp_path):
    m = _long(tmp_path,
              _c("Foo", "Any", "MC2", "FooAny", "1.0.0", "Yes"),
              _c("Foo", "Level 2", "HTAN2", "FooL2", "1.0.0", "Yes"),
              _c("Bar", "Any", "NF", "BarAny", "3.0.0", "Yes"))
    rows = [_r(1, DatasetAssay="Foo", DatasetProcessingLevel="Level 2"),
            _r(2, DatasetAssay="Bar", DatasetProcessingLevel="Level 2")]
    d = _one(rows, mapping=m)[0]
    assert d["MetadataTypes"] == "HTAN2:FooL2 v1.0.0; NF:BarAny v3.0.0"


def test_candidates_content_order_dedup_and_fallback_suffix(tmp_path):
    m = _long(tmp_path,
              _c("Foo", "Any", "NF", "Zed", "1.0.0", "No"),
              _c("Foo", "Any", "ADKP", "Beta", "1.0.0", "No"),
              _c("Foo", "Any", "ADKP", "Alpha", "2.0.0", "No"),
              _c("Foo", "Any", "MC2", "Pref", "1.0.0", "Yes", "Yes"),
              _c("Bar", "Any", "ADKP", "Alpha", "2.0.0", "No"),   # duplicate across assays
              _c("Bar", "Any", "HTAN2", "BarPref", "1.0.0", "Yes"))
    rows = [_r(1, DatasetAssay="Foo"), _r(2, DatasetAssay="Bar")]
    d = _one(rows, mapping=m)[0]
    assert d["MetadataTypes"] == "HTAN2:BarPref v1.0.0; MC2:Pref v1.0.0 (fallback)"
    assert d["MetadataCandidates"] == "ADKP:Alpha v2.0.0; ADKP:Beta v1.0.0; NF:Zed v1.0.0"


def test_unmapped_when_no_level_or_any_rows(tmp_path):
    m = _long(tmp_path, _c("Foo", "Level 1", "HTAN2", "L1T", "1.0.0", "Yes"))
    d = _lvl(m, "Level 3")
    assert d["MetadataTypes"] == "" and d["MetadataCandidates"] == ""
    assert d["UnmappedAssays"] == "Foo"
    assert "Unmapped assays: Foo" in d["ReviewFlags"]


def test_old_format_mapping_treated_as_any_preferred_no_version():
    m = sl.load_mapping(MAPPING)
    c = m["rnaseq"][0]
    assert (c["Level"], c["Namespace"], c["Version"], c["IsPreferred"]) == ("Any", "NF", "", True)
    d = _lvl(m, "Level 2", assay="RNA-seq")
    assert d["MetadataTypes"] == "NF:RNASeqTemplate" and d["MetadataCandidates"] == ""


def test_metadata_candidates_column_directly_after_metadata_types():
    i = sl.OUTPUT_COLUMNS.index("MetadataTypes")
    assert sl.OUTPUT_COLUMNS[i + 1] == "MetadataCandidates"


def test_default_mapping_is_candidates_file():
    assert sl.DEFAULT_MAPPING.name == "landscape_assay_schema_candidates.csv"


def test_schema_preferred_outright_not_also_listed_as_fallback(tmp_path):
    m = _long(tmp_path,
              _c("Foo", "Any", "MC2", "ImgT", "1.0.0", "Yes"),
              _c("Bar", "Any", "MC2", "ImgT", "1.0.0", "Yes", "Yes"))
    rows = [_r(1, DatasetAssay="Foo", DatasetCategory="x", DatasetProcessingLevel="Level 1"),
            _r(2, DatasetAssay="Bar", DatasetCategory="x", DatasetProcessingLevel="Level 1")]
    assert _one(rows, mapping=m)[0]["MetadataTypes"] == "MC2:ImgT v1.0.0"


# ---------------------------------------------------------------- split, dates, canonical formats, compact
FORMATS_CSV = SKILL / "references" / "canonical_formats.csv"
FORMATS = sl.load_formats(FORMATS_CSV)
COMPACT_HEADER = ["DataType", "ProcessingLevel", "Assays", "FileFormats", "PreferredFileFormat",
                  "RecommendedRepository", "Priority", "CategoryStudyCount", "StudyCount", "DatasetCount",
                  "Species", "EarliestAvailableDate", "ExpectedDataVolume"]


def _sum(rows, formats=None):
    return {(d["DataType"], d["ProcessingLevel"]): d for d in sl.summarize(rows, {}, RULES, None, formats)[0]}


def test_rules_assay_resolution():
    ar = DEFAULT_RULES["assay_resolution"]
    assert ar["single-cell"] == ["single-cell RNA-seq", "single cell ATAC-seq", "scCGI-seq"]
    assert ar["single-nucleus"] == ["single-nucleus RNA-seq", "single-nucleus ATAC-seq"]


def test_split_datatype_naming_and_per_datatype_priority():
    rows = [
        _r(1, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 3"),
        _r(2, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 3"),
        _r(3, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 3"),
        _r(3, DatasetCategory="gene expression", DatasetAssay="Single cell RNA seq", DatasetProcessingLevel="Level 3"),
        _r(4, DatasetCategory="chromatin activity", DatasetAssay="single-nucleus ATAC-seq", DatasetProcessingLevel="Level 3"),
    ]
    s = _sum(rows)
    assert set(s) == {("gene expression", "Level 3"), ("gene expression (single-cell)", "Level 3"),
                      ("chromatin activity (single-nucleus)", "Level 3")}
    bulk, sc = s["gene expression", "Level 3"], s["gene expression (single-cell)", "Level 3"]
    assert bulk["Assays"] == "RNA-seq" and sc["Assays"] == "Single cell RNA seq"
    assert bulk["CategoryStudyCount"] == 3 and sc["CategoryStudyCount"] == 1
    assert bulk["Priority"] == "High" and sc["Priority"] == "Low"


def test_rules_key_on_category_not_datatype_label():
    # genomic variants is cap-exempt; the split label must not defeat that
    rows = [_r(1, DatasetCategory="genomic variants", DatasetAssay="single-cell RNA-seq", DatasetSpecies="Homo sapiens",
               DatasetProcessingLevel="Level 3", DatasetFileFormat="vcf"),
            _r(2, DatasetCategory="gene expression", DatasetAssay="single-cell RNA-seq", DatasetSpecies="Homo sapiens",
               DatasetProcessingLevel="Level 3", DatasetFileFormat="h5ad")]
    s = _sum(rows)
    g = s["genomic variants (single-cell)", "Level 3"]
    assert g["ProjectedSensitivity"] == "Controlled" and g["NeedsInput"] == ""
    assert g["RecommendedRepository"] == "dbGaP"
    assert s["gene expression (single-cell)", "Level 3"]["ProjectedSensitivity"] == "Registered"
    # Unknown exclusion from High keys on the category too
    prio, _ = sl.assign_priority({"Unknown (single-cell)": 4, "a": 4}, 4, RULES, {"Unknown (single-cell)": "Unknown"})
    assert prio["Unknown (single-cell)"] == "Medium"


@pytest.mark.parametrize("text,iso", [
    ("2025-03-07", "2025-03-07"), ("2025/03/07", "2025-03-07"), ("03/07/2025", "2025-03-07"),
    ("12/31/2025", "2025-12-31"), ("1741305600000", "2025-03-07"), ("  2025-03-07  ", "2025-03-07"),
    ("2025-03-07T10:20:30Z", "2025-03-07"), ("2025-03-07 10:20", "2025-03-07"),
])
def test_parse_date_formats(text, iso):
    assert sl.parse_date(text) == iso


@pytest.mark.parametrize("text", ["", "   ", "soon", "31/12/2025", "2025-13-01", "20250307", "12345"])
def test_parse_date_rejects(text):
    assert sl.parse_date(text) is None


def test_earliest_date_and_unparseable_flag():
    rows = [_r(1, UploadByDate="2026-05-01"), _r(2, UploadByDate="01/15/2025"), _r(3, UploadByDate="soon"),
            _r(4, UploadByDate=""), _r(5, UploadByDate="1741305600000")]
    d = _one(rows)[0]
    assert d["EarliestAvailableDate"] == "2025-01-15"
    assert "1 row(s) with unparseable UploadByDate" in d["ReviewFlags"]
    d2 = _one([_r(1, UploadByDate=""), _r(2, UploadByDate="never")])[0]
    assert d2["EarliestAvailableDate"] == "" and "unparseable" in d2["ReviewFlags"]
    assert _one([_r(1)])[0]["EarliestAvailableDate"] == ""
    assert "unparseable" not in _one([_r(1)])[0]["ReviewFlags"]


def _fmt_table(*rows):
    return [{"Assay": sl._key(a), "Category": sl.cf(c), "Level": lv, "Formats": f.split("; ")} for a, c, lv, f in rows]


def test_canonical_lookup_order():
    t = _fmt_table(("foo", "", "Level 3", "A"), ("foo", "", "Any", "B"), ("", "cat", "Level 3", "C"), ("", "Cat", "Any", "D"))
    assert sl.lookup_format(t, "foo", "CAT", "Level 3") == ["A"]
    assert sl.lookup_format(t, "foo", "cat", "Level 2") == ["B"]
    assert sl.lookup_format(t[2:], "foo", "cat", "Level 3") == ["C"]
    assert sl.lookup_format(t[2:], "foo", "cat", "Level 1") == ["D"]
    assert sl.lookup_format(t[2:], "foo", "other", "Level 1") == []
    # assay matched on alphanumeric key, through summarize
    d = _sum([_r(1, DatasetAssay="Foo-Bar", DatasetCategory="cat", DatasetProcessingLevel="Level 3", DatasetFileFormat="A")],
             _fmt_table(("foo bar", "", "Level 3", "A"), ("", "cat", "Any", "D")))
    assert d["cat", "Level 3"]["PreferredFileFormat"] == "A"


def test_preferred_union_cocanonical_and_category_fallback():
    t = _fmt_table(("a1", "", "Level 3", "h5ad; mtx"), ("a2", "", "Any", "csv"), ("", "x", "Any", "tsv"))
    rows = [_r(1, DatasetAssay="a1", DatasetProcessingLevel="Level 3", DatasetFileFormat="h5ad"),
            _r(2, DatasetAssay="a2", DatasetProcessingLevel="Level 3", DatasetFileFormat="h5ad"),
            _r(3, DatasetAssay="a3", DatasetProcessingLevel="Level 3", DatasetFileFormat="h5ad")]
    d = _sum(rows, t)["x", "Level 3"]
    assert d["PreferredFileFormat"] == "csv; h5ad; mtx; tsv"
    assert "No canonical format" not in d["ReviewFlags"]


def test_no_canonical_format_flag():
    t = _fmt_table(("a1", "", "Any", "csv"))
    d = _sum([_r(1, DatasetAssay="a1", DatasetProcessingLevel="Level 3", DatasetFileFormat="csv"),
              _r(2, DatasetAssay="a2", DatasetProcessingLevel="Level 3", DatasetFileFormat="csv")], t)["x", "Level 3"]
    assert d["PreferredFileFormat"] == "csv" and "No canonical format for: a2" in d["ReviewFlags"]
    d = _sum([_r(1, DatasetProcessingLevel="Level 3")], t)["x", "Level 3"]
    assert d["PreferredFileFormat"] == "" and "No canonical format for: x" in d["ReviewFlags"]
    # feature off without a table
    assert "canonical" not in _sum([_r(1)])["x", "Unspecified"]["ReviewFlags"]


def test_declared_vs_preferred_flag_and_exemptions():
    t = _fmt_table(("", "x", "Any", "csv; tsv"))
    def flags(level, fmt):
        d = _sum([_r(1, DatasetProcessingLevel=level, DatasetFileFormat=fmt)], t)
        return next(iter(d.values()))["ReviewFlags"]
    assert "Declared formats don't include preferred format (csv; tsv)" in flags("Level 3", "xlsx")
    assert "Declared formats don't include" not in flags("Level 3", "TSV")
    assert "Declared formats don't include" not in flags("Auxiliary", "xlsx")
    assert "Declared formats don't include" not in flags("Not Applicable", "xlsx")


def test_every_canonical_format_in_enum():
    meta = json.loads((SKILL / "references" / "file_format_enum.json").read_text())
    enum = set(meta["enum"])
    assert meta["schema"] == "NAMhub-Landscape" and enum
    for e in FORMATS:
        assert e["Formats"], e
        for f in e["Formats"]:
            assert f in enum, (e, f)


def test_canonical_file_shape_and_coverage():
    with open(FORMATS_CSV, newline="") as fh:
        r = csv.DictReader(fh)
        assert r.fieldnames == ["Assay", "Category", "ProcessingLevel", "PreferredFileFormat", "Rationale"]
        rows = list(r)
    assert all(bool(x["Assay"]) != bool(x["Category"]) and x["Rationale"] for x in rows)
    levels = {"Any", "Level 1", "Level 2", "Level 3", "Level 4", "Auxiliary", "Metadata", "Not Applicable"}
    assert {x["ProcessingLevel"] for x in rows} <= levels
    keys = [(x["Assay"], x["Category"], x["ProcessingLevel"]) for x in rows]
    assert len(keys) == len(set(keys))
    cand = SKILL.parents[2] / "landscape_assay_schema_candidates.csv"
    if cand.exists():  # every candidate assay has at least one entry
        with open(cand, newline="", encoding="utf-8-sig") as fh:
            for a in {x["Assay"] for x in csv.DictReader(fh)}:
                assert any(e["Assay"] == sl._key(a) for e in FORMATS), a
    # known guidance spot checks
    look = lambda a, c, lv: sl.lookup_format(FORMATS, sl._key(a), c, lv)
    assert look("RNA-seq", "x", "Level 3") == ["tsv"]
    assert look("single-cell RNA-seq", "x", "Level 3") == ["h5ad", "mtx"]
    assert look("flow cytometry", "x", "Level 1") == ["fcs"]
    assert look("unknown assay", "model", "Any") == ["Github Repo"]


def test_default_summary_has_preferred_and_date_columns_in_order(tmp_path):
    rows = [_r(1, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 1",
               DatasetFileFormat="fastq", UploadByDate="2025-02-01")]
    d = sl.summarize(rows, {}, RULES, None, FORMATS)[0]
    out = tmp_path / "o"
    sl.write_outputs(d, 1, out)
    with open(out / "landscape_summary.csv", newline="") as fh:
        r = csv.reader(fh)
        h = next(r)
        row = dict(zip(h, next(r)))
    assert h.index("PreferredFileFormat") == h.index("FileFormats") + 1
    assert h.index("EarliestAvailableDate") == h.index("EstimatedFileCount") + 1
    assert row["PreferredFileFormat"] == "fastq" and row["EarliestAvailableDate"] == "2025-02-01"
    pk = json.loads((out / "review_packet.json").read_text())["data_types"][0]["row"]
    assert pk["PreferredFileFormat"] == "fastq" and pk["EarliestAvailableDate"] == "2025-02-01"


def test_compact_outputs_header_order_and_md(tmp_path):
    rows = [_r(1, DatasetCategory="gene expression", DatasetAssay="RNA-seq | x", DatasetProcessingLevel="Level 1",
               DatasetFileFormat="fastq", UploadByDate="2025-02-01"),
            _r(2, DatasetCategory="image", DatasetProcessingLevel="Level 2")]
    d = sl.summarize(rows, {}, RULES, None, FORMATS)[0]
    out = tmp_path / "o"
    sl.write_outputs(d, 2, out)
    with open(out / "landscape_summary_compact.csv", newline="") as fh:
        r = csv.reader(fh)
        assert next(r) == COMPACT_HEADER
        body = list(r)
    with open(out / "landscape_summary.csv", newline="") as fh:
        full = list(csv.DictReader(fh))
    assert [b[0:2] for b in body] == [[f["DataType"], f["ProcessingLevel"]] for f in full]
    md = (out / "landscape_summary_compact.md").read_text().splitlines()
    assert md[0] == "| " + " | ".join(COMPACT_HEADER) + " |"
    assert md[1].startswith("|---") and len(md) == 2 + len(full)
    assert "RNA-seq \\| x" in md[2]


def test_compact_from_reviewed_csv(tmp_path):
    reviewed = tmp_path / "reviewed.csv"
    cols = COMPACT_HEADER + ["ReviewNotes", "ProjectedSensitivity"]
    with open(reviewed, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow({"DataType": "image", "ProcessingLevel": "Level 1", "RecommendedRepository": "IDR | x",
                    "Priority": "High", "ReviewNotes": "changed", "ProjectedSensitivity": "Open"})
    out = tmp_path / "o"
    script = SKILL / "scripts" / "summarize_landscape.py"
    subprocess.run([sys.executable, str(script), "--compact-from", str(reviewed), "--out-dir", str(out)],
                   check=True, capture_output=True)
    with open(out / "landscape_summary_compact.csv", newline="") as fh:
        r = csv.reader(fh)
        assert next(r) == COMPACT_HEADER
        row = next(r)
    assert row[COMPACT_HEADER.index("RecommendedRepository")] == "IDR | x"
    assert "IDR \\| x" in (out / "landscape_summary_compact.md").read_text()
    assert not (out / "landscape_summary.csv").exists()
    bad = tmp_path / "bad.csv"
    bad.write_text("DataType\nx\n")
    assert subprocess.run([sys.executable, str(script), "--compact-from", str(bad), "--out-dir", str(out)],
                          capture_output=True).returncode != 0


def test_code_rows_prefer_code_format_not_assay_data_format(tmp_path):
    f = tmp_path / "fmt.csv"
    f.write_text("Assay,Category,ProcessingLevel,PreferredFileFormat,Rationale\n"
                 "immunofluorescence,,Any,ome-tiff,img\n"
                 ",model,Any,Github Repo,code\n")
    formats = sl.load_formats(f)
    rows = [_r(1, DatasetAssay="immunofluorescence", DatasetCategory="image",
               DatasetProcessingLevel="Auxiliary", DatasetFileFormat="Python script")]
    result, _ = sl.summarize(rows, {}, DEFAULT_RULES, formats=formats)
    assert result[0]["PreferredFileFormat"] == "Github Repo"
