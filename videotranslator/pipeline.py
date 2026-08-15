"""Run and track the source-video to target-language pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
COMMANDS = HERE / "commands"
RUNNABLE_STAGES = (
    "extract",
    "separate",
    "translate",
    "qa",
    "diarize",
    "tts",
    "review",
    "subtitle_mux",
    "assemble",
    "final_qa",
)
STAGES = RUNNABLE_STAGES


def now() -> str:
    """Return the current UTC time as an ISO-8601 manifest timestamp."""
    return datetime.now(timezone.utc).isoformat()


def load_config(path: Path) -> dict[str, Any]:
    """Load pipeline JSON and resolve its media paths relative to the file.

    Example:: if ``config/pipeline.json`` contains ``../episode.mp4``, the
    returned ``input_video`` is an absolute path beside the ``config`` folder.
    """
    config = json.loads(path.read_text(encoding="utf-8"))
    required = ("project_id", "input_video", "output_root")
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError("Missing pipeline configuration: " + ", ".join(missing))
    base = path.resolve().parent
    config["input_video"] = str((base / config["input_video"]).resolve())
    config["output_root"] = str((base / config["output_root"]).resolve())
    return config


def paths(config: dict[str, Any]) -> dict[str, Path]:
    """Derive every deterministic artifact path for a pipeline project.

    Example:: ``paths(config)["srt"]`` points to the canonical repaired English
    subtitle file consumed by both review rendering and final subtitle muxing.
    """
    video = Path(config["input_video"])
    root = Path(config["output_root"])
    stem = video.stem
    target_language = config.get("translation", {}).get("target_language", "en")
    return {
        "video": video,
        "root": root,
        "manifest": root / "manifest.json",
        "audio": root / "audio" / f"{stem}.wav",
        "separation_dir": root / "separation",
        "accompaniment": root / "separation" / "accompaniment.wav",
        "vocals": root / "separation" / "vocals.wav",
        "separation_report": root / "separation" / "separation.json",
        "transcript_dir": root / "transcripts",
        "transcript_json": root / "transcripts" / f"{stem}.auto.{target_language}.json",
        "srt": root / "transcripts" / f"{stem}.auto.{target_language}.srt",
        "source_transcript": root / "transcripts" / f"{stem}.source.json",
        "decisions": root / "transcripts" / f"{stem}.decisions.json",
        "approved_script": root / "transcripts" / f"{stem}.approved.json",
        "diarized_script": root / "diarization" / f"{stem}.assigned.json",
        "diarization_report": root / "diarization" / f"{stem}.speakers.json",
        "qa": root / "qa" / f"{stem}.qa.json",
        "review": root / "review" / f"{stem}.top-subs.mp4",
        "subtitled": root / "final" / f"{stem}.{target_language}-subs.mp4",
        "dub_dir": root / "dub",
        "dub_manifest": root / "dub" / "dub-manifest.json",
        "dubbed": root / "final" / f"{stem}.{target_language}-dubbed.mp4",
        "assembly_report": root / "final" / f"{stem}.{target_language}-dubbed.assembly.json",
        "final_qa": root / "qa" / f"{stem}.{target_language}-dubbed.qa.json",
    }


def load_manifest(config: dict[str, Any], artifact_paths: dict[str, Path]) -> dict:
    """Load an existing run manifest or initialize all known pipeline stages."""
    manifest_path = artifact_paths["manifest"]
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for stage in STAGES:
            manifest.setdefault("stages", {}).setdefault(stage, {"status": "pending"})
        return manifest
    return {
        "schema_version": 1,
        "project_id": config["project_id"],
        "created_at": now(),
        "updated_at": now(),
        "input_video": str(artifact_paths["video"]),
        "stages": {stage: {"status": "pending"} for stage in STAGES},
    }


def save_manifest(path: Path, manifest: dict) -> None:
    """Update the manifest timestamp and persist it as formatted JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = now()
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def stage_command(stage: str, config: dict, artifact_paths: dict[str, Path]) -> tuple[list[str], list[Path]]:
    """Build a subprocess command and expected outputs for one runnable stage.

    Keeping command construction separate makes stage behavior inspectable and
    testable without launching FFmpeg or Whisper.
    """
    python = sys.executable
    if stage == "extract":
        return [python, str(COMMANDS / "extract_audio.py"), str(artifact_paths["video"]), "-o", str(artifact_paths["audio"])], [artifact_paths["audio"]]
    if stage == "separate":
        separation = config.get("separation", {})
        command = [python, str(COMMANDS / "separate_audio.py"), str(artifact_paths["video"]), "-o", str(artifact_paths["separation_dir"]), "--model", separation.get("model", "htdemucs"), "--device", separation.get("device", config.get("compute", {}).get("device", "auto")), "--shifts", str(separation.get("shifts", 1))]
        return command, [artifact_paths["accompaniment"], artifact_paths["vocals"], artifact_paths["separation_report"]]
    if stage == "translate":
        settings = config.get("translation", {})
        quality = config.get("quality", {})
        command = [python, str(COMMANDS / "auto_prepare_script.py"), str(artifact_paths["audio"]), "--project-id", config["project_id"], "--model", settings.get("model", "small"), "--device", config.get("compute", {}).get("device", "auto"), "--maximum-duration", str(quality.get("maximum_segment_duration", 12.0)), "--maximum-characters", str(quality.get("maximum_subtitle_characters", 84)), "-o", str(artifact_paths["transcript_dir"])]
        if settings.get("fallback_model"):
            command += ["--fallback-model", settings["fallback_model"]]
        if settings.get("source_language"):
            command += ["--language", settings["source_language"]]
        command += ["--target-language", settings.get("target_language", "en")]
        if settings.get("translation_model"):
            command += ["--translation-model", settings["translation_model"]]
        if settings.get("source_model_language"):
            command += ["--source-model-language", settings["source_model_language"]]
        if settings.get("target_model_language"):
            command += ["--target-model-language", settings["target_model_language"]]
        command += [
            "--maximum-low-confidence-ratio",
            str(quality.get("maximum_low_confidence_ratio", 0.2)),
            "--maximum-rejection-ratio",
            str(quality.get("maximum_rejection_ratio", 0.05)),
        ]
        return command, [artifact_paths["transcript_json"], artifact_paths["srt"], artifact_paths["source_transcript"], artifact_paths["decisions"], artifact_paths["approved_script"]]
    if stage == "qa":
        quality = config.get("quality", {})
        command = [
            python, str(COMMANDS / "qa_transcript.py"), str(artifact_paths["transcript_json"]),
            "-o", str(artifact_paths["qa"]),
            "--source-transcript", str(artifact_paths["source_transcript"]),
            "--minimum-duration", str(quality.get("minimum_subtitle_duration", 0.5)),
            "--maximum-duration", str(quality.get("maximum_segment_duration", 12.0)),
            "--maximum-characters", str(quality.get("maximum_subtitle_characters", 84)),
            "--maximum-line-characters", str(quality.get("maximum_subtitle_line_characters", 42)),
            "--maximum-lines", str(quality.get("maximum_subtitle_lines", 2)),
            "--maximum-characters-per-second", str(quality.get("maximum_subtitle_characters_per_second", 20.0)),
            "--minimum-source-event-coverage", str(quality.get("minimum_source_event_coverage", 0.98)),
            "--minimum-source-time-coverage", str(quality.get("minimum_source_time_coverage", 0.95)),
        ]
        return command, [artifact_paths["qa"]]
    if stage == "tts":
        settings = config.get("translation", {})
        dubbing = config.get("dubbing", {})
        command = [python, str(COMMANDS / "generate_dub.py"), str(artifact_paths["diarized_script"]), "-o", str(artifact_paths["dub_dir"]), "--target-language", settings.get("target_language", "en"), "--rate", dubbing.get("rate", "+0%"), "--retries", str(dubbing.get("retries", 3))]
        if dubbing.get("voice"):
            command += ["--voice", dubbing["voice"]]
        return command, [artifact_paths["dub_manifest"]]
    if stage == "diarize":
        settings = config.get("translation", {})
        diarization = config.get("diarization", {})
        command = [python, str(COMMANDS / "diarize_speakers.py"), str(artifact_paths["approved_script"]), str(artifact_paths["audio"]), "--target-language", settings.get("target_language", "en"), "--maximum-speakers", str(diarization.get("maximum_speakers", 10)), "--embedding-model", diarization.get("embedding_model", "microsoft/wavlm-base-plus-sv"), "--device", config.get("compute", {}).get("device", "auto"), "--output-script", str(artifact_paths["diarized_script"]), "--output-report", str(artifact_paths["diarization_report"])]
        return command, [artifact_paths["diarized_script"], artifact_paths["diarization_report"]]
    if stage == "review":
        return [python, str(COMMANDS / "burn_subtitles.py"), str(artifact_paths["video"]), str(artifact_paths["srt"]), "-o", str(artifact_paths["review"])], [artifact_paths["review"]]
    if stage == "subtitle_mux":
        return [python, str(COMMANDS / "mux_subtitles.py"), str(artifact_paths["video"]), str(artifact_paths["srt"]), "-o", str(artifact_paths["subtitled"])], [artifact_paths["subtitled"]]
    if stage == "assemble":
        dubbing = config.get("dubbing", {})
        command = [python, str(COMMANDS / "assemble_dub.py"), str(artifact_paths["video"]), str(artifact_paths["dub_manifest"]), "-o", str(artifact_paths["dubbed"]), "--subtitles", str(artifact_paths["srt"]), "--background", str(artifact_paths["accompaniment"]), "--source-volume", str(dubbing.get("source_volume", 0.8)), "--dub-volume", str(dubbing.get("dub_volume", 1.0)), "--minimum-occupancy", str(dubbing.get("minimum_dialogue_occupancy", 0.65)), "--minimum-tempo", str(dubbing.get("minimum_tempo", 0.75))]
        return command, [artifact_paths["dubbed"], artifact_paths["assembly_report"]]
    if stage == "final_qa":
        quality = config.get("quality", {})
        command = [python, str(COMMANDS / "qa_final.py"), str(artifact_paths["dubbed"]), str(artifact_paths["dub_manifest"]), str(artifact_paths["diarized_script"]), str(artifact_paths["assembly_report"]), str(artifact_paths["vocals"]), str(artifact_paths["accompaniment"]), "-o", str(artifact_paths["final_qa"]), "--maximum-tempo", str(quality.get("maximum_tempo_factor", 3.0)), "--maximum-leakage", str(quality.get("maximum_vocal_leakage", 0.35))]
        return command, [artifact_paths["final_qa"]]
    raise ValueError(f"Unknown stage: {stage}")


def run(config_path: Path, through: str, force: bool) -> None:
    """Run ordered stages through ``through`` and record their manifest state.

    Completed stages with all expected artifacts are skipped unless ``force`` is
    true. A failed subprocess is recorded before the exception is re-raised.
    """
    config = load_config(config_path)
    artifact_paths = paths(config)
    if not artifact_paths["video"].is_file():
        raise FileNotFoundError(f"Input video not found: {artifact_paths['video']}")
    manifest = load_manifest(config, artifact_paths)

    for stage in RUNNABLE_STAGES[: RUNNABLE_STAGES.index(through) + 1]:
        command, outputs = stage_command(stage, config, artifact_paths)
        state = manifest["stages"][stage]
        if not force and state.get("status") == "completed" and all(path.exists() for path in outputs):
            print(f"Skipping completed stage: {stage}")
            continue
        print(f"Running stage: {stage}")
        state.update({"status": "running", "started_at": now(), "command": command})
        save_manifest(artifact_paths["manifest"], manifest)
        try:
            subprocess.run(command, cwd=HERE, check=True)
        except BaseException as error:
            state.update({"status": "failed", "finished_at": now(), "error": str(error)})
            save_manifest(artifact_paths["manifest"], manifest)
            raise
        state.update(
            {
                "status": "completed",
                "finished_at": now(),
                "artifacts": [str(path) for path in outputs],
            }
        )
        state.pop("error", None)
        save_manifest(artifact_paths["manifest"], manifest)

    print(f"Pipeline complete through: {through}")
    print(f"Manifest: {artifact_paths['manifest']}")


def show_status(config_path: Path) -> None:
    """Print the recorded state of runnable and planned stages."""
    config = load_config(config_path)
    artifact_paths = paths(config)
    manifest = load_manifest(config, artifact_paths)
    print(f"Project: {manifest['project_id']}")
    for stage in STAGES:
        print(f"  {stage:14} {manifest['stages'][stage]['status']}")


def main() -> None:
    """Dispatch pipeline, subtitle, or project-history commands."""
    if len(sys.argv) > 1 and sys.argv[1] == "history":
        from .commands.project_history import main as history_main

        history_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "subtitles":
        from .commands.create_subtitles import main as subtitle_main

        subtitle_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "reprocess-subtitles":
        from .commands.reprocess_subtitles import main as reprocess_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        reprocess_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "qualify-speech-translation":
        from .commands.qualify_speech_translation import main as qualify_main

        qualify_main(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description="Structured video translation pipeline")
    parser.add_argument("config", type=Path, help="Pipeline configuration JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run stages in order")
    run_parser.add_argument("--through", choices=RUNNABLE_STAGES, default="review")
    run_parser.add_argument("--force", action="store_true")
    subparsers.add_parser("status", help="Show recorded stage status")
    args = parser.parse_args()
    if args.command == "run":
        run(args.config, args.through, args.force)
    else:
        show_status(args.config)


if __name__ == "__main__":
    main()
