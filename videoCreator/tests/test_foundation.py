"""Foundation tests for project scaffolding and Markdown lineage."""

import json

import pytest

from video_creator.analysis import (
    ExtractiveAnalysisProvider, apply_analysis_decisions,
    build_analysis_review_template, validate_analysis,
)
from video_creator.project import (
    analyze_project_source, ingest_project_source, initialize_project, validate_project,
)
from video_creator.narration import (
    MappingNarrationProvider, adapt_narration, build_narration_plan,
    build_narration_response_template, validate_adapted_narration,
    validate_narration_plan,
)
from video_creator.scenes import (
    DeterministicSceneEnrichmentProvider, enrich_scenes, segment_scenes,
    validate_enriched_scenes, validate_scenes,
)
from video_creator.source import ingest_markdown, validate_source
from video_creator.storyboard import plan_storyboard, validate_storyboard


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


def test_analysis_approval_requires_complete_source_bound_decisions(tmp_path):
    manuscript = tmp_path / "story.md"
    manuscript.write_text("## City\n\nMira met Mira.\n", encoding="utf-8")
    source = ingest_markdown(manuscript)
    draft = ExtractiveAnalysisProvider().analyze(
        manuscript.read_text(encoding="utf-8"), source["sha256"],
    )
    decisions = build_analysis_review_template(draft)
    decisions.update({
        "reviewer": "editor", "reviewer_type": "human",
        "reviewed_at": "2026-08-20T20:00:00Z",
    })
    decisions["entities"][0].update({
        "status": "approved", "canonical_id": "mira", "canonical_name": "Mira",
        "kind": "character", "aliases": ["Mira"],
    })
    decisions["settings"][0].update({
        "status": "approved", "canonical_id": "city", "canonical_name": "City",
    })
    approved = apply_analysis_decisions(draft, decisions)
    assert approved["status"] == "approved" and approved["release_usable"] is True
    assert approved["entities"][0]["canonical_id"] == "mira"
    assert validate_analysis(approved, source) == []


def test_analysis_approval_rejects_stale_or_pending_decisions(tmp_path):
    manuscript = tmp_path / "story.md"
    manuscript.write_text("# Story\n\nMira met Mira.\n", encoding="utf-8")
    source = ingest_markdown(manuscript)
    draft = ExtractiveAnalysisProvider().analyze(
        manuscript.read_text(encoding="utf-8"), source["sha256"],
    )
    decisions = build_analysis_review_template(draft)
    decisions.update({
        "reviewer": "editor", "reviewer_type": "human",
        "reviewed_at": "2026-08-20T20:00:00Z",
    })
    decisions["analysis_fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="stale"):
        apply_analysis_decisions(draft, decisions)


def test_model_assisted_review_is_planning_only(tmp_path):
    manuscript = tmp_path / "story.md"
    manuscript.write_text("# Story\n\nMira met Mira.\n", encoding="utf-8")
    source = ingest_markdown(manuscript)
    draft = ExtractiveAnalysisProvider().analyze(
        manuscript.read_text(encoding="utf-8"), source["sha256"],
    )
    decisions = build_analysis_review_template(draft)
    decisions.update({
        "reviewer": "fixture-model", "reviewer_type": "model_assisted",
        "reviewed_at": "2026-08-20T20:00:00Z",
    })
    decisions["entities"][0].update({
        "status": "approved", "canonical_id": "mira", "canonical_name": "Mira",
        "kind": "character",
    })
    decisions["settings"][0]["status"] = "rejected"
    reviewed = apply_analysis_decisions(draft, decisions)
    assert reviewed["status"] == "reviewed_draft"
    assert reviewed["planning_usable"] is True
    assert reviewed["release_usable"] is False


def test_narration_plan_uses_bounded_source_ranges_and_canonical_ids(tmp_path):
    text = "# Story\n\nMira entered.\n\nMira waited.\n\nNight fell.\n"
    manuscript = tmp_path / "story.md"
    manuscript.write_text(text, encoding="utf-8")
    source = ingest_markdown(manuscript)
    analysis = {
        "analysis_id": "analysis-0001", "status": "reviewed_draft",
        "planning_usable": True,
        "entities": [{
            "review_status": "approved", "canonical_id": "mira",
            "name": "Mira", "canonical_name": "Mira", "aliases": [],
        }],
    }
    plan = build_narration_plan(text, source, analysis, maximum_source_characters=200)
    assert plan["blocks"][0]["canonical_entity_ids"] == ["mira"]
    assert all(block["adapted_text"] is None for block in plan["blocks"])
    assert validate_narration_plan(plan, source, analysis) == []


def test_narration_plan_never_crosses_source_sections(tmp_path):
    text = "## First\n\nMira waits.\n\n## Second\n\nMira leaves.\n"
    manuscript = tmp_path / "story.md"
    manuscript.write_text(text, encoding="utf-8")
    source = ingest_markdown(manuscript)
    analysis = {
        "analysis_id": "analysis-0001", "status": "reviewed_draft",
        "planning_usable": True,
        "entities": [{
            "review_status": "approved", "canonical_id": "mira",
            "name": "Mira", "canonical_name": "Mira", "aliases": [],
        }],
    }
    plan = build_narration_plan(text, source, analysis, maximum_source_characters=1000)
    assert len(plan["blocks"]) == 2
    assert plan["blocks"][0]["source_end"] <= source["sections"][1]["source_start"]


def test_narration_plan_rejects_unreviewed_analysis(tmp_path):
    manuscript = tmp_path / "story.md"
    manuscript.write_text("# Story\n\nA synthetic paragraph.\n", encoding="utf-8")
    source = ingest_markdown(manuscript)
    with pytest.raises(ValueError, match="reviewed canonical"):
        build_narration_plan(
            manuscript.read_text(encoding="utf-8"), source,
            {"analysis_id": "draft", "planning_usable": False},
        )


def adapted_fixture(tmp_path):
    """Return synthetic source, analysis, plan, and adapted narration."""
    text = "# Story\n\nMira entered the city.\n\nMira waited for dawn.\n\nNight fell quietly.\n"
    manuscript = tmp_path / "story.md"
    manuscript.write_text(text, encoding="utf-8")
    source = ingest_markdown(manuscript)
    analysis = {
        "analysis_id": "analysis-0001", "status": "reviewed_draft",
        "planning_usable": True,
        "entities": [{
            "review_status": "approved", "canonical_id": "mira", "name": "Mira",
            "canonical_name": "Mira", "aliases": [],
        }],
        "settings": [{
            "review_status": "approved", "canonical_id": "city",
            "source_start": 0,
        }],
    }
    plan = build_narration_plan(text, source, analysis, maximum_source_characters=200)
    responses = {
        block["narration_id"]: {
            "text": "Mira entered the city and waited until night fell.",
            "tone": "quiet", "canonical_entity_ids": ["mira"],
        } for block in plan["blocks"]
    }
    narration = adapt_narration(plan, text, MappingNarrationProvider(responses))
    return source, analysis, plan, narration


def test_adaptation_and_scene_contracts_cover_every_block(tmp_path):
    _source, analysis, plan, narration = adapted_fixture(tmp_path)
    assert validate_adapted_narration(narration, plan) == []
    scenes = segment_scenes(narration, analysis, maximum_blocks=2)
    assert validate_scenes(scenes, narration, analysis) == []
    assert [item for scene in scenes["scenes"] for item in scene["narration_ids"]] == [
        block["narration_id"] for block in narration["blocks"]
    ]


def test_scene_validation_rejects_setting_boundary_crossing(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    analysis["settings"].append({
        "review_status": "approved", "canonical_id": "later-city",
        "source_start": narration["blocks"][0]["source_start"] + 1,
    })
    scenes = segment_scenes(narration, analysis)
    assert validate_scenes(scenes, narration, analysis) == [
        "scene crosses setting boundary for scene-0001: ['later-city']",
    ]


def test_automatic_scene_enrichment_promotes_complete_decisions(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    draft = segment_scenes(narration, analysis)
    enriched = enrich_scenes(draft, narration)
    assert enriched["status"] == "auto_accepted"
    assert enriched["exception_report"] == []
    assert all(scene["status"] == "auto_accepted" for scene in enriched["scenes"])
    assert all(scene["automatic_qa"]["decision"] == "accept" for scene in enriched["scenes"])
    assert validate_enriched_scenes(enriched, narration, analysis) == []


def test_automatic_scene_enrichment_uses_fallback_after_retries(tmp_path):
    class IncompleteProvider:
        name = "incomplete-fixture"

        def enrich(self, scene, narration_blocks):
            return {"story_event": "", "mood": "quiet", "visual_intent": ""}

    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    draft = segment_scenes(narration, analysis)
    enriched = enrich_scenes(draft, narration, IncompleteProvider())
    assert enriched["status"] == "auto_accepted"
    assert enriched["exception_report"] == []
    qa = enriched["scenes"][0]["automatic_qa"]
    assert qa["maximum_attempts"] == 2
    assert qa["used_fallback"] is True
    assert [attempt["decision"] for attempt in qa["attempts"]] == [
        "retry", "retry", "accept",
    ]
    assert validate_enriched_scenes(enriched, narration, analysis) == []


def test_automatic_scene_enrichment_reports_exhausted_fallback(tmp_path):
    class IncompleteProvider:
        name = "incomplete-fixture"

        def enrich(self, scene, narration_blocks):
            return {"story_event": "", "mood": "quiet", "visual_intent": ""}

    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    draft = segment_scenes(narration, analysis)
    provider = IncompleteProvider()
    enriched = enrich_scenes(
        draft, narration, provider, maximum_attempts=1, fallback_provider=provider,
    )
    assert enriched["status"] == "retry_required"
    assert enriched["exception_report"][0]["next_action"] == "retry"
    assert validate_enriched_scenes(enriched, narration, analysis) == [
        "missing story_event for scene-0001",
        "missing visual_intent for scene-0001",
    ]


def test_scene_enrichment_selectively_regenerates_changed_dependencies(tmp_path):
    class CountingProvider:
        name = "counting-fixture-v1"

        def __init__(self):
            self.calls = []
            self.delegate = DeterministicSceneEnrichmentProvider()

        def enrich(self, scene, narration_blocks):
            self.calls.append(scene["scene_id"])
            return self.delegate.enrich(scene, narration_blocks)

    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    second = json.loads(json.dumps(narration["blocks"][0]))
    second["narration_id"] = "narration-0002"
    second["source_start"] = second["source_end"] + 1
    second["source_end"] = second["source_start"] + 20
    narration["blocks"].append(second)
    draft = segment_scenes(narration, analysis, maximum_blocks=1)
    provider = CountingProvider()
    first = enrich_scenes(draft, narration, provider)
    assert provider.calls == ["scene-0001", "scene-0002"]

    provider.calls.clear()
    narration["blocks"][0]["adapted_text"] += " A supported revision."
    revised_draft = segment_scenes(narration, analysis, maximum_blocks=1)
    second_result = enrich_scenes(
        revised_draft, narration, provider, previous_scenes=first,
    )
    assert provider.calls == ["scene-0001"]
    assert second_result["regeneration"] == {
        "reused_scene_ids": ["scene-0002"],
        "regenerated_scene_ids": ["scene-0001"],
    }
    assert second_result["scenes"][1] == first["scenes"][1]


def test_storyboard_covers_scenes_and_selectively_reuses_shots(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    duplicate = json.loads(json.dumps(scenes["scenes"][0]))
    duplicate["scene_id"] = "scene-0002"
    duplicate["dependency_sha256"] = "2" * 64
    scenes["scenes"].append(duplicate)
    first = plan_storyboard(scenes, target_shot_seconds=15)
    assert validate_storyboard(first, scenes) == []
    assert {shot["scene_id"] for shot in first["shots"]} == {"scene-0001", "scene-0002"}

    scenes["scenes"][0]["dependency_sha256"] = "3" * 64
    second = plan_storyboard(
        scenes, target_shot_seconds=15, previous_storyboard=first,
    )
    assert all(identifier.startswith("scene-0001") for identifier in (
        second["regeneration"]["regenerated_shot_ids"]
    ))
    assert all(identifier.startswith("scene-0002") for identifier in (
        second["regeneration"]["reused_shot_ids"]
    ))
    assert validate_storyboard(second, scenes) == []


def test_adaptation_blocks_invented_numbers_and_entities(tmp_path):
    _source, _analysis, plan, _narration = adapted_fixture(tmp_path)
    provider = MappingNarrationProvider({
        plan["blocks"][0]["narration_id"]: {
            "text": "Mira met 99 dragons.", "tone": "tense",
            "canonical_entity_ids": ["invented"],
        },
    })
    with pytest.raises(ValueError, match="unsupported numbers.*unsupported entities"):
        adapt_narration(plan, "# Story\n\nMira entered the city.\n\nMira waited for dawn.\n\nNight fell quietly.\n", provider)


def test_response_template_covers_all_planned_blocks(tmp_path):
    _source, _analysis, plan, _narration = adapted_fixture(tmp_path)
    template = build_narration_response_template(plan)
    assert [item["narration_id"] for item in template["responses"]] == [
        block["narration_id"] for block in plan["blocks"]
    ]
    assert all(item["text"] is None for item in template["responses"])
