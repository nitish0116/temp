"""Autonomous, resumable execution of the complete video-creator pipeline."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .analysis import build_analysis_review_template
from .artifacts import read_json, write_json_atomic
from .images import ImageProvider
from .local_audio_environment import ACTIVE_FLAG as AUDIO_ACTIVE_FLAG, run_local_audio
from .project import (
    adapt_project_narration, align_project_subtitles, analyze_project_source,
    approve_project_analysis, compile_project_prompts, compile_project_timeline,
    enrich_project_scenes, evaluate_project, generate_project_character_references,
    generate_project_images, generate_project_narration_audio,
    generate_project_shot_pilot, ingest_project_source, initialize_project, now,
    plan_project_narration, plan_project_storyboard, render_project_video,
    review_project_character_references, review_project_images,
    review_project_shot_pilot, segment_project_scenes, validate_project,
)
from .series import (
    load_shared_references, publish_shared_references,
    seed_analysis_with_shared_characters,
)
from .source import ingest_markdown, normalize_markdown


REPOSITORY = Path(__file__).resolve().parents[2]


def _portable_path(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPOSITORY).as_posix()
    except ValueError:
        return path.name


def _slug(value: str, fallback: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return result or fallback


def _entity_kind(name: str, text: str) -> str | None:
    """Classify an extracted proper name from explicit local linguistic evidence."""
    folded = name.casefold()
    if folded in {
        "an", "because", "closing the", "however", "instead", "my", "no",
        "once", "one", "taking", "we", "whether",
    }:
        return None
    escaped = re.escape(name)
    if any(term in folded for term in ("glades", "hall")) or folded == "earth":
        return "location"
    if folded.endswith("ing") or folded in {"dwarven"}:
        return "concept"
    if re.search(rf"\b{escaped}\s+(?:guild|council|army|academy|church)\b", text, re.I) or re.search(
        rf"\b{escaped}\b", text, re.I,
    ) and any(word in folded for word in ("guild", "council", "academy")):
        return "organization"
    location_patterns = (
        rf"(?:continent|kingdom|forest|city|town|village|region|homeland)\s+(?:of|called)\s+{escaped}\b",
        rf"\b{escaped}\b\s+(?:continent|kingdom|city|town|village|forest|glades|hall)\b",
        rf"\b(?:in|on|from|near|toward|across)\s+(?:the\s+)?{escaped}\b",
    )
    if any(re.search(pattern, text, re.I) for pattern in location_patterns):
        return "location"
    return "character"


def _automatic_analysis_decisions(root: Path) -> Path:
    draft = read_json(root / "analysis/entities.json")
    source_text = (root / "source/manuscript.md").read_text(encoding="utf-8")
    decisions = build_analysis_review_template(draft)
    decisions.update({
        "reviewer": "video-creator-auto-review-v1",
        "reviewer_type": "model_assisted", "reviewed_at": now(),
    })
    used = set()
    draft_entities = {item["entity_id"]: item for item in draft["entities"]}
    kinds = {
        item["entity_id"]: _entity_kind(item["name"], source_text)
        for item in draft["entities"]
    }
    character_names = {
        item["entity_id"]: item["name"] for item in draft["entities"]
        if kinds[item["entity_id"]] == "character"
    }
    aliases: dict[str, str] = {}
    values = list(character_names.items())
    for identifier, name in values:
        folded = name.casefold()
        longer = [
            (other_id, other_name) for other_id, other_name in values
            if other_id != identifier and len(other_name) > len(name)
            and (
                other_name.casefold().startswith(folded + " ")
                or (len(name) >= 3 and other_name.casefold().startswith(folded))
            )
        ]
        if longer:
            aliases[identifier] = max(longer, key=lambda value: len(value[1]))[0]
            continue
        surname_matches = [
            other_id for other_id, other_name in values
            if other_id != identifier and other_name.casefold().endswith(" " + folded)
        ]
        if surname_matches:
            kinds[identifier] = None

    names_by_folded = {name.casefold(): identifier for identifier, name in values}
    for role, pattern in {
        "mother": r"\bmother(?:\s+and\s+father)?[-, ]+([A-Z][a-z]+)\b",
        "father": r"\bfather[-, ]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    }.items():
        role_id = names_by_folded.get(role)
        match = re.search(pattern, source_text)
        target_id = names_by_folded.get(match.group(1).casefold()) if match else None
        if role_id and target_id and role_id != target_id:
            aliases[role_id] = target_id

    decision_by_id = {item["entity_id"]: item for item in decisions["entities"]}
    for alias_id, target_id in aliases.items():
        target = decision_by_id[target_id]
        target["aliases"] = sorted(set(target.get("aliases", [])) | {character_names[alias_id]})

    for index, item in enumerate(decisions["entities"], start=1):
        kind = kinds[item["entity_id"]]
        if kind is None or item["entity_id"] in aliases:
            item["status"] = "rejected"
            continue
        identifier = draft_entities[item["entity_id"]].get("series_canonical_id") or _slug(
            item["canonical_name"], f"entity-{index}",
        )
        while identifier in used:
            identifier = f"{identifier}-{index}"
        used.add(identifier)
        item.update({"status": "approved", "canonical_id": identifier, "kind": kind})
    for index, item in enumerate(decisions["settings"], start=1):
        identifier = _slug(item["canonical_name"], f"setting-{index}")
        while identifier in used:
            identifier = f"{identifier}-{index}"
        used.add(identifier)
        item.update({"status": "approved", "canonical_id": identifier})
    output = root / "analysis/auto-decisions.json"
    write_json_atomic(output, decisions)
    return output


def _automatic_narration_responses(root: Path) -> Path:
    plan = read_json(root / "script/narration.plan.json")
    text = normalize_markdown((root / "source/manuscript.md").read_text(encoding="utf-8"))
    responses = {
        "schema_version": 1, "narration_plan_id": plan["narration_plan_id"],
        "source_sha256": plan["source_sha256"], "provider": "source-faithful-auto-v1",
        "responses": [],
    }
    for block in plan["blocks"]:
        source = text[block["source_start"]:block["source_end"]].strip()
        responses["responses"].append({
            "narration_id": block["narration_id"], "text": source,
            "tone": "expressive narrative", "canonical_entity_ids": block["canonical_entity_ids"],
        })
    output = root / "script/narration.auto-responses.json"
    write_json_atomic(output, responses)
    return output


def run_project(
    root: Path, manuscript: Path, *, project_id: str, title: str,
    rights_status: str = "unverified", series_library: Path | None = None,
    image_provider: ImageProvider | None = None, candidates_per_item: int = 1,
    maximum_attempts: int = 2, offline: bool = False, delegate_audio: bool = True,
) -> dict:
    """Run every stage once, resuming accepted artifacts and failing with one report."""
    root = root.resolve(); manuscript = manuscript.resolve()
    executed: list[str] = []
    try:
        if not manuscript.is_file():
            raise FileNotFoundError(f"manuscript not found: {manuscript}")
        if not (root / "project.json").is_file():
            initialize_project(root, project_id, title, rights_status); executed.append("init")
        manifest = read_json(root / "project.json")
        current_source = ingest_markdown(manuscript)
        source_stage = manifest["stages"].get("source", {})
        if source_stage.get("status") != "generated":
            ingest_project_source(root, manuscript); executed.append("source")
        elif source_stage.get("input_sha256") != current_source["sha256"]:
            raise ValueError("workspace belongs to a different manuscript; use a new workspace and the same series library")

        manifest = read_json(root / "project.json")
        analysis_status = manifest["stages"].get("analysis", {}).get("status")
        if analysis_status in {None, "pending"}:
            analysis = analyze_project_source(root, manuscript)
            if series_library:
                analysis = seed_analysis_with_shared_characters(
                    analysis, normalize_markdown(manuscript.read_text(encoding="utf-8-sig")),
                    series_library,
                )
                write_json_atomic(root / "analysis/entities.json", analysis)
            executed.append("analysis")
            analysis_status = "generated"
        if analysis_status == "generated":
            approve_project_analysis(root, _automatic_analysis_decisions(root)); executed.append("analysis_review")

        manifest = read_json(root / "project.json")
        narration_status = manifest["stages"].get("narration", {}).get("status")
        if narration_status in {None, "pending"}:
            plan_project_narration(root, manuscript); executed.append("narration_plan")
            narration_status = "planned"
        if narration_status == "planned":
            adapt_project_narration(root, manuscript, _automatic_narration_responses(root))
            executed.append("narration_adaptation")

        manifest = read_json(root / "project.json")
        scenes_status = manifest["stages"].get("scenes", {}).get("status")
        if scenes_status in {None, "pending"}:
            segment_project_scenes(root); executed.append("scene_segmentation"); scenes_status = "draft"
        if scenes_status == "draft":
            result = enrich_project_scenes(root, maximum_attempts=maximum_attempts)
            executed.append("scene_enrichment")
            if result["status"] != "auto_accepted":
                raise RuntimeError("scene enrichment exhausted its automatic retry budget")

        manifest = read_json(root / "project.json")
        if manifest["stages"].get("storyboard", {}).get("status") != "auto_accepted":
            plan_project_storyboard(root); executed.append("storyboard")
        manifest = read_json(root / "project.json")
        if manifest["stages"].get("prompts", {}).get("status") != "auto_accepted":
            compile_project_prompts(root); executed.append("prompts")

        manifest = read_json(root / "project.json")
        if manifest["stages"].get("canonical_references", {}).get("status") != "auto_accepted":
            prompts = read_json(root / manifest["stages"]["prompts"]["artifact"])
            reused = load_shared_references(series_library, prompts, root) if series_library else {}
            generate_project_character_references(
                root, image_provider, candidates_per_item=candidates_per_item,
                maximum_attempts=maximum_attempts, reused_references=reused,
            ); executed.append("character_references")
            review = review_project_character_references(root); executed.append("character_reference_review")
            if review["status"] != "auto_accepted":
                raise RuntimeError("character reference review exhausted its automatic retry budget")
            if series_library:
                manifest = read_json(root / "project.json")
                publish_shared_references(
                    series_library,
                    read_json(root / manifest["stages"]["canonical_references"]["artifact"]),
                    prompts, root,
                ); executed.append("series_reference_publish")

        manifest = read_json(root / "project.json")
        if manifest["stages"].get("shot_pilot", {}).get("status") != "generated" and manifest["stages"].get("shot_pilot_review", {}).get("status") != "auto_accepted":
            generate_project_shot_pilot(root, image_provider, candidates_per_item=1,
                                        maximum_attempts=maximum_attempts)
            executed.append("shot_pilot")
        manifest = read_json(root / "project.json")
        if manifest["stages"].get("shot_pilot_review", {}).get("status") != "auto_accepted":
            review = review_project_shot_pilot(root); executed.append("shot_pilot_review")
            if review["status"] != "auto_accepted":
                raise RuntimeError("shot pilot review exhausted its automatic retry budget")

        manifest = read_json(root / "project.json")
        if manifest["stages"].get("images", {}).get("status") != "auto_accepted":
            generate_project_images(root, image_provider, candidates_per_item=candidates_per_item,
                                    maximum_attempts=maximum_attempts)
            executed.append("images")
        manifest = read_json(root / "project.json")
        if manifest["stages"].get("image_review", {}).get("status") != "auto_accepted":
            review = review_project_images(root); executed.append("image_review")
            if review["status"] != "auto_accepted":
                raise RuntimeError("production image review exhausted its automatic retry budget")

        manifest = read_json(root / "project.json")
        if manifest["stages"].get("audio", {}).get("status") != "auto_accepted":
            if delegate_audio and not os.environ.get(AUDIO_ACTIVE_FLAG):
                arguments = ["generate-audio", str(root)] + (["--offline"] if offline else [])
                if run_local_audio(arguments) != 0:
                    raise RuntimeError("managed audio environment failed to generate narration")
            else:
                generate_project_narration_audio(root)
            executed.append("audio")

        for stage, status, function in (
            ("subtitles", "auto_accepted", align_project_subtitles),
            ("timeline", "auto_accepted", compile_project_timeline),
            ("render", "rendered", render_project_video),
            ("evaluation", "passed", evaluate_project),
        ):
            manifest = read_json(root / "project.json")
            if manifest["stages"].get(stage, {}).get("status") != status:
                function(root); executed.append(stage)

        issues = validate_project(root)
        if issues:
            raise RuntimeError("final project validation failed: " + "; ".join(issues))
        report = {
            "schema_version": 1, "status": "completed", "workspace": _portable_path(root),
            "series_library": _portable_path(series_library),
            "executed_stages": executed, "next_action": "verify rights before release",
        }
    except Exception as error:
        report = {
            "schema_version": 1, "status": "needs_attention", "workspace": _portable_path(root),
            "series_library": _portable_path(series_library),
            "executed_stages": executed, "error": str(error),
            "next_action": "resolve the reported exception and rerun the same command",
        }
    write_json_atomic(root / "reports/run-report.json", report)
    return report
