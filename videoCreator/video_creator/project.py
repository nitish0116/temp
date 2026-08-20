"""Project scaffolding, manifest state, and validation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .analysis import AnalysisProvider, ExtractiveAnalysisProvider, validate_analysis
from .artifacts import read_json, write_json_atomic
from .source import ingest_markdown, normalize_markdown, validate_source


RIGHTS_STATES = {"unverified", "authorized", "original", "public-domain"}
DIRECTORIES = (
    "source", "analysis", "script", "storyboard", "references/characters",
    "references/locations", "references/costumes", "references/props",
    "prompts", "images", "audio/narration", "audio/music", "audio/sfx",
    "subtitles", "timeline", "renders/previews", "renders/final", "reports",
)


def now() -> str:
    """Return a timezone-qualified UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def initialize_project(root: Path, project_id: str, title: str, rights_status: str) -> dict:
    """Create deterministic project directories and the initial manifest."""
    if not project_id or not all(character.isalnum() or character in "-_" for character in project_id):
        raise ValueError("project_id must use letters, digits, hyphens, or underscores")
    if rights_status not in RIGHTS_STATES:
        raise ValueError(f"unsupported rights status: {rights_status}")
    for relative in DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "project_id": project_id,
        "title": title,
        "rights": {"status": rights_status, "release_blocked": rights_status == "unverified"},
        "created_at": now(),
        "stages": {
            "source": {"status": "pending"},
            "analysis": {"status": "pending"},
            "narration": {"status": "pending"},
            "scenes": {"status": "pending"},
        },
    }
    write_json_atomic(root / "project.json", manifest)
    return manifest


def ingest_project_source(root: Path, manuscript: Path) -> dict:
    """Ingest a manuscript and update its project stage state."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    source = ingest_markdown(manuscript)
    output = root / "source" / "source.json"
    write_json_atomic(output, source)
    manifest["stages"]["source"] = {
        "status": "generated", "artifact": "source/source.json",
        "input_sha256": source["sha256"], "updated_at": now(),
    }
    write_json_atomic(manifest_path, manifest)
    return source


def analyze_project_source(
    root: Path, manuscript: Path, provider: AnalysisProvider | None = None,
) -> dict:
    """Generate a source-bound draft analysis for explicit human review."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    source = read_json(root / "source" / "source.json")
    text = normalize_markdown(manuscript.read_text(encoding="utf-8-sig"))
    current = ingest_markdown(manuscript)
    if current["sha256"] != source["sha256"]:
        raise ValueError("manuscript changed after ingestion; ingest it again before analysis")
    selected = provider or ExtractiveAnalysisProvider()
    analysis = selected.analyze(text, source["sha256"])
    issues = validate_analysis(analysis, source)
    if issues:
        raise ValueError("invalid analysis: " + "; ".join(issues))
    output = root / "analysis" / "entities.json"
    write_json_atomic(output, analysis)
    manifest["stages"]["analysis"] = {
        "status": "generated", "artifact": "analysis/entities.json",
        "input_sha256": source["sha256"], "provider": selected.name,
        "updated_at": now(), "approval_required": True,
    }
    write_json_atomic(manifest_path, manifest)
    return analysis


def validate_project(root: Path) -> list[str]:
    """Validate the available project and source contracts."""
    issues = []
    manifest = read_json(root / "project.json")
    if manifest.get("schema_version") != 1:
        issues.append("unsupported project schema_version")
    rights = manifest.get("rights", {})
    if rights.get("status") not in RIGHTS_STATES:
        issues.append("invalid rights status")
    if rights.get("status") == "unverified" and not rights.get("release_blocked"):
        issues.append("unverified rights must block release")
    source_path = root / "source" / "source.json"
    if manifest.get("stages", {}).get("source", {}).get("status") == "generated":
        if not source_path.is_file():
            issues.append("generated source artifact is missing")
        else:
            issues.extend(validate_source(read_json(source_path)))
    analysis_path = root / "analysis" / "entities.json"
    if manifest.get("stages", {}).get("analysis", {}).get("status") == "generated":
        if not analysis_path.is_file():
            issues.append("generated analysis artifact is missing")
        elif source_path.is_file():
            issues.extend(validate_analysis(read_json(analysis_path), read_json(source_path)))
    return issues
