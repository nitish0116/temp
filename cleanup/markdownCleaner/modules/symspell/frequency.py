"""Frequency scoring backed by the broader ``wordfreq`` corpus."""

from __future__ import annotations

from collections.abc import Callable


class WordfreqScorer:
    """Expose wordfreq Zipf scores and comparable per-billion ranks.

    The optional callable makes the adapter deterministic in tests. If the
    dependency is unavailable, scoring is disabled and callers can fall back to
    the bundled SymSpell frequency dictionary.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        language: str = "en",
        wordlist: str = "large",
        lookup: Callable[..., float] | None = None,
    ) -> None:
        self.language = language
        self.wordlist = wordlist
        self.available = False
        self._lookup: Callable[..., float] | None = None
        if not enabled:
            return
        if lookup is not None:
            self._lookup = lookup
            self.available = True
            return
        try:
            from wordfreq import zipf_frequency
        except ImportError:
            return
        self._lookup = zipf_frequency
        self.available = True

    def zipf(self, word: str) -> float:
        """Return the word's Zipf frequency, or zero when unavailable."""
        if not self._lookup or not word:
            return 0.0
        return float(
            self._lookup(word, self.language, wordlist=self.wordlist)
        )

    def rank(self, word: str) -> int:
        """Return an integer per-billion score suitable for candidate ranking."""
        score = self.zipf(word)
        return round(10**score) if score > 0 else 0
