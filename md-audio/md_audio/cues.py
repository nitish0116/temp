"""Write approximate CUE and YouTube chapter metadata for narrated audio."""

from __future__ import annotations

from pathlib import Path


def estimate_chunk_durations_ms(chunks: list[str], total_ms: int) -> list[int]:
    """Distribute total duration across chunks in proportion to character count."""
    total_characters = sum(map(len, chunks))
    if total_characters == 0 or total_ms == 0:
        return [0] * len(chunks)

    durations: list[int] = []
    allocated = 0
    for index, chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            duration = total_ms - allocated
        else:
            duration = round(len(chunk) / total_characters * total_ms)
            allocated += duration
        durations.append(duration)
    return durations


def milliseconds_to_cue(milliseconds: int) -> str:
    """Convert milliseconds to the CUE ``MM:SS:FF`` timestamp format."""
    total_seconds = milliseconds // 1000
    frames = (milliseconds % 1000) * 75 // 1000
    return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}:{frames:02d}"


def milliseconds_to_youtube(milliseconds: int) -> str:
    """Convert milliseconds to a YouTube ``M:SS`` or ``H:MM:SS`` timestamp."""
    total_seconds = milliseconds // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return (
        f"{hours}:{minutes:02d}:{seconds:02d}"
        if hours
        else f"{minutes}:{seconds:02d}"
    )


def write_cue_file(
    output_mp3: Path,
    scene_map: dict[int, str],
    chunks: list[str],
    total_ms: int,
) -> tuple[Path, Path]:
    """Write an approximate CUE sheet and YouTube chapter timestamp file."""
    durations = estimate_chunk_durations_ms(chunks, total_ms)
    cumulative: list[int] = []
    running = 0
    for duration in durations:
        cumulative.append(running)
        running += duration

    scenes = [
        (cumulative[index] if index < len(cumulative) else 0, scene_map[index])
        for index in sorted(scene_map)
    ]
    if scenes and scenes[0][0] != 0:
        scenes.insert(0, (0, scenes[0][1]))
    if not scenes:
        scenes = [(0, output_mp3.stem)]

    cue_path = output_mp3.with_suffix(".cue")
    cue_lines = [f'FILE "{output_mp3.name}" MP3']
    for track_number, (start_ms, title) in enumerate(scenes, start=1):
        cue_lines.extend(
            [
                f"  TRACK {track_number:02d} AUDIO",
                f'    TITLE "{title}"',
                f"    INDEX 01 {milliseconds_to_cue(start_ms)}",
            ]
        )
    cue_path.write_text("\n".join(cue_lines) + "\n", encoding="utf-8")

    youtube_path = output_mp3.with_name(f"{output_mp3.stem}_youtube_chapters.txt")
    youtube_lines = [
        "Paste these timestamps into the YouTube video description:",
        "",
        *(f"{milliseconds_to_youtube(start_ms)} {title}" for start_ms, title in scenes),
        "",
        "Note: YouTube requires at least 3 timestamps and the first must be 0:00.",
        "Chapters appear automatically once the video is published.",
    ]
    youtube_path.write_text("\n".join(youtube_lines) + "\n", encoding="utf-8")
    return cue_path, youtube_path
