"""Foundation tests for project scaffolding and Markdown lineage."""

import json

import pytest

from video_creator.analysis import ExtractiveAnalysisProvider, validate_analysis
from video_creator.project import (
    analyze_project_source, ingest_project_source, initialize_project, validate_project,
)
from video_creator.source import ingest_markdown, validate_source


def test_markdown_ingestion_preserves_stable_section_ranges(tmp_path):
    manuscript = tmp_path / "story.md"
    manuscript.write_text("## Dawn\n\nFirst scene.\n\n## Night\n\nSecond scene.\n", encoding="utf-8")
    source = ingest_markdown(manuscript)
    assert [section["section_id"] for section in source["sections"]] == ["sec-0001", "sec-0002"]
    assert [section["title"] for section in source["sections"]] == ["Dawn", "Night"]
    assert source["sections"][0]["source_end"] == source["sections"][1]["source_start"]
    assert validate_source(source) == []


def test_project_ingestion_is_valid_and_rights_fail_closed(tmp_path):
    workspace = tmp_path / "workspace"
    manuscript = tmp_path / "story.md"
    manuscript.write_text("# Owned fixture\n\nA short synthetic story.\n", encoding="utf-8")
    initialize_project(workspace, "fixture-story", "Fixture Story", "unverified")
    source = ingest_project_source(workspace, manuscript)
    manifest = json.loads((workspace / "project.json").read_text(encoding="utf-8"))
    assert manifest["rights"] == {"status": "unverified", "release_blocked": True}
    assert manifest["stages"]["source"]["input_sha256"] == source["sha256"]
    assert validate_project(workspace) == []


def test_project_rejects_unsafe_identifier(tmp_path):
    with pytest.raises(ValueError, match="project_id"):
        initialize_project(tmp_path / "workspace", "unsafe project/id", "Bad", "original")


def test_extractive_analysis_is_draft_and_source_grounded(tmp_path):
    manuscript = tmp_path / "story.md"
    manuscript.write_text(
        "## Dawn, Test City\n\nMira met Rowan. Mira warned Rowan.\n", encoding="utf-8",
    )
    source = ingest_markdown(manuscript)
    analysis = ExtractiveAnalysisProvider().analyze(
        manuscript.read_text(encoding="utf-8"), source["sha256"],
    )
    assert analysis["status"] == "draft" and analysis["release_usable"] is False
    assert {item["name"] for item in analysis["entities"]} == {"Mira", "Rowan"}
    assert analysis["settings"][0]["heading"] == "Dawn, Test City"
    assert validate_analysis(analysis, source) == []


def test_project_analysis_rejects_changed_manuscript(tmp_path):
    workspace = tmp_path / "workspace"
    manuscript = tmp_path / "story.md"
    manuscript.write_text("# Story\n\nMira met Mira.\n", encoding="utf-8")
    initialize_project(workspace, "fixture", "Fixture", "original")
    ingest_project_source(workspace, manuscript)
    manuscript.write_text("# Story\n\nThe source changed.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after ingestion"):
        analyze_project_source(workspace, manuscript)
