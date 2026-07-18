"""
modules/symspell/dictionary.py

Dictionary management for SymSpell correction.

Supports:

- standard frequency dictionary
- custom glossary
- learned words
"""

from __future__ import annotations

import json

from pathlib import Path


class DictionaryManager:
    """
    Manages word frequency data.
    """

    def __init__(
        self,
        dictionary_path=None,
        glossary_path=None,
        learned_path=None,
    ):

        self.words = {}

        self.protected_words = set()

        self.dictionary_path = Path(dictionary_path) if dictionary_path else None

        self.glossary_path = Path(glossary_path) if glossary_path else None

        self.learned_path = Path(learned_path) if learned_path else None

    # ---------------------------------------------------------

    def load(
        self,
    ):
        """
        Load all word sources.
        """

        if self.dictionary_path:

            self._load_frequency_dictionary(self.dictionary_path)

        if self.glossary_path:

            self._load_glossary(self.glossary_path)

        if self.learned_path:

            self._load_learned_words(self.learned_path)

    # ---------------------------------------------------------

    def _load_frequency_dictionary(
        self,
        path: Path,
    ):
        """
        Load:

            word frequency

        format:

            the 5000000
            of 4000000

        """

        if not path.exists():

            return

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:

            for line in file:

                line = line.strip()

                if not line:

                    continue

                parts = line.split()

                if len(parts) != 2:

                    continue

                word, frequency = parts

                self.words[word.lower()] = int(frequency)

    # ---------------------------------------------------------

    def _load_glossary(
        self,
        path: Path,
    ):
        """
        Load novel-specific vocabulary.

        Example:

        [
            "Ainz",
            "Yggdrasil"
        ]

        """

        if not path.exists():

            return

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:

            words = json.load(file)

        for word in words:

            self.add_word(
                word,
                frequency=100000,
                protected=True,
            )

    # ---------------------------------------------------------

    def _load_learned_words(
        self,
        path: Path,
    ):
        """
        Load words learned from previous runs.
        """

        if not path.exists():

            return

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(file)

        for word, frequency in data.items():

            self.add_word(
                word,
                frequency,
                protected=False,
            )

    # ---------------------------------------------------------

    def add_word(
        self,
        word: str,
        frequency: int = 1,
        protected: bool = False,
    ):
        """
        Add word to dictionary.
        """

        key = word.lower()

        self.words[key] = (
            self.words.get(
                key,
                0,
            )
            + frequency
        )

        if protected:

            self.protected_words.add(key)

    # ---------------------------------------------------------

    def contains(
        self,
        word: str,
    ) -> bool:
        """
        Check whether word exists.
        """

        return word.lower() in self.words

    # ---------------------------------------------------------

    def is_protected(
        self,
        word: str,
    ) -> bool:
        """
        Check protected vocabulary.
        """

        return word.lower() in self.protected_words

    # ---------------------------------------------------------

    def frequency(
        self,
        word: str,
    ) -> int:
        """
        Return word frequency.
        """

        return self.words.get(
            word.lower(),
            0,
        )

    # ---------------------------------------------------------

    def save_learned_words(
        self,
    ):
        """
        Save vocabulary learned during processing.
        """

        if not self.learned_path:

            return

        with self.learned_path.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                self.words,
                file,
                indent=2,
                ensure_ascii=False,
            )
