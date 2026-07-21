"""Dictionary loading for safe SymSpell correction."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Iterable


class DictionaryManager:
    """Manage the main frequency dictionary and protected vocabulary.

    Example:
        ``instance = DictionaryManager()``
        Expected behavior: Manage the main frequency dictionary and protected vocabulary.
    """

    BUILTIN_NAMES = {"builtin", "builtin:en", "builtin:en-82k", "symspellpy"}

    def __init__(self, dictionary_path=None, glossary_path=None, learned_path=None):
        """Configure primary, glossary, and learned vocabulary sources.

        Example:
            ``instance = DictionaryManager()``
            Expected behavior: Configure primary, glossary, and learned vocabulary sources.
        """
        self.words: dict[str, int] = {}
        self.protected_words: set[str] = set()
        self.dictionary_path = dictionary_path
        self.glossary_path = Path(glossary_path) if glossary_path else None
        self.learned_path = Path(learned_path) if learned_path else None

    def load(self) -> None:
        """Load all configured vocabulary sources into memory.

        Example:
            ``result = instance.load()``
            Expected behavior: Load all configured vocabulary sources into memory.
        """
        if self.dictionary_path:
            path = self._resolve_dictionary_path(self.dictionary_path)
            self._load_frequency_dictionary(path)
        if self.glossary_path:
            self._load_glossary(self.glossary_path)
        if self.learned_path:
            self._load_learned_words(self.learned_path)

    @classmethod
    def _resolve_dictionary_path(cls, value) -> Path:
        """Resolve a built-in dictionary alias or explicit path.

        Example:
            ``result = DictionaryManager._resolve_dictionary_path("value")``
            Expected behavior: Resolve a built-in dictionary alias or explicit path.
        """
        text = str(value).strip()
        if text.lower() not in cls.BUILTIN_NAMES:
            return Path(text)

        try:
            resource = resources.files("symspellpy").joinpath(
                "frequency_dictionary_en_82_765.txt"
            )
            # The normal installed package exposes a real filesystem path.
            return Path(str(resource))
        except (ModuleNotFoundError, TypeError) as exc:
            raise RuntimeError(
                "SymSpell is enabled with the built-in English dictionary, but "
                "the 'symspellpy' package is not installed. Run: "
                "pip install -r requirements.txt"
            ) from exc

    def _load_frequency_dictionary(self, path: Path) -> None:
        """Load word-frequency pairs from a SymSpell text dictionary.

        Example:
            ``result = instance._load_frequency_dictionary(Path("output.json"))``
            Expected behavior: Load word-frequency pairs from a SymSpell text dictionary.
        """
        if not path.exists():
            raise FileNotFoundError(f"SymSpell dictionary not found: {path}")
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                parts = line.strip().split()
                if len(parts) < 2:
                    continue
                word = parts[0]
                try:
                    frequency = int(parts[1])
                except ValueError:
                    continue
                self.add_word(word, frequency)

    def _load_glossary(self, path: Path) -> None:
        """Load custom terms as frequent protected vocabulary.

        Example:
            ``result = instance._load_glossary(Path("output.json"))``
            Expected behavior: Load custom terms as frequent protected vocabulary.
        """
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        words: Iterable[str]
        if isinstance(data, dict):
            words = data.keys()
        elif isinstance(data, list):
            words = data
        else:
            return
        for word in words:
            self.add_word(str(word), frequency=100_000, protected=True)

    def _load_learned_words(self, path: Path) -> None:
        """Load user-approved learned words as protected vocabulary.

        Learned terms are intentionally *not* used as correction targets. This
        prevents a typo accidentally learned in one book from being propagated
        into later books.

        Example:
            ``result = instance._load_learned_words(Path("output.json"))``
            Expected behavior: Load user-approved learned words as protected vocabulary.
        """
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "words" in data:
            words = data["words"] if isinstance(data["words"], list) else []
        elif isinstance(data, dict):
            words = [word for word in data if not str(word).startswith("_")]
        else:
            words = data if isinstance(data, list) else []
        for word in words:
            self.add_word(str(word), frequency=1, protected=True)

    def add_word(self, word: str, frequency: int = 1, protected: bool = False) -> None:
        """Add or update a normalized word and optionally protect it.

        Example:
            ``result = instance.add_word("teh")``
            Expected behavior: Add or update a normalized word and optionally protect it.
        """
        key = word.lower()
        if not key:
            return
        self.words[key] = max(self.words.get(key, 0), int(frequency))
        if protected:
            self.protected_words.add(key)

    def protect(self, word: str) -> None:
        """Exempt a word from automatic correction.

        Example:
            ``instance.protect("teh")``
            Expected behavior: Exempt a word from automatic correction.
        """
        if word:
            self.protected_words.add(word.lower())

    def contains(self, word: str) -> bool:
        """Return whether a word exists in the combined dictionary.

        Example:
            ``result = instance.contains("teh")``
            Expected behavior: Return whether a word exists in the combined dictionary.
        """
        return word.lower() in self.words

    def is_protected(self, word: str) -> bool:
        """Return whether a word is exempt from correction.

        Example:
            ``result = instance.is_protected("teh")``
            Expected behavior: Return whether a word is exempt from correction.
        """
        return word.lower() in self.protected_words

    def frequency(self, word: str) -> int:
        """Return a word's frequency or zero when unknown.

        Example:
            ``result = instance.frequency("teh")``
            Expected behavior: Return a word's frequency or zero when unknown.
        """
        return self.words.get(word.lower(), 0)
