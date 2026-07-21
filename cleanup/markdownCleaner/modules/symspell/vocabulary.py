"""Report-only discovery and explicit approval of domain vocabulary."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re

from ..core.stage import PipelineStage, StageResult
from .dictionary import DictionaryManager
from .engine import SymSpellEngine


WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+|-[A-Za-z]+)*")
TERM = re.compile(rf"{WORD.pattern}(?:\s+{WORD.pattern})*")
LEARNED_DESCRIPTION = (
    "Words explicitly reviewed by the user. Add entries with "
    "`python -m markdownCleaner.cli --learn-words WORD ...`."
)


def _word_list(data, *, label: str) -> list[str]:
    """Extract words from legacy or structured vocabulary JSON.

    Example:
        ``_word_list({"words": ["sitrep"]}, label="Learned words")`` returns
        ``["sitrep"]``. Legacy JSON lists and word-keyed objects remain valid.
    """
    if isinstance(data, list):
        return [str(word) for word in data]
    if isinstance(data, dict):
        if "words" in data:
            words = data["words"]
            if not isinstance(words, list):
                raise ValueError(f"{label} JSON field 'words' must be a list.")
            return [str(word) for word in words]
        return [str(word) for word in data if not str(word).startswith("_")]
    raise ValueError(f"{label} JSON must contain a list or object.")


def _merge_words(path: str | Path, words: list[str], *, structured: bool) -> list[str]:
    """Validate, deduplicate, sort, and persist reviewed vocabulary.

    Example:
        ``_merge_words(path, ["sitrep"], structured=True)`` writes a readable
        object containing instructions and a sorted ``words`` list.
    """
    target = Path(path)
    existing: list[str] = []
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in {target}: line {exc.lineno}, column {exc.colno}. "
                "Use --learn-words to update this file safely."
            ) from exc
        existing = _word_list(data, label="Vocabulary")

    by_key = {word.casefold(): word for word in existing if word.strip()}
    added: list[str] = []
    for raw in words:
        word = str(raw).strip()
        if not TERM.fullmatch(word) or len(word) < 2:
            raise ValueError(f"Invalid vocabulary word: {raw!r}")
        if word.casefold() not in by_key:
            by_key[word.casefold()] = word
            added.append(word)

    target.parent.mkdir(parents=True, exist_ok=True)
    values = sorted(by_key.values(), key=str.casefold)
    data = (
        {"_description": LEARNED_DESCRIPTION, "words": values} if structured else values
    )
    target.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return added


def merge_approved_words(path: str | Path, words: list[str]) -> list[str]:
    """Merge explicitly approved terms into a JSON glossary.

    Existing list- or object-based glossaries are accepted. Terms are validated,
    deduplicated case-insensitively, sorted for stable review, and written as a
    JSON list. Nothing is added without appearing in ``words``.

    Example::

        added = merge_approved_words(
            "data/custom_words.json", ["sitrep", "Ainz Ooal Gown"]
        )

    Returns:
        The newly inserted terms; existing terms are omitted from this list.

    Raises:
        ValueError: If the glossary shape or an approved term is invalid.
    """
    return _merge_words(path, words, structured=False)


def merge_learned_words(path: str | Path, words: list[str]) -> list[str]:
    """Safely add reviewed terms to the structured learned-word file.

    Example:
        ``merge_learned_words("data/learned_words.json", ["sitrep", "noncoms"])``
        validates and adds only terms that are not already present.
    """
    return _merge_words(path, words, structured=True)


class VocabularyCandidateStage(PipelineStage):
    """Discover domain vocabulary for review without silently approving it.

    The report-only workflow counts token forms and source lines, excludes known
    or protected words, and attaches the best dictionary suggestion when one
    exists. Candidates are stored in ``context.metadata['glossary_candidates']``
    and logged as ``pending_review``. The document and glossary remain unchanged.

    A reviewer can later approve a candidate explicitly with::

        python -m markdownCleaner.cli --approve-words sitrep noncoms

    Example:
        ``instance = VocabularyCandidateStage(config)``
        Expected behavior: Discover domain vocabulary for review without silently approving it.
    """

    name = "VocabularyCandidates"
    config_section = "vocabulary_candidates"

    def process(self, context) -> StageResult:
        """Collect and report repeated unknown terms as review-only candidates.

        The configured occurrence threshold suppresses one-off noise, while the
        report limit bounds memory and audit output. ``changes`` in the returned
        result means findings reported—not text mutations—for this stage.

        Example:
            ``result = instance.process(context)``
            Expected behavior: Collect and report repeated unknown terms as review-only candidates.
        """
        text = context.current_markdown or context.original_markdown
        manager = DictionaryManager(
            dictionary_path=context.config.resolve_path(
                context.config.get("symspell.dictionary", "builtin:en-82k")
            ),
            glossary_path=context.config.resolve_path(
                context.config.get("symspell.glossary")
            ),
            learned_path=context.config.resolve_path(
                context.config.get("symspell.learned")
            ),
        )
        manager.load()
        for word in context.config.get("symspell.protected", []) or []:
            manager.protect(str(word))

        counts: Counter[str] = Counter()
        forms: dict[str, Counter[str]] = defaultdict(Counter)
        lines: dict[str, list[int]] = defaultdict(list)
        for line_number, line in enumerate(text.splitlines(), 1):
            for token in WORD.findall(line):
                key = token.casefold()
                counts[key] += 1
                forms[key][token] += 1
                if len(lines[key]) < 10:
                    lines[key].append(line_number)

        engine = SymSpellEngine(
            max_edit_distance=int(context.config.get("symspell.max_edit_distance", 2))
        )
        minimum_frequency = int(
            context.config.get("symspell.minimum_dictionary_frequency", 1)
        )
        for word, frequency in manager.words.items():
            if frequency >= minimum_frequency:
                engine.add_word(word, frequency)

        minimum = int(self.get_config("minimum_occurrences", 3))
        limit = int(self.get_config("report_limit", 200))
        candidates: list[dict] = []
        if limit <= 0:
            context.metadata["glossary_candidates"] = candidates
            return StageResult(stage=self.name, changes=0)
        for key, count in counts.most_common():
            if count < minimum or manager.contains(key) or manager.is_protected(key):
                continue
            if len(key) < 4 or not key.isalpha():
                continue
            display = forms[key].most_common(1)[0][0]
            suggestions = engine.lookup(key)
            best = suggestions[0] if suggestions else None
            item = {
                "word": display,
                "occurrences": count,
                "lines": lines[key],
                "suggested_correction": best.corrected if best else None,
                "edit_distance": best.distance if best else None,
                "confidence": round(best.confidence, 2) if best else None,
                "status": "pending_review",
            }
            candidates.append(item)
            context.tracker.add(
                stage=self.name,
                block_index=-1,
                segment_index=-1,
                line=lines[key][0] if lines[key] else 0,
                before=display,
                after=display,
                confidence=0.0,
                reason=(
                    f"Candidate only; {count} occurrences. Explicit approval "
                    "is required before adding it to custom_words.json."
                ),
            )
            if len(candidates) >= limit:
                break

        context.metadata["glossary_candidates"] = candidates
        return StageResult(stage=self.name, changes=len(candidates))
