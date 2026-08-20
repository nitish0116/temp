"""Qualify COMETKiwi against reviewed translations and critical corruptions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable

from .cometkiwi_quality import DEFAULT_COMETKIWI_MODEL, CometKiwiQualityEstimator
from .qa_machine_review import CalibrationFixture, MachineReviewPolicy, calibrate_machine_reviewer


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = PROJECT / "tests" / "fixtures" / "machine_review_calibration.json"
DEFAULT_REPORT = PROJECT / "docs" / "machine-review-qualification.json"


def prepare_machine_review_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Route Hugging Face and Torch assets to the shared workspace cache."""
    env = dict(os.environ if source is None else source)
    root = Path(env.get("PYTHON_CACHE_HOME") or PROJECT.parent / ".model-cache").expanduser()
    env.setdefault("PYTHON_CACHE_HOME", str(root))
    env.setdefault("HF_HOME", str(root / "huggingface"))
    env.setdefault("HF_HUB_CACHE", str(root / "huggingface" / "hub"))
    env.setdefault("HUGGINGFACE_HUB_CACHE", str(root / "huggingface" / "hub"))
    env.setdefault("TORCH_HOME", str(root / "torch"))
    env.setdefault("TEMP", str(root / "tmp"))
    env.setdefault("TMP", str(root / "tmp"))
    for key in ("HF_HOME", "HF_HUB_CACHE", "TORCH_HOME", "TEMP"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env


def load_fixtures(path: Path) -> tuple[str, list[CalibrationFixture]]:
    """Load the versioned reviewed/adversarial fixture contract."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported machine-review fixture schema")
    fixture_set = str(payload.get("fixture_set") or "").strip()
    if not fixture_set:
        raise ValueError("machine-review fixture_set must be nonempty")
    fixtures = []
    for item in payload.get("fixtures", []):
        rejected = tuple(str(value).strip() for value in item.get("rejected_translations", []))
        fixture = CalibrationFixture(
            fixture_id=str(item.get("id") or "").strip(),
            source_text=str(item.get("source_text") or "").strip(),
            accepted_translation=str(item.get("accepted_translation") or "").strip(),
            rejected_translations=rejected,
            required_terms=tuple(str(value) for value in item.get("required_terms", [])),
            forbidden_terms=tuple(str(value) for value in item.get("forbidden_terms", [])),
        )
        if not fixture.fixture_id or not fixture.source_text or not fixture.accepted_translation:
            raise ValueError("machine-review fixtures require id, source, and accepted translation")
        if not rejected or any(not value for value in rejected):
            raise ValueError(f"fixture {fixture.fixture_id} requires nonempty rejected translations")
        fixtures.append(fixture)
    if not fixtures:
        raise ValueError("machine-review fixture set must not be empty")
    return fixture_set, fixtures


def run_qualification(
    fixtures_path: Path,
    estimate_quality: Callable[[str, str], float],
    *,
    model: str,
    threshold: float,
) -> dict:
    """Score every fixture and return release-blocking qualification evidence."""
    fixture_set, fixtures = load_fixtures(fixtures_path)
    policy = MachineReviewPolicy(minimum_quality_score=threshold)
    report = calibrate_machine_reviewer(fixtures, estimate_quality, policy)
    report.update({
        "fixture_set": fixture_set,
        "model": model,
        "minimum_quality_score": threshold,
        "release_qualified": report["passed"],
        "acceptance_rule": (
            "Every reviewed translation must meet the threshold and every critical "
            "corruption must be blocked by deterministic checks or score below it."
        ),
    })
    return report


def main(argv: list[str] | None = None) -> None:
    """Run real COMETKiwi qualification and write auditable JSON evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_COMETKIWI_MODEL)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--minimum-quality-score", type=float, default=0.85)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--output-report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    env = prepare_machine_review_environment()
    if args.offline:
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
    cache_keys = {
        "PYTHON_CACHE_HOME", "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE",
        "TORCH_HOME", "TEMP", "TMP", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
    }
    os.environ.update({key: env[key] for key in cache_keys if key in env})
    estimator = CometKiwiQualityEstimator(
        args.model, device=args.device, local_files_only=args.offline,
        batch_size=args.batch_size,
    )
    report = run_qualification(
        args.fixtures, estimator, model=args.model,
        threshold=args.minimum_quality_score,
    )
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)
