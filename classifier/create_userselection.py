"""Export pending master examples into userselection.json for review."""

import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
MASTER_PATH = HERE / "data" / "broken_word_training.json"
OUTPUT_PATH = HERE / "userselection.json"
LABELS = ("join", "keep_spaced")


def main() -> None:
    with MASTER_PATH.open("r", encoding="utf-8") as source_file:
        master = json.load(source_file)

    grouped = {label: [] for label in LABELS}
    for example in master.get("examples", []):
        if example.get("review_status") != "pending":
            continue
        label = example.get("transformer_label")
        if label not in grouped:
            raise ValueError(f"Unsupported transformer_label: {label!r}")
        grouped[label].append(example.copy())

    with OUTPUT_PATH.open("w", encoding="utf-8") as output_file:
        json.dump(grouped, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    counts = ", ".join(f"{label}={len(grouped[label])}" for label in LABELS)
    print(f"Wrote {OUTPUT_PATH} ({counts})")


if __name__ == "__main__":
    main()
