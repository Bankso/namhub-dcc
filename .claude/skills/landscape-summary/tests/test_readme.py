"""Tests for README.md / run_info.json. Fixtures are synthetic (see test_old_manifest.py)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_old_manifest import MAPPING, OLD, REVISED, SCRIPT, write_revised
import summarize_landscape as sl

FORBIDDEN = ["Alpha Cardio", "Heart tissue", "ALPHA-", "Beta Lung", "Lung RNA", "Lung proteome", "Gamma", "GAMMA-",
             "Imaging set", "Orphan metabolite", "ZZ-custom", "BETA-", "ZNF123", "GSE111111", "12345678"]


def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    d = tmp_path_factory.mktemp("pkg")
    write_revised(d / "revised.csv")
    out = d / "out"
    r = run_cli(d / "revised.csv", "--out-dir", out, "--mapping", MAPPING, "--old-manifests", OLD)
    assert r.returncode == 0, r.stderr
    return out, d


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    d = tmp_path_factory.mktemp("plain")
    write_revised(d / "revised.csv")
    out = d / "out"
    assert run_cli(d / "revised.csv", "--out-dir", out, "--mapping", MAPPING).returncode == 0
    return out


def test_readme_and_run_info_written(pkg):
    out, _ = pkg
    text = (out / "README.md").read_text()
    assert text.startswith("# Landscape summary package")
    for h in ("## Run at a glance", "## Start here", "## Files in this package", "## How to read the summary",
              "## Column reference", "## Flags and decisions needing input", "## Caveats", "## How this was produced"):
        assert h in text
    info = json.loads((out / "run_info.json").read_text())
    assert info["input_file_count"] == 1 and info["input_row_count"] == len(REVISED)
    assert info["excluded_row_count"] == 0 and info["study_count"] == 3
    assert info["output_row_count"] > 0
    assert info["old_manifests"]["used"] is True and info["old_manifests"]["linked_row_count"] > 0
    assert info["mapping"]["file"] == "fixture_mapping.csv" and info["mapping"]["row_count"] == 4
    assert info["config_files"] == {"rules": "rules.json", "canonical_formats": "canonical_formats.csv",
                                    "format_level_rules": "format_level_rules.json"}
    assert info["script"] == ".claude/skills/landscape-summary/scripts/summarize_landscape.py"
    assert info["generated_at"].endswith("Z")
    assert "landscape_enums.json" in info["enum_notes"]


def test_no_landscape_content_or_local_paths(pkg):
    out, d = pkg
    for name in ("README.md", "run_info.json"):
        text = (out / name).read_text()
        for bad in FORBIDDEN:
            assert bad not in text, (name, bad)
        assert str(d) not in text and "/private/" not in text and "/Users/" not in text


def test_command_placeholders(pkg):
    out, _ = pkg
    cmd = json.loads((out / "run_info.json").read_text())["command"]
    assert cmd == ("python3 .claude/skills/landscape-summary/scripts/summarize_landscape.py <INPUT> "
                   "--out-dir <OUT_DIR> --old-manifests <OLD_MANIFESTS> --mapping fixture_mapping.csv")
    assert cmd in (out / "README.md").read_text()


def test_lists_only_present_files(plain):
    text = (plain / "README.md").read_text()
    listed = {line.split("`")[1] for line in text.splitlines() if line.startswith("| `")}
    present = {p.name for p in plain.iterdir()}
    assert listed <= present
    assert "landscape_summary_reviewed.csv" not in listed and "landscape_summary.md" not in listed
    assert {"landscape_summary.csv", "landscape_summary_compact.csv", "landscape_summary_compact.md", "review_packet.json",
            "excluded_rows.csv", "run_info.json", "README.md"} <= listed
    assert "old_manifest_links.csv" not in listed and "proposed_corrections.csv" not in listed


def test_reconciliation_section_only_with_old_manifests(pkg, plain):
    assert "## Reconciliation with earlier landscapes" in (pkg[0] / "README.md").read_text()
    assert "Reconciliation with earlier landscapes" not in (plain / "README.md").read_text()
    assert "Old manifests used: no" in (plain / "README.md").read_text()


def test_rules_values_in_readme(pkg):
    text = (pkg[0] / "README.md").read_text()
    rules = json.loads((SCRIPT.parents[1] / "references" / "rules.json").read_text())
    for lv, tier in rules["level_max_tier"].items():
        assert f"{lv} is capped at {tier}" in text
    for c in rules["level_cap_exempt_categories"]:
        assert c in text
    for r in rules["repository_rules"]:
        assert r["repository"] in text


def test_readme_only_picks_up_new_files(tmp_path):
    write_revised(tmp_path / "revised.csv")
    out = tmp_path / "out"
    assert run_cli(tmp_path / "revised.csv", "--out-dir", out, "--mapping", MAPPING).returncode == 0
    assert "| `landscape_summary_reviewed.csv` |" not in (out / "README.md").read_text()
    (out / "landscape_summary_reviewed.csv").write_text("DataType\n")
    (out / "landscape_summary.md").write_text("x\n")
    r = run_cli("--readme-only", "--out-dir", out)
    assert r.returncode == 0, r.stderr
    text = (out / "README.md").read_text()
    assert "| `landscape_summary_reviewed.csv` |" in text and "| `landscape_summary.md` |" in text
    assert "curated full table" in text


def test_readme_only_without_run_info_errors(tmp_path):
    r = run_cli("--readme-only", "--out-dir", tmp_path)
    assert r.returncode != 0 and "run_info.json" in r.stderr
    assert not (tmp_path / "README.md").exists()


def test_every_output_column_has_a_description():
    for c in sl.OUTPUT_COLUMNS:
        assert sl.COLUMN_DESCRIPTIONS.get(c), c
    assert sl.COLUMN_DESCRIPTIONS.get("ReviewNotes")
    text_cols = set(sl.COLUMN_DESCRIPTIONS) - set(sl.OUTPUT_COLUMNS) - {"ReviewNotes"}
    assert not text_cols
