"""Immutable configuration used by the SymSpell components."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.config import require_bool


@dataclass(frozen=True, slots=True)
class SymSpellSettings:
    """Parse the SymSpell section once into strongly typed values."""

    dictionary: str = "builtin:en-82k"
    glossary: str | None = None
    learned: str | None = None
    broken_word_decisions: str | None = None
    protected_terms: tuple[str, ...] = ()
    max_edit_distance: int = 2
    maximum_auto_edit_distance: int = 1
    confidence_threshold: float = 92.0
    minimum_word_length: int = 4
    minimum_candidate_frequency: int = 1_000
    minimum_dictionary_frequency: int = 1
    ambiguity_margin: float = 2.0
    auto_protect_proper_nouns: bool = True
    proper_noun_minimum_occurrences: int = 2
    broken_word_merge_minimum_frequency: int = 100_000
    wordfreq_enabled: bool = True
    wordfreq_language: str = "en"
    wordfreq_wordlist: str = "large"
    wordfreq_minimum_zipf: float = 2.5
    maximum_merge_passes: int = 3
    dehyphenation_zipf_margin: float = 0.5

    @classmethod
    def from_config(cls, config) -> "SymSpellSettings":
        """Build settings from a pipeline configuration object."""

        get = config.get
        return cls(
            dictionary=(
                _optional_text(
                    get("symspell.dictionary", "builtin:en-82k")
                )
                or "builtin:en-82k"
            ),
            glossary=_optional_text(get("symspell.glossary")),
            learned=_optional_text(get("symspell.learned")),
            broken_word_decisions=_optional_text(
                get("symspell.broken_word_decisions")
            ),
            protected_terms=tuple(
                str(term)
                for term in (get("symspell.protected", []) or [])
            ),
            max_edit_distance=int(
                get("symspell.max_edit_distance", 2)
            ),
            maximum_auto_edit_distance=int(
                get("symspell.max_auto_edit_distance", 1)
            ),
            confidence_threshold=float(
                get("symspell.confidence_threshold", 92)
            ),
            minimum_word_length=int(
                get("symspell.minimum_word_length", 4)
            ),
            minimum_candidate_frequency=int(
                get("symspell.minimum_candidate_frequency", 1_000)
            ),
            minimum_dictionary_frequency=int(
                get("symspell.minimum_dictionary_frequency", 1)
            ),
            ambiguity_margin=float(
                get("symspell.ambiguity_margin", 2)
            ),
            auto_protect_proper_nouns=require_bool(
                get("symspell.auto_protect_proper_nouns", True),
                "symspell.auto_protect_proper_nouns",
            ),
            proper_noun_minimum_occurrences=int(
                get("symspell.proper_noun_min_occurrences", 2)
            ),
            broken_word_merge_minimum_frequency=int(
                get(
                    "symspell.broken_word_merge_minimum_frequency",
                    100_000,
                )
            ),
            wordfreq_enabled=require_bool(
                get("symspell.wordfreq_enabled", True),
                "symspell.wordfreq_enabled",
            ),
            wordfreq_language=str(
                get("symspell.wordfreq_language", "en")
            ),
            wordfreq_wordlist=str(
                get("symspell.wordfreq_wordlist", "large")
            ),
            wordfreq_minimum_zipf=float(
                get("symspell.wordfreq_minimum_zipf", 2.5)
            ),
            maximum_merge_passes=int(
                get("symspell.maximum_merge_passes", 3)
            ),
            dehyphenation_zipf_margin=float(
                get("symspell.dehyphenation_zipf_margin", 0.5)
            ),
        )


def _optional_text(value) -> str | None:
    """Normalize an optional path-like setting without turning None into text."""

    return None if value is None else str(value)
