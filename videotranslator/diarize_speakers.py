"""Assign stable local speaker clusters and distinct Piper voices to dialogue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import librosa
import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.mixture import GaussianMixture
import torch
from transformers import Wav2Vec2FeatureExtractor, WavLMForXVector

from generate_dub import available_voices


SAMPLE_RATE = 16_000
FEMALE_VOICE_MARKERS = ("amy", "alba", "cori", "female", "jenny", "kathleen", "kristin", "ljspeech")
MALE_VOICE_MARKERS = ("alan", "lessac", "male", "ryan", "joe", "danny")


def segment_audio(wav: np.ndarray, start: float, end: float) -> np.ndarray:
    """Slice a timed utterance and pad very short cues for stable embeddings."""
    first = max(0, round(start * SAMPLE_RATE))
    last = min(len(wav), round(end * SAMPLE_RATE))
    utterance = wav[first:last]
    minimum = SAMPLE_RATE // 2
    if len(utterance) < minimum:
        utterance = np.pad(utterance, (0, minimum - len(utterance)))
    return utterance.astype(np.float32, copy=False)


def choose_clusters(embeddings: np.ndarray, maximum_speakers: int) -> tuple[np.ndarray, dict]:
    """Combine silhouette and Gaussian BIC estimates into conservative labels."""
    count = len(embeddings)
    if count < 3:
        return np.zeros(count, dtype=int), {
            "selected_speakers": 1, "silhouette_speakers": 1,
            "bic_speakers": 1, "silhouette_score": 0.0,
        }
    upper = min(maximum_speakers, max(2, count // 5), count - 1)
    best_labels = np.zeros(count, dtype=int)
    best_silhouette = -1.0
    best_objective = -1.0
    silhouette_speakers = 2
    for speakers in range(2, upper + 1):
        labels = AgglomerativeClustering(
            n_clusters=speakers, metric="cosine", linkage="average"
        ).fit_predict(embeddings)
        score = float(silhouette_score(embeddings, labels, metric="cosine"))
        objective = score - (0.01 * speakers)
        if objective > best_objective:
            best_labels, best_silhouette, best_objective = labels, score, objective
            silhouette_speakers = speakers
    reduced = PCA(
        n_components=min(20, count - 1, embeddings.shape[1]),
        whiten=True, random_state=0,
    ).fit_transform(embeddings)
    bics = {}
    for speakers in range(2, upper + 1):
        model = GaussianMixture(
            speakers, covariance_type="diag", random_state=0, n_init=5
        ).fit(reduced)
        bics[speakers] = float(model.bic(reduced))
    bic_speakers = min(bics, key=bics.get)
    selected_speakers = max(silhouette_speakers, bic_speakers)
    if selected_speakers != silhouette_speakers:
        best_labels = AgglomerativeClustering(
            n_clusters=selected_speakers, metric="cosine", linkage="average"
        ).fit_predict(embeddings)
        best_silhouette = float(
            silhouette_score(embeddings, best_labels, metric="cosine")
        )
    return best_labels, {
        "selected_speakers": selected_speakers,
        "silhouette_speakers": silhouette_speakers,
        "bic_speakers": bic_speakers,
        "silhouette_score": round(best_silhouette, 4),
    }


def speaker_embeddings(
    utterances: list[np.ndarray], model_name: str, batch_size: int = 8
) -> np.ndarray:
    """Create normalized local WavLM speaker x-vectors in small CPU batches."""
    extractor = Wav2Vec2FeatureExtractor.from_pretrained(model_name)
    model = WavLMForXVector.from_pretrained(model_name)
    model.eval()
    results = []
    with torch.inference_mode():
        for start in range(0, len(utterances), batch_size):
            inputs = extractor(
                utterances[start : start + batch_size], sampling_rate=SAMPLE_RATE,
                padding=True, return_tensors="pt",
            )
            embedding = torch.nn.functional.normalize(model(**inputs).embeddings, dim=-1)
            results.append(embedding.cpu().numpy())
    return np.concatenate(results)


def estimate_voice_style(utterances: list[np.ndarray]) -> tuple[str, float | None]:
    """Estimate a broad high/low pitch style without asserting speaker gender."""
    combined = np.concatenate(utterances)
    if len(combined) > SAMPLE_RATE * 60:
        combined = combined[: SAMPLE_RATE * 60]
    pitches = librosa.yin(combined, fmin=65, fmax=350, sr=SAMPLE_RATE)
    pitches = pitches[np.isfinite(pitches)]
    if not len(pitches):
        return "neutral", None
    median = float(np.median(pitches))
    if median >= 175:
        return "high", round(median, 2)
    if median <= 150:
        return "low", round(median, 2)
    return "neutral", round(median, 2)


def split_clusters_by_pitch(labels: np.ndarray, utterances: list[np.ndarray]) -> tuple[np.ndarray, list[str]]:
    """Prevent acoustically distinct high/low voices from sharing one cluster.

    Neutral short cues retain their embedding cluster. High and low pitch groups
    are separated only when both occur inside the same acoustic cluster, making
    the rule independent of source language and character names.
    """
    styles = [estimate_voice_style([utterance])[0] for utterance in utterances]
    keys: list[tuple[int, str]] = []
    for cluster in sorted(set(int(label) for label in labels)):
        cluster_styles = {styles[index] for index, label in enumerate(labels) if label == cluster}
        split = "high" in cluster_styles and "low" in cluster_styles
        for index, label in enumerate(labels):
            if label == cluster:
                keys.append((index, styles[index] if split and styles[index] != "neutral" else f"cluster-{cluster}"))
    relabeled = np.empty(len(labels), dtype=int)
    identities: dict[tuple[int, str], int] = {}
    for index, style_key in sorted(keys):
        identity = (int(labels[index]), style_key)
        identities.setdefault(identity, len(identities))
        relabeled[index] = identities[identity]
    return relabeled, styles


def voice_style(voice_name: str) -> str:
    """Classify known Piper voice-name markers as high, low, or neutral style."""
    lowered = voice_name.lower()
    if any(marker in lowered for marker in FEMALE_VOICE_MARKERS):
        return "high"
    if any(marker in lowered for marker in MALE_VOICE_MARKERS):
        return "low"
    return "neutral"


def assign_voices(styles: list[str], voices: list[str]) -> list[str]:
    """Assign stable distinct voices while preferring matching pitch styles."""
    if not voices:
        raise ValueError("No target-language Piper voices are available")
    assignments = []
    used: set[str] = set()
    for style in styles:
        preferred = [voice for voice in voices if voice_style(voice) == style and voice not in used]
        unused = [voice for voice in voices if voice not in used]
        voice = (preferred or unused or voices)[0]
        assignments.append(voice)
        used.add(voice)
    return assignments


def diarize(
    approved_script: dict,
    audio_path: Path,
    target_language: str,
    maximum_speakers: int,
    embedding_model: str,
) -> tuple[dict, dict]:
    """Cluster approved cues, assign voices, and return script plus audit report."""
    if approved_script.get("approval", {}).get("status") != "approved":
        raise ValueError("Diarization input must be an automatically approved script")
    wav, _sample_rate = librosa.load(audio_path, sr=SAMPLE_RATE, mono=True)
    utterances = [
        segment_audio(wav, segment["start"], segment["end"])
        for segment in approved_script["segments"]
    ]
    embeddings = speaker_embeddings(utterances, embedding_model)
    labels, selection = choose_clusters(embeddings, maximum_speakers)
    labels, segment_styles = split_clusters_by_pitch(labels, utterances)
    unique_labels = sorted(set(int(label) for label in labels))
    selection["pitch_aware_speakers"] = len(unique_labels)
    styles_and_pitch = [
        estimate_voice_style([utterances[i] for i, label in enumerate(labels) if label == cluster])
        for cluster in unique_labels
    ]
    voices = assign_voices(
        [style for style, _pitch in styles_and_pitch], available_voices(target_language)
    )
    cluster_details = {}
    for position, cluster in enumerate(unique_labels):
        cluster_details[cluster] = {
            "speaker": f"speaker-{position + 1:02d}",
            "voice": voices[position],
            "voice_style": styles_and_pitch[position][0],
            "median_pitch_hz": styles_and_pitch[position][1],
            "segment_count": int(sum(labels == cluster)),
        }
    updated = json.loads(json.dumps(approved_script))
    for index, (segment, label) in enumerate(zip(updated["segments"], labels)):
        detail = cluster_details[int(label)]
        segment["speaker"] = detail["speaker"]
        segment["voice"] = detail["voice"]
        segment["voice_style"] = segment_styles[index]
    report = {
        "schema_version": 1,
        "method": "wavlm-xvector-agglomerative-cosine",
        "embedding_model": embedding_model,
        "speaker_count": len(unique_labels),
        "selection": selection,
        "speakers": list(cluster_details.values()),
    }
    return updated, report


def main() -> None:
    """Run local diarization and persist the assigned script and report."""
    parser = argparse.ArgumentParser(description="Assign speakers and local TTS voices.")
    parser.add_argument("approved_script", type=Path)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--maximum-speakers", type=int, default=10)
    parser.add_argument("--embedding-model", default="microsoft/wavlm-base-plus-sv")
    parser.add_argument("--output-script", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    approved = json.loads(args.approved_script.read_text(encoding="utf-8"))
    assigned, report = diarize(
        approved, args.audio, args.target_language, args.maximum_speakers,
        args.embedding_model,
    )
    args.output_script.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_script.write_text(json.dumps(assigned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Assigned {report['speaker_count']} speakers "
        f"(silhouette={report['selection']['silhouette_score']})"
    )
    print(f"Diarized script: {args.output_script.resolve()}")


if __name__ == "__main__":
    main()
