"""Classifier dataset collection and reviewer-field preservation."""

from __future__ import annotations

import json
from pathlib import Path

from markdownCleaner.modules.symspell.training_data import (
    BoundaryTrainingDataWriter,
    example_id,
)


def _example() -> dict[str, object]:
    spaced = "The sol diers entered the city."
    joined = "The soldiers entered the city."
    return {
        "id": example_id(spaced, joined, "sol diers"),
        "context": spaced,
        "spaced_text": spaced,
        "joined_text": joined,
        "left": "sol",
        "right": "diers",
        "replacement": "soldiers",
        "transformer_label": "join",
        "transformer_spaced_score": -8.42,
        "transformer_joined_score": -2.17,
        "transformer_margin": 6.25,
        "user_label": None,
        "review_status": "pending",
        "reviewed_at": None,
        "user_notes": "",
        "evidence": "wordfreq",
    }


def test_writer_deduplicates_and_preserves_user_review(tmp_path):
    path = tmp_path / "training.json"
    writer = BoundaryTrainingDataWriter(path, "Volume 01.md")
    writer.add([_example()])

    document = json.loads(path.read_text(encoding="utf-8"))
    document["examples"][0].update(
        {
            "user_label": "keep_spaced",
            "review_status": "reviewed",
            "reviewed_at": "2026-08-03T12:00:00+05:30",
            "user_notes": "Checked against the source PDF.",
        }
    )
    path.write_text(json.dumps(document), encoding="utf-8")

    refreshed = _example()
    refreshed["transformer_margin"] = 7.0
    writer.add([refreshed])
    result = json.loads(path.read_text(encoding="utf-8"))

    assert len(result["examples"]) == 1
    saved = result["examples"][0]
    assert saved["source_file"] == "Volume 01.md"
    assert saved["transformer_margin"] == 7.0
    assert saved["user_label"] == "keep_spaced"
    assert saved["review_status"] == "reviewed"
    assert saved["user_notes"] == "Checked against the source PDF."


def test_writer_falls_back_when_windows_blocks_atomic_replace(
    tmp_path, monkeypatch
):
    path = tmp_path / "training.json"
    path.write_text('{"schema_version": 1, "examples": []}\n', encoding="utf-8")
    writer = BoundaryTrainingDataWriter(path, "Volume 01.md")

    def deny_replace(self, target):
        raise PermissionError(5, "Access is denied", str(target))

    monkeypatch.setattr(Path, "replace", deny_replace)

    assert writer.add([_example()]) == 1
    document = json.loads(path.read_text(encoding="utf-8"))
    assert len(document["examples"]) == 1
    assert not path.with_suffix(".json.tmp").exists()
