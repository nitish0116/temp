"""Foundation tests for project scaffolding and Markdown lineage."""

import json

import pytest

from video_creator.project import ingest_project_source, initialize_project, validate_project
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
