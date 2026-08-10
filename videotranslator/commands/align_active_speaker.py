"""Align dubbed cue onsets to visible mouth motion in multi-face scenes."""

from __future__ import annotations


import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


def intersection_over_union(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> float:
    """Return intersection-over-union for two ``x, y, width, height`` boxes."""
    ax1, ay1, aw, ah = first
    bx1, by1, bw, bh = second
    ax2, ay2, bx2, by2 = ax1 + aw, ay1 + ah, bx1 + bw, by1 + bh
    overlap = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0, min(ay2, by2) - max(ay1, by1)
    )
    union = aw * ah + bw * bh - overlap
    return overlap / union if union else 0.0


def bounded_onset_offset(
    cue_start: float, visual_onset: float | None, maximum_offset: float
) -> float:
    """Return a signed, bounded correction from audio onset to visual onset."""
    if visual_onset is None:
        return 0.0
    return round(max(-maximum_offset, min(maximum_offset, visual_onset - cue_start)), 3)


def timeline_safe_offset(
    requested: float,
    start: float,
    duration: float,
    previous_audio_end: float | None,
    next_start: float | None,
) -> tuple[float, bool]:
    """Clamp an onset shift so synthesized speech cannot overlap its neighbors."""
    lower = previous_audio_end - start if previous_audio_end is not None else float("-inf")
    upper = next_start - start - duration if next_start is not None else float("inf")
    if lower > upper:
        return 0.0, True
    safe = max(lower, min(upper, requested))
    return round(safe, 3), abs(safe - requested) > 0.0005


def dominant_track(scores: dict[int, float], dominance_ratio: float) -> tuple[int | None, float]:
    """Select a track only when its motion clearly dominates the runner-up."""
    positive = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not positive or positive[0][1] <= 0:
        return None, 0.0
    runner_up = positive[1][1] if len(positive) > 1 else 0.0
    ratio = positive[0][1] / max(runner_up, 1e-6)
    return (positive[0][0], ratio) if ratio >= dominance_ratio else (None, ratio)


def mouth_patch(gray: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray | None:
    """Extract a normalized lower-face patch used only for motion measurement."""
    x, y, width, height = box
    top = y + round(height * 0.55)
    bottom = min(gray.shape[0], y + height)
    left, right = max(0, x), min(gray.shape[1], x + width)
    if bottom <= top or right <= left:
        return None
    return cv2.resize(gray[top:bottom, left:right], (48, 24))


@dataclass
class FaceTrack:
    """Hold cue-local face geometry and consecutive lower-face motion samples."""

    identifier: int
    box: tuple[int, int, int, int]
    last_patch: np.ndarray | None = None
    samples: list[tuple[float, float]] = field(default_factory=list)
    detections: int = 0


def assign_detections(
    tracks: list[FaceTrack], detections: list[tuple[int, int, int, int]], minimum_iou: float = 0.2
) -> list[tuple[FaceTrack, tuple[int, int, int, int]]]:
    """Greedily associate current face detections with cue-local tracks."""
    assignments = []
    unused = set(range(len(detections)))
    for track in tracks:
        candidates = [(intersection_over_union(track.box, detections[index]), index) for index in unused]
        if not candidates:
            continue
        score, index = max(candidates)
        if score >= minimum_iou:
            assignments.append((track, detections[index]))
            unused.remove(index)
    for index in sorted(unused):
        track = FaceTrack(len(tracks), detections[index])
        tracks.append(track)
        assignments.append((track, detections[index]))
    return assignments


def analyze_cue(
    capture: cv2.VideoCapture,
    detector: cv2.CascadeClassifier,
    start: float,
    end: float,
    sample_rate: float,
    dominance_ratio: float,
    motion_threshold: float,
) -> dict:
    """Track faces around one cue and locate the dominant mouth-motion onset."""
    analysis_start = max(0.0, start - 0.3)
    analysis_end = end + 0.3
    tracks: list[FaceTrack] = []
    timestamp = analysis_start
    frame_count = 0
    while timestamp <= analysis_end:
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ok, frame = capture.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        scale = min(1.0, 640 / gray.shape[1])
        detection_frame = cv2.resize(gray, None, fx=scale, fy=scale) if scale < 1 else gray
        found = detector.detectMultiScale(
            detection_frame, scaleFactor=1.12, minNeighbors=5, minSize=(32, 32)
        )
        detections = [
            tuple(round(value / scale) for value in box) for box in found
        ]
        for track, box in assign_detections(tracks, detections):
            patch = mouth_patch(gray, box)
            motion = 0.0
            if patch is not None and track.last_patch is not None:
                motion = float(cv2.absdiff(patch, track.last_patch).mean() / 255.0)
            track.samples.append((timestamp, motion))
            track.last_patch = patch
            track.box = box
            track.detections += 1
        frame_count += 1
        timestamp += 1 / sample_rate
    eligible = [track for track in tracks if track.detections >= 3]
    scores = {}
    for track in eligible:
        cue_motion = [
            motion for time, motion in track.samples if start <= time <= end
        ]
        scores[track.identifier] = float(np.mean(cue_motion)) if cue_motion else 0.0
    selected_id, ratio = dominant_track(scores, dominance_ratio)
    selected = next((track for track in eligible if track.identifier == selected_id), None)
    onset = None
    if selected is not None:
        active = [(time, motion) for time, motion in selected.samples if start - 0.25 <= time <= end]
        onset = next((time for time, motion in active if motion >= motion_threshold), None)
    return {
        "visible_face_count": len(eligible),
        "frame_count": frame_count,
        "active_face_track": selected_id,
        "active_face_box": list(selected.box) if selected else None,
        "dominance_ratio": round(ratio, 3),
        "motion_scores": {str(key): round(value, 5) for key, value in scores.items()},
        "visual_onset": round(onset, 3) if onset is not None else None,
    }


def align_active_speakers(
    video: Path,
    script: dict,
    manifest: dict,
    sample_rate: float,
    dominance_ratio: float,
    motion_threshold: float,
    maximum_offset: float,
) -> tuple[dict, dict]:
    """Analyze all cues, apply confident bounded onset shifts, and return QA data."""
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"Unable to open video: {video}")
    detector = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    if detector.empty():
        raise RuntimeError("OpenCV frontal-face detector is unavailable")
    clips = [dict(clip) for clip in manifest["clips"]]
    if len(clips) != len(script["segments"]):
        raise ValueError("Script and dub manifest must contain the same number of cues")
    decisions = []
    try:
        previous_audio_end = None
        for index, (segment, clip) in enumerate(zip(script["segments"], clips)):
            visual = analyze_cue(
                capture,
                detector,
                float(segment["start"]),
                float(segment["end"]),
                sample_rate,
                dominance_ratio,
                motion_threshold,
            )
            multi_face = visual["visible_face_count"] >= 2
            confident = multi_face and visual["active_face_track"] is not None and visual["visual_onset"] is not None
            requested_offset = bounded_onset_offset(
                float(clip["start"]), visual["visual_onset"], maximum_offset
            ) if confident else 0.0
            next_start = (
                float(clips[index + 1]["start"])
                if index + 1 < len(clips)
                else None
            )
            if confident:
                offset, safety_clamped = timeline_safe_offset(
                    requested_offset,
                    float(clip["start"]),
                    float(clip["generated_duration"]),
                    previous_audio_end,
                    next_start,
                )
            else:
                offset, safety_clamped = 0.0, False
            clip["start"] = round(float(clip["start"]) + offset, 3)
            clip["end"] = round(float(clip["end"]) + offset, 3)
            previous_audio_end = clip["start"] + float(clip["generated_duration"])
            decisions.append(
                {
                    "segment_id": clip["segment_id"],
                    "speaker": segment.get("speaker"),
                    **visual,
                    "status": "aligned" if confident else ("ambiguous" if multi_face else "not-multi-face"),
                    "requested_onset_offset": requested_offset,
                    "onset_offset": offset,
                    "timeline_safety_clamped": safety_clamped,
                }
            )
    finally:
        capture.release()
    aligned = {**manifest, "provider": f"{manifest['provider']}+active-speaker", "clips": clips}
    multi = [decision for decision in decisions if decision["visible_face_count"] >= 2]
    ambiguous = [decision for decision in multi if decision["status"] == "ambiguous"]
    report = {
        "schema_version": 1,
        "automatic": True,
        "status": "passed",
        "segment_count": len(decisions),
        "multi_face_segment_count": len(multi),
        "aligned_multi_face_segment_count": len(multi) - len(ambiguous),
        "ambiguous_multi_face_segment_count": len(ambiguous),
        "maximum_onset_offset": maximum_offset,
        "decisions": decisions,
    }
    return aligned, report


def main() -> None:
    """Run active-speaker analysis and write aligned manifest plus audit report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("script", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--sample-rate", type=float, default=8.0)
    parser.add_argument("--dominance-ratio", type=float, default=1.35)
    parser.add_argument("--motion-threshold", type=float, default=0.018)
    parser.add_argument("--maximum-offset", type=float, default=0.25)
    args = parser.parse_args()
    script = json.loads(args.script.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    aligned, report = align_active_speakers(
        args.video,
        script,
        manifest,
        args.sample_rate,
        args.dominance_ratio,
        args.motion_threshold,
        args.maximum_offset,
    )
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(json.dumps(aligned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Active-speaker alignment: {report['aligned_multi_face_segment_count']}/"
        f"{report['multi_face_segment_count']} multi-face cues aligned; "
        f"{report['ambiguous_multi_face_segment_count']} ambiguous"
    )


if __name__ == "__main__":
    main()
