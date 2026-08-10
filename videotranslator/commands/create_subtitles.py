"""Create translated subtitles through an automatic retry-and-QA workflow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
RECOVERY_PROFILES = (
    {"name": "conservative", "minimum_duration": 0.25, "merge_gap": 0.0, "padding": 0.35, "minimum_log_probability": -1.0, "maximum_no_speech": 0.65},
    {"name": "balanced", "minimum_duration": 0.12, "merge_gap": 0.2, "padding": 0.5, "minimum_log_probability": -1.5, "maximum_no_speech": 0.85},
    {"name": "maximum_coverage", "minimum_duration": 0.10, "merge_gap": 0.35, "padding": 0.65, "minimum_log_probability": -2.0, "maximum_no_speech": 0.95},
)


def artifact_paths(video: Path, output: Path, target_language: str) -> dict[str, Path]:
    """Return deterministic paths used by an unattended subtitle run."""
    stem = video.stem
    return {
        "audio": output / "audio" / f"{stem}.wav",
        "transcription_dir": output / "transcription",
        "source": output / "transcription" / f"{stem}.json",
        "aligned": output / "alignment" / "aligned.json",
        "reconciled": output / "alignment" / "reconciled.json",
        "alignment_report": output / "alignment" / "report.json",
        "diarized": output / "diarization" / "assigned.json",
        "diarization_report": output / "diarization" / "report.json",
        "attempts": output / "attempts",
        "final": output / "final.srt",
        "rejected": output / "rejected.srt",
        "report": output / "subtitle-pipeline-report.json",
        "target_json": output / f"final.{target_language}.json",
    }


def quality_score(report: dict) -> float:
    """Rank failed attempts primarily by independent speech-time coverage."""
    diarized = report.get("diarized_coverage") or {}
    source = report.get("source_coverage") or {}
    return (
        float(diarized.get("time_coverage", 0.0)) * 4
        + float(diarized.get("turn_coverage", 0.0)) * 2
        + float(source.get("event_coverage", source.get("source_event_coverage", 0.0)))
        - len(report.get("issues", [])) * 0.001
    )


def run_command(command: list[str], expected: list[Path], *, force: bool, env: dict[str, str]) -> None:
    """Run one stage unless all expected artifacts already exist."""
    if not force and expected and all(path.is_file() for path in expected):
        print(f"Skipping existing stage: {expected[0].parent.name}")
        return
    print("Running:", " ".join(command))
    subprocess.run(command, check=True, env=env)
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise RuntimeError("Stage did not create expected artifacts: " + ", ".join(missing))


def create_subtitles(args: argparse.Namespace) -> dict:
    """Run transcription, alignment, diarization, recovery, translation, and QA."""
    video = args.video.resolve()
    if not video.is_file():
        raise FileNotFoundError(f"Input video not found: {video}")
    output = (args.output or Path("videotranslator/outputs") / video.stem).resolve()
    paths = artifact_paths(video, output, args.target_language)
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if args.offline:
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    python = sys.executable

    run_command([python, str(HERE / "extract_audio.py"), str(video), "-o", str(paths["audio"])], [paths["audio"]], force=args.force, env=env)
    transcribe = [python, str(HERE / "transcribe.py"), str(paths["audio"]), "--model", args.whisper_model, "--device", args.device, "-o", str(paths["transcription_dir"]), "--vad-threshold", "0.35", "--minimum-speech-ms", "100", "--minimum-silence-ms", "300", "--speech-padding-ms", "350", "--no-speech-threshold", "0.8"]
    if args.source_language:
        transcribe += ["--language", args.source_language]
    run_command(transcribe, [paths["source"]], force=args.force, env=env)
    run_command([python, str(HERE / "force_align.py"), str(paths["source"]), str(paths["source"]), str(paths["audio"]), "--device", args.device, "--output-transcript", str(paths["aligned"]), "--output-reconciled", str(paths["reconciled"]), "--output-report", str(paths["alignment_report"])], [paths["aligned"], paths["reconciled"], paths["alignment_report"]], force=args.force, env=env)
    diarize = [python, str(HERE / "diarize_pyannote.py"), str(paths["reconciled"]), str(paths["audio"]), "--device", args.device, "--output-script", str(paths["diarized"]), "--output-report", str(paths["diarization_report"])]
    if args.maximum_speakers:
        diarize += ["--maximum-speakers", str(args.maximum_speakers)]
    run_command(diarize, [paths["diarized"], paths["diarization_report"]], force=args.force, env=env)

    attempts = []
    for index, profile in enumerate(RECOVERY_PROFILES[: args.maximum_attempts], start=1):
        attempt = paths["attempts"] / f"{index:02}-{profile['name']}"
        recovered, recovery_report = attempt / "source.complete.json", attempt / "recovery.json"
        translated, aligned_srt = attempt / "translated.json", attempt / "translated.srt"
        repaired, repaired_srt, qa_path = attempt / "subtitles.json", attempt / "subtitles.srt", attempt / "qa.json"
        recovery = [python, str(HERE / "recover_missing_speech.py"), str(paths["reconciled"]), str(paths["diarization_report"]), str(paths["audio"]), "--strong-transcript", str(paths["source"]), "--model", args.whisper_model, "--device", args.device, "--minimum-duration", str(profile["minimum_duration"]), "--merge-gap", str(profile["merge_gap"]), "--padding", str(profile["padding"]), "--minimum-log-probability", str(profile["minimum_log_probability"]), "--maximum-no-speech", str(profile["maximum_no_speech"]), "--output-transcript", str(recovered), "--output-report", str(recovery_report)]
        source_language = json.loads(paths["source"].read_text(encoding="utf-8")).get("language")
        if source_language:
            recovery += ["--language", source_language]
        run_command(recovery, [recovered, recovery_report], force=args.force, env=env)
        run_command([python, str(HERE / "translate_subtitles.py"), str(recovered), "--target-language", args.target_language, "--device", args.device, "--output-json", str(translated), "--output-srt", str(aligned_srt)], [translated, aligned_srt], force=args.force, env=env)
        run_command([python, str(HERE / "repair_subtitles.py"), str(translated), "--output-json", str(repaired), "--output-srt", str(repaired_srt), "--minimum-duration", "0.52", "--maximum-characters", "64", "--maximum-characters-per-second", "19"], [repaired, repaired_srt], force=args.force, env=env)
        qa = [python, str(HERE / "qa_transcript.py"), str(repaired), "-o", str(qa_path), "--source-transcript", str(paths["source"]), "--diarization-report", str(paths["diarization_report"]), "--minimum-diarized-turn-coverage", str(args.minimum_diarized_turn_coverage), "--minimum-diarized-time-coverage", str(args.minimum_diarized_time_coverage)]
        completed = subprocess.run(qa, check=False, env=env)
        report = json.loads(qa_path.read_text(encoding="utf-8"))
        attempts.append({"number": index, "profile": profile["name"], "passed": report["passed"], "score": quality_score(report), "srt": str(repaired_srt), "qa": str(qa_path), "qa_report": report})
        if completed.returncode == 0 and report["passed"]:
            break

    best = max(attempts, key=lambda item: item["score"])
    passed = next((item for item in attempts if item["passed"]), None)
    selected = passed or best
    destination = paths["final"] if passed else paths["rejected"]
    shutil.copy2(selected["srt"], destination)
    shutil.copy2(Path(selected["srt"]).with_name("subtitles.json"), paths["target_json"])
    other = paths["rejected"] if passed else paths["final"]
    if other.exists():
        other.unlink()
    result = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "status": "passed" if passed else "rejected", "input_video": str(video), "output_srt": str(destination), "selected_attempt": selected["number"], "attempts": attempts}
    paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Subtitle pipeline {result['status']}: {destination}")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the standalone or package-level subtitle command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--source-language")
    parser.add_argument("--whisper-model", default="large-v3")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--maximum-speakers", type=int)
    parser.add_argument("--maximum-attempts", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--minimum-diarized-turn-coverage", type=float, default=0.90)
    parser.add_argument("--minimum-diarized-time-coverage", type=float, default=0.90)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Execute the automatic subtitle workflow and fail if QA rejects it."""
    result = create_subtitles(parse_args(argv))
    if result["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
