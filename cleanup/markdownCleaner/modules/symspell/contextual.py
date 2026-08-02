"""Report likely real-word OCR substitutions using reviewed context rules."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from ..core.stage import PipelineStage, StageResult


WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")


@dataclass(frozen=True, slots=True)
class ContextualRule:
    source: str
    suggestion: str
    previous: frozenset[str] = frozenset()
    following: frozenset[str] = frozenset()
    confidence: float = 60.0

    def matches(self, previous: str, following: str) -> bool:
        previous_match = bool(
            self.previous and previous.casefold() in self.previous
        )
        following_match = bool(
            self.following and following.casefold() in self.following
        )
        return previous_match or following_match


def load_contextual_rules(path: str | Path | None) -> tuple[ContextualRule, ...]:
    if not path or not Path(path).exists():
        return ()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_rules = data.get("rules", []) if isinstance(data, dict) else []
    if not isinstance(raw_rules, list):
        raise ValueError("Contextual real-word rules must contain a rules list.")
    rules: list[ContextualRule] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source", "")).casefold().strip()
        suggestion = str(raw.get("suggestion", "")).strip()
        if not source or not suggestion:
            continue
        rules.append(
            ContextualRule(
                source=source,
                suggestion=suggestion,
                previous=frozenset(
                    str(word).casefold() for word in raw.get("previous", [])
                ),
                following=frozenset(
                    str(word).casefold() for word in raw.get("following", [])
                ),
                confidence=float(raw.get("confidence", 60.0)),
            )
        )
    return tuple(rules)


class ContextualRealWordStage(PipelineStage):
    """Emit review-only suggestions; never rewrite dictionary-valid words."""

    name = "ContextualRealWords"
    config_section = "contextual_real_words"

    def process(self, context) -> StageResult:
        rules = load_contextual_rules(
            context.config.resolve_path(self.get_config("rules"))
        )
        by_source: dict[str, list[ContextualRule]] = {}
        for rule in rules:
            by_source.setdefault(rule.source, []).append(rule)
        limit = int(self.get_config("report_limit", 200))
        findings = 0
        for segment in context.iter_segments():
            matches = list(WORD.finditer(segment.current_text))
            for index, match in enumerate(matches):
                if findings >= limit:
                    return StageResult(stage=self.name, changes=0)
                previous = matches[index - 1].group(0) if index else ""
                following = (
                    matches[index + 1].group(0) if index + 1 < len(matches) else ""
                )
                for rule in by_source.get(match.group(0).casefold(), []):
                    if not rule.matches(previous, following):
                        continue
                    start = matches[max(0, index - 3)].start()
                    end = matches[min(len(matches) - 1, index + 3)].end()
                    excerpt = segment.current_text[start:end]
                    relative = match.start() - start
                    suggested = (
                        excerpt[:relative]
                        + rule.suggestion
                        + excerpt[relative + len(match.group(0)) :]
                    )
                    context.tracker.add(
                        stage=self.name,
                        block_index=segment.block_index,
                        segment_index=segment.segment_index,
                        line=segment.start_line
                        + segment.current_text[: match.start()].count("\n"),
                        before=excerpt,
                        after=suggested,
                        confidence=rule.confidence,
                        reason=(
                            "Report only; contextual real-word OCR candidate "
                            f"{match.group(0)!r} -> {rule.suggestion!r}"
                        ),
                        broken_word=match.group(0),
                        applied=False,
                    )
                    findings += 1
                    break
        return StageResult(stage=self.name, changes=0)
