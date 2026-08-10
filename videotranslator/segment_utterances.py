"""Build readable subtitle utterances from timestamped words."""

from __future__ import annotations

import re


TERMINAL_PUNCTUATION = re.compile(r"[.!?。！？؟।॥…][\"'”’»）】]*$")
CJK_CHARACTER = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
CONTINUATION_PUNCTUATION = re.compile(r"[,;:\u060c\u061b\u3001\uff0c\uff1b\uff1a\u2014-][\"'\u201d\u2019\u00bb\uff09\u3011]*$")


def join_words(words: list[dict]) -> str:
    """Join Whisper/CTC tokens without inserting spaces into CJK text."""
    tokens = [str(word.get("word", "")) for word in words]
    if any(token[:1].isspace() for token in tokens):
        return "".join(tokens).strip()
    combined = "".join(tokens)
    if CJK_CHARACTER.search(combined) and not re.search(r"[A-Za-z0-9]", combined):
        return "".join(tokens).strip()
    return " ".join(token.strip() for token in tokens).strip()


def _same_speaker(left: list[dict], right: list[dict]) -> bool:
    """Return whether two word groups have the same known/unknown speaker."""
    return left[-1].get("speaker") == right[0].get("speaker")


def _can_merge(left: list[dict], right: list[dict], maximum_duration: float, maximum_characters: int) -> bool:
    """Check speaker, timing, duration, and text-size constraints for a merge."""
    return (
        _same_speaker(left, right)
        and float(right[0]["start"]) >= float(left[-1]["end"])
        and float(right[-1]["end"]) - float(left[0]["start"]) <= maximum_duration
        and len(join_words(left + right)) <= maximum_characters
    )


def merge_fragments(
    groups: list[list[dict]],
    maximum_duration: float = 8.0,
    maximum_characters: int = 84,
    short_duration: float = 0.4,
    short_gap: float = 0.45,
    continuation_gap: float = 0.9,
) -> list[list[dict]]:
    """Merge only incomplete or ultra-short same-speaker subtitle fragments.

    Terminally punctuated short utterances remain independent. Continuation
    punctuation prefers the following cue; an unpunctuated ultra-short fragment
    may join either neighbor. Hard cue limits and speaker boundaries always win.
    """
    merged = [list(group) for group in groups if group]
    index = 0
    while index < len(merged):
        group = merged[index]
        text = join_words(group)
        duration = float(group[-1]["end"]) - float(group[0]["start"])
        continuation = bool(CONTINUATION_PUNCTUATION.search(text))
        ultra_short = duration < short_duration and len(text) <= 4 and not TERMINAL_PUNCTUATION.search(text)
        if not continuation and not ultra_short:
            index += 1
            continue

        allowed_gap = continuation_gap if continuation else short_gap
        if index + 1 < len(merged):
            following = merged[index + 1]
            gap = float(following[0]["start"]) - float(group[-1]["end"])
            if gap <= allowed_gap and _can_merge(group, following, maximum_duration, maximum_characters):
                merged[index] = group + following
                del merged[index + 1]
                continue
        if not continuation and index > 0:
            previous = merged[index - 1]
            gap = float(group[0]["start"]) - float(previous[-1]["end"])
            if gap <= allowed_gap and _can_merge(previous, group, maximum_duration, maximum_characters):
                merged[index - 1] = previous + group
                del merged[index]
                index = max(0, index - 1)
                continue
        index += 1
    return merged


def segment_words(
    words: list[dict],
    maximum_duration: float = 8.0,
    maximum_characters: int = 84,
    pause_threshold: float = 0.8,
    punctuation_pause: float = 0.12,
) -> list[list[dict]]:
    """Split words on speakers, pauses, sentence endings, and subtitle limits.

    Every input word appears in exactly one output group. A sentence-ending mark
    becomes a boundary only when followed by a measurable pause, avoiding splits
    after abbreviations spoken continuously.
    """
    ordered = sorted(words, key=lambda word: (float(word["start"]), float(word["end"])))
    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in ordered:
        if current:
            previous = current[-1]
            pause = max(0.0, float(word["start"]) - float(previous["end"]))
            duration = float(word["end"]) - float(current[0]["start"])
            characters = len(join_words(current + [word]))
            old_speaker = previous.get("speaker")
            new_speaker = word.get("speaker")
            speaker_changed = old_speaker is not None and new_speaker is not None and old_speaker != new_speaker
            sentence_ended = bool(TERMINAL_PUNCTUATION.search(str(previous.get("word", "")).strip()))
            if (
                speaker_changed
                or pause >= pause_threshold
                or (sentence_ended and pause >= punctuation_pause)
                or duration > maximum_duration
                or characters > maximum_characters
            ):
                groups.append(current)
                current = []
        current.append(word)
    if current:
        groups.append(current)
    return merge_fragments(groups, maximum_duration, maximum_characters)
