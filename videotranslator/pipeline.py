"""Run and track the video-to-English pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
RUNNABLE_STAGES = (
    "extract",
    "translate",
    "qa",
    "review",
    "subtitle_mux",
)
STAGES = RUNNABLE_STAGES + ("tts", "align", "mix", "export")


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
    return {
        "video": video,
        "root": root,
        "manifest": root / "manifest.json",
        "audio": root / "audio" / f"{stem}.wav",
        "transcript_dir": root / "transcripts",
        "transcript_json": root / "transcripts" / f"{stem}.auto.en.json",
        "srt": root / "transcripts" / f"{stem}.auto.en.srt",
        "source_transcript": root / "transcripts" / f"{stem}.source.json",
        "decisions": root / "transcripts" / f"{stem}.decisions.json",
        "approved_script": root / "transcripts" / f"{stem}.approved.json",
        "qa": root / "qa" / f"{stem}.qa.json",
        "review": root / "review" / f"{stem}.top-subs.mp4",
        "subtitled": root / "final" / f"{stem}.english-subs.mp4",
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
        return [python, str(HERE / "extract_audio.py"), str(artifact_paths["video"]), "-o", str(artifact_paths["audio"])], [artifact_paths["audio"]]
    if stage == "translate":
        settings = config.get("translation", {})
        quality = config.get("quality", {})
        command = [python, str(HERE / "auto_prepare_script.py"), str(artifact_paths["audio"]), "--project-id", config["project_id"], "--model", settings.get("model", "small"), "--maximum-duration", str(quality.get("maximum_segment_duration", 12.0)), "--maximum-characters", str(quality.get("maximum_subtitle_characters", 84)), "-o", str(artifact_paths["transcript_dir"])]
        if settings.get("fallback_model"):
            command += ["--fallback-model", settings["fallback_model"]]
        if settings.get("source_language"):
            command += ["--language", settings["source_language"]]
        command += [
            "--maximum-low-confidence-ratio",
            str(quality.get("maximum_low_confidence_ratio", 0.2)),
            "--maximum-rejection-ratio",
            str(quality.get("maximum_rejection_ratio", 0.05)),
        ]
        return command, [artifact_paths["transcript_json"], artifact_paths["srt"], artifact_paths["source_transcript"], artifact_paths["decisions"], artifact_paths["approved_script"]]
    if stage == "qa":
        maximum = str(config.get("quality", {}).get("maximum_segment_duration", 12.0))
        return [python, str(HERE / "qa_transcript.py"), str(artifact_paths["transcript_json"]), "-o", str(artifact_paths["qa"]), "--maximum-duration", maximum], [artifact_paths["qa"]]
    if stage == "review":
        return [python, str(HERE / "burn_subtitles.py"), str(artifact_paths["video"]), str(artifact_paths["srt"]), "-o", str(artifact_paths["review"])], [artifact_paths["review"]]
    if stage == "subtitle_mux":
        return [python, str(HERE / "mux_subtitles.py"), str(artifact_paths["video"]), str(artifact_paths["srt"]), "-o", str(artifact_paths["subtitled"])], [artifact_paths["subtitled"]]
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
    """Dispatch the pipeline ``run`` or ``status`` command."""
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
