"""Tests for the repeatable COMETKiwi adversarial qualification command."""

import json

from videotranslator.commands.qualify_machine_review import load_fixtures, run_qualification
from videotranslator.pipeline import main


def fixture_file(tmp_path):
    """Write one compact reviewed fixture set for offline qualification."""
    path = tmp_path / "fixtures.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "fixture_set": "test-v1",
        "fixtures": [{
            "id": "cute", "source_language": "ko", "source_text": "참귀엽죠?",
            "accepted_translation": "Isn't he cute?",
            "rejected_translations": ["Isn't he cruel?"],
        }],
    }), encoding="utf-8")
    return path


def test_qualification_passes_only_when_good_and_bad_scores_separate(tmp_path):
    path = fixture_file(tmp_path)
    report = run_qualification(
        path, lambda source, target: 0.95 if "cute" in target else 0.2,
        model="fixture", threshold=0.85,
    )
    assert report["passed"] is True
    assert report["release_qualified"] is True
    assert report["fixture_set"] == "test-v1"


def test_qualification_fails_when_plausible_corruption_scores_high(tmp_path):
    report = run_qualification(
        fixture_file(tmp_path), lambda source, target: 0.95,
        model="fixture", threshold=0.85,
    )
    assert report["passed"] is False
    assert report["release_qualified"] is False


def test_fixture_loader_rejects_empty_set(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"schema_version": 1, "fixture_set": "empty", "fixtures": []}))
    try:
        load_fixtures(path)
    except ValueError as error:
        assert "must not be empty" in str(error)
    else:
        raise AssertionError("empty fixture set was accepted")


def test_pipeline_dispatches_machine_review_help(monkeypatch):
    monkeypatch.setattr("sys.argv", ["videotranslator", "qualify-machine-review", "--help"])
    try:
        main()
    except SystemExit as error:
        assert error.code == 0
