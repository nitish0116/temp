"""Project scaffolding, manifest state, and validation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .analysis import (
    AnalysisProvider, ExtractiveAnalysisProvider, apply_analysis_decisions,
    build_analysis_review_template, validate_analysis,
)
from .artifacts import read_json, write_json_atomic
from .narration import build_narration_plan, validate_narration_plan
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


def write_analysis_review_template(root: Path, output: Path) -> dict:
    """Write a complete pending decision template for the current draft."""
    template = build_analysis_review_template(read_json(root / "analysis" / "entities.json"))
    write_json_atomic(output, template)
    return template


def approve_project_analysis(root: Path, decisions_path: Path) -> dict:
    """Apply complete source-bound entity and setting review decisions."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    draft = read_json(root / "analysis" / "entities.json")
    approved = apply_analysis_decisions(draft, read_json(decisions_path))
    source = read_json(root / "source" / "source.json")
    issues = validate_analysis(approved, source)
    if issues:
        raise ValueError("invalid approved analysis: " + "; ".join(issues))
    suffix = "approved" if approved["status"] == "approved" else "reviewed"
    output = root / "analysis" / f"entities.{suffix}.json"
    write_json_atomic(output, approved)
    manifest["stages"]["analysis"] = {
        "status": approved["status"], "artifact": f"analysis/entities.{suffix}.json",
        "input_sha256": source["sha256"], "provider": draft["provider"],
        "updated_at": now(), "approval_required": not approved["release_usable"],
    }
    write_json_atomic(manifest_path, manifest)
    return approved


def plan_project_narration(
    root: Path, manuscript: Path, *, maximum_source_characters: int = 2400,
) -> dict:
    """Create bounded narration work units from reviewed planning identities."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    source = read_json(root / "source" / "source.json")
    analysis_stage = manifest["stages"]["analysis"]
    if analysis_stage.get("status") not in {"reviewed_draft", "approved"}:
        raise ValueError("narration planning requires reviewed analysis")
    analysis = read_json(root / analysis_stage["artifact"])
    text = manuscript.read_text(encoding="utf-8-sig")
    plan = build_narration_plan(
        text, source, analysis,
        maximum_source_characters=maximum_source_characters,
    )
    issues = validate_narration_plan(plan, source, analysis)
    if issues:
        raise ValueError("invalid narration plan: " + "; ".join(issues))
    output = root / "script" / "narration.plan.json"
    write_json_atomic(output, plan)
    manifest["stages"]["narration"] = {
        "status": "planned", "artifact": "script/narration.plan.json",
        "input_sha256": source["sha256"], "updated_at": now(),
        "approval_required": True,
    }
    write_json_atomic(manifest_path, manifest)
    return plan


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
    if manifest.get("stages", {}).get("analysis", {}).get("status") in {
        "generated", "reviewed_draft", "approved",
    }:
        if not analysis_path.is_file():
            issues.append("generated analysis artifact is missing")
        elif source_path.is_file():
            selected = root / manifest["stages"]["analysis"].get(
                "artifact", "analysis/entities.json",
            )
            if not selected.is_file():
                issues.append("selected analysis artifact is missing")
            else:
                issues.extend(validate_analysis(read_json(selected), read_json(source_path)))
    narration_stage = manifest.get("stages", {}).get("narration", {})
    if narration_stage.get("status") == "planned":
        narration_path = root / narration_stage.get("artifact", "")
        analysis_artifact = manifest["stages"]["analysis"].get("artifact", "")
        if not narration_path.is_file() or not analysis_artifact:
            issues.append("planned narration dependencies are missing")
        else:
            issues.extend(validate_narration_plan(
                read_json(narration_path), read_json(source_path),
                read_json(root / analysis_artifact),
            ))
    return issues
