"""Tests for fail-closed multi-route translation adjudication."""

import json

from videotranslator.commands.qa_multi_route_adjudication import (
    AdjudicationRequest,
    adjudicate_multi_route,
    adjudication_prompt,
    parse_adjudication_response,
)
from videotranslator.commands.qa_translation_integrity import adjudication_coverage_issues
from videotranslator.commands.create_subtitles import parse_args
from videotranslator.tests.test_translation_agreement import translated_document


def test_verified_correction_records_routes_and_provenance(tmp_path):
    document = translated_document()
    document["segments"][0]["metadata"]["speech_translation"] = {"status": "ok", "text": "A place in Seoul?"}
    output, report = adjudicate_multi_route(
        document, {"group-1": "Was there such a place in Seoul?"},
        lambda request: json.dumps({"status": "verified", "translation": "Was there a place like that in Seoul?", "reason": "source names Seoul"}),
        model="fixture", cache_directory=tmp_path,
    )
    assert report["passed"] is True
    assert output["segments"][0]["translated_text"].endswith("Seoul?")
    assert report["checks"][0]["evidence_routes"] == ["primary", "dedicated_mt", "speech_translation"]
    assert output["segments"][0]["provenance"][-1]["stage"] == "multi-route-adjudication"


def test_unresolved_keeps_original_and_blocks_report():
    document = translated_document()
    output, report = adjudicate_multi_route(
        document, {"group-1": "Unrelated"},
        lambda request: json.dumps({"status": "unresolved", "translation": None, "reason": "insufficient evidence"}),
        model="fixture",
    )
    assert report["passed"] is False and report["unresolved_count"] == 1
    assert output["segments"][0]["translated_text"].startswith("Seattle")


def test_invalid_response_fails_closed_without_cache(tmp_path):
    document = translated_document()
    output, report = adjudicate_multi_route(
        document, {}, lambda request: "not json", model="fixture", cache_directory=tmp_path,
    )
    assert report["checks"][0]["error"].startswith("ValueError")
    assert output["segments"][0]["translated_text"] == document["segments"][0]["translated_text"]
    assert not list(tmp_path.iterdir())


def test_verified_adjudication_fails_closed_when_source_clause_is_omitted():
    document = translated_document()
    document["segments"][0]["source_text"] = "달산리? 서울에 그런 곳도 있었니?"
    original = document["segments"][0]["translated_text"]
    output, report = adjudicate_multi_route(
        document, {"group-1": "Was there such a place in Seoul?"},
        lambda request: json.dumps({
            "status": "verified", "translation": "Was there such a place in Seoul?",
            "reason": "incorrectly omitted the first clause",
        }), model="fixture",
    )
    assert report["passed"] is False
    assert "source_clause_omission" in report["checks"][0]["error"]
    assert output["segments"][0]["translated_text"] == original


def test_adjudication_coverage_preserves_all_clauses_and_latin_identifiers():
    assert adjudication_coverage_issues(
        "Dalsan-ri? 서울에 그런 곳도 있었니?",
        "Dalsan-ri? Was there such a place in Seoul?",
    ) == []
    issues = adjudication_coverage_issues("Ask Agent-X. Ready?", "Are you ready?")
    assert {item["type"] for item in issues} == {
        "source_clause_omission", "source_identifier_omission",
    }


def test_prompt_requires_source_authority_and_explicit_unresolved():
    request = AdjudicationRequest("g", "zh", "en", "source", (), (), (("primary", "candidate"),))
    prompt = adjudication_prompt(request)
    assert "source text as authority" in prompt
    assert "synthesize a corrected translation" in prompt
    assert '"status":"unresolved"' in prompt
    assert parse_adjudication_response('{"status":"unresolved","translation":null}') ["status"] == "unresolved"


def test_multi_route_adjudication_is_opt_in_with_qualified_defaults():
    args = parse_args(["video.mp4"])
    assert args.multi_route_adjudication is False
    assert args.dedicated_translation_model == "google/madlad400-3b-mt"
    assert args.adjudication_model == "qwen2.5:7b"
