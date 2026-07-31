"""Small delete-index spell-correction engine."""

from __future__ import annotations

from collections import defaultdict

from .candidate import CorrectionCandidate


class SymSpellEngine:
    """Index dictionary delete variants and rank nearby words."""

    def __init__(self, max_edit_distance: int = 2) -> None:
        if max_edit_distance < 0:
            raise ValueError("max_edit_distance cannot be negative")
        self.max_edit_distance = max_edit_distance
        self.words: dict[str, int] = {}
        self.deletes: defaultdict[str, set[str]] = defaultdict(set)

    def add_word(self, word: str, frequency: int) -> None:
        """Add a normalized dictionary word and its delete variants."""

        key = word.casefold()
        if not key:
            return
        self.words[key] = max(self.words.get(key, 0), int(frequency))
        for deletion in self._generate_deletes(key):
            self.deletes[deletion].add(key)

    def lookup(self, word: str) -> list[CorrectionCandidate]:
        """Return candidates within the configured edit-distance bound."""

        query = word.casefold()
        if query in self.words:
            return []

        possible: set[str] = set()
        variants = self._generate_deletes(query)
        variants.add(query)
        for variant in variants:
            # A shorter query can itself be a dictionary word's delete key.
            possible.update(self.deletes.get(variant, ()))
            # A longer query can produce the exact dictionary word by deletion.
            if variant in self.words:
                possible.add(variant)

        candidates: list[CorrectionCandidate] = []
        for corrected in possible:
            distance = self.edit_distance(query, corrected)
            if distance > self.max_edit_distance:
                continue
            candidate = CorrectionCandidate(
                original=query,
                corrected=corrected,
                distance=distance,
                frequency=self.words[corrected],
                source="symspell",
            )
            candidate.calculate_confidence()
            candidates.append(candidate)

        return sorted(
            candidates,
            key=lambda item: (
                -item.confidence,
                -item.frequency,
                item.corrected,
            ),
        )

    def _generate_deletes(self, word: str) -> set[str]:
        """Generate unique strings produced by at most N deletions."""

        deletes: set[str] = set()
        queue = {word}
        for _ in range(self.max_edit_distance):
            next_queue: set[str] = set()
            for item in queue:
                for index in range(len(item)):
                    deletion = item[:index] + item[index + 1 :]
                    if deletion in deletes:
                        continue
                    deletes.add(deletion)
                    next_queue.add(deletion)
            queue = next_queue
        return deletes

    @staticmethod
    def edit_distance(left: str, right: str) -> int:
        """Return optimal-string-alignment Damerau-Levenshtein distance."""

        rows = len(left) + 1
        columns = len(right) + 1
        matrix = [[0] * columns for _ in range(rows)]
        for row in range(rows):
            matrix[row][0] = row
        for column in range(columns):
            matrix[0][column] = column

        for row in range(1, rows):
            for column in range(1, columns):
                substitution = (
                    0 if left[row - 1] == right[column - 1] else 1
                )
                matrix[row][column] = min(
                    matrix[row - 1][column] + 1,
                    matrix[row][column - 1] + 1,
                    matrix[row - 1][column - 1] + substitution,
                )
                if (
                    row > 1
                    and column > 1
                    and left[row - 1] == right[column - 2]
                    and left[row - 2] == right[column - 1]
                ):
                    matrix[row][column] = min(
                        matrix[row][column],
                        matrix[row - 2][column - 2] + 1,
                    )

        return matrix[-1][-1]
