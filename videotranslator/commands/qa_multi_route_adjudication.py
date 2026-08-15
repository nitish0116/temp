"""Fail-closed adjudication across source-grounded translation evidence routes."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .canonical_timed_text import append_provenance, validate_canonical_timed_text
from .qa_translation_integrity import adjudication_coverage_issues, integrity_issues
from .runtime_device import ollama_gpu_available


ADJUDICATION_PROTOCOL_VERSION = 3


class OllamaAdjudicator:
    """Strict local JSON adjudicator backed by the Ollama generate API."""

    def __init__(self, model: str, endpoint: str = "http://127.0.0.1:11434", device: str = "auto", timeout_seconds: int = 180):
        """Configure deterministic local inference without loading weights here."""
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.use_gpu = ollama_gpu_available(device)
        self.timeout_seconds = timeout_seconds

    def __call__(self, request: AdjudicationRequest) -> str:
        """Return the model's JSON contract after removing hidden think blocks."""
        options = {"temperature": 0, "num_predict": 384, "repeat_penalty": 1.05}
        if not self.use_gpu:
            options["num_gpu"] = 0
        payload = json.dumps({
            "model": self.model, "prompt": adjudication_prompt(request) + "\n/no_think",
            "stream": False, "think": False, "format": "json", "options": options,
        }).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.endpoint}/api/generate", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                result = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Ollama adjudication failed for {request.group_id}: {error}") from error
        return re.sub(r"<think>.*?</think>", "", str(result.get("response") or ""), flags=re.I | re.S).strip()


@dataclass(frozen=True)
class AdjudicationRequest:
    """Source dialogue, context, and independently produced target candidates."""

    group_id: str
    source_language: str
    target_language: str
    source_text: str
    previous_source: tuple[str, ...]
    following_source: tuple[str, ...]
    candidates: tuple[tuple[str, str], ...]


def adjudication_prompt(request: AdjudicationRequest) -> str:
    """Render a strict source-grounded JSON adjudication request."""
    evidence = "\n".join(f"- {route}: {text}" for route, text in request.candidates)
    return (
        f"Verify the English translation of CURRENT {request.source_language} dialogue. "
        "Use source text as authority and candidates only as evidence. Preserve names, "
        "numbers, polarity, and meaning. If the source meaning is clear but every candidate "
        "is imperfect, synthesize a corrected translation and mark it verified. Use unresolved "
        "only when the CURRENT source itself is genuinely insufficient or ambiguous. Translate "
        "CURRENT only, preserve every clause in CURRENT, and use surrounding source solely to "
        "disambiguate it; unrelated context must not cause omission or refusal. Return JSON only: either "
        '{"status":"verified","translation":"...","reason":"..."} or '
        '{"status":"unresolved","translation":null,"reason":"..."}.\n'
        f"PREVIOUS SOURCE: {list(request.previous_source)}\n"
        f"CURRENT SOURCE: {request.source_text}\n"
        f"FOLLOWING SOURCE: {list(request.following_source)}\nCANDIDATES:\n{evidence}"
    )


def parse_adjudication_response(value: str) -> dict:
    """Parse the exact verified/unresolved response contract or raise ValueError."""
    try:
        result = json.loads(value.strip())
    except json.JSONDecodeError as error:
        raise ValueError("adjudicator returned invalid JSON") from error
    if not isinstance(result, dict) or result.get("status") not in {"verified", "unresolved"}:
        raise ValueError("adjudicator returned invalid status")
    translation = result.get("translation")
    if result["status"] == "verified" and (not isinstance(translation, str) or not translation.strip()):
        raise ValueError("verified adjudication requires translation")
    if result["status"] == "unresolved" and translation is not None:
        raise ValueError("unresolved adjudication must not supply translation")
    return {"status": result["status"], "translation": translation, "reason": str(result.get("reason") or "")}


def cache_key(request: AdjudicationRequest, model: str) -> str:
    """Hash the complete versioned evidence package for deterministic reuse."""
    payload = {"protocol": ADJUDICATION_PROTOCOL_VERSION, "model": model, "request": asdict(request)}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def adjudicate_multi_route(
    document: dict, dedicated_candidates: dict[str, str],
    adjudicate: Callable[[AdjudicationRequest], str], *, model: str,
    cache_directory: Path | None = None, context_size: int = 3,
) -> tuple[dict, dict]:
    """Verify every group or retain its original translation as unresolved."""
    validate_canonical_timed_text(document)
    output = deepcopy(document)
    checks = []
    segments = output["segments"]
    for index, segment in enumerate(segments):
        group_id = str(segment["semantic_group_id"])
        routes = [("primary", str(segment.get("translated_text") or ""))]
        dedicated = dedicated_candidates.get(group_id)
        if dedicated:
            routes.append(("dedicated_mt", dedicated))
        speech = segment.get("metadata", {}).get("speech_translation", {})
        if speech.get("status") == "ok" and speech.get("text"):
            routes.append(("speech_translation", str(speech["text"])))
        request = AdjudicationRequest(
            group_id=group_id, source_language=output["source_language"],
            target_language=output["output_language"], source_text=str(segment["source_text"]),
            previous_source=tuple(str(item["source_text"]) for item in segments[max(0, index-context_size):index]),
            following_source=tuple(str(item["source_text"]) for item in segments[index+1:index+context_size+1]),
            candidates=tuple(routes),
        )
        path = None if cache_directory is None else cache_directory / f"{cache_key(request, model)}.json"
        cache_hit = bool(path and path.is_file())
        try:
            raw = path.read_text(encoding="utf-8") if cache_hit else adjudicate(request)
            result = parse_adjudication_response(raw)
            if result["status"] == "verified":
                issues = integrity_issues(request.source_text, result["translation"])
                issues.extend(adjudication_coverage_issues(request.source_text, result["translation"]))
                if issues:
                    issue_types = ", ".join(str(item["type"]) for item in issues)
                    raise ValueError(f"verified translation failed integrity checks: {issue_types}")
            if path and not cache_hit:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
            passed = result["status"] == "verified"
            error = None
        except (OSError, RuntimeError, ValueError) as exc:
            result = {"status": "unresolved", "translation": None, "reason": "adjudication failure"}
            passed, error = False, f"{type(exc).__name__}: {exc}"
        if passed:
            segment["translated_text"] = result["translation"].strip()
            segment["provenance"] = append_provenance(
                segment, "multi-route-adjudication", "source-grounded-verification",
                model=model, evidence_routes=[route for route, _ in routes], reason=result["reason"],
            )
        checks.append({"semantic_group_id": group_id, "status": result["status"], "passed": passed, "selected_translation": result["translation"], "reason": result["reason"], "error": error, "cache_hit": cache_hit, "evidence_routes": [route for route, _ in routes]})
    report = {"schema_version": 1, "protocol_version": ADJUDICATION_PROTOCOL_VERSION, "model": model, "passed": all(item["passed"] for item in checks), "group_count": len(checks), "unresolved_count": sum(not item["passed"] for item in checks), "checks": checks}
    output["metadata"] = {**output.get("metadata", {}), "multi_route_adjudication": report}
    validate_canonical_timed_text(output)
    return output, report
