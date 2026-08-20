"""Deterministic narration alignment and subtitle serialization."""
from __future__ import annotations
import re
from pathlib import Path

def _chunks(text: str, maximum_characters: int = 42) -> list[str]:
    chunks = []
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
        current = []
        for word in sentence.split():
            if current and len(" ".join([*current, word])) > maximum_characters:
                chunks.append(" ".join(current)); current = []
            current.append(word)
        if current: chunks.append(" ".join(current))
    return chunks

def align_narration(narration: dict, audio: dict) -> dict:
    audio_map = {item["narration_id"]: item for item in audio["clips"]}
    cues, cursor = [], 0.0
    for block in narration["blocks"]:
        identifier = block["narration_id"]
        duration = float(audio_map[identifier]["duration_seconds"])
        chunks = _chunks(block["adapted_text"]); weights = [max(1, len(x)) for x in chunks]
        block_end = cursor + duration
        for index, (chunk, weight) in enumerate(zip(chunks, weights), 1):
            end = cursor + duration * weight / sum(weights)
            cues.append({"cue_id": f"{identifier}-cue-{index:03d}", "narration_id": identifier,
                         "start_seconds": round(cursor, 3), "end_seconds": round(end, 3), "text": chunk})
            cursor = end
        cursor = block_end; cues[-1]["end_seconds"] = round(cursor, 3)
    return {"schema_version": 1, "status": "auto_accepted",
            "method": "audio-duration-proportional-sentence-v1",
            "source_sha256": narration["source_sha256"], "duration_seconds": round(cursor, 3), "cues": cues}

def _timestamp(value: float, separator: str) -> str:
    milliseconds = round(value * 1000); hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000); seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{milliseconds:03d}"

def write_subtitles(alignment: dict, srt_path: Path, vtt_path: Path) -> None:
    srt, vtt = [], ["WEBVTT", ""]
    for index, cue in enumerate(alignment["cues"], 1):
        srt.extend([str(index), f"{_timestamp(cue['start_seconds'], ',')} --> {_timestamp(cue['end_seconds'], ',')}", cue["text"], ""])
        vtt.extend([f"{_timestamp(cue['start_seconds'], '.')} --> {_timestamp(cue['end_seconds'], '.')}", cue["text"], ""])
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("\n".join(srt), encoding="utf-8"); vtt_path.write_text("\n".join(vtt), encoding="utf-8")

def validate_alignment(alignment: dict) -> list[str]:
    issues, previous_end = [], 0.0
    for cue in alignment.get("cues", []):
        start, end = float(cue.get("start_seconds", -1)), float(cue.get("end_seconds", -1))
        if start < previous_end - 0.002: issues.append(f"overlapping cue: {cue.get('cue_id')}")
        if end <= start: issues.append(f"invalid cue duration: {cue.get('cue_id')}")
        if len(cue.get("text", "")) > 42: issues.append(f"cue too long: {cue.get('cue_id')}")
        previous_end = end
    if abs(previous_end - float(alignment.get("duration_seconds", 0))) > 0.01: issues.append("subtitle coverage does not match audio duration")
    return issues
