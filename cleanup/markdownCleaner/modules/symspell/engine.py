"""
modules/symspell/engine.py

Core SymSpell lookup engine.

Provides:

- delete dictionary generation
- candidate lookup
- edit distance ranking
"""

from __future__ import annotations

from collections import defaultdict

from .candidate import (
    CorrectionCandidate,
)


class SymSpellEngine:
    """SymSpell implementation.

    Example:
        ``instance = SymSpellEngine()``
        Expected behavior: SymSpell implementation.
    """

    def __init__(
        self,
        max_edit_distance: int = 2,
    ):
        """Initialize empty word and delete indexes for an edit-distance limit.

        Example:
            ``instance = SymSpellEngine()``
            Expected behavior: Initialize empty word and delete indexes for an edit-distance limit.
        """

        self.max_edit_distance = max_edit_distance

        #
        # word -> frequency
        #

        self.words = {}

        #
        # delete -> possible words
        #

        self.deletes = defaultdict(set)

    # ---------------------------------------------------------

    def add_word(
        self,
        word: str,
        frequency: int,
    ):
        """Add dictionary word.

        Example:
            ``result = instance.add_word("teh", 1000)``
            Expected behavior: Add dictionary word.
        """

        key = word.lower()

        self.words[key] = frequency

        for delete in self._generate_deletes(key):

            self.deletes[delete].add(key)

    # ---------------------------------------------------------

    def lookup(
        self,
        word: str,
    ):
        """Find correction candidates.

        Example:
            ``result = instance.lookup("teh")``
            Expected behavior: Find correction candidates.
        """

        word = word.lower()

        candidates = []

        #
        # Exact match
        #

        if word in self.words:

            return []

        deletes = self._generate_deletes(word)

        possible = set()

        for delete in deletes:

            for candidate in self.deletes.get(delete, []):

                possible.add(candidate)

        for candidate in possible:

            distance = self.edit_distance(
                word,
                candidate,
            )

            if distance <= (self.max_edit_distance):

                item = CorrectionCandidate(
                    original=word,
                    corrected=candidate,
                    distance=distance,
                    frequency=self.words.get(
                        candidate,
                        0,
                    ),
                    source="symspell",
                )

                item.calculate_confidence()

                candidates.append(item)

        return sorted(candidates, key=lambda x: (-x.confidence))

    # ---------------------------------------------------------

    def _generate_deletes(
        self,
        word,
    ):
        """Generate delete variants.

        Example:
            ``result = instance._generate_deletes("teh")``
            Expected behavior: Generate delete variants.
        """

        deletes = set()

        queue = {word}

        for _ in range(self.max_edit_distance):

            next_queue = set()

            for item in queue:

                for i in range(len(item)):

                    deletion = item[:i] + item[i + 1 :]

                    if deletion not in deletes:

                        deletes.add(deletion)

                        next_queue.add(deletion)

            queue = next_queue

        return deletes

    # ---------------------------------------------------------

    def edit_distance(
        self,
        a,
        b,
    ):
        """Damerau-Levenshtein distance.

        Simplified implementation.

        Example:
            ``result = instance.edit_distance(a, b)``
            Expected behavior: Damerau-Levenshtein distance.
        """

        rows = len(a) + 1

        cols = len(b) + 1

        matrix = [[0 for _ in range(cols)] for _ in range(rows)]

        for i in range(rows):

            matrix[i][0] = i

        for j in range(cols):

            matrix[0][j] = j

        for i in range(1, rows):

            for j in range(1, cols):

                cost = 0 if a[i - 1] == b[j - 1] else 1

                matrix[i][j] = min(
                    matrix[i - 1][j] + 1,
                    matrix[i][j - 1] + 1,
                    matrix[i - 1][j - 1] + cost,
                )

        return matrix[-1][-1]
