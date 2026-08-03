"""Fine-tune a transformer to classify OCR word boundaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random

import yaml

from .dataset import (
    BOUNDARY_MARKER,
    DatasetSplits,
    TrainingExample,
    load_reviewed_examples,
    split_by_source,
    write_split_manifest,
)
from .evaluate import binary_metrics


SPECIAL_TOKENS = [BOUNDARY_MARKER, "[SPACED]", "[JOINED]"]


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _validate_splits(splits: DatasetSplits) -> None:
    if not splits.train:
        raise ValueError("No reviewed examples are available for training.")
    if not splits.validation or not splits.test:
        raise ValueError(
            "Training requires reviewed examples from at least three source files "
            "to create leakage-safe train, validation, and test splits."
        )
    labels = {example.label for example in splits.train}
    if labels != {0, 1}:
        raise ValueError("The training split must contain both user labels.")


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _device(requested: str) -> str:
    import torch

    normalized = requested.strip().casefold()
    if normalized not in {"auto", "cpu", "cuda"}:
        raise ValueError("training.device must be auto, cpu, or cuda.")
    selected = "cuda" if normalized == "auto" and torch.cuda.is_available() else (
        "cpu" if normalized == "auto" else normalized
    )
    if selected == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable.")
    return selected


def _loader(examples, tokenizer, *, batch_size: int, maximum_length: int, shuffle: bool):
    import torch

    class EncodedDataset(torch.utils.data.Dataset):
        def __len__(self):
            return len(examples)

        def __getitem__(self, index):
            example = examples[index]
            return example.text, example.label

    def collate(batch):
        texts, labels = zip(*batch)
        encoded = tokenizer(
            list(texts),
            padding=True,
            truncation=True,
            max_length=maximum_length,
            return_tensors="pt",
        )
        encoded["labels"] = torch.tensor(labels, dtype=torch.long)
        return encoded

    return torch.utils.data.DataLoader(
        EncodedDataset(),
        batch_size=batch_size,
        shuffle=shuffle,
        collate_fn=collate,
    )


def _evaluate_model(model, loader, device: str) -> tuple[float, list[int], list[float]]:
    import torch

    model.eval()
    losses: list[float] = []
    labels: list[int] = []
    probabilities: list[float] = []
    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            output = model(**batch)
            losses.append(float(output.loss.item()))
            probabilities.extend(
                torch.softmax(output.logits, dim=-1)[:, 1].cpu().tolist()
            )
            labels.extend(batch["labels"].cpu().tolist())
    return sum(losses) / len(losses), labels, probabilities


def train(config_path: str | Path) -> dict[str, object]:
    """Train from reviewed data and save the best validation checkpoint."""

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    source = Path(config_path).resolve()
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    base = source.parent
    data_config = config["data"]
    model_config = config["model"]
    training = config["training"]
    seed = int(training["random_seed"])
    examples = load_reviewed_examples(_resolve(base, data_config["dataset"]))
    splits = split_by_source(
        examples,
        seed=seed,
        train_ratio=float(data_config["train_ratio"]),
        validation_ratio=float(data_config["validation_ratio"]),
    )
    _validate_splits(splits)
    write_split_manifest(
        _resolve(base, data_config["split_manifest"]), splits, seed=seed
    )
    _seed_everything(seed)
    device = _device(str(training["device"]))
    output_directory = _resolve(base, training["output_directory"])
    output_directory.mkdir(parents=True, exist_ok=True)
    local_only = bool(model_config.get("local_files_only", True))
    tokenizer = AutoTokenizer.from_pretrained(
        model_config["base_model"], local_files_only=local_only
    )
    tokenizer.add_special_tokens({"additional_special_tokens": SPECIAL_TOKENS})
    model = AutoModelForSequenceClassification.from_pretrained(
        model_config["base_model"],
        num_labels=2,
        id2label={0: "keep_spaced", 1: "join"},
        label2id={"keep_spaced": 0, "join": 1},
        local_files_only=local_only,
    )
    model.resize_token_embeddings(len(tokenizer))
    model.to(device)
    batch_size = int(training["batch_size"])
    maximum_length = int(model_config["maximum_length"])
    train_loader = _loader(
        splits.train,
        tokenizer,
        batch_size=batch_size,
        maximum_length=maximum_length,
        shuffle=True,
    )
    validation_loader = _loader(
        splits.validation,
        tokenizer,
        batch_size=batch_size,
        maximum_length=maximum_length,
        shuffle=False,
    )
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(training["learning_rate"]),
        weight_decay=float(training["weight_decay"]),
    )
    history: list[dict[str, object]] = []
    best_key: tuple[float, float, float] | None = None
    for epoch in range(1, int(training["epochs"]) + 1):
        model.train()
        train_losses: list[float] = []
        for batch in train_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            optimizer.zero_grad(set_to_none=True)
            output = model(**batch)
            output.loss.backward()
            optimizer.step()
            train_losses.append(float(output.loss.item()))
        validation_loss, labels, probabilities = _evaluate_model(
            model, validation_loader, device
        )
        metrics = binary_metrics(labels, probabilities)
        join = metrics["join"]
        key = (float(join["precision"]), float(join["recall"]), -validation_loss)
        epoch_result = {
            "epoch": epoch,
            "training_loss": sum(train_losses) / len(train_losses),
            "validation_loss": validation_loss,
            "metrics": metrics,
        }
        history.append(epoch_result)
        print(json.dumps(epoch_result, indent=2))
        if best_key is None or key > best_key:
            best_key = key
            model.save_pretrained(output_directory)
            tokenizer.save_pretrained(output_directory)

    counts = {
        "train": len(splits.train),
        "validation": len(splits.validation),
        "test": len(splits.test),
        "join": sum(example.label == 1 for example in examples),
        "keep_spaced": sum(example.label == 0 for example in examples),
    }
    metadata = {
        "base_model": model_config["base_model"],
        "dataset_schema_version": 1,
        "device": device,
        "counts": counts,
        "history": history,
        "best_validation_key": best_key,
        "config": config,
    }
    (output_directory / "training_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("config.yaml")
    )
    args = parser.parse_args()
    metadata = train(args.config)
    print(json.dumps(metadata["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
