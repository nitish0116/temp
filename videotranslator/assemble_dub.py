"""Align generated speech, mix it with the source soundtrack, and export video."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def media_duration(path: Path) -> float:
    """Return media duration in seconds using FFprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def tempo_filters(factor: float) -> list[str]:
    """Split a tempo factor into FFmpeg-compatible ``atempo`` filters.

    Example: a factor of 4 becomes two ``atempo=2`` filters.
    """
    filters: list[str] = []
    while factor > 2.0:
        filters.append("atempo=2")
        factor /= 2.0
    while factor < 0.5:
        filters.append("atempo=0.5")
        factor /= 0.5
    if abs(factor - 1.0) > 0.001:
        filters.append(f"atempo={factor:.6f}")
    return filters


def build_alignment_graph(clips: list[dict], duration: float) -> str:
    """Build a filter graph that fits clips into cue windows and delays them."""
    chains = [f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.3f}[base]"]
    labels = ["[base]"]
    for input_index, clip in enumerate(clips):
        window = float(clip["end"]) - float(clip["start"])
        generated = float(clip["generated_duration"])
        filters = ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if generated > window and window > 0:
            filters.extend(tempo_filters(generated / window))
        filters.extend(
            [
                f"atrim=duration={window:.6f}",
                f"adelay={round(float(clip['start']) * 1000)}:all=1",
            ]
        )
        label = f"c{input_index + 1}"
        chains.append(f"[{input_index}:a]{','.join(filters)}[{label}]")
        labels.append(f"[{label}]")
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,alimiter=limit=0.95[dub]")
    return ";\n".join(chains)


def assemble_dub(
    video: Path,
    manifest_path: Path,
    output: Path,
    subtitles: Path | None = None,
    background: Path | None = None,
    source_volume: float = 0.55,
    dub_volume: float = 1.0,
) -> dict:
    """Create an aligned dialogue track and mux a ducked source-audio mix."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    clips = [clip for clip in manifest["clips"] if clip.get("status") in {"generated", "aligned"}]
    if not clips:
        raise ValueError("Dub manifest contains no generated clips")
    missing = [clip["audio_path"] for clip in clips if not Path(clip["audio_path"]).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing {len(missing)} generated audio clips; first: {missing[0]}")

    output.parent.mkdir(parents=True, exist_ok=True)
    duration = media_duration(video)
    dialogue_path = output.with_suffix(".dialogue.wav")
    with tempfile.NamedTemporaryFile("w", suffix=".ffgraph", encoding="utf-8", delete=False) as graph_file:
        graph_path = Path(graph_file.name)
        graph_file.write(build_alignment_graph(clips, duration))
    try:
        align_command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        for clip in clips:
            align_command += ["-i", clip["audio_path"]]
        align_command += ["-filter_complex_script", str(graph_path), "-map", "[dub]", "-c:a", "pcm_s16le", str(dialogue_path)]
        subprocess.run(align_command, check=True)
    finally:
        graph_path.unlink(missing_ok=True)

    background_index = 2 if background else 0
    mix_method = "demucs-accompaniment-plus-aligned-dub" if background else "sidechain-duck-source-under-aligned-dub"
    if background:
        mix = f"[{background_index}:a]volume={source_volume}[background];[background][1:a]amix=inputs=2:duration=longest:weights='1 {dub_volume}':normalize=0,alimiter=limit=0.95[mix]"
    else:
        mix = f"[0:a]volume={source_volume}[source];[source][1:a]sidechaincompress=threshold=0.015:ratio=12:attack=15:release=300[ducked];[ducked][1:a]amix=inputs=2:duration=first:weights='1 {dub_volume}':normalize=0,alimiter=limit=0.95[mix]"
    export_command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(dialogue_path)]
    if background:
        export_command += ["-i", str(background)]
    if subtitles:
        export_command += ["-i", str(subtitles)]
    export_command += ["-filter_complex", mix, "-map", "0:v:0", "-map", "[mix]"]
    if subtitles:
        subtitle_index = 3 if background else 2
        export_command += ["-map", f"{subtitle_index}:0", "-c:s", "mov_text", "-metadata:s:s:0", f"language={manifest['target_language']}"]
    export_command += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(output)]
    subprocess.run(export_command, check=True)

    report = {
        "schema_version": 1,
        "project_id": manifest["project_id"],
        "target_language": manifest["target_language"],
        "input_video": str(video.resolve()),
        "dialogue_track": str(dialogue_path.resolve()),
        "output_video": str(output.resolve()),
        "clip_count": len(clips),
        "source_volume": source_volume,
        "dub_volume": dub_volume,
        "mix_method": mix_method,
        "background_track": str(background.resolve()) if background else None,
        "subtitles": str(subtitles.resolve()) if subtitles else None,
    }
    output.with_suffix(".assembly.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    """Parse CLI arguments and render the final dubbed video."""
    parser = argparse.ArgumentParser(description="Align, mix, and export a generated video dub.")
    parser.add_argument("video", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--subtitles", type=Path)
    parser.add_argument("--background", type=Path, help="Separated accompaniment/no-vocals track")
    parser.add_argument("--source-volume", type=float, default=0.55)
    parser.add_argument("--dub-volume", type=float, default=1.0)
    args = parser.parse_args()
    report = assemble_dub(args.video, args.manifest, args.output, args.subtitles, args.background, args.source_volume, args.dub_volume)
    print(f"Aligned {report['clip_count']} clips")
    print(f"Final dubbed video: {report['output_video']}")


if __name__ == "__main__":
    main()
