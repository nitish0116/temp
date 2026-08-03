"""Persistent, human-reviewable boundary-classifier training examples."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable, Mapping

SCHEMA_VERSION = 1
USER_FIELDS = ("user_label", "review_status", "reviewed_at", "user_notes")


def example_id(spaced_text: str, joined_text: str, broken_word: str) -> str:
    normalized = "\n".join(
        " ".join(value.casefold().split())
        for value in (spaced_text, joined_text, broken_word)
    )
    return sha256(normalized.encode("utf-8")).hexdigest()


class BoundaryTrainingDataWriter:
    """Merge generated examples while retaining reviewer-edited fields."""

    def __init__(self, path: str | Path, source_file: str | None = None) -> None:
        self.path = Path(path)
        self.source_file = Path(source_file).name if source_file else None

    def add(self, examples: Iterable[Mapping[str, object]]) -> int:
        generated = [dict(example) for example in examples]
        if not generated:
            return 0
        document = self._load()
        existing = {
            str(item.get("id")): item
            for item in document["examples"]
            if isinstance(item, dict) and item.get("id")
        }
        for item in generated:
            identifier = str(item["id"])
            previous = existing.get(identifier, {})
            item.setdefault("source_file", self.source_file)
            for field in USER_FIELDS:
                item[field] = previous.get(field, item.get(field))
            existing[identifier] = item
        document["examples"] = [existing[key] for key in sorted(existing)]
        self._write(document)
        return len(generated)

    def _load(self) -> dict[str, object]:
        if not self.path.exists():
            return {"schema_version": SCHEMA_VERSION, "examples": []}
        value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or not isinstance(value.get("examples"), list):
            raise ValueError("Classifier training data must contain an examples list.")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported classifier training-data schema version.")
        return value

    def _write(self, document: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


__all__ = ["BoundaryTrainingDataWriter", "example_id"]
