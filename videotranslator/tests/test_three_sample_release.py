"""Release smoke tests for the three cached multilingual sample runs."""

import json
from pathlib import Path

import pytest

from videotranslator.commands.canonical_timed_text import validate_canonical_timed_text
from videotranslator.commands.qa_transcript import analyze
from videotranslator.commands.qa_semantic_reference import (
    evaluate_semantic_references,
    references_from_manifest,
)


PROJECT = Path(__file__).parents[1]
MANIFEST = PROJECT / "tests" / "fixtures" / "three_sample_release_review.json"


def samples() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["samples"]


@pytest.mark.parametrize("sample", samples(), ids=lambda item: item["id"])
def test_cached_three_sample_pipeline_artifacts_pass_structural_smoke(sample: dict):
    """Recheck the promoted cached artifacts without invoking models or a network."""
    output = PROJECT / "outputs" / sample["output_directory"]
    report_path = output / "subtitle-pipeline-report.json"
    canonical_path = output / "final.en.json"
    srt_path = output / "final.srt"
    if not all(path.is_file() for path in (report_path, canonical_path, srt_path)):
        pytest.skip("cached sample output is not present in this checkout")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    validate_canonical_timed_text(canonical)
    qa = analyze(canonical, maximum_duration=12.0)

    assert report["status"] == sample["expected_status"]
    assert len(canonical["segments"]) >= sample["expected_minimum_cues"]
    assert not qa["issues"]
    assert srt_path.read_text(encoding="utf-8").count(" --> ") == len(canonical["segments"])


def test_verified_semantic_defects_remain_explicit_release_blockers():
    """Protect the review findings until a stronger translation run replaces them."""
    defects = [
        defect
        for sample in samples()
        for defect in sample["manual_review"].get("verified_defects", [])
    ]
    assert defects, "the release fixture must retain its verified semantic failures"
    for defect in defects:
        generated = defect["generated"].casefold()
        assert any(term.casefold() not in generated for term in defect["required_terms"])
        assert any(term.casefold() in generated for term in defect["forbidden_terms"])


@pytest.mark.parametrize(
    "sample",
    [item for item in samples() if item["manual_review"].get("verified_defects")],
    ids=lambda item: item["id"],
)
def test_cached_defective_outputs_fail_the_executable_semantic_gate(sample: dict):
    """Confirm reviewed defects block the corresponding cached canonical output."""
    canonical_path = PROJECT / "outputs" / sample["output_directory"] / "final.en.json"
    if not canonical_path.is_file():
        pytest.skip("cached sample output is not present in this checkout")
    document = json.loads(canonical_path.read_text(encoding="utf-8"))
    references = references_from_manifest(
        json.loads(MANIFEST.read_text(encoding="utf-8")), sample["output_directory"]
    )
    report = evaluate_semantic_references(document, references)
    assert report["passed"] is False
    assert report["failed_reference_count"] >= 1
