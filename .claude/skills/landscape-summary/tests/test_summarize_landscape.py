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
    return {(d["DataType"], d["Assay"], d["ProcessingLevel"]): d for d in result}, total, rows


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
    assert ("clinical data", "clinical data", "Level 3") in s  # most common spelling wins
    assert len({k[0] for k in s if k[0].casefold() == "clinical data"}) == 1
    cd3, cdna = s["clinical data", "clinical data", "Level 3"], s["clinical data", "clinical data", "Not Applicable"]
    assert cd3["RowCount"] == 2 and cdna["RowCount"] == 1
    assert cd3["AssayStudyCount"] == cdna["AssayStudyCount"] == 3
    assert cd3["StudyCount"] == 2 and cdna["StudyCount"] == 1


def test_metadata_mapping_case_insensitive_fallback_unmapped(summary):
    s, _, _ = summary
    ge, zebra = s["gene expression", "RNA-seq", "Level 3"], s["gene expression", "Zebra assay", "Level 3"]
    # " rna-seq " (lowercase, padded) maps to RNA-seq; Zebra assay does not
    assert ge["MetadataTypes"] == "NF:RNASeqTemplate" and ge["UnmappedAssays"] == ""
    assert zebra["UnmappedAssays"] == "Zebra assay"
    assert "Unmapped assays" in zebra["ReviewFlags"]
    assert s["image", "Atomic force microscopy", "Level 1"]["MetadataTypes"] == "NF:MaterialScienceAssayTemplate (fallback)"
    assert s["proteomics", "Mass spectrometry", "Level 1"]["MetadataTypes"] == "NF:MassSpecAssayTemplate"


def test_file_count_all_units_and_skips(summary):
    s, _, _ = summary
    # L1: 10*2 (per sample); RNA-seq L3: ceil(10/5) (samples per file) + 4*1 + ceil(8/4); Zebra L3: 6*3
    assert s["gene expression", "RNA-seq", "Level 1"]["EstimatedFileCount"] == "20"
    assert s["gene expression", "RNA-seq", "Level 3"]["EstimatedFileCount"] == "8"
    assert s["gene expression", "Zebra assay", "Level 3"]["EstimatedFileCount"] == "18"
    assert s["image", "Atomic force microscopy", "Level 1"]["EstimatedFileCount"] == "3"
    assert s["image", "Unknown", "Level 2"]["EstimatedFileCount"] == "4"
    assert s["clinical data", "clinical data", "Level 3"]["EstimatedFileCount"] == "2"
    assert s["clinical data", "clinical data", "Not Applicable"]["EstimatedFileCount"] == "1"
    prot = s["proteomics", "Mass spectrometry", "Level 1"]
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
    ge1, ge3 = s["gene expression", "RNA-seq", "Level 1"], s["gene expression", "RNA-seq", "Level 3"]
    assert ge1["StudyCount"] == 1 and ge3["StudyCount"] == 3
    assert ge1["AssayStudyCount"] == ge3["AssayStudyCount"] == 3  # assay breadth (S1, S3, S4), shared by all its levels
    # RNA-seq L3: S1 G1, S3 D7, S4 D13
    assert ge3["DatasetCount"] == 3
    assert ge1["RowCount"] == 1 and ge3["RowCount"] == 3


def test_sensitivity_max_over_rows(summary):
    s, _, _ = summary
    ge1, ge3 = s["gene expression", "RNA-seq", "Level 1"], s["gene expression", "RNA-seq", "Level 3"]
    assert ge1["ProjectedSensitivity"] == "Controlled"  # human fastq Level 1 row
    assert "Controlled" in ge1["SensitivityRationale"] and "1 row(s)" in ge1["SensitivityRationale"]
    assert ge3["ProjectedSensitivity"] == "Registered"  # max over the Level 3 rows
    assert "unknown species" in ge3["ReviewFlags"]
    assert s["image", "Atomic force microscopy", "Level 1"]["ProjectedSensitivity"] == "Open"
    assert s["model", "Unknown", "Not Applicable"]["ProjectedSensitivity"] == "Open"
    assert s["Unknown", "Unknown", "Level 3"]["ProjectedSensitivity"] == "Open"


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
    ge1, ge3 = s["gene expression", "RNA-seq", "Level 1"], s["gene expression", "RNA-seq", "Level 3"]
    assert ge1["RecommendedRepository"] == "dbGaP" and ge1["OtherRepositories"] == ""
    assert ge3["RecommendedRepository"] == "GEO"  # follows the Registered rows
    assert "Mixed sensitivity" not in ge3["ReviewFlags"]  # the Open (mouse) row is a different assay now
    assert s["gene expression", "Zebra assay", "Level 3"]["ProjectedSensitivity"] == "Open"
    assert "3 row(s) -> GEO" in ge3["RepositoryRationale"]
    assert "NAMHub also hosts metadata" in ge3["RepositoryRationale"]
    cd = s["clinical data", "clinical data", "Not Applicable"]  # uncapped level
    assert cd["RecommendedRepository"] == "Synapse (controlled access)"
    assert "NAMHub also hosts" not in cd["RepositoryRationale"]
    # "clinical data" is not on the exempt list, so Level 3 is capped to Registered
    cd3 = s["clinical data", "clinical data", "Level 3"]
    assert cd3["ProjectedSensitivity"] == "Registered"
    assert "2 row(s) lowered Controlled -> Registered by Level 3" in cd3["NeedsInput"]
    assert s["image", "Atomic force microscopy", "Level 1"]["RecommendedRepository"] == "BioImage Archive"
    assert s["proteomics", "Mass spectrometry", "Level 1"]["RecommendedRepository"] == "PRIDE"
    assert s["model", "Unknown", "Not Applicable"]["RecommendedRepository"] == "GitHub + Zenodo"
    assert s["Unknown", "Unknown", "Level 3"]["RecommendedRepository"] == "Synapse (open)"


def test_priority_tiers(summary):
    s, _, _ = summary
    # Priority is per assay (AssayStudyCount): RNA-seq levels share it, the one-study Zebra assay is Low
    for k in (("RNA-seq", "Level 1"), ("RNA-seq", "Level 3")):
        assert s["gene expression", k[0], k[1]]["Priority"] == "High"
    assert s["gene expression", "Zebra assay", "Level 3"]["Priority"] == "Low"
    assert s["clinical data", "clinical data", "Level 3"]["Priority"] == "High"
    assert s["clinical data", "clinical data", "Not Applicable"]["Priority"] == "High"
    assert s["image", "Atomic force microscopy", "Level 1"]["Priority"] == s["image", "Unknown", "Level 2"]["Priority"] == "Low"
    assert s["proteomics", "Mass spectrometry", "Level 1"]["Priority"] == "Low"
    assert s["model", "Unknown", "Not Applicable"]["Priority"] == "Low"


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
    assert header == ["AssayType", "DataType", "ProcessingLevel", "Assay", "FileFormats", "QuestionedFormats", "PreferredFileFormat", "CanonicalFileFormat", "MetadataTypes", "MetadataCandidates", "UnmappedAssays",
                      "ProjectedSensitivity", "SensitivityRationale", "RecommendedRepository", "OtherRepositories",
                      "RepositoryRationale", "Priority", "AssayStudyCount", "StudyCount", "DatasetCount",
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
    # sort: priority, AssayStudyCount desc
    keys = [(order[r[header.index("Priority")]], -int(r[header.index("AssayStudyCount")])) for r in rows]
    assert keys == sorted(keys)
    assert header.index("QuestionedFormats") == header.index("FileFormats") + 1


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
    # single-cell / single-nucleus assays split into their own DataType rows
    assert set(res) == {"x", "x (single-cell)", "x (single-nucleus)"}
    assert res["x (single-cell)"]["MetadataTypes"] == "NF:AtacT"
    assert res["x (single-nucleus)"]["UnmappedAssays"] == "single-nucleus ATAC-seq"
    d = res["x"]
    assert d["MetadataTypes"] == "NF:SynT"
    # the two synthesis spellings collapse into one displayed assay
    assert "ilico" in d["Assay"].replace(" ", "").replace("-", "").lower() and d["RowCount"] == 2


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
    assert {d["AssayStudyCount"] for d in result} == {4}
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
    assert d["Assay"] == "pharmacokinetic ADME assay" and d["RowCount"] == 2 and d["MetadataTypes"] == "NF:PkT"
    assert d["UnmappedAssays"] == ""


def test_unspecified_assays_flagged_not_unmapped():
    rows = [_r(1, DatasetAssay="Unknown"), _r(2, DatasetAssay=""), _r(3, DatasetAssay="Zebra")]
    res = {d["Assay"]: d for d in _one(rows)}
    assert set(res) == {"Unknown", "Zebra"} and res["Unknown"]["RowCount"] == 2
    assert res["Zebra"]["UnmappedAssays"] == "Zebra" and res["Unknown"]["UnmappedAssays"] == ""
    assert "2 row(s) with no specific assay (Unknown/blank); set DatasetAssay" in res["Unknown"]["ReviewFlags"]


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
    res = {d["Assay"]: d for d in _one(rows, mapping=m)}
    assert res["Foo"]["MetadataTypes"] == "HTAN2:FooL2 v1.0.0" and res["Bar"]["MetadataTypes"] == "NF:BarAny v3.0.0"


def test_candidates_content_order_dedup_and_fallback_suffix(tmp_path):
    m = _long(tmp_path,
              _c("Foo", "Any", "NF", "Zed", "1.0.0", "No"),
              _c("Foo", "Any", "ADKP", "Beta", "1.0.0", "No"),
              _c("Foo", "Any", "ADKP", "Alpha", "2.0.0", "No"),
              _c("Foo", "Any", "MC2", "Pref", "1.0.0", "Yes", "Yes"),
              _c("Bar", "Any", "ADKP", "Alpha", "2.0.0", "No"),   # duplicate across assays
              _c("Bar", "Any", "HTAN2", "BarPref", "1.0.0", "Yes"))
    res = {d["Assay"]: d for d in _one([_r(1, DatasetAssay="Foo"), _r(2, DatasetAssay="Bar")], mapping=m)}
    assert res["Bar"]["MetadataTypes"] == "HTAN2:BarPref v1.0.0" and res["Bar"]["MetadataCandidates"] == "ADKP:Alpha v2.0.0"
    assert res["Foo"]["MetadataTypes"] == "MC2:Pref v1.0.0 (fallback)"
    assert res["Foo"]["MetadataCandidates"] == "ADKP:Alpha v2.0.0; ADKP:Beta v1.0.0; NF:Zed v1.0.0"


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
    # one row per assay now, so the fallback label is judged within each assay's own row
    res = {d["Assay"]: d["MetadataTypes"] for d in _one(rows, mapping=m)}
    assert res == {"Foo": "MC2:ImgT v1.0.0", "Bar": "MC2:ImgT v1.0.0 (fallback)"}


# ---------------------------------------------------------------- split, dates, canonical formats, compact
FORMATS_CSV = SKILL / "references" / "canonical_formats.csv"
FORMATS = sl.load_formats(FORMATS_CSV)
FLR = json.loads((SKILL / "references" / "format_level_rules.json").read_text())
AT = sl.load_assay_types(SKILL / "references" / "assay_types.json")
COMPACT_HEADER = ["Essential/key data?", "AssayType", "DataType", "Assay", "ProcessingLevel", "Formats", "PreferredFormat",
                  "Priority", "AssayStudyCount", "StudyCount", "DatasetCount", "Species", "EarliestAvailableDate",
                  "ExpectedDataVolume"]


def _sum(rows, formats=None):
    return {(d["DataType"], d["ProcessingLevel"]): d for d in sl.summarize(rows, {}, RULES, None, formats)[0]}


def _sum3(rows, formats=None):
    return {(d["DataType"], d["Assay"], d["ProcessingLevel"]): d for d in sl.summarize(rows, {}, RULES, None, formats)[0]}


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
    assert bulk["Assay"] == "RNA-seq" and sc["Assay"] == "Single cell RNA seq"
    assert bulk["AssayStudyCount"] == 3 and sc["AssayStudyCount"] == 1
    assert bulk["Priority"] == "High" and sc["Priority"] == "Low"


def test_rules_key_on_category_not_datatype_label():
    # genomic variants is cap-exempt; the split label must not defeat that
    rows = [_r(1, DatasetCategory="genomic variants", DatasetAssay="single-cell RNA-seq", DatasetSpecies="Homo sapiens",
               DatasetProcessingLevel="Level 3", DatasetFileFormat="vcf"),
            _r(2, DatasetCategory="gene expression", DatasetAssay="single-nucleus RNA-seq", DatasetSpecies="Homo sapiens",
               DatasetProcessingLevel="Level 3", DatasetFileFormat="h5ad")]
    s = _sum(rows)
    g = s["genomic variants (single-cell)", "Level 3"]
    assert g["ProjectedSensitivity"] == "Controlled" and g["NeedsInput"] == ""
    assert g["RecommendedRepository"] == "dbGaP"
    assert s["gene expression (single-nucleus)", "Level 3"]["ProjectedSensitivity"] == "Registered"
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
    s = _sum3(rows, t)  # one row per assay: assay entry (level-specific or Any), else the category entry
    assert s["x", "a1", "Level 3"]["CanonicalFileFormat"] == "h5ad; mtx"
    assert s["x", "a2", "Level 3"]["CanonicalFileFormat"] == "csv"
    assert s["x", "a3", "Level 3"]["CanonicalFileFormat"] == "tsv"
    assert all("No canonical format" not in d["ReviewFlags"] for d in s.values())
    assert s["x", "a1", "Level 3"]["PreferredFileFormat"] == "h5ad"  # h5ad declared; mtx has no family and is not declared
    assert s["x", "a2", "Level 3"]["PreferredFileFormat"] == ""  # hdf5 family does not ground csv


def test_no_canonical_format_flag():
    t = _fmt_table(("a1", "", "Any", "csv"))
    s = _sum3([_r(1, DatasetAssay="a1", DatasetProcessingLevel="Level 3", DatasetFileFormat="csv"),
               _r(2, DatasetAssay="a2", DatasetProcessingLevel="Level 3", DatasetFileFormat="csv")], t)
    assert s["x", "a1", "Level 3"]["PreferredFileFormat"] == "csv"
    d = s["x", "a2", "Level 3"]
    assert d["PreferredFileFormat"] == "" and "No canonical format for: a2" in d["ReviewFlags"]
    d = _sum([_r(1, DatasetProcessingLevel="Level 3")], t)["x", "Level 3"]
    assert d["PreferredFileFormat"] == "" and "No canonical format for: x" in d["ReviewFlags"]
    # feature off without a table
    assert "canonical" not in _sum([_r(1)])["x", "Unspecified"]["ReviewFlags"]


def test_grounded_preferred_format_flag_and_exemptions():
    t = _fmt_table(("", "x", "Any", "csv; tsv"))
    def one(level, fmt):
        d = _sum([_r(1, DatasetProcessingLevel=level, DatasetFileFormat=fmt)], t)
        return next(iter(d.values()))
    d = one("Level 3", "xlsx")  # excel is tabular, but xlsx is not an enum spelling: no family -> not grounded
    assert d["CanonicalFileFormat"] == "csv; tsv" and d["PreferredFileFormat"] == ""
    assert "Canonical format csv; tsv not grounded in declared formats (xlsx)" in d["ReviewFlags"]
    assert "Declared formats don't include" not in d["ReviewFlags"]
    assert one("Level 3", "TSV")["PreferredFileFormat"] == "csv; tsv"  # same format (case-insensitive)
    assert one("Level 3", "txt")["PreferredFileFormat"] == "csv; tsv"  # same family (tabular)
    assert one("Level 3", "excel")["PreferredFileFormat"] == "csv; tsv"
    d = one("Level 3", "h5")
    assert d["PreferredFileFormat"] == "" and "not grounded in declared formats (h5)" in d["ReviewFlags"]
    # no declared formats: blank, and nothing to ground against so no flag
    d = one("Level 3", "")
    assert d["PreferredFileFormat"] == "" and d["CanonicalFileFormat"] == "csv; tsv" and "not grounded" not in d["ReviewFlags"]
    # grounding applies at Auxiliary / Not Applicable too
    for lv in ("Auxiliary", "Not Applicable"):
        assert one(lv, "xlsx")["PreferredFileFormat"] == ""
        assert one(lv, "txt")["PreferredFileFormat"] == "csv; tsv"


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
    assert h.index("PreferredFileFormat") == h.index("QuestionedFormats") + 1
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
    h = COMPACT_HEADER
    assert all(b[h.index("Essential/key data?")] == "" for b in body)
    # RNA-seq (Transcriptomics) comes before the unmatched image category/assay
    assert [b[h.index("DataType")] for b in body] == ["gene expression", "image"]
    assert body[0][h.index("ProcessingLevel")] == "Level 1/Raw data" and body[0][h.index("Formats")] == "fastq"
    md = (out / "landscape_summary_compact.md").read_text().splitlines()
    assert md[0] == "| " + " | ".join(COMPACT_HEADER) + " |"
    assert md[1].startswith("|---") and len(md) == 2 + len(body)
    assert "RNA-seq \\| x" in md[2]


def _row(**k):
    return {**{c: "" for c in sl.OUTPUT_COLUMNS}, **k}


def _compact(rows, **kw):
    out, n_ex, n_sc = sl.build_compact(rows, RULES, FORMATS, FLR, AT)
    return out, n_ex, n_sc


def test_compact_level_labels_exclusions_ordering_and_scaffold():
    mk = lambda at, dt, assay, lv, **k: _row(AssayType=at, DataType=dt, Assay=assay, ProcessingLevel=lv, **k)
    rows = [
        mk("Imaging", "image", "histology", "Level 3", Priority="High", StudyCount=2),
        mk("Transcriptomics (Bulk)", "gene expression", "RNA-seq", "Level 3", PreferredFileFormat="tsv"),
        mk("Transcriptomics (Bulk)", "gene expression", "RNA-seq", "Level 1"),
        mk("Transcriptomics (Bulk)", "gene expression", "RNA-seq", "Metadata"),
        mk("Transcriptomics (Bulk)", "gene expression", "RNA-seq", "Auxiliary"),
        mk("Unknown", "clinical", "Unknown", "Level 1"),
        mk("Unknown", "data index", "Unknown", "Level 1"),
        mk("zzz assay", "zzz", "zzz assay", "Level 2"),
    ]
    out, n_ex, n_sc = _compact(rows)
    assert (n_ex, n_sc) == (3, 1)  # Metadata level, clinical, data index; scaffold: RNA-seq Level 2
    assert [(r["AssayType"], r["ProcessingLevel"]) for r in out] == [
        ("Transcriptomics (Bulk)", "Level 1/Raw data"),
        ("Transcriptomics (Bulk)", "Level 2/Minimally processed or QC'd"),
        ("Transcriptomics (Bulk)", "Level 3/Processed, extracted, or summarized"),
        ("Transcriptomics (Bulk)", "Auxiliary"),
        ("Imaging", "Level 3/Processed, extracted, or summarized"),
        ("zzz assay", "Level 2/Minimally processed or QC'd")]
    sc = out[1]  # scaffold row: only identity columns and the canonical format
    assert sc["PreferredFormat"] == "bam" and sc["Priority"] == sc["StudyCount"] == sc["Formats"] == ""
    assert all(r["Essential/key data?"] == "" for r in out)
    # imaging is not a sequencing assay: no scaffold for its missing levels
    assert sum(1 for r in out if r["AssayType"] == "Imaging") == 1


def test_compact_scaffold_needs_a_declared_level_and_stops_at_highest():
    mk = lambda assay, lv: _row(AssayType="Transcriptomics (Bulk)", DataType="gene expression", Assay=assay, ProcessingLevel=lv)
    out, _, n_sc = _compact([mk("RNA-seq", "Level 2")])
    assert n_sc == 1 and [r["ProcessingLevel"][:7] for r in out] == ["Level 1", "Level 2"]
    assert _compact([mk("RNA-seq", "Level 4")])[2] == 0  # Level 4 does not drive scaffolds
    assert _compact([mk("RNA-seq", "Auxiliary")])[2] == 0


def test_compact_from_reviewed_csv(tmp_path):
    reviewed = tmp_path / "reviewed.csv"
    cols = sl.OUTPUT_COLUMNS + ["ReviewNotes"]
    with open(reviewed, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow(_row(AssayType="Imaging", DataType="image", Assay="histology", ProcessingLevel="Level 1",
                        FileFormats="tiff | x", Priority="High", ReviewNotes="changed", ProjectedSensitivity="Open"))
        w.writerow(_row(AssayType="Transcriptomics (Bulk)", DataType="gene expression", Assay="RNA-seq",
                        ProcessingLevel="Level 3", FileFormats="tsv", Priority="High"))
    out = tmp_path / "o"
    script = SKILL / "scripts" / "summarize_landscape.py"
    subprocess.run([sys.executable, str(script), "--compact-from", str(reviewed), "--out-dir", str(out)],
                   check=True, capture_output=True)
    with open(out / "landscape_summary_compact.csv", newline="") as fh:
        r = csv.reader(fh)
        assert next(r) == COMPACT_HEADER
        rows = list(r)
    # RNA-seq first (assay-type order) and its Level 1/2 scaffold rows, then imaging
    assert [x[COMPACT_HEADER.index("AssayType")] for x in rows] == ["Transcriptomics (Bulk)"] * 3 + ["Imaging"]
    assert rows[-1][COMPACT_HEADER.index("Formats")] == "tiff | x"
    assert "tiff \\| x" in (out / "landscape_summary_compact.md").read_text()
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


# ---------------------------------------------------------------- assay split, code level, questioned formats
def test_split_by_assay_breadth_is_per_assay():
    rows = [_r(1, DatasetCategory="x", DatasetAssay="alpha", DatasetProcessingLevel="Level 3"),
            _r(2, DatasetCategory="x", DatasetAssay="Beta", DatasetProcessingLevel="Level 3"),
            _r(3, DatasetCategory="x", DatasetAssay="alpha", DatasetProcessingLevel="Level 3")]
    s = _sum3(rows)
    assert set(s) == {("x", "alpha", "Level 3"), ("x", "Beta", "Level 3")}
    a, b = s["x", "alpha", "Level 3"], s["x", "Beta", "Level 3"]
    assert (a["RowCount"], a["StudyCount"], b["RowCount"], b["StudyCount"]) == (2, 2, 1, 1)
    # Priority follows each assay's own breadth (AssayStudyCount), not the category's
    assert (a["AssayStudyCount"], b["AssayStudyCount"]) == (2, 1) and (a["Priority"], b["Priority"]) == ("High", "Low")
    assert "Assays" not in a


def test_code_model_rows_merge_at_code_model_level():
    assert RULES["code_model_level"] == "Not Applicable"
    rows = [_r(1, DatasetCategory="model", DatasetProcessingLevel="Auxiliary", DatasetFileFormat="Python script"),
            _r(2, DatasetCategory="model", DatasetProcessingLevel="", DatasetFileFormat=""),
            _r(3, DatasetCategory="model", DatasetProcessingLevel="Level 4", DatasetFileFormat="Github Repo"),
            # model category but data format: not code; other categories' Auxiliary code rows are untouched
            _r(4, DatasetCategory="model", DatasetProcessingLevel="Level 4", DatasetFileFormat="csv"),
            _r(5, DatasetCategory="image", DatasetProcessingLevel="Auxiliary", DatasetFileFormat="Python script")]
    s = _sum3(rows)
    assert s["model", "Unknown", "Not Applicable"]["RowCount"] == 3
    assert s["model", "Unknown", "Level 4"]["RowCount"] == 1
    assert ("image", "Unknown", "Auxiliary") in s and ("model", "Unknown", "Auxiliary") not in s


def test_questioned_tabular_formats_at_sequencing_level_1():
    mk = lambda i, assay, fmt, lv="Level 1": _r(i, DatasetCategory="gene expression", DatasetAssay=assay,
                                               DatasetProcessingLevel=lv, DatasetFileFormat=fmt)
    rows = [mk(1, "RNA-seq", "fastq"), mk(2, "RNA-seq", "csv"), mk(3, "RNA-seq", "csv"), mk(4, "RNA-seq", "tsv"),
            mk(5, "RNA-seq", "RData"), mk(6, "RNA-seq", "h5"),
            mk(7, "RNA-seq", "csv", "Level 3"),  # only Level 1 is questioned
            mk(8, "immunofluorescence", "csv")]  # not a sequencing assay
    s = _sum3(rows)
    d = s["gene expression", "RNA-seq", "Level 1"]
    assert d["FileFormats"] == "fastq; h5; RData"
    assert d["QuestionedFormats"] == "csv; tsv"
    assert "Format(s) not plausible as raw sequencing data: csv (2 row(s)); excluded from FileFormats" in d["ReviewFlags"]
    assert "tsv (1 row(s))" in d["ReviewFlags"]
    assert s["gene expression", "RNA-seq", "Level 3"]["FileFormats"] == "csv"
    img = s["gene expression", "immunofluorescence", "Level 1"]
    assert img["FileFormats"] == "csv" and img["QuestionedFormats"] == ""


def test_atac_level_3_preferred_is_bed():
    for a in ("ATAC-seq", "single cell ATAC-seq", "single-nucleus ATAC-seq"):
        assert sl.lookup_format(FORMATS, sl._key(a), "x", "Level 3") == ["bed"], a
    assert sl.lookup_format(FORMATS, "", "chromatin activity", "Level 3") == ["bed"]
    assert "bed" in json.loads((SKILL / "references" / "file_format_enum.json").read_text())["enum"]


def test_assay_type_matching_categories_and_fallback():
    m = lambda a, c="x": sl.match_assay_type(a, c, AT)
    assert m("rna-seq") == "Transcriptomics (Bulk)"
    assert m("Single-cell RNA-seq") == "Transcriptomics (Single-cell/Single-nucleus)"
    assert m("in silico synthesis", "model") == "In silico model"
    assert m("in silico synthesis", "Model") == "In silico model"
    assert m("in silico synthesis", "image") == "In silico assay"
    assert m("Weird assay") == "Weird assay"
    names = [t["name"] for t in AT["types"]]
    assert names.index("Spatial Chromatin profiling") == names.index("Chromatin profiling (Single-cell/Single-nucleus)") + 1
    key = lambda n: sl.assay_type_sort_key(n, AT)
    assert key("ELISA") < key("Luminex") < key("Aardvark assay") < key("Zebra assay")
    # AssayType column in the summary
    rows = [_r(1, DatasetCategory="model", DatasetAssay="in silico synthesis", DatasetProcessingLevel="Level 3",
               DatasetFileFormat="csv"),
            _r(2, DatasetCategory="x", DatasetAssay="in silico synthesis", DatasetProcessingLevel="Level 3"),
            _r(3, DatasetCategory="x", DatasetAssay="Weird", DatasetProcessingLevel="Level 3")]
    s = _sum3(rows)
    assert s["model", "in silico synthesis", "Level 3"]["AssayType"] == "In silico model"
    assert s["x", "in silico synthesis", "Level 3"]["AssayType"] == "In silico assay"
    assert s["x", "Weird", "Level 3"]["AssayType"] == "Weird"


def test_assay_types_json_shape():
    d = json.loads((SKILL / "references" / "assay_types.json").read_text())
    assert d["columns"][3] == "Level 2/Minimally processed or QC'd description and file format"
    assert len(d["columns"]) == 8 and d["columns"][0] == "Assay Category" and d["columns"][7] == "Established repository name"
    assert [t["name"] for t in d["assay_types"]][:3] == ["Transcriptomics (Bulk)",
                                                          "Transcriptomics (Single-cell/Single-nucleus)", "Spatial Transcriptomics"]
    assert d["assay_types"][-2]["categories"] == ["model"]


# ---------------------------------------------------------------- assay type summary
def _sheet(rows, **kw):
    result, _ = sl.summarize(rows, {}, RULES, None, FORMATS)
    sheet, ev = sl.build_assay_type_summary(result, RULES, AT)
    return {r["Assay Type"]: r for r in sheet}, ev


def test_assay_type_summary_confirmed_only_and_evidence():
    mk = lambda i, fmt, lv, **k: _r(i, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel=lv,
                                    DatasetFileFormat=fmt, **k)
    filled = mk(5, "bam", "Level 2")
    filled["_fills"] = [{"LandscapeId": "L5", "Field": "DatasetProcessingLevel", "Value": "Level 2", "Source": "old stated"}]
    rows = [mk(1, "fastq", "Level 1"), mk(2, "csv", "Level 1"),  # csv questioned at sequencing Level 1
            mk(3, "tsv", "Level 3"), mk(4, "tsv", "Level 3"), mk(6, "h5ad", "Level 3"),
            mk(7, "mtx", ""),  # Unspecified level: never fills a level column
            filled,  # level came from an old-manifest fill
            mk(8, "xlsx", "Auxiliary"), mk(9, "tsv", "Auxiliary"),  # tsv already in Level 3: not repeated
            mk(10, "json", "Not Applicable"),
            mk(11, "pdf", "Metadata")]  # compact-excluded level
    sheet, ev = _sheet(rows)
    row = sheet["Transcriptomics (Bulk)"]
    assert list(row) == AT["columns"]
    c = AT["columns"]
    assert row["Assay Category"] == "gene expression" and row["Assay Type"] == "Transcriptomics (Bulk)"
    assert row[c[2]] == "fastq"            # csv questioned
    assert row[c[3]] == ""                 # the only Level 2 value came from a fill
    assert row[c[4]] == "tsv; h5ad"        # most studies first, then name
    assert row[c[5]] == ""
    assert row["Additional file types"] == "json; xlsx"
    assert "mtx" not in "".join(row.values()) and "pdf" not in "".join(row.values())
    assert row["Established repository name"] == ""
    status = {(e["Column"], e["Value"]): e["Status"] for e in ev}
    assert status[(c[2], "csv")] == "excluded: questioned format"
    assert status[("(no level)", "mtx")] == "excluded: level unspecified"
    assert status[(c[3], "bam")] == "excluded: level from old-manifest fill"
    assert status[("(no level)", "pdf")] == "excluded: compact exclusion (level: Metadata)"
    assert status[(c[2], "fastq")] == "confirmed"
    tsv = next(e for e in ev if e["Value"] == "tsv" and e["Column"] == c[4])
    assert tsv["StudyCount"] == 2 and tsv["LandscapeIds"] == "L3; L4"
    assert list(ev[0]) == sl.EVIDENCE_COLUMNS


def test_assay_type_summary_rows_order_and_empty_types():
    rows = [_r(1, DatasetCategory="x", DatasetAssay="Zed", DatasetProcessingLevel="Level 1", DatasetFileFormat="a"),
            _r(2, DatasetCategory="x", DatasetAssay="Aaa", DatasetProcessingLevel="Level 1", DatasetFileFormat="b"),
            _r(3, DatasetCategory="clinical", DatasetAssay="Clin", DatasetProcessingLevel="Level 1", DatasetFileFormat="c")]
    sheet, ev = _sheet(rows)
    names = list(sheet)
    assert names[:len(AT["types"])] == [t["name"] for t in AT["types"]]
    assert names[len(AT["types"]):] == ["Aaa", "Zed"]  # unmatched sorted last; clinical is compact-excluded
    blank = sheet["Spatial Chromatin profiling"]
    assert all(v == "" for k, v in blank.items() if k != "Assay Type")
    assert any(e["AssayType"] == "Clin" and e["Status"] == "excluded: compact exclusion (category: clinical)" for e in ev)


def test_assay_type_summary_repository_from_text_only_and_pmid_ignored():
    rows = [_r(1, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 1",
               DatasetFileFormat="fastq", Comments="deposited as GSE12345 (see PMID: 31337)"),
            _r(2, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 1",
               DatasetFileFormat="fastq", DatasetDescription="raw in SRP000001 and PRJEB1234, models syn1234567; GSE67890"),
            _r(3, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 1",
               DatasetFileFormat="fastq", Comments="PMID:99999 only")]
    sheet, ev = _sheet(rows)
    assert sheet["Transcriptomics (Bulk)"]["Established repository name"] == \
        "ENA (PRJEB1234); GEO (GSE12345, GSE67890); SRA (SRP000001); Synapse (syn1234567)"
    assert "PMID" not in sheet["Transcriptomics (Bulk)"]["Established repository name"]
    # nothing is projected from the repository rules for a type with no accession
    assert sheet["Imaging"]["Established repository name"] == ""
    assert sl.row_accessions(_mk(Comments="10.5281/zenodo.123456 and ST000123, PXD000001, MTBLS5, EGAS0001, phs000123.v1, GPL570")) == [
        ("Zenodo", "zenodo.123456"), ("Metabolomics Workbench", "ST000123"), ("PRIDE", "PXD000001"),
        ("MetaboLights", "MTBLS5"), ("EGA", "EGAS0001"), ("dbGaP", "phs000123.v1"), ("GEO", "GPL570")]


def test_assay_type_outputs_written(tmp_path):
    out = tmp_path / "o"
    rows = [_r(1, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 1",
               DatasetFileFormat="fastq", Comments="a | b")]
    d = sl.summarize(rows, {}, RULES, None, FORMATS)[0]
    sl.write_outputs(d, 1, out)
    with open(out / "assay_type_summary.csv", newline="") as fh:
        r = csv.reader(fh)
        assert next(r) == AT["columns"]
        assert len(list(r)) == len(AT["types"])
    md = (out / "assay_type_summary.md").read_text().splitlines()
    assert md[0] == "| " + " | ".join(AT["columns"]) + " |" and len(md) == 2 + len(AT["types"])
    with open(out / "assay_type_evidence.csv", newline="") as fh:
        assert next(csv.reader(fh)) == ["AssayType", "Column", "Value", "Status", "StudyCount", "LandscapeIds"]
    info_cols = sl.package_readme.COMPACT_HEADERS
    assert info_cols == sl.COMPACT_COLUMNS


# ---------------------------------------------------------------- merge across categories by assay
def test_categories_sharing_an_assay_merge_into_one_row():
    rows = [_r(1, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 3"),
            _r(2, DatasetCategory="count matrix", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 3"),
            _r(3, DatasetCategory="count matrix", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 1"),
            _r(4, DatasetCategory="gene expression", DatasetAssay="single-cell RNA-seq", DatasetProcessingLevel="Level 3"),
            _r(5, DatasetCategory="count matrix", DatasetAssay="single-cell RNA-seq", DatasetProcessingLevel="Level 3")]
    s = _sum3(rows)
    assert set(s) == {("count matrix; gene expression", "RNA-seq", "Level 3"), ("count matrix", "RNA-seq", "Level 1"),
                      ("count matrix; gene expression (single-cell)", "single-cell RNA-seq", "Level 3")}
    m = s["count matrix; gene expression", "RNA-seq", "Level 3"]
    assert m["RowCount"] == 2 and m["StudyCount"] == 2
    assert m["AssayStudyCount"] == s["count matrix", "RNA-seq", "Level 1"]["AssayStudyCount"] == 3  # S1, S2, S3 at any level
    assert m["Priority"] == s["count matrix", "RNA-seq", "Level 1"]["Priority"]
    assert "CategoryStudyCount" not in m


def test_merged_group_takes_max_sensitivity_with_mixed_flag():
    rows = [_r(1, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 1",
               DatasetSpecies="Homo sapiens", DatasetFileFormat="fastq"),
            _r(2, DatasetCategory="count matrix", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 1",
               DatasetSpecies="Mus musculus", DatasetFileFormat="fastq")]
    d = _sum3(rows)["count matrix; gene expression", "RNA-seq", "Level 1"]
    assert d["ProjectedSensitivity"] == "Controlled" and "Mixed sensitivity: 1 Controlled / 1 Open" in d["ReviewFlags"]


def test_computational_and_unspecified_assays_keep_category_in_key():
    rows = [_r(1, DatasetCategory="gene expression", DatasetAssay="in silico synthesis", DatasetProcessingLevel="Level 4"),
            _r(2, DatasetCategory="count matrix", DatasetAssay="in silico synthesis", DatasetProcessingLevel="Level 4"),
            _r(3, DatasetCategory="gene expression", DatasetAssay="RNA-seq", DatasetProcessingLevel="Level 4"),
            _r(4, DatasetCategory="gene expression", DatasetAssay="", DatasetProcessingLevel="Level 4"),
            _r(5, DatasetCategory="count matrix", DatasetAssay="Unknown", DatasetProcessingLevel="Level 4")]
    s = _sum3(rows)
    assert set(s) == {("gene expression", "in silico synthesis", "Level 4"), ("count matrix", "in silico synthesis", "Level 4"),
                      ("gene expression", "RNA-seq", "Level 4"), ("gene expression", "Unknown", "Level 4"),
                      ("count matrix", "Unknown", "Level 4")}
    # AssayStudyCount for the exceptions counts that assay within that category only
    assert s["gene expression", "in silico synthesis", "Level 4"]["AssayStudyCount"] == 1


def test_spatial_rows_need_platform_confirmation():
    rows = [_r(1, DatasetCategory="gene expression", DatasetAssay="spatial transcriptomics", DatasetProcessingLevel="Level 1")]
    d = _sum3(rows, FORMATS)["gene expression (spatial)", "spatial transcriptomics", "Level 1"]
    assert "Preferred format is platform-dependent; confirm platform" in d["NeedsInput"]
    assert sl.lookup_format(FORMATS, "spatialtranscriptomics", "x", "Level 1") == ["fastq"]
    assert sl.lookup_format(FORMATS, "spatialtranscriptomics", "x", "Level 2") == ["bam"]
    assert sl.lookup_format(FORMATS, "spatialtranscriptomics", "x", "Level 3") == ["h5ad"]
    assert all("platform" not in d["NeedsInput"] for d in _sum3([_r(1, DatasetAssay="RNA-seq")]).values())


def test_format_families_in_rules_exist_in_enum():
    enum = set(json.loads((SKILL / "references" / "file_format_enum.json").read_text())["enum"])
    fam = RULES["format_families"]
    assert {"tabular", "image", "hdf5", "sequence", "alignment", "code"} <= set(fam)
    # "Jupyter notebook" is a code_model format that the file-format enum does not list; every other member must exist
    assert all(f in enum for fs in fam.values() for f in fs if f != "Jupyter notebook")
    assert sl.format_grounded({"csv"}, ["txt"], {sl._key(f): n for n, fs in fam.items() for f in fs})


# ---------------------------------------------------------------- review fixes: subset grounding, ordering, questioned families, repositories
def test_grounded_preferred_is_the_grounded_subset():
    t = _fmt_table(("brightfield microscopy", "", "Level 3", "csv; ome-tiff"), ("", "text data", "Any", "csv; Github Repo"),
                   ("", "electrophysiology", "Any", "csv; Github Repo"))
    mk = lambda cat, assay, lv, fmt: _r(1, DatasetCategory=cat, DatasetAssay=assay, DatasetProcessingLevel=lv, DatasetFileFormat=fmt)
    d = next(iter(_sum3([mk("image", "brightfield microscopy", "Level 3", "csv")], t).values()))
    assert d["CanonicalFileFormat"] == "csv; ome-tiff" and d["PreferredFileFormat"] == "csv"
    assert "Canonical format ome-tiff not grounded in declared formats (csv)" in d["ReviewFlags"]
    rows = [mk("text data", "", "Auxiliary", "RData"), mk("text data", "", "Auxiliary", "txt")]
    d = next(iter(_sum3(rows, t).values()))
    assert d["PreferredFileFormat"] == "csv" and "Canonical format Github Repo not grounded" in d["ReviewFlags"]
    d = next(iter(_sum3([mk("electrophysiology", "", "Not Applicable", "json")], t).values()))
    assert d["PreferredFileFormat"] == "" and "csv; Github Repo not grounded" in d["ReviewFlags"]
    # scaffold rows keep the full canonical value
    full = [_row(AssayType="Transcriptomics (Bulk)", DataType="gene expression", Assay="RNA-seq", ProcessingLevel="Level 2")]
    comp = _compact(full)[0]  # declared Level 2 row has a blank (ungrounded) value; the Level 1 scaffold keeps canonical
    assert [c["PreferredFormat"] for c in comp] == ["fastq", ""]


def test_ordering_levels_read_in_order_within_each_assay():
    rows = [_r(1, DatasetCategory="drug screen", DatasetAssay="ELISA", DatasetProcessingLevel="Level 3"),
            _r(2, DatasetCategory="immunoassay", DatasetAssay="ELISA", DatasetProcessingLevel="Level 3"),
            _r(3, DatasetCategory="immunoassay", DatasetAssay="ELISA", DatasetProcessingLevel="Level 1"),
            _r(4, DatasetCategory="x", DatasetAssay="ELISA", DatasetProcessingLevel="Level 2")]
    result = sl.summarize(rows, {}, RULES)[0]
    assert [d["ProcessingLevel"] for d in result] == ["Level 1", "Level 2", "Level 3"]
    assert result[2]["DataType"] == "drug screen; immunoassay"
    out = [{**{c: "" for c in sl.OUTPUT_COLUMNS}, **{k: d[k] for k in ("AssayType", "DataType", "Assay", "ProcessingLevel")}}
           for d in result]
    comp = sl.build_compact(out[::-1], RULES, FORMATS, FLR, AT)[0]
    assert [c["ProcessingLevel"][:7] for c in comp] == ["Level 1", "Level 2", "Level 3"]


def test_disallowed_format_families_are_questioned_at_any_level():
    mk = lambda i, assay, cat, fmt, lv="Level 3": _r(i, DatasetCategory=cat, DatasetAssay=assay, DatasetProcessingLevel=lv,
                                                    DatasetFileFormat=fmt)
    rows = [mk(1, "immunohistochemistry", "image", "fastq"), mk(2, "immunohistochemistry", "image", "csv"),
            mk(3, "immunohistochemistry", "image", "tif"),
            mk(4, "mass spectrometry", "proteomics", "ome-tiff"), mk(5, "mass spectrometry", "proteomics", "mzML"),
            mk(6, "RNA-seq", "gene expression", "png", "Level 2"), mk(7, "RNA-seq", "gene expression", "csv", "Level 3")]
    s = _sum3(rows)
    ihc = s["image", "immunohistochemistry", "Level 3"]
    assert ihc["FileFormats"] == "csv; tif" and ihc["QuestionedFormats"] == "fastq"  # csv is not disallowed for imaging
    assert "Format(s) not plausible for imaging assays: fastq (1 row(s)); excluded from FileFormats" in ihc["ReviewFlags"]
    ms = s["proteomics", "mass spectrometry", "Level 3"]
    assert ms["FileFormats"] == "mzML" and ms["QuestionedFormats"] == "ome-tiff"
    assert s["gene expression", "RNA-seq", "Level 2"]["QuestionedFormats"] == "png"
    assert s["gene expression", "RNA-seq", "Level 3"]["QuestionedFormats"] == ""
    sheet, ev = _sheet(rows)
    c = AT["columns"]
    assert sheet["Imaging"][c[4]] == "csv; tif" and sheet["Mass spectrometry"][c[4]] == "mzML"
    assert sheet["Transcriptomics (Bulk)"][c[3]] == ""
    st = {(e["AssayType"], e["Value"]): e["Status"] for e in ev}
    assert st[("Imaging", "fastq")] == "excluded: questioned format"
    assert st[("Mass spectrometry", "ome-tiff")] == "excluded: questioned format"


def test_repository_must_fit_the_assay_family_and_old_items_must_match_assay():
    mk = lambda i, assay, text: _r(i, DatasetCategory="x", DatasetAssay=assay, DatasetProcessingLevel="Level 1",
                                   DatasetFileFormat="tif", Comments=text)
    rows = [mk(1, "histology", "see GSE111 and syn123 and zenodo.55"), mk(2, "mass spectrometry", "PXD000001 and GSE222"),
            mk(3, "RNA-seq", "GSE333")]
    sheet, ev = _sheet(rows)
    assert sheet["Imaging"]["Established repository name"] == "Synapse (syn123); Zenodo (zenodo.55)"
    assert sheet["Mass spectrometry"]["Established repository name"] == "PRIDE (PXD000001)"
    assert sheet["Transcriptomics (Bulk)"]["Established repository name"] == "GEO (GSE333)"
    bad = {(e["AssayType"], e["Value"]) for e in ev if e["Status"] == "excluded: repository does not hold this assay type"}
    assert bad == {("Imaging", "GEO (GSE111)"), ("Mass spectrometry", "GEO (GSE222)")}
    assert RULES["repository_assay_families"]["GEO"] == "sequencing_assays"
    # old-manifest item text counts only for items of the same assay
    item = lambda assay, text: {"Assay": assay, "Description": text, "Comments": ""}
    row = _mk(DatasetAssay="RNA-seq", _link={"items": [item("RNA-seq", "GSE1"), item("histology", "GSE2"), item("", "GSE3")]})
    assert sl.row_accessions(row) == [("GEO", "GSE1")]
