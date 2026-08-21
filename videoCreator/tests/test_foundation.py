"""Foundation tests for project scaffolding and Markdown lineage."""

import json
import sys
from types import SimpleNamespace

import pytest

from video_creator.analysis import (
    ExtractiveAnalysisProvider, apply_analysis_decisions,
    build_analysis_review_template, validate_analysis,
)
from video_creator.artifacts import sha256_file
from video_creator.audio import (
    DeterministicToneProvider, generate_narration_audio, validate_narration_audio,
)
from video_creator.images import (
    AnimeIPAdapterImageProvider, DeterministicFixtureImageProvider, SanaImageProvider, generate_assets,
    validate_assets,
)
from video_creator.project import (
    analyze_project_source, ingest_project_source, initialize_project, validate_project,
)
from video_creator.narration import (
    MappingNarrationProvider, adapt_narration, build_narration_plan,
    build_narration_response_template, validate_adapted_narration,
    validate_narration_plan,
)
from video_creator.prompts import (
    DEFAULT_VISUAL_STYLE, _compact_event, _visualize_narrative_beat, compile_prompts,
    validate_prompts,
)
from video_creator.scenes import (
    DeterministicSceneEnrichmentProvider, enrich_scenes, segment_scenes,
    validate_enriched_scenes, validate_scenes,
)
from video_creator.subtitles import _chunks, align_narration, validate_alignment, write_subtitles
from video_creator.source import ingest_markdown, validate_source
from video_creator.storyboard import plan_storyboard, validate_storyboard
from video_creator.visual_review import SmolVLMReviewer, review_character_references, review_shot_assets


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


def test_extractive_analysis_keeps_explicit_single_mention_fictional_location():
    text = "They settled in a remote city called Ashber before winter arrived."
    analysis = ExtractiveAnalysisProvider().analyze(text, "a" * 64)
    candidates = {item["name"]: item for item in analysis["entities"]}
    assert candidates["Ashber"]["mention_count"] == 1


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
            "canonical_name": "Mira", "aliases": [], "kind": "character",
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


def test_storyboard_links_characters_only_to_their_local_shot_sentence():
    scenes = {
        "status": "auto_accepted", "scene_plan_id": "scene-plan-0001",
        "source_sha256": "a" * 64, "scenes": [{
            "scene_id": "scene-0001", "status": "auto_accepted",
            "estimated_narration_seconds": 20, "setting_id": "prologue",
            "canonical_entity_ids": ["arthur"], "mood": "calm",
            "visual_intent": "Establish prologue", "dependency_sha256": "b" * 64,
        }],
    }
    narration = {"blocks": [{
        "narration_id": "narration-0001",
        "adapted_text": "The continent stretched beyond the mountains. Arthur opened the book.",
    }]}
    scenes["scenes"][0]["narration_ids"] = ["narration-0001"]
    analysis = {"entities": [{
        "review_status": "approved", "kind": "character", "canonical_id": "arthur",
        "name": "Arthur", "canonical_name": "Arthur", "aliases": ["Art"],
    }]}
    storyboard = plan_storyboard(
        scenes, target_shot_seconds=10, narration=narration, analysis=analysis,
    )
    assert storyboard["shots"][0]["canonical_entity_ids"] == []
    assert storyboard["shots"][1]["canonical_entity_ids"] == ["arthur"]
    assert validate_storyboard(storyboard, scenes) == []


def test_storyboard_resolves_pronoun_continuation_to_last_character():
    scenes = {
        "status": "auto_accepted", "scene_plan_id": "scene-plan-0001",
        "source_sha256": "a" * 64, "scenes": [{
            "scene_id": "scene-0001", "status": "auto_accepted",
            "estimated_narration_seconds": 20, "setting_id": "home",
            "canonical_entity_ids": ["alice", "arthur"], "mood": "warm",
            "visual_intent": "Establish home", "dependency_sha256": "b" * 64,
            "narration_ids": ["narration-0001"],
        }],
    }
    narration = {"blocks": [{
        "narration_id": "narration-0001",
        "adapted_text": "Alice entered the nursery. She lifted Arthur from his crib.",
    }]}
    analysis = {"entities": [
        {"review_status": "approved", "kind": "character", "canonical_id": "alice",
         "name": "Alice", "canonical_name": "Alice", "aliases": []},
        {"review_status": "approved", "kind": "character", "canonical_id": "arthur",
         "name": "Arthur", "canonical_name": "Arthur", "aliases": []},
    ]}
    shots = plan_storyboard(
        scenes, target_shot_seconds=10, narration=narration, analysis=analysis,
    )["shots"]
    assert shots[0]["canonical_entity_ids"] == ["alice"]
    assert shots[1]["canonical_entity_ids"] == ["alice", "arthur"]


def test_prompt_conditioning_is_limited_to_primary_visible_character(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    analysis["entities"].append({
        "review_status": "approved", "kind": "character", "canonical_id": "rowan",
        "name": "Rowan", "canonical_name": "Rowan", "aliases": [],
    })
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    scenes["scenes"][0]["canonical_entity_ids"] = ["mira", "rowan"]
    storyboard = plan_storyboard(scenes)
    prompts = compile_prompts(storyboard, analysis)
    assert all(len(item["reference_ids"]) == 1 for item in prompts["prompts"])


def test_image_generation_preserves_underlying_provider_errors(tmp_path):
    class FailingProvider:
        name = "failing-provider"

        def generate(self, *_args, **_kwargs):
            raise RuntimeError("CUDA out of memory")

    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    prompts = compile_prompts(plan_storyboard(scenes), analysis)
    with pytest.raises(ValueError, match="CUDA out of memory"):
        generate_assets(
            prompts, tmp_path, FailingProvider(), candidates_per_item=1,
            maximum_attempts=1, asset_kinds=frozenset({"shot"}),
        )


def test_prompt_compilation_is_complete_optional_and_selective(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    storyboard = plan_storyboard(scenes)
    first = compile_prompts(storyboard, analysis)
    assert validate_prompts(first, storyboard, analysis) == []
    assert first["reference_requirements"] == [{
        "reference_id": "character-mira",
        "canonical_entity_id": "mira",
        "canonical_name": "Mira",
        "aliases": ["Mira"],
        "source_evidence": [],
        "reference_prompt": (
            "Single full-body reference portrait of Mira, exactly one human character, one front view, "
            "natural standing pose, neutral plain background, anime-style illustration, polished "
            "cinematic anime key art, clean anime linework, cel shading. Appearance: human adult person. "
            "No text, no captions, no panels, no turnaround sheet, no alternate views, no robotic or "
            "non-human anatomy."
        ),
        "character_profile": {
            "name": "Mira", "species": "human", "age_stage": "adult",
            "presentation": "person", "visible_traits": [], "source_evidence": [],
        },
        "brief_compiler": "source-character-profile-v2",
        "visual_constraints": ["human adult person"],
        "selection_mode": "optional_user_override",
        "default_action": "generate_and_auto_rank",
        "status": "default_ready",
    }]
    assert all(item["reference_ids"] == ["character-mira"] for item in first["prompts"])
    second = compile_prompts(storyboard, analysis, previous=first)
    assert second["regeneration"]["regenerated_shot_ids"] == []
    assert second["regeneration"]["reused_shot_ids"] == [
        shot["shot_id"] for shot in storyboard["shots"]
    ]


def test_character_reference_prompt_includes_bounded_source_evidence(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    storyboard = plan_storyboard(scenes)
    compiled = compile_prompts(
        storyboard, analysis,
        source_text="Mira was a young pilot in a blue uniform. " * 20,
    )
    requirement = compiled["reference_requirements"][0]
    assert requirement["source_evidence"]
    assert "young pilot in a blue uniform" in requirement["source_evidence"][0]
    assert "Evidence:" not in requirement["reference_prompt"]
    assert len(requirement["source_evidence"]) <= 3


def test_character_profiles_are_source_grounded_and_single_view(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    analysis["entities"][0]["aliases"] = ["Mother"]
    source = (
        "Mother Alice had striking auburn hair and brown eyes, long eye lashes, and a perky nose. "
        "Alice smiled at her newborn baby."
    )
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    requirement = compile_prompts(
        plan_storyboard(scenes), analysis, source_text=source,
    )["reference_requirements"][0]
    assert requirement["character_profile"]["species"] == "human"
    assert requirement["character_profile"]["presentation"] == "woman"
    assert any(
        trait.endswith("auburn hair")
        for trait in requirement["character_profile"]["visible_traits"]
    )
    assert "brown eyes" in requirement["character_profile"]["visible_traits"]
    assert "exactly one human character" in requirement["reference_prompt"]
    assert "no turnaround sheet" in requirement["reference_prompt"].casefold()


def test_compact_event_keeps_one_coherent_contiguous_event():
    event = " ".join(f"word{index}" for index in range(50))
    compact = _compact_event(event)
    assert compact.split() == event.split()[:36]


def test_contrastive_narrative_beat_becomes_visual_exclusion():
    beat = "Revelation beat: Instead of a train platform, there is a stone orphanage."
    visual = _visualize_narrative_beat(beat)
    assert "Show a stone orphanage" in visual
    assert "train platform" not in visual


def test_default_prompts_enforce_anime_illustration_style(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    prompts = compile_prompts(plan_storyboard(scenes, target_shot_seconds=30), analysis)
    assert all(item["style"] == DEFAULT_VISUAL_STYLE for item in prompts["prompts"])
    assert all("Anime cinematic story scene" in item["prompt"] for item in prompts["prompts"])
    assert all("photorealism" in item["negative_prompt"] for item in prompts["prompts"])
    assert all(
        DEFAULT_VISUAL_STYLE in item["reference_prompt"]
        for item in prompts["reference_requirements"]
    )


def test_shot_generation_consumes_canonical_reference_images(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    prompts = compile_prompts(plan_storyboard(scenes, target_shot_seconds=30), analysis)
    reference = tmp_path / "references" / "mira.png"
    DeterministicFixtureImageProvider().generate("Mira", reference, seed=7)
    assets = generate_assets(
        prompts, tmp_path, candidates_per_item=1, asset_kinds=frozenset({"shot"}),
        canonical_references={
            "character-mira": {"path": "references/mira.png", "sha256": sha256_file(reference)},
        },
        reference_conditioning=True,
    )
    assert assets["reference_conditioning"] is True
    assert validate_assets(assets, prompts, tmp_path) == []


def test_ip_adapter_generates_character_free_shot_with_zero_strength(tmp_path, monkeypatch):
    from PIL import Image

    class FakePipeline:
        def __init__(self):
            self.scales = []
            self.arguments = None

        def set_ip_adapter_scale(self, scale):
            self.scales.append(scale)

        def __call__(self, **arguments):
            self.arguments = arguments
            return SimpleNamespace(images=[Image.new("RGB", (8, 8), "blue")])

    class FakeGenerator:
        def __init__(self, device):
            self.device = device

        def manual_seed(self, seed):
            self.seed = seed
            return self

    pipeline = FakePipeline()
    provider = AnimeIPAdapterImageProvider(device="cpu")
    provider._pipeline = pipeline
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(Generator=FakeGenerator))
    output = tmp_path / "environment.png"

    provider.generate("an empty nursery at dawn", output, seed=17)

    assert output.is_file()
    assert pipeline.scales == [0.0]
    assert pipeline.arguments["ip_adapter_image"][0].size == (512, 512)
    assert pipeline.arguments["generator"].seed == 17


def test_narration_audio_is_complete_and_selectively_reused(tmp_path):
    _source, _analysis, _plan, narration = adapted_fixture(tmp_path)
    first = generate_narration_audio(narration, tmp_path, DeterministicToneProvider())
    assert validate_narration_audio(first, narration, tmp_path) == []
    second = generate_narration_audio(
        narration, tmp_path, DeterministicToneProvider(), previous=first,
    )
    assert second["regeneration"]["reused_ids"] == [
        block["narration_id"] for block in narration["blocks"]
    ]

    alignment = align_narration(narration, first)
    assert validate_alignment(alignment) == []
    write_subtitles(alignment, tmp_path / "out.srt", tmp_path / "out.vtt")
    assert "WEBVTT" in (tmp_path / "out.vtt").read_text(encoding="utf-8")


def test_subtitle_chunks_split_oversized_hyphenated_word_without_text_loss():
    text = "shining-light-with-a-faint-hum-from-her-freaking- hands type of healing."
    chunks = _chunks(text)
    assert chunks == [
        "shining-light-with-a-faint-hum-from-her-",
        "freaking- hands type of healing.",
    ]
    assert all(len(chunk) <= 42 for chunk in chunks)
    assert "".join(chunks) == text


def test_fixture_images_are_ranked_selected_and_hash_validated(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    storyboard = plan_storyboard(scenes, target_shot_seconds=30)
    prompts = compile_prompts(storyboard, analysis)
    assets = generate_assets(prompts, tmp_path, candidates_per_item=2)
    assert validate_assets(assets, prompts, tmp_path) == []
    assert len(assets["assets"]) == len(prompts["prompts"]) + 1
    assert all(len(item["candidates"]) == 2 for item in assets["assets"])
    assert all(item["selection"] == "automatic_rank" for item in assets["assets"])
    selected = assets["assets"][0]
    winner = max(
        selected["candidates"], key=lambda item: (item["score"]["total"], item["candidate_id"]),
    )
    assert selected["selected_candidate_id"] == winner["candidate_id"]

    damaged = tmp_path / selected["candidates"][0]["path"]
    damaged.write_bytes(b"damaged")
    assert validate_assets(assets, prompts, tmp_path) == [
        f"candidate hash mismatch: {selected['candidates'][0]['candidate_id']}",
    ]


def test_image_assets_reuse_valid_files_without_provider_calls(tmp_path):
    class CountingProvider:
        name = "counting-image-v1"

        def __init__(self):
            self.calls = 0
            self.delegate = DeterministicFixtureImageProvider()

        def generate(self, prompt, output, *, seed):
            self.calls += 1
            self.delegate.generate(prompt, output, seed=seed)

    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    prompts = compile_prompts(plan_storyboard(scenes, target_shot_seconds=30), analysis)
    provider = CountingProvider()
    first = generate_assets(prompts, tmp_path, provider, candidates_per_item=1)
    assert provider.calls == len(first["assets"])
    provider.calls = 0
    second = generate_assets(
        prompts, tmp_path, provider, candidates_per_item=1, previous=first,
    )
    assert provider.calls == 0
    assert second["regeneration"]["regenerated_asset_ids"] == []
    assert second["regeneration"]["reused_asset_ids"] == [
        item["asset_id"] for item in first["assets"]
    ]


def test_character_reference_hash_invalidates_only_dependent_shots(tmp_path):
    class CountingProvider:
        name = "counting-reference-dependency-v1"

        def __init__(self):
            self.calls = []
            self.delegate = DeterministicFixtureImageProvider()

        def generate(self, prompt, output, *, seed):
            self.calls.append(output)
            self.delegate.generate(prompt, output, seed=seed)

    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    prompts = compile_prompts(plan_storyboard(scenes, target_shot_seconds=30), analysis)
    independent = json.loads(json.dumps(prompts["prompts"][0]))
    independent["shot_id"] = "independent-shot"
    prompts["prompts"].append(independent)
    prompts["prompts"][1]["reference_ids"] = []
    provider = CountingProvider()
    first = generate_assets(
        prompts, tmp_path, provider, candidates_per_item=1,
        asset_kinds=frozenset({"shot"}), canonical_references={"character-mira": "a" * 64},
    )
    provider.calls.clear()
    second = generate_assets(
        prompts, tmp_path, provider, candidates_per_item=1, previous=first,
        asset_kinds=frozenset({"shot"}), canonical_references={"character-mira": "b" * 64},
    )
    assert prompts["prompts"][0]["shot_id"] in second["regeneration"]["regenerated_asset_ids"]
    assert prompts["prompts"][1]["shot_id"] in second["regeneration"]["reused_asset_ids"]


def test_image_provider_failures_retry_then_use_fallback(tmp_path):
    class FailingProvider:
        name = "failing-image-v1"

        def generate(self, prompt, output, *, seed):
            raise RuntimeError("temporary provider failure")

    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    prompts = compile_prompts(plan_storyboard(scenes, target_shot_seconds=30), analysis)
    assets = generate_assets(
        prompts, tmp_path, FailingProvider(), candidates_per_item=1, maximum_attempts=2,
        fallback_provider=DeterministicFixtureImageProvider(),
    )
    attempts = assets["assets"][0]["candidates"][0]["generation_attempts"]
    assert [item["status"] for item in attempts] == ["failed", "failed", "generated"]
    assert attempts[-1]["provider"] == "deterministic-fixture-image-v1"
    assert validate_assets(assets, prompts, tmp_path) == []


def test_sana_provider_requires_a_complete_local_cache(tmp_path):
    provider = SanaImageProvider(cache_directory=tmp_path / "missing-model")
    with pytest.raises(RuntimeError, match="local Sana model cache is missing"):
        provider.generate("synthetic prompt", tmp_path / "output.png", seed=1)
    assert "Sana_1600M_1024px_diffusers" in provider.name
    assert "@ac0da2ff55fb" in provider.name


def test_character_reference_generation_can_run_before_shot_images(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    prompts = compile_prompts(plan_storyboard(scenes, target_shot_seconds=30), analysis)
    references = generate_assets(
        prompts, tmp_path, candidates_per_item=2,
        asset_kinds=frozenset({"character_reference"}),
        asset_namespace="reference-stage",
    )
    assert references["asset_kinds"] == ["character_reference"]
    assert references["asset_namespace"] == "reference-stage"
    assert [item["kind"] for item in references["assets"]] == ["character_reference"]
    assert validate_assets(references, prompts, tmp_path) == []


def test_semantic_character_review_fails_closed_and_selects_passing_candidate(tmp_path):
    class Reviewer:
        name = "fixture-reviewer"

        def review(self, image, brief):
            accepted = image.name == "candidate-02.png"
            return {"accepted": accepted, "score": 0.9 if accepted else 0.2, "reasons": [brief[:20]]}

    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    prompts = compile_prompts(plan_storyboard(scenes, target_shot_seconds=30), analysis)
    assets = generate_assets(
        prompts, tmp_path, candidates_per_item=2,
        asset_kinds=frozenset({"character_reference"}),
    )
    review = review_character_references(assets, prompts, tmp_path, Reviewer())
    assert review["status"] == "auto_accepted"
    assert review["assets"][0]["selected_candidate_id"].endswith("candidate-02")


def test_shot_pilot_rejects_camera_only_prompt_variants(tmp_path):
    class Reviewer:
        name = "accepting-reviewer"

        def review(self, image, brief):
            return {"accepted": True, "score": 0.9, "reasons": ["accepted"]}

    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    prompts = compile_prompts(plan_storyboard(scenes, target_shot_seconds=30), analysis)
    duplicate = json.loads(json.dumps(prompts["prompts"][0]))
    duplicate["shot_id"] = "duplicate-shot"
    duplicate["prompt"] = duplicate["prompt"].replace("slow push", "slow pan")
    prompts["prompts"].append(duplicate)
    assets = generate_assets(
        prompts, tmp_path, candidates_per_item=1, asset_kinds=frozenset({"shot"}),
    )
    review = review_shot_assets(assets, prompts, tmp_path, Reviewer())
    assert review["status"] == "retry_required"
    assert review["issues"] == [
        "pilot shots require distinct narrative visual beats, not camera-only variants",
    ]


def test_prompts_carry_machine_reviewable_scene_contract(tmp_path):
    _source, analysis, _plan, narration = adapted_fixture(tmp_path)
    scenes = enrich_scenes(segment_scenes(narration, analysis), narration)
    prompts = compile_prompts(plan_storyboard(scenes, target_shot_seconds=30), analysis)
    item = prompts["prompts"][0]
    assert item["scene_contract"]["visible_event"]
    assert item["scene_contract"]["setting"]
    assert item["scene_contract"]["characters"] == ["Mira"]
    assert "story scene" in item["prompt"]
    assert "Event:" in item["prompt"]


def test_semantic_reviewer_rejects_bare_accept_response(tmp_path):
    image = tmp_path / "candidate.png"
    image.write_bytes(b"fixture")
    reviewer = SmolVLMReviewer()
    reviewer._pipeline = lambda **_kwargs: [{"generated_text": "ACCEPT"}]
    result = reviewer.review_scene(image, {
        "setting": "nursery", "visible_event": "an infant struggles to breathe",
        "characters": ["Tanya"], "mood": "disoriented",
    }, [])
    assert result["accepted"] is False
    assert result["score"] == 0.0
    assert result["reasons"][0].startswith("invalid response")


def test_semantic_scene_review_sends_canonical_reference_image(tmp_path):
    scene = tmp_path / "scene.png"
    reference = tmp_path / "alice.png"
    scene.write_bytes(b"scene")
    reference.write_bytes(b"reference")
    captured = {}
    reviewer = SmolVLMReviewer()

    def pipeline(**kwargs):
        captured.update(kwargs)
        return [{"generated_text": json.dumps({
            "score": 0.9, "character_match": True,
            "setting_match": True, "action_match": True, "reasons": ["matched"],
        })}]

    reviewer._pipeline = pipeline
    result = reviewer.review_scene(scene, {
        "setting": "nursery", "visible_event": "Alice lifts Arthur",
        "characters": ["Alice", "Arthur"], "mood": "warm",
    }, [reference])
    content = captured["text"][0]["content"]
    assert [item["path"] for item in content if item["type"] == "image"] == [
        str(scene.resolve()), str(reference.resolve()),
    ]
    assert result["accepted"] is True


def test_semantic_reviewer_requires_every_scene_match(tmp_path):
    image = tmp_path / "candidate.png"
    image.write_bytes(b"fixture")
    reviewer = SmolVLMReviewer()
    reviewer._pipeline = lambda **_kwargs: [{"generated_text": json.dumps({
        "accepted": True, "score": 0.95, "character_match": True,
        "setting_match": True, "action_match": False,
        "reasons": ["the required event is not visible"],
    })}]
    result = reviewer.review_scene(image, {
        "setting": "nursery", "visible_event": "an infant struggles to breathe",
        "characters": ["Tanya"], "mood": "disoriented",
    }, [])
    assert result["accepted"] is False
    assert result["action_match"] is False


def test_semantic_reviewer_derives_acceptance_from_required_matches(tmp_path):
    image = tmp_path / "candidate.png"
    image.write_bytes(b"fixture")
    reviewer = SmolVLMReviewer()
    reviewer._pipeline = lambda **_kwargs: [{"generated_text": json.dumps({
        "accepted": False, "score": 0.75, "character_match": True,
        "setting_match": True, "action_match": True, "reasons": ["all criteria match"],
    })}]
    result = reviewer.review_scene(image, {
        "setting": "nursery", "visible_event": "an infant cries in a crib",
        "characters": ["Tanya"], "mood": "disoriented",
    }, [])
    assert result["accepted"] is True


def test_semantic_reviewer_recovers_criteria_before_truncated_rationale(tmp_path):
    image = tmp_path / "candidate.png"
    image.write_bytes(b"fixture")
    reviewer = SmolVLMReviewer()
    reviewer._pipeline = lambda **_kwargs: [{"generated_text": (
        '{"score": 0.75, "character_match": true, "setting_match": true, '
        '"action_match": true, "reasons": [{"reason": "unfinished'
    )}]
    result = reviewer.review_scene(image, {
        "setting": "nursery", "visible_event": "an infant cries in a crib",
        "characters": ["Tanya"], "mood": "disoriented",
    }, [])
    assert result["accepted"] is True
    assert result["reasons"] == [
        "mandatory criteria recovered; optional rationale was truncated",
    ]


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
