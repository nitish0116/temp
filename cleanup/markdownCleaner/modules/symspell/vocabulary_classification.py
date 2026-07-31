"""Conservative contextual classification for vocabulary candidates."""

from __future__ import annotations

from collections import Counter


DETERMINERS = {
    "a",
    "an",
    "the",
    "this",
    "that",
    "these",
    "those",
    "each",
    "every",
}
SUBJECT_PRONOUNS = {"i", "you", "he", "she", "it", "we", "they"}
MODALS = {
    "can",
    "could",
    "may",
    "might",
    "must",
    "shall",
    "should",
    "will",
    "would",
}
COPULAS = {
    "am",
    "are",
    "be",
    "been",
    "being",
    "is",
    "was",
    "were",
    "become",
    "seem",
}
DEGREE_WORDS = {
    "fairly",
    "less",
    "more",
    "most",
    "quite",
    "rather",
    "so",
    "too",
    "very",
}
NOUN_TITLES = {
    "captain",
    "colonel",
    "doctor",
    "general",
    "major",
    "mr",
    "mrs",
    "professor",
}


def classify_candidate(
    word: str,
    contexts: list[tuple[str | None, str | None]] | None = None,
) -> tuple[str, float, str]:
    """Infer noun, adjective, or verb only from strong local context."""

    value = word.strip()
    if not value:
        return "unknown", 0.0, "empty candidate"

    votes: Counter[str] = Counter()
    evidence: Counter[str] = Counter()
    for previous, following in contexts or []:
        previous = previous.casefold() if previous else None
        following = following.casefold() if following else None
        if previous == "to" or previous in MODALS:
            votes["verb"] += 3
            evidence["infinitive/modal context"] += 1
        if previous in SUBJECT_PRONOUNS and following in DETERMINERS:
            votes["verb"] += 3
            evidence["subject + candidate + object context"] += 1
        if following in DETERMINERS:
            votes["verb"] += 2
            evidence["candidate followed by determiner/object"] += 1
        if previous in DEGREE_WORDS or previous in COPULAS:
            votes["adjective"] += 2
            evidence["degree/copular context"] += 1
        if previous in NOUN_TITLES or following in COPULAS:
            votes["noun"] += 3
            evidence["title or subject-before-copula context"] += 1
        if previous in DETERMINERS:
            category = (
                "noun"
                if following in COPULAS or following is None
                else "adjective"
            )
            votes[category] += 2
            evidence[f"determiner + {category} context"] += 1

    if not votes:
        return "unknown", 0.0, "insufficient contextual evidence"

    ranked = votes.most_common()
    winner, score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if score == runner_up:
        return "unknown", 0.0, "conflicting contextual evidence"

    confidence = round(min(0.95, 0.55 + (score - runner_up) * 0.1), 2)
    basis = evidence.most_common(1)[0][0]
    return winner, confidence, basis


__all__ = ["classify_candidate"]
