"""Autonomous pipeline and cross-manuscript character reuse tests."""

from __future__ import annotations

from pathlib import Path

from video_creator.artifacts import read_json, sha256_file, write_json_atomic
from video_creator.cli import parser
from video_creator.images import DeterministicFixtureImageProvider
from video_creator.orchestrator import run_project
from video_creator.orchestrator import _automatic_analysis_decisions, _entity_kind
from video_creator.project import generate_project_character_references, initialize_project
from video_creator.series import (
    load_shared_references, publish_shared_references, seed_analysis_with_shared_characters,
)
from video_creator.visual_review import SmolVLMReviewer


def _prompts() -> dict:
    return {
        "schema_version": 1, "prompt_set_id": "prompt-set-0001",
        "source_sha256": "a" * 64, "status": "auto_accepted",
        "reference_requirements": [{
            "reference_id": "character-mira", "canonical_entity_id": "mira",
            "canonical_name": "Mira", "default_action": "standing",
            "reference_prompt": "anime character Mira", "visual_constraints": [],
        }],
        "prompts": [],
    }


def test_series_library_reuses_reference_without_generation(tmp_path):
    first = tmp_path / "part-1"; second = tmp_path / "part-2"; library = tmp_path / "series"
    source = first / "references/characters/character-mira.png"
    DeterministicFixtureImageProvider().generate("Mira", source, seed=4)
    canonical = {"references": [{
        "reference_id": "character-mira", "path": "references/characters/character-mira.png",
        "sha256": sha256_file(source),
    }]}
    publish_shared_references(library, canonical, _prompts(), first)
    seeded = seed_analysis_with_shared_characters(
        {"entities": []}, "Mira appears only once in this short part.", library,
    )
    assert seeded["entities"][0]["series_canonical_id"] == "mira"
    assert seeded["entities"][0]["mention_count"] == 1

    initialize_project(second, "part-2", "Part 2", "unverified")
    prompts_path = second / "prompts/image-prompts.json"
    write_json_atomic(prompts_path, _prompts())
    manifest = read_json(second / "project.json")
    manifest["stages"]["prompts"] = {
        "status": "auto_accepted", "artifact": "prompts/image-prompts.json",
    }
    write_json_atomic(second / "project.json", manifest)
    reused = load_shared_references(library, _prompts(), second)
    changed_prompts = _prompts()
    changed_prompts["reference_requirements"][0]["reference_prompt"] = "Mira with corrected blue hair"
    assert load_shared_references(library, changed_prompts, second) == {}

    class NoGeneration:
        name = "must-not-run"

        def generate(self, *_args, **_kwargs):
            raise AssertionError("an established character must not be regenerated")

    assets = generate_project_character_references(
        second, NoGeneration(), candidates_per_item=1, reused_references=reused,
    )
    assert assets["regeneration"] == {
        "reused_asset_ids": ["character-mira"], "regenerated_asset_ids": [],
    }
    assert assets["assets"][0]["selection"] == "series_reuse"
    assert (second / assets["assets"][0]["candidates"][0]["path"]).is_file()


def test_run_command_resumes_complete_fixture_pipeline(tmp_path, monkeypatch):
    manuscript = tmp_path / "part.md"
    manuscript.write_text(
        "# Arrival\n\nMira entered the quiet hall. Mira watched the windows. "
        "Mira heard the rain and crossed the room carefully.\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "part"
    accepted_review = lambda *_args, **_kwargs: {
        "accepted": True, "score": 0.9, "character_match": True,
        "setting_match": True, "action_match": True, "reasons": ["fixture"],
    }
    monkeypatch.setattr(SmolVLMReviewer, "review", accepted_review)
    monkeypatch.setattr(SmolVLMReviewer, "review_scene", accepted_review)

    import video_creator.orchestrator as orchestrator

    def complete(stage, status):
        def function(root):
            manifest = read_json(root / "project.json")
            manifest["stages"][stage] = {"status": status, "artifact": f"fixture/{stage}.json"}
            write_json_atomic(root / "project.json", manifest)
            return {"status": status}
        return function

    monkeypatch.setattr(orchestrator, "generate_project_narration_audio", complete("audio", "auto_accepted"))
    monkeypatch.setattr(orchestrator, "align_project_subtitles", complete("subtitles", "auto_accepted"))
    monkeypatch.setattr(orchestrator, "compile_project_timeline", complete("timeline", "auto_accepted"))
    monkeypatch.setattr(orchestrator, "render_project_video", complete("render", "rendered"))
    monkeypatch.setattr(orchestrator, "evaluate_project", complete("evaluation", "passed"))
    monkeypatch.setattr(orchestrator, "validate_project", lambda _root: [])

    first = run_project(
        workspace, manuscript, project_id="part", title="Part",
        image_provider=DeterministicFixtureImageProvider(), candidates_per_item=1,
        delegate_audio=False,
    )
    second = run_project(
        workspace, manuscript, project_id="part", title="Part",
        image_provider=DeterministicFixtureImageProvider(), candidates_per_item=1,
        delegate_audio=False,
    )

    assert first["status"] == "completed"
    assert first["executed_stages"][0] == "init"
    assert first["executed_stages"][-1] == "evaluation"
    assert second["status"] == "completed"
    assert second["executed_stages"] == []


def test_cli_exposes_single_run_command_with_series_library(tmp_path):
    args = parser().parse_args([
        "run", str(tmp_path / "workspace"), str(tmp_path / "part.md"),
        "--series-library", str(tmp_path / "series"), "--offline",
    ])
    assert args.command == "run"
    assert args.provider == "hybrid"
    assert args.offline is True


def test_fictional_entity_typing_rejects_noise_and_merges_aliases(tmp_path):
    text = (
        "My thoughts wandered. The continent of Dicathen contains the kingdom of Sapin. "
        "The Beast Glades remain dangerous. Arthur entered the Adventurers Guild. "
        "Arthur was called Art for short. Augmenting strengthens the body."
    )
    assert _entity_kind("My", text) is None
    assert _entity_kind("Dicathen", text) == "location"
    assert _entity_kind("Sapin", text) == "location"
    assert _entity_kind("Beast Glades", text) == "location"
    assert _entity_kind("Adventurers Guild", text) == "organization"
    assert _entity_kind("Augmenting", text) == "concept"
    assert _entity_kind("Arthur", text) == "character"

    (tmp_path / "analysis").mkdir(); (tmp_path / "source").mkdir()
    (tmp_path / "source/manuscript.md").write_text(text, encoding="utf-8")
    draft = {
        "schema_version": 1, "analysis_id": "analysis-0001", "provider": "fixture",
        "source_sha256": "a" * 64, "status": "draft", "release_usable": False,
        "entities": [
            {"entity_id": f"entity-{index}", "name": name, "kind": "unknown",
             "mention_count": 2, "evidence": [], "review_status": "needs_review"}
            for index, name in enumerate(("Arthur", "Art", "Dicathen", "My"), start=1)
        ],
        "settings": [], "world_rules": [], "continuity_facts": [],
    }
    write_json_atomic(tmp_path / "analysis/entities.json", draft)
    decisions = read_json(_automatic_analysis_decisions(tmp_path))
    by_name = {item["canonical_name"]: item for item in decisions["entities"]}
    assert by_name["Arthur"]["kind"] == "character"
    assert by_name["Arthur"]["aliases"] == ["Art"]
    assert by_name["Art"]["status"] == "rejected"
    assert by_name["Dicathen"]["kind"] == "location"
    assert by_name["My"]["status"] == "rejected"
