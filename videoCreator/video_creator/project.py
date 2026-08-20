"""Project scaffolding, manifest state, and validation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

from .analysis import (
    AnalysisProvider, ExtractiveAnalysisProvider, apply_analysis_decisions,
    build_analysis_review_template, validate_analysis,
)
from .artifacts import read_json, sha256_file, write_json_atomic
from .images import ImageProvider, generate_assets, validate_assets
from .narration import (
    MappingNarrationProvider, adapt_narration, build_narration_plan,
    build_narration_response_template,
    validate_adapted_narration, validate_narration_plan,
)
from .prompts import compile_prompts, validate_prompts
from .scenes import (
    SceneEnrichmentProvider, enrich_scenes, segment_scenes,
    validate_enriched_scenes, validate_scenes,
)
from .source import ingest_markdown, normalize_markdown, validate_source
from .storyboard import plan_storyboard, validate_storyboard
from .visual_review import review_character_references, review_shot_assets


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
            "storyboard": {"status": "pending"},
            "prompts": {"status": "pending"},
            "images": {"status": "pending"},
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
    normalized = normalize_markdown(manuscript.read_text(encoding="utf-8-sig"))
    (root / "source" / "manuscript.md").write_text(normalized, encoding="utf-8")
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


def adapt_project_narration(root: Path, manuscript: Path, responses_path: Path) -> dict:
    """Apply pre-generated provider responses to every planned narration unit."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    plan = read_json(root / "script" / "narration.plan.json")
    responses = read_json(responses_path)
    if responses.get("schema_version") != 1:
        raise ValueError("unsupported narration response schema_version")
    if responses.get("narration_plan_id") != plan["narration_plan_id"]:
        raise ValueError("narration responses do not match the current plan")
    values = responses.get("responses", [])
    mapping = {str(item.get("narration_id") or ""): item for item in values}
    if len(mapping) != len(values) or set(mapping) != {
        item["narration_id"] for item in plan["blocks"]
    }:
        raise ValueError("narration responses must cover every planned block exactly once")
    provider = MappingNarrationProvider(
        mapping, name=str(responses.get("provider") or "mapping-provider-v1"),
    )
    narration = adapt_narration(
        plan, manuscript.read_text(encoding="utf-8-sig"), provider,
    )
    issues = validate_adapted_narration(narration, plan)
    if issues:
        raise ValueError("invalid adapted narration: " + "; ".join(issues))
    output = root / "script" / "narration.json"
    write_json_atomic(output, narration)
    manifest["stages"]["narration"] = {
        "status": "adapted_draft", "artifact": "script/narration.json",
        "plan_artifact": "script/narration.plan.json", "provider": provider.name,
        "input_sha256": plan["source_sha256"], "updated_at": now(),
        "approval_required": True,
    }
    write_json_atomic(manifest_path, manifest)
    return narration


def write_narration_response_template(root: Path, output: Path) -> dict:
    """Write pending provider responses for every current narration block."""
    template = build_narration_response_template(
        read_json(root / "script" / "narration.plan.json"),
    )
    write_json_atomic(output, template)
    return template


def segment_project_scenes(root: Path, *, maximum_blocks: int = 2) -> dict:
    """Create draft scenes from validated adapted narration."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    if manifest["stages"]["narration"].get("status") != "adapted_draft":
        raise ValueError("scene segmentation requires adapted narration")
    narration = read_json(root / manifest["stages"]["narration"]["artifact"])
    analysis = read_json(root / manifest["stages"]["analysis"]["artifact"])
    scenes = segment_scenes(narration, analysis, maximum_blocks=maximum_blocks)
    issues = validate_scenes(scenes, narration, analysis)
    if issues:
        raise ValueError("invalid scene plan: " + "; ".join(issues))
    output = root / "storyboard" / "scenes.json"
    write_json_atomic(output, scenes)
    manifest["stages"]["scenes"] = {
        "status": "draft", "artifact": "storyboard/scenes.json",
        "input_sha256": narration["source_sha256"], "updated_at": now(),
        "approval_required": True,
    }
    write_json_atomic(manifest_path, manifest)
    return scenes


def enrich_project_scenes(
    root: Path, provider: SceneEnrichmentProvider | None = None,
    *, acceptance_threshold: float = 0.8, maximum_attempts: int = 2,
) -> dict:
    """Automatically enrich and promote scenes without an editorial prompt."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    scene_stage = manifest["stages"]["scenes"]
    if scene_stage.get("status") != "draft":
        raise ValueError("automatic scene enrichment requires draft scenes")
    scenes = read_json(root / scene_stage["artifact"])
    narration = read_json(root / manifest["stages"]["narration"]["artifact"])
    analysis = read_json(root / manifest["stages"]["analysis"]["artifact"])
    previous_path = root / "storyboard" / "scenes.enriched.json"
    previous = read_json(previous_path) if previous_path.is_file() else None
    result = enrich_scenes(
        scenes, narration, provider,
        acceptance_threshold=acceptance_threshold,
        maximum_attempts=maximum_attempts,
        previous_scenes=previous,
    )
    issues = validate_enriched_scenes(result, narration, analysis)
    if issues:
        raise ValueError("invalid enriched scene plan: " + "; ".join(issues))
    output = root / "storyboard" / "scenes.enriched.json"
    write_json_atomic(output, result)
    manifest["stages"]["scenes"] = {
        "status": result["status"], "artifact": "storyboard/scenes.enriched.json",
        "draft_artifact": scene_stage["artifact"], "provider": result["provider"],
        "input_sha256": narration["source_sha256"], "updated_at": now(),
        "approval_required": result["status"] != "auto_accepted",
        "exception_count": len(result["exception_report"]),
    }
    write_json_atomic(manifest_path, manifest)
    return result


def plan_project_storyboard(root: Path, *, target_shot_seconds: float = 15.0) -> dict:
    """Plan and selectively reuse autonomous storyboard shots."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    scene_stage = manifest["stages"]["scenes"]
    if scene_stage.get("status") != "auto_accepted":
        raise ValueError("storyboard planning requires automatically accepted scenes")
    scenes = read_json(root / scene_stage["artifact"])
    output = root / "storyboard" / "shots.json"
    previous = read_json(output) if output.is_file() else None
    storyboard = plan_storyboard(
        scenes, target_shot_seconds=target_shot_seconds,
        previous_storyboard=previous,
    )
    issues = validate_storyboard(storyboard, scenes)
    if issues:
        raise ValueError("invalid storyboard: " + "; ".join(issues))
    write_json_atomic(output, storyboard)
    manifest["stages"]["storyboard"] = {
        "status": "auto_accepted", "artifact": "storyboard/shots.json",
        "input_sha256": scenes["source_sha256"], "planner": storyboard["planner"],
        "updated_at": now(), "approval_required": False,
        "reused_count": len(storyboard["regeneration"]["reused_shot_ids"]),
        "regenerated_count": len(storyboard["regeneration"]["regenerated_shot_ids"]),
    }
    write_json_atomic(manifest_path, manifest)
    return storyboard


def compile_project_prompts(root: Path, *, style: str = "cinematic illustrated realism") -> dict:
    """Compile image prompts and nonblocking character-reference defaults."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    storyboard_stage = manifest.get("stages", {}).get("storyboard", {})
    if storyboard_stage.get("status") != "auto_accepted":
        raise ValueError("prompt compilation requires an accepted storyboard")
    storyboard = read_json(root / storyboard_stage["artifact"])
    analysis = read_json(root / manifest["stages"]["analysis"]["artifact"])
    output = root / "prompts" / "image-prompts.json"
    previous = read_json(output) if output.is_file() else None
    manuscript_path = root / "source" / "manuscript.md"
    source_text = manuscript_path.read_text(encoding="utf-8") if manuscript_path.is_file() else None
    compiled = compile_prompts(
        storyboard, analysis, style=style, previous=previous, source_text=source_text,
    )
    issues = validate_prompts(compiled, storyboard, analysis)
    if issues:
        raise ValueError("invalid image prompts: " + "; ".join(issues))
    write_json_atomic(output, compiled)
    manifest["stages"]["prompts"] = {
        "status": "auto_accepted", "artifact": "prompts/image-prompts.json",
        "input_sha256": storyboard["source_sha256"], "compiler": compiled["compiler"],
        "updated_at": now(), "approval_required": False,
        "optional_character_choices": len(compiled["reference_requirements"]),
        "reused_count": len(compiled["regeneration"]["reused_shot_ids"]),
        "regenerated_count": len(compiled["regeneration"]["regenerated_shot_ids"]),
    }
    write_json_atomic(manifest_path, manifest)
    return compiled


def generate_project_images(
    root: Path, provider: ImageProvider | None = None, *, candidates_per_item: int = 2,
    maximum_attempts: int = 2,
) -> dict:
    """Generate, rank, and select all visual candidates automatically."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    prompt_stage = manifest.get("stages", {}).get("prompts", {})
    if prompt_stage.get("status") != "auto_accepted":
        raise ValueError("image generation requires accepted prompts")
    prompts = read_json(root / prompt_stage["artifact"])
    output = root / "images" / "assets.json"
    previous = read_json(output) if output.is_file() else None
    canonical_stage = manifest.get("stages", {}).get("canonical_references", {})
    canonical_hashes = {}
    if canonical_stage.get("status") == "auto_accepted":
        canonical = read_json(root / canonical_stage["artifact"])
        canonical_hashes = {
            item["reference_id"]: item["sha256"] for item in canonical["references"]
        }
    assets = generate_assets(
        prompts, root, provider, candidates_per_item=candidates_per_item,
        maximum_attempts=maximum_attempts, previous=previous,
        canonical_references=canonical_hashes,
    )
    issues = validate_assets(assets, prompts, root)
    if issues:
        raise ValueError("invalid generated images: " + "; ".join(issues))
    write_json_atomic(output, assets)
    manifest["stages"]["images"] = {
        "status": "auto_accepted", "artifact": "images/assets.json",
        "input_sha256": prompts["source_sha256"], "provider": assets["provider"],
        "updated_at": now(), "approval_required": False,
        "asset_count": len(assets["assets"]),
        "reused_count": len(assets["regeneration"]["reused_asset_ids"]),
        "regenerated_count": len(assets["regeneration"]["regenerated_asset_ids"]),
    }
    write_json_atomic(manifest_path, manifest)
    return assets


def generate_project_character_references(
    root: Path, provider: ImageProvider | None = None, *, candidates_per_item: int = 2,
    maximum_attempts: int = 2,
) -> dict:
    """Generate and auto-select canonical character references before shot images."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    prompt_stage = manifest.get("stages", {}).get("prompts", {})
    if prompt_stage.get("status") != "auto_accepted":
        raise ValueError("character-reference generation requires accepted prompts")
    prompts = read_json(root / prompt_stage["artifact"])
    output = root / "images" / "character-references.json"
    previous = read_json(output) if output.is_file() else None
    assets = generate_assets(
        prompts, root, provider, candidates_per_item=candidates_per_item,
        maximum_attempts=maximum_attempts, previous=previous,
        asset_kinds=frozenset({"character_reference"}),
        asset_namespace="reference-stage",
    )
    issues = validate_assets(assets, prompts, root)
    if issues:
        raise ValueError("invalid character references: " + "; ".join(issues))
    write_json_atomic(output, assets)
    manifest["stages"]["character_references"] = {
        "status": "auto_accepted", "artifact": "images/character-references.json",
        "input_sha256": prompts["source_sha256"], "provider": assets["provider"],
        "updated_at": now(), "approval_required": False,
        "optional_user_override": True, "asset_count": len(assets["assets"]),
        "reused_count": len(assets["regeneration"]["reused_asset_ids"]),
        "regenerated_count": len(assets["regeneration"]["regenerated_asset_ids"]),
    }
    write_json_atomic(manifest_path, manifest)
    return assets


def generate_project_shot_pilot(
    root: Path, provider: ImageProvider | None = None, *, shot_limit: int = 4,
    candidates_per_item: int = 1, maximum_attempts: int = 2,
) -> dict:
    """Generate an isolated bounded shot batch before full production expansion."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    prompts = read_json(root / manifest["stages"]["prompts"]["artifact"])
    canonical_stage = manifest.get("stages", {}).get("canonical_references", {})
    if canonical_stage.get("status") != "auto_accepted":
        raise ValueError("shot pilot requires accepted canonical references")
    canonical = read_json(root / canonical_stage["artifact"])
    hashes = {item["reference_id"]: item["sha256"] for item in canonical["references"]}
    identifiers = frozenset(item["shot_id"] for item in prompts["prompts"][:shot_limit])
    output = root / "images" / "shot-pilot.json"
    previous = read_json(output) if output.is_file() else None
    assets = generate_assets(
        prompts, root, provider, candidates_per_item=candidates_per_item,
        maximum_attempts=maximum_attempts, previous=previous,
        asset_kinds=frozenset({"shot"}), asset_namespace="shot-pilot",
        canonical_references=hashes, asset_ids=identifiers,
    )
    issues = validate_assets(assets, prompts, root)
    if issues:
        raise ValueError("invalid shot pilot: " + "; ".join(issues))
    write_json_atomic(output, assets)
    manifest["stages"]["shot_pilot"] = {
        "status": "generated", "artifact": "images/shot-pilot.json",
        "provider": assets["provider"], "shot_count": len(assets["assets"]),
        "updated_at": now(), "approval_required": False,
    }
    write_json_atomic(manifest_path, manifest)
    return assets


def review_project_shot_pilot(root: Path) -> dict:
    """Review the bounded production pilot and block expansion on any failure."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    stage = manifest.get("stages", {}).get("shot_pilot", {})
    if stage.get("status") != "generated":
        raise ValueError("shot pilot review requires generated pilot images")
    assets = read_json(root / stage["artifact"])
    prompts = read_json(root / manifest["stages"]["prompts"]["artifact"])
    review = review_shot_assets(assets, prompts, root)
    output = root / "images" / "shot-pilot-review.json"
    write_json_atomic(output, review)
    manifest["stages"]["shot_pilot_review"] = {
        "status": review["status"], "artifact": "images/shot-pilot-review.json",
        "reviewer": review["reviewer"], "updated_at": now(),
        "approval_required": False,
    }
    write_json_atomic(manifest_path, manifest)
    return review


def review_project_character_references(root: Path) -> dict:
    """Semantically review references and fail closed on source mismatches."""
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    stage = manifest.get("stages", {}).get("character_references", {})
    if stage.get("status") != "auto_accepted":
        raise ValueError("semantic review requires generated character references")
    assets = read_json(root / stage["artifact"])
    prompts = read_json(root / manifest["stages"]["prompts"]["artifact"])
    review = review_character_references(assets, prompts, root)
    output = root / "images" / "character-reference-review.json"
    write_json_atomic(output, review)
    manifest["stages"]["character_reference_review"] = {
        "status": review["status"], "artifact": "images/character-reference-review.json",
        "reviewer": review["reviewer"], "updated_at": now(),
        "approval_required": False,
    }
    if review["status"] == "auto_accepted":
        by_asset = {item["asset_id"]: item for item in assets["assets"]}
        promoted = []
        for item in review["assets"]:
            asset = by_asset[item["asset_id"]]
            candidate = next(
                value for value in asset["candidates"]
                if value["candidate_id"] == item["selected_candidate_id"]
            )
            relative = Path("references") / "characters" / f"{item['asset_id']}.png"
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / candidate["path"], destination)
            promoted.append({
                "reference_id": item["asset_id"],
                "selected_candidate_id": item["selected_candidate_id"],
                "path": relative.as_posix(), "sha256": sha256_file(destination),
                "reviewer": review["reviewer"],
            })
        canonical = {
            "schema_version": 1, "status": "auto_accepted",
            "source_sha256": prompts["source_sha256"], "references": promoted,
        }
        canonical_output = root / "references" / "characters.json"
        write_json_atomic(canonical_output, canonical)
        manifest["stages"]["canonical_references"] = {
            "status": "auto_accepted", "artifact": "references/characters.json",
            "updated_at": now(), "reference_count": len(promoted),
            "approval_required": False,
        }
    write_json_atomic(manifest_path, manifest)
    return review


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
    if narration_stage.get("status") in {"planned", "adapted_draft"}:
        narration_path = root / narration_stage.get("artifact", "")
        analysis_artifact = manifest["stages"]["analysis"].get("artifact", "")
        if not narration_path.is_file() or not analysis_artifact:
            issues.append("planned narration dependencies are missing")
        else:
            if narration_stage["status"] == "planned":
                issues.extend(validate_narration_plan(
                    read_json(narration_path), read_json(source_path),
                    read_json(root / analysis_artifact),
                ))
            else:
                plan_path = root / narration_stage.get("plan_artifact", "")
                if not plan_path.is_file():
                    issues.append("adapted narration plan is missing")
                else:
                    issues.extend(validate_adapted_narration(
                        read_json(narration_path), read_json(plan_path),
                    ))
    scenes_stage = manifest.get("stages", {}).get("scenes", {})
    if scenes_stage.get("status") in {"draft", "auto_accepted", "retry_required"}:
        scene_path = root / scenes_stage.get("artifact", "")
        if not scene_path.is_file() or narration_stage.get("status") != "adapted_draft":
            issues.append("draft scene dependencies are missing")
        else:
            validator = (
                validate_scenes if scenes_stage["status"] == "draft"
                else validate_enriched_scenes
            )
            issues.extend(validator(
                read_json(scene_path), read_json(root / narration_stage["artifact"]),
                read_json(root / manifest["stages"]["analysis"]["artifact"]),
            ))
    storyboard_stage = manifest.get("stages", {}).get("storyboard", {})
    if storyboard_stage.get("status") == "auto_accepted":
        storyboard_path = root / storyboard_stage.get("artifact", "")
        scene_path = root / scenes_stage.get("artifact", "")
        if not storyboard_path.is_file() or not scene_path.is_file():
            issues.append("storyboard dependencies are missing")
        else:
            issues.extend(validate_storyboard(
                read_json(storyboard_path), read_json(scene_path),
            ))
    prompts_stage = manifest.get("stages", {}).get("prompts", {})
    if prompts_stage.get("status") == "auto_accepted":
        prompts_path = root / prompts_stage.get("artifact", "")
        storyboard_path = root / storyboard_stage.get("artifact", "")
        analysis_path = root / manifest["stages"]["analysis"].get("artifact", "")
        if not prompts_path.is_file() or not storyboard_path.is_file() or not analysis_path.is_file():
            issues.append("image prompt dependencies are missing")
        else:
            issues.extend(validate_prompts(
                read_json(prompts_path), read_json(storyboard_path), read_json(analysis_path),
            ))
    images_stage = manifest.get("stages", {}).get("images", {})
    if images_stage.get("status") == "auto_accepted":
        assets_path = root / images_stage.get("artifact", "")
        prompts_path = root / prompts_stage.get("artifact", "")
        if not assets_path.is_file() or not prompts_path.is_file():
            issues.append("image asset dependencies are missing")
        else:
            issues.extend(validate_assets(
                read_json(assets_path), read_json(prompts_path), root,
            ))
    references_stage = manifest.get("stages", {}).get("character_references", {})
    if references_stage.get("status") == "auto_accepted":
        references_path = root / references_stage.get("artifact", "")
        prompts_path = root / prompts_stage.get("artifact", "")
        if not references_path.is_file() or not prompts_path.is_file():
            issues.append("character-reference dependencies are missing")
        else:
            issues.extend(validate_assets(
                read_json(references_path), read_json(prompts_path), root,
            ))
    canonical_stage = manifest.get("stages", {}).get("canonical_references", {})
    if canonical_stage.get("status") == "auto_accepted":
        canonical_path = root / canonical_stage.get("artifact", "")
        if not canonical_path.is_file():
            issues.append("canonical character-reference manifest is missing")
        else:
            canonical = read_json(canonical_path)
            expected_ids = {
                item["reference_id"] for item in read_json(
                    root / prompts_stage["artifact"]
                ).get("reference_requirements", [])
            }
            actual_ids = {item.get("reference_id") for item in canonical.get("references", [])}
            if actual_ids != expected_ids:
                issues.append("canonical references must cover every character exactly")
            for item in canonical.get("references", []):
                path = root / str(item.get("path") or "")
                if not path.is_file() or sha256_file(path) != item.get("sha256"):
                    issues.append(f"canonical reference hash mismatch: {item.get('reference_id')}")
    return issues
