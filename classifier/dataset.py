"""Validate reviewed examples and split them without document leakage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import random
from typing import Iterable


LABEL_TO_ID = {"keep_spaced": 0, "join": 1}
BOUNDARY_MARKER = "<BOUNDARY>"


@dataclass(frozen=True, slots=True)
class TrainingExample:
    id: str
    source_file: str
    text: str
    label: int
    user_label: str


@dataclass(frozen=True, slots=True)
class DatasetSplits:
    train: tuple[TrainingExample, ...]
    validation: tuple[TrainingExample, ...]
    test: tuple[TrainingExample, ...]

    def as_dict(self) -> dict[str, tuple[TrainingExample, ...]]:
        return {
            "train": self.train,
            "validation": self.validation,
            "test": self.test,
        }


def classifier_text(record: dict[str, object]) -> str:
    """Serialize both alternatives while marking the candidate boundary."""

    spaced = str(record.get("spaced_text", "")).strip()
    joined = str(record.get("joined_text", "")).strip()
    left = str(record.get("left", "")).strip()
    right = str(record.get("right", "")).strip()
    broken = f"{left} {right}".strip()
    index = spaced.casefold().find(broken.casefold()) if broken else -1
    if index >= 0:
        marked = (
            spaced[:index]
            + f"{left} {BOUNDARY_MARKER} {right}"
            + spaced[index + len(broken) :]
        )
    else:
        marked = f"{spaced} [BOUNDARY={broken}]"
    return f"[SPACED] {marked}\n[JOINED] {joined}"


def load_reviewed_examples(path: str | Path) -> list[TrainingExample]:
    """Load only reviewed join/keep labels and reject malformed trusted data."""

    source = Path(path)
    document = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Expected classifier dataset schema_version 1.")
    records = document.get("examples")
    if not isinstance(records, list):
        raise ValueError("Classifier dataset must contain an examples list.")

    examples: list[TrainingExample] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or record.get("review_status") != "reviewed":
            continue
        user_label = record.get("user_label")
        if user_label in {None, "skip"}:
            continue
        if user_label not in LABEL_TO_ID:
            raise ValueError(f"Invalid reviewed user_label: {user_label!r}")
        identifier = str(record.get("id", "")).strip()
        source_file = str(record.get("source_file", "")).strip()
        if not identifier or not source_file:
            raise ValueError("Reviewed examples require id and source_file.")
        if identifier in seen:
            raise ValueError(f"Duplicate reviewed example id: {identifier}")
        seen.add(identifier)
        text = classifier_text(record)
        if not str(record.get("spaced_text", "")).strip() or not str(
            record.get("joined_text", "")
        ).strip():
            raise ValueError(f"Reviewed example {identifier} lacks variant text.")
        examples.append(
            TrainingExample(
                id=identifier,
                source_file=source_file,
                text=text,
                label=LABEL_TO_ID[str(user_label)],
                user_label=str(user_label),
            )
        )
    return examples


def split_by_source(
    examples: Iterable[TrainingExample],
    *,
    seed: int = 42,
    train_ratio: float = 0.8,
    validation_ratio: float = 0.1,
) -> DatasetSplits:
    """Split whole source documents so one book cannot leak across sets."""

    if not 0 < train_ratio < 1 or not 0 <= validation_ratio < 1:
        raise ValueError("Split ratios must be between zero and one.")
    if train_ratio + validation_ratio >= 1:
        raise ValueError("Train and validation ratios must leave a test split.")
    grouped: dict[str, list[TrainingExample]] = {}
    for example in examples:
        grouped.setdefault(example.source_file, []).append(example)
    sources = sorted(grouped)
    random.Random(seed).shuffle(sources)
    count = len(sources)
    if count == 0:
        return DatasetSplits((), (), ())
    if count == 1:
        train_sources, validation_sources, test_sources = sources, [], []
    elif count == 2:
        train_sources, validation_sources, test_sources = sources[:1], [], sources[1:]
    else:
        validation_count = max(1, round(count * validation_ratio))
        test_count = max(1, count - round(count * train_ratio) - validation_count)
        train_count = count - validation_count - test_count
        if train_count < 1:
            train_count = 1
            test_count = count - train_count - validation_count
        train_sources = sources[:train_count]
        validation_sources = sources[train_count : train_count + validation_count]
        test_sources = sources[train_count + validation_count :]

    def collect(selected: list[str]) -> tuple[TrainingExample, ...]:
        return tuple(item for name in selected for item in grouped[name])

    return DatasetSplits(
        collect(train_sources),
        collect(validation_sources),
        collect(test_sources),
    )


def write_split_manifest(
    path: str | Path,
    splits: DatasetSplits,
    *,
    seed: int,
) -> None:
    """Persist exact example IDs and source files for reproducible evaluation."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "random_seed": seed,
        "splits": {
            name: [asdict(example) for example in values]
            for name, values in splits.as_dict().items()
        },
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


__all__ = [
    "BOUNDARY_MARKER",
    "DatasetSplits",
    "LABEL_TO_ID",
    "TrainingExample",
    "classifier_text",
    "load_reviewed_examples",
    "split_by_source",
    "write_split_manifest",
]
