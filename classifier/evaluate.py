"""Evaluate a trained boundary classifier and derive conservative thresholds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import yaml

from .dataset import BOUNDARY_MARKER, TrainingExample


def binary_metrics(labels: Sequence[int], probabilities: Sequence[float]) -> dict[str, object]:
    """Return confusion counts and per-class precision/recall at 0.5."""

    if len(labels) != len(probabilities):
        raise ValueError("Labels and probabilities must have equal lengths.")
    predicted = [int(value >= 0.5) for value in probabilities]
    tp = sum(actual == 1 and guess == 1 for actual, guess in zip(labels, predicted))
    tn = sum(actual == 0 and guess == 0 for actual, guess in zip(labels, predicted))
    fp = sum(actual == 0 and guess == 1 for actual, guess in zip(labels, predicted))
    fn = sum(actual == 1 and guess == 0 for actual, guess in zip(labels, predicted))

    def ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 0.0

    join_precision = ratio(tp, tp + fp)
    join_recall = ratio(tp, tp + fn)
    keep_precision = ratio(tn, tn + fn)
    keep_recall = ratio(tn, tn + fp)
    accuracy = ratio(tp + tn, len(labels))
    return {
        "examples": len(labels),
        "accuracy": accuracy,
        "join": {"precision": join_precision, "recall": join_recall},
        "keep_spaced": {"precision": keep_precision, "recall": keep_recall},
        "confusion_matrix": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
    }


def select_thresholds(
    labels: Sequence[int],
    probabilities: Sequence[float],
    *,
    minimum_join_precision: float = 0.97,
    minimum_keep_precision: float = 0.97,
) -> dict[str, float | None]:
    """Maximize class recall while meeting minimum auto-decision precision."""

    if len(labels) != len(probabilities):
        raise ValueError("Labels and probabilities must have equal lengths.")
    if not labels:
        return {"join_threshold": None, "keep_spaced_threshold": None}

    def best_threshold(target: int, minimum_precision: float) -> float | None:
        candidates = sorted(set(probabilities), reverse=target == 1)
        positives = sum(label == target for label in labels)
        best: tuple[float, float] | None = None
        for threshold in candidates:
            selected = [
                index
                for index, probability in enumerate(probabilities)
                if (probability >= threshold if target == 1 else probability <= threshold)
            ]
            correct = sum(labels[index] == target for index in selected)
            precision = correct / len(selected) if selected else 0.0
            recall = correct / positives if positives else 0.0
            if precision >= minimum_precision and (
                best is None or recall > best[0]
            ):
                best = (recall, threshold)
        return None if best is None else best[1]

    return {
        "join_threshold": best_threshold(1, minimum_join_precision),
        "keep_spaced_threshold": best_threshold(0, minimum_keep_precision),
    }


def predict_probabilities(
    examples: Sequence[TrainingExample],
    model_directory: str | Path,
    *,
    batch_size: int,
    maximum_length: int,
    device: str,
) -> list[float]:
    """Load a saved checkpoint and return P(join) for each example."""

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_path = str(model_directory)
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_path, local_files_only=True
    )
    selected_device = (
        "cuda" if device == "auto" and torch.cuda.is_available()
        else "cpu" if device == "auto"
        else device
    )
    if selected_device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable.")
    model.to(selected_device)
    model.eval()
    probabilities: list[float] = []
    for offset in range(0, len(examples), batch_size):
        batch = examples[offset : offset + batch_size]
        encoded = tokenizer(
            [example.text for example in batch],
            padding=True,
            truncation=True,
            max_length=maximum_length,
            return_tensors="pt",
        )
        encoded = {key: value.to(selected_device) for key, value in encoded.items()}
        with torch.no_grad():
            logits = model(**encoded).logits
            values = torch.softmax(logits, dim=-1)[:, 1]
        probabilities.extend(float(value) for value in values.cpu().tolist())
    return probabilities


def _load_test_examples(path: Path) -> list[TrainingExample]:
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    records = manifest.get("splits", {}).get("test", [])
    return [TrainingExample(**record) for record in records]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    parser.add_argument("--model", type=Path, default=None)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    base = config_path.parent
    training = config["training"]
    model_config = config["model"]
    evaluation = config["evaluation"]
    model_directory = args.model or Path(training["output_directory"])
    manifest_path = base / config["data"]["split_manifest"]
    examples = _load_test_examples(manifest_path)
    if not examples:
        raise ValueError("The split manifest contains no test examples.")
    probabilities = predict_probabilities(
        examples,
        model_directory,
        batch_size=int(training["batch_size"]),
        maximum_length=int(model_config["maximum_length"]),
        device=str(training["device"]),
    )
    labels = [example.label for example in examples]
    result = binary_metrics(labels, probabilities)
    result["thresholds"] = select_thresholds(
        labels,
        probabilities,
        minimum_join_precision=float(evaluation["minimum_join_precision"]),
        minimum_keep_precision=float(evaluation["minimum_keep_precision"]),
    )
    output = Path(model_directory) / "evaluation.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"Evaluation written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
