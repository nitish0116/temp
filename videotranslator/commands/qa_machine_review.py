"""Calibrated, fail-closed automatic review for translation candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from .qa_translation_integrity import adjudication_coverage_issues, integrity_issues


MACHINE_REVIEW_PROTOCOL_VERSION = 2
QualityEstimator = Callable[[str, str], float]
SemanticSimilarity = Callable[[str, str], float]
BackTranslator = Callable[[str, str, str], str]


@dataclass(frozen=True)
class MachineReviewPolicy:
    """Conservative thresholds that must be calibrated before use."""

    minimum_quality_score: float = 0.85
    minimum_semantic_similarity: float = 0.90
    minimum_round_trip_score: float = 0.85
    minimum_agreeing_routes: int = 2

    def __post_init__(self) -> None:
        """Reject unsafe thresholds and single-route machine approval."""
        for name in (
            "minimum_quality_score", "minimum_semantic_similarity",
            "minimum_round_trip_score",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.minimum_agreeing_routes < 2:
            raise ValueError("minimum_agreeing_routes must be at least 2")


@dataclass(frozen=True)
class CalibrationFixture:
    """A reviewed translation paired with critical-error mutations."""

    fixture_id: str
    source_text: str
    accepted_translation: str
    rejected_translations: tuple[str, ...]
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class TerminologyRule:
    """Bind observable source terms to required and forbidden target wording."""

    rule_id: str
    source_terms: tuple[str, ...]
    required_target_terms: tuple[str, ...]
    forbidden_target_terms: tuple[str, ...] = ()


def _normalized(text: str) -> str:
    """Normalize superficial punctuation and casing for exact agreement."""
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def _bounded_score(value: object, label: str) -> float:
    """Validate that a model adapter returned a finite normalized score."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} returned a nonnumeric score")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"{label} returned a score outside 0..1")
    return score


def deterministic_issues(source_text: str, translation: str) -> list[dict]:
    """Combine all model-independent release gates used by machine review."""
    issues = integrity_issues(source_text, translation)
    issues.extend(adjudication_coverage_issues(source_text, translation))
    return issues


def terminology_issues(
    source_text: str, translation: str, rules: Iterable[TerminologyRule],
) -> list[dict]:
    """Enforce reviewed source-triggered terminology without model judgment."""
    source = source_text.casefold()
    target = translation.casefold()
    issues = []
    for rule in rules:
        if rule.source_terms and not any(term.casefold() in source for term in rule.source_terms):
            continue
        missing = [term for term in rule.required_target_terms if term.casefold() not in target]
        forbidden = [term for term in rule.forbidden_target_terms if term.casefold() in target]
        if missing or forbidden:
            issues.append({
                "type": "terminology_mismatch", "rule_id": rule.rule_id,
                "missing": missing, "forbidden": forbidden,
            })
    return issues


ENTITY = re.compile(r"(?<![\w-])[A-Z][a-z]+(?:[-'][A-Za-z]+)*(?![\w-])")
ENTITY_STOPWORDS = {
    "A", "An", "And", "Are", "Did", "Do", "Does", "He", "How", "I", "Is",
    "It", "She", "Sign", "That", "The", "There", "They", "This", "Was", "Were",
    "What", "When", "Where", "Who", "Why", "Will", "Would", "You",
}


def entity_consensus_issues(
    translation: str, route_candidates: dict[str, str], *, minimum_routes: int = 2,
) -> list[dict]:
    """Require proper-name tokens independently present in multiple routes."""
    votes: dict[str, set[str]] = {}
    display: dict[str, str] = {}
    for route, candidate in route_candidates.items():
        for entity in set(ENTITY.findall(candidate)) - ENTITY_STOPWORDS:
            folded = entity.casefold()
            votes.setdefault(folded, set()).add(route)
            display.setdefault(folded, entity)
    required = sorted(
        display[entity] for entity, routes in votes.items()
        if len(routes) >= minimum_routes
    )
    target = translation.casefold()
    missing = [entity for entity in required if entity.casefold() not in target]
    return [{"type": "entity_consensus_mismatch", "missing": missing}] if missing else []


def review_candidate(
    source_text: str,
    translation: str,
    route_candidates: dict[str, str],
    *,
    source_language: str,
    target_language: str,
    estimate_quality: QualityEstimator,
    semantic_similarity: SemanticSimilarity,
    back_translate: BackTranslator,
    policy: MachineReviewPolicy,
    calibration_id: str,
    terminology_rules: Iterable[TerminologyRule] = (),
) -> dict:
    """Return machine_verified only when every independent gate passes."""
    failures: list[dict] = []
    issues = deterministic_issues(source_text, translation)
    issues.extend(terminology_issues(source_text, translation, terminology_rules))
    issues.extend(entity_consensus_issues(
        translation, route_candidates, minimum_routes=policy.minimum_agreeing_routes,
    ))
    if issues:
        failures.append({"type": "deterministic_integrity", "issues": issues})

    quality_score = _bounded_score(
        estimate_quality(source_text, translation), "quality estimator"
    )
    if quality_score < policy.minimum_quality_score:
        failures.append({"type": "quality_score", "score": quality_score})

    agreeing_routes = []
    for route, candidate in route_candidates.items():
        if not candidate.strip():
            continue
        similarity = 1.0 if _normalized(candidate) == _normalized(translation) else _bounded_score(
            semantic_similarity(translation, candidate), "semantic similarity"
        )
        if similarity >= policy.minimum_semantic_similarity:
            agreeing_routes.append(route)
    if len(agreeing_routes) < policy.minimum_agreeing_routes:
        failures.append({
            "type": "insufficient_independent_agreement",
            "agreeing_routes": agreeing_routes,
        })

    round_trip = back_translate(translation, target_language, source_language).strip()
    round_trip_score = _bounded_score(
        semantic_similarity(source_text, round_trip), "round-trip similarity"
    )
    if round_trip_score < policy.minimum_round_trip_score:
        failures.append({"type": "round_trip_score", "score": round_trip_score})

    passed = not failures
    return {
        "schema_version": 1,
        "protocol_version": MACHINE_REVIEW_PROTOCOL_VERSION,
        "status": "machine_verified" if passed else "unresolved",
        "passed": passed,
        "quality_score": quality_score,
        "round_trip_score": round_trip_score,
        "agreeing_routes": agreeing_routes,
        "back_translation": round_trip,
        "failures": failures,
        "calibration_id": calibration_id,
    }


def calibrate_machine_reviewer(
    fixtures: Iterable[CalibrationFixture],
    estimate_quality: QualityEstimator,
    policy: MachineReviewPolicy,
) -> dict:
    """Require every known-good fixture and reject every critical corruption."""
    results = []
    for fixture in fixtures:
        rule = TerminologyRule(
            rule_id=fixture.fixture_id, source_terms=(fixture.source_text,),
            required_target_terms=fixture.required_terms,
            forbidden_target_terms=fixture.forbidden_terms,
        )
        accepted_issues = deterministic_issues(fixture.source_text, fixture.accepted_translation)
        accepted_issues.extend(terminology_issues(
            fixture.source_text, fixture.accepted_translation, (rule,),
        ))
        accepted_score = _bounded_score(
            estimate_quality(fixture.source_text, fixture.accepted_translation),
            "quality estimator",
        )
        rejected = []
        for translation in fixture.rejected_translations:
            score = _bounded_score(
                estimate_quality(fixture.source_text, translation), "quality estimator"
            )
            issues = deterministic_issues(fixture.source_text, translation)
            issues.extend(terminology_issues(fixture.source_text, translation, (rule,)))
            rejected.append({
                "translation": translation,
                "score": score,
                "blocked": bool(issues) or score < policy.minimum_quality_score,
                "deterministic_issues": issues,
            })
        passed = (
            accepted_score >= policy.minimum_quality_score
            and not accepted_issues
            and all(item["blocked"] for item in rejected)
        )
        results.append({
            "fixture_id": fixture.fixture_id,
            "passed": passed,
            "accepted_score": accepted_score,
            "accepted_issues": accepted_issues,
            "rejected": rejected,
        })
    results = list(results)
    return {
        "schema_version": 1,
        "protocol_version": MACHINE_REVIEW_PROTOCOL_VERSION,
        "passed": bool(results) and all(item["passed"] for item in results),
        "fixture_count": len(results),
        "results": results,
    }
