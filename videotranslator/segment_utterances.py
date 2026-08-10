"""Build readable subtitle utterances from timestamped words."""

from __future__ import annotations

import re


TERMINAL_PUNCTUATION = re.compile(r"[.!?。！？؟।॥…][\"'”’»）】]*$")
CJK_CHARACTER = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def join_words(words: list[dict]) -> str:
    """Join Whisper/CTC tokens without inserting spaces into CJK text."""
    tokens = [str(word.get("word", "")) for word in words]
    if any(token[:1].isspace() for token in tokens):
        return "".join(tokens).strip()
    combined = "".join(tokens)
    if CJK_CHARACTER.search(combined) and not re.search(r"[A-Za-z0-9]", combined):
        return "".join(tokens).strip()
    return " ".join(token.strip() for token in tokens).strip()


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
    return groups
