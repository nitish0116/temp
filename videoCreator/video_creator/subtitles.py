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
        clip = audio_map[identifier]; duration = float(clip["duration_seconds"]); block_start = cursor
        timing_segments = (clip.get("timing") or {}).get("segments")
        if not timing_segments:
            timing_segments = [{"text": block["adapted_text"], "start_seconds": 0.0, "end_seconds": duration}]
        cue_index = 0
        for segment in timing_segments:
            chunks = _chunks(segment["text"]); weights = [max(1, len(x)) for x in chunks]
            segment_start = block_start + float(segment["start_seconds"])
            segment_duration = float(segment["end_seconds"]) - float(segment["start_seconds"])
            segment_cursor = segment_start
            for chunk, weight in zip(chunks, weights):
                cue_index += 1; end = segment_cursor + segment_duration * weight / sum(weights)
                cues.append({"cue_id": f"{identifier}-cue-{cue_index:03d}", "narration_id": identifier,
                             "start_seconds": round(segment_cursor, 3), "end_seconds": round(end, 3), "text": chunk})
                segment_cursor = end
            cues[-1]["end_seconds"] = round(block_start + float(segment["end_seconds"]), 3)
        cursor = block_start + duration
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
    trailing_silence = float(alignment.get("duration_seconds", 0)) - previous_end
    if not 0 <= trailing_silence <= 1.25: issues.append("subtitle coverage does not match audio duration")
    return issues
