"""Build trainingdata.json from user-selected IDs and update the master data."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
MASTER_PATH = HERE / "data" / "broken_word_training.json"
JOINED_PATH = HERE / "joined.json"
KEEP_SPACED_PATH = HERE / "keep_spaced.json"
TRAINING_PATH = HERE / "trainingdata.json"


def read_ids(path: Path) -> list[str]:
    """Read either a JSON ID array or an object containing an ``ids`` array."""
    with path.open("r", encoding="utf-8") as input_file:
        value = json.load(input_file)
    if isinstance(value, dict):
        value = value.get("ids")
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{path.name} must be an array of ID strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{path.name} contains duplicate IDs")
    return value


def write_json_atomic(path: Path, value: Any) -> None:
    """Replace a JSON file only after its complete replacement is written."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
            json.dump(value, output_file, ensure_ascii=False, indent=2)
            output_file.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    joined_ids = read_ids(JOINED_PATH)
    keep_spaced_ids = read_ids(KEEP_SPACED_PATH)
    overlap = set(joined_ids) & set(keep_spaced_ids)
    if overlap:
        raise ValueError(
            "IDs cannot appear in both selection files: " + ", ".join(sorted(overlap))
        )

    with MASTER_PATH.open("r", encoding="utf-8") as master_file:
        master = json.load(master_file)

    examples = master.get("examples")
    if not isinstance(examples, list):
        raise ValueError("Master JSON must contain an examples array")
    by_id = {example.get("id"): example for example in examples}
    selections = [(item_id, "join") for item_id in joined_ids]
    selections += [(item_id, "keep_spaced") for item_id in keep_spaced_ids]
    missing = [item_id for item_id, _ in selections if item_id not in by_id]
    if missing:
        raise ValueError("IDs not found in master data: " + ", ".join(missing))

    reviewed_at = datetime.now(timezone.utc).isoformat()
    selected_examples = []
    for item_id, user_label in selections:
        example = by_id[item_id]
        example["user_label"] = user_label
        example["review_status"] = "reviewed"
        example["reviewed_at"] = reviewed_at
        selected_examples.append(example.copy())

    training_data = {
        "schema_version": master.get("schema_version", 1),
        "examples": selected_examples,
    }
    # Write training output first; validation above ensures the master is untouched on errors.
    write_json_atomic(TRAINING_PATH, training_data)
    write_json_atomic(MASTER_PATH, master)
    print(
        f"Wrote {TRAINING_PATH} and reviewed {len(selected_examples)} master examples "
        f"(join={len(joined_ids)}, keep_spaced={len(keep_spaced_ids)})"
    )


if __name__ == "__main__":
    main()
