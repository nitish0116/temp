from __future__ import annotations

import json

import pytest

from classifier.dataset import (
    BOUNDARY_MARKER,
    TrainingExample,
    load_reviewed_examples,
    split_by_source,
)


def test_loads_only_trusted_labels_and_marks_boundary(tmp_path):
    path = tmp_path / "training.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "examples": [
                    {
                        "id": "accepted",
                        "source_file": "one.md",
                        "spaced_text": "The sol diers advanced.",
                        "joined_text": "The soldiers advanced.",
                        "left": "sol",
                        "right": "diers",
                        "user_label": "join",
                        "review_status": "reviewed",
                    },
                    {
                        "id": "pending",
                        "source_file": "one.md",
                        "spaced_text": "log in",
                        "joined_text": "login",
                        "left": "log",
                        "right": "in",
                        "user_label": None,
                        "review_status": "pending",
                    },
                    {
                        "id": "skipped",
                        "source_file": "one.md",
                        "spaced_text": "a b",
                        "joined_text": "ab",
                        "left": "a",
                        "right": "b",
                        "user_label": "skip",
                        "review_status": "reviewed",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    examples = load_reviewed_examples(path)

    assert len(examples) == 1
    assert examples[0].label == 1
    assert f"sol {BOUNDARY_MARKER} diers" in examples[0].text
    assert "[JOINED] The soldiers advanced." in examples[0].text


def test_invalid_reviewed_label_is_rejected(tmp_path):
    path = tmp_path / "training.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "examples": [
                    {
                        "id": "bad",
                        "source_file": "one.md",
                        "user_label": "maybe",
                        "review_status": "reviewed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid reviewed user_label"):
        load_reviewed_examples(path)


def test_split_keeps_each_source_in_exactly_one_partition():
    examples = [
        TrainingExample(str(index), f"book-{index // 2}.md", "text", index % 2, "join")
        for index in range(10)
    ]

    splits = split_by_source(
        examples, seed=42, train_ratio=0.6, validation_ratio=0.2
    )
    source_sets = [
        {example.source_file for example in partition}
        for partition in (splits.train, splits.validation, splits.test)
    ]

    assert all(source_sets)
    assert source_sets[0].isdisjoint(source_sets[1])
    assert source_sets[0].isdisjoint(source_sets[2])
    assert source_sets[1].isdisjoint(source_sets[2])

