# Video Translator Code Reference

This document describes the implementation of the `videotranslator` module. See
`README.md` for installation, pipeline stages, and operator instructions.

## Architecture

The module is a collection of small command-line programs coordinated by
`pipeline.py`. Each stage communicates through files rather than in-memory state:

```text
source video
  -> extracted WAV
  -> separated vocals and accompaniment
  -> separate source transcript and English translation
  -> candidate scoring, fallback retry, and automatic approval
  -> QA report
  -> review video / selectable-subtitle video
  -> approved script
  -> local target-language TTS clips and dub manifest
  -> aligned dialogue, ducked source mix, subtitles, and final MP4
  -> automatic final quality gate and report
```

`manifest.json` records commands, timestamps, status, and output paths. This makes
an interrupted run resumable and provides provenance for every generated artifact.

## `pipeline.py`

The main orchestrator. It does not contain media-processing logic; it builds and
runs commands for the specialized scripts.

## `transcribe.py`

The standalone transcription tool accepts configurable VAD threshold, minimum
speech/silence duration, speech padding, and Whisper no-speech threshold. JSON
output includes word timestamps so a later forced-alignment stage can reconcile
newly recovered speech with the canonical transcript. Source language remains
auto-detected unless explicitly configured.

## `force_align.py`

- `align_one(...)` runs CTC forced alignment inside a padded rough Whisper window.
- `split_aligned_words(...)` rebuilds readable cues from acoustic word boundaries.
- `reconciliation_candidates(...)` identifies old cues with no aligned-word evidence.
- `build_reconciled_transcript(...)` combines aligned speech and uncovered reference
  cues while recording provenance and preventing silent dialogue loss.
- `align_transcript(...)` loads the language-specific model once and returns aligned,
  reconciled, and audit-report artifacts.

The bundled automatic mapping currently covers Korean. Other input languages use
the same implementation by supplying a compatible Hugging Face Wav2Vec2 CTC model;
the stage fails clearly when no language model is configured.

## `diarize_pyannote.py`

- `assign_turns(...)` maps exclusive diarization turns to transcript cues by
  maximum temporal overlap, with auditable nearest-turn fallback only when needed.
- `diarize(...)` runs the official local `community-1` pipeline on an in-memory
  waveform, assigns stable IDs by first appearance, and records all source turns.
- `main()` requires `HF_TOKEN` from the environment and never persists credentials.

This stage establishes persistent speaker identity only. Voice-characteristic
matching belongs to the following stage and must not use pitch as a gender claim.
`requirements-diarization.txt` isolates its heavier optional dependency set from
the base installation.

## `match_speaker_voices.py`

- `acoustic_profile(...)` measures five characteristics without interpreting them
  as gender.
- `collect_speaker_audio(...)` aggregates bounded pyannote turns per persistent ID.
- `synthesize_probe(...)` creates reusable neutral Piper comparison samples.
- `match_profiles(...)` uses weighted standardized distance and Hungarian assignment
  to select globally optimal unique voices.
- `match_voices(...)` writes persistent voice assignments and a complete feature,
  weight, distance, and provenance report.

## `translate_constrained.py`

- `available_windows(...)` adds only bounded trailing silence to each cue window.
- `deduplicate_adjacent_cues(...)` merges adjacent same-speaker alignment fragments
  using language-independent Unicode text containment.
- `voice_rates(...)` derives target speech rates from cached assigned-voice probes.
- `character_budget(...)` converts a speaking window and voice rate into a target
  text budget.
- `generate_translation(...)` runs repetition-controlled NLLB generation.
- `translate_constrained(...)` retries lines over budget and produces a blocking
  automatic fit report while preserving speaker and voice metadata.

```powershell
python translate_constrained.py outputs/voices.assigned.json `
  --target-language en --probe-dir outputs/probes `
  --output-script outputs/english.constrained.json `
  --output-report outputs/translation-report.json
```

The process exits with an error if any generated line remains over its permitted
ratio, so unsuitable translations cannot silently proceed to TTS.

- `now()` returns UTC timestamps used in manifests.
- `load_config(path)` loads JSON, checks required settings, and resolves paths from
  the configuration file's directory.
- `paths(config)` derives canonical paths for every artifact.
- `load_manifest(config, artifact_paths)` resumes an existing project or creates
  pending state for implemented and planned stages.
- `save_manifest(path, manifest)` persists updated state.
- `stage_command(stage, config, artifact_paths)` maps a stage to its command and
  expected files without executing it.
- `run(config_path, through, force)` executes stages in order and skips completed
  work unless forced.
- `show_status(config_path)` prints the current stage table.
- `main()` implements the `run` and `status` CLI.

Typical use:

```powershell
python pipeline.py pipeline.example.json run --through review
python pipeline.py pipeline.example.json status
```

Failures from child programs are re-raised after the affected stage is marked
`failed`. A stage is skipped only when both its manifest state and expected files
indicate completion.

## `extract_audio.py`

Normalizes video audio for speech recognition.

- `extract_audio(input_path, output_path)` invokes FFmpeg to produce mono, 16 kHz,
  signed 16-bit PCM WAV.
- `parse_args()` defines the standalone command-line interface.
- `main()` chooses the default output path and executes extraction.

```powershell
python extract_audio.py episode.mp4 -o outputs/audio/episode.wav
```

It raises `FileNotFoundError` for missing input, `RuntimeError` when FFmpeg is not
available, and `subprocess.CalledProcessError` when conversion fails.

## `auto_prepare_script.py`

This is the canonical translation and automatic timing-repair implementation.

- `split_words(words, maximum_duration, maximum_chars)` divides word timestamps at
  long pauses, duration limits, or subtitle-length limits.
- `transcribe_and_decide(...)` runs source transcription or English translation
  with VAD and word timestamps.
- `quality_metrics(...)` and `passes_gate(...)` enforce automatic thresholds.
- `run_candidate(...)` evaluates independent source and English passes for a model.
- `make_approval(...)` records stable segment IDs and automatic approval metadata.
- `write_srt(path, segments)` serializes the repaired timing as SRT.
- `main()` selects the best passing candidate and writes approved artifacts.

```powershell
python auto_prepare_script.py outputs/audio/episode.wav `
  --project-id episode-1 --language ko --model small --fallback-model medium `
  -o outputs/episode-1/transcripts
```

The decision report records every candidate and threshold. If the primary model
fails, the fallback runs automatically. If none passes, the command fails rather
than requesting user review or sending uncertain text to TTS.

## `transcribe.py`

A simpler standalone Whisper interface retained for ad hoc transcription.

- `srt_timestamp(seconds)` converts floating-point seconds into SRT timestamps.
- `parse_args()` selects source language, Whisper model, and transcribe/translate
  task.
- `main()` writes plain text, structured JSON, and SRT.

Use `auto_prepare_script.py` for production pipeline runs because it adds word-level
repair, hallucination handling, and an auditable decision report.

For non-English targets, `translate_target(...)` loads NLLB locally and preserves
the finalized source cue boundaries. `nllb_code(...)` maps common ISO language codes
and accepts explicit NLLB codes for less common languages.

## `qa_transcript.py`

Performs deterministic checks without invoking a model.

- `analyze(transcript, maximum_duration)` detects overlapping cues, non-positive
  durations, long cues, and empty text.
- `main()` reads transcript JSON and writes a QA report.

```powershell
python qa_transcript.py episode.auto.en.json -o episode.qa.json `
  --maximum-duration 8
```

The CLI exits normally when issues are found because QA findings are review data,
not a program crash. Consumers must inspect the report's `passed` value.

## `burn_subtitles.py`

Creates a visual review copy.

- `escape_filter_path(path)` quotes Windows paths for FFmpeg filters.
- `burn_subtitles(video, subtitles, output)` renders generated English subtitles at
  the top while preserving the source audio.
- `parse_args()` and `main()` provide the CLI.

The video is re-encoded with H.264 because burned subtitles become image pixels.
The English position deliberately leaves bottom hardcoded subtitles visible.

## `mux_subtitles.py`

Creates a delivery copy with selectable subtitles.

- `mux_subtitles(video, subtitles, output)` copies video/audio and adds English SRT
  as MP4 `mov_text`.
- `parse_args()` and `main()` provide the CLI.

Unlike `burn_subtitles.py`, this operation does not re-encode video or audio and
therefore preserves quality. Player support determines display position and style.

## `generate_dub.py`

Generates voice clips locally; approved text is never sent to a TTS service.

- `select_voice(...)` downloads only Piper's public voice index and chooses a stable
  medium-quality voice matching the target language.
- `ensure_voice(...)` caches the selected public ONNX model.
- `rate_to_length_scale(...)` translates percentage speed into Piper timing.
- `generate_clip(...)` synthesizes and measures one WAV with retries.
- `generate_dub(...)` enforces automatic approval, reuses cached clips, and returns
  a schema-compliant multi-voice dub manifest. Cache reuse also checks text and voice.
- `media_duration(...)` uses FFprobe to measure generated audio.

```powershell
python generate_dub.py outputs/transcripts/episode.approved.json `
  -o outputs/dub --target-language en
```

## `separate_audio.py`

- `separate_audio(...)` extracts a stereo 44.1 kHz source mix, runs local Demucs
  two-stem inference, and writes reusable `vocals.wav` and `accompaniment.wav`.
- Audio tensors are written with SoundFile, avoiding platform codec dependencies.
- Only the accompaniment enters the final translated mix; the vocal stem is kept
  for diagnostics and alternate exports.

## `assemble_dub.py`

- `tempo_filters(factor)` decomposes large tempo changes into safe FFmpeg filters.
- `build_alignment_graph(clips, duration)` places clips at cue timestamps and
  speeds up only speech that would overrun its allotted window.
- `assemble_dub(...)` renders the dialogue timeline, ducks and mixes the source
  soundtrack, copies the video stream, and optionally adds selectable subtitles.

```powershell
python assemble_dub.py episode.mp4 outputs/dub/dub-manifest.json `
  -o outputs/final/episode.en-dubbed.mp4 --subtitles episode.en.srt
```

## `qa_final.py`

- `probe_media(...)` verifies program duration and required media streams.
- `audio_levels(...)` detects unsafe peaks and out-of-range program loudness.
- `stem_leakage(...)` calculates a bounded correlation estimate between isolated
  vocals and accompaniment without loading the whole episode into memory.
- `normalize_mix(...)` applies a safe bounded gain correction when required.
- `evaluate(...)` combines clip availability, tempo, speaker/voice consistency,
  streams, duration, loudness, and leakage into an automatic pass/fail decision.

Warnings remain auditable without blocking delivery; structural, timing, missing
media, or audio-safety failures stop the pipeline without requesting user review.

## `diarize_speakers.py`

Performs local acoustic speaker clustering before TTS.

- `segment_audio(...)` extracts each approved timing window.
- `speaker_embeddings(...)` produces normalized local WavLM speaker x-vectors.
- `choose_clusters(...)` selects speaker count with penalized cosine silhouette.
- `split_clusters_by_pitch(...)` splits mixed high/low acoustic clusters before
  voice assignment, preventing a dominant cluster from absorbing unlike voices.
- `estimate_voice_style(...)` derives a broad high/low/neutral pitch style.
- `assign_voices(...)` consistently assigns distinct Piper voices to clusters.
- `diarize(...)` writes stable `speaker-NN` and `voice` values into every segment.

The pitch style is an acoustic matching hint, not a gender assertion. Recurring
clusters keep the same voice throughout the video, and the report records cluster
size, pitch estimate, selected voice, method, and silhouette score.

`auto_prepare_script.py` uses a single detected-language transcript as canonical
timing. `translate_target(...)` applies local NLLB for every different target,
including English, while `translation_coverage(...)` requires one-to-one cue and
timestamp preservation. `clean_translation_repetition(...)` removes runaway
decoder loops without source-language-specific rules.

## Configuration and schemas

`pipeline.example.json` documents a complete project configuration. JSON Schema
files under `schemas/` define boundaries between stages:

- `pipeline-config.schema.json`: source, output, translation, quality, and dubbing
  settings.
- `transcript.schema.json`: detected language and timed English cues.
- `approved-script.schema.json`: automatically approved text and decision state.
- `dub-manifest.schema.json`: generated voice clips and alignment metadata.
- `manifest.schema.json`: stage lifecycle, commands, errors, and artifact paths.
- `final-qa.schema.json`: automatic final checks, findings, and pass/fail state.

Schemas use JSON Schema draft 2020-12. Generated output JSON is deliberately kept
outside Git; schemas and the example configuration are versioned.

## Tests

`tests/test_pipeline_tools.py` covers deterministic logic that does not require a
model download or full media encode:

- pause-based word splitting;
- QA issue classification;
- automatic gate and approval behavior;
- configuration-relative artifact paths.

Run from `videotranslator`:

```powershell
..\.venv\Scripts\python.exe -m pytest tests -q
```

## Extension points

Alignment, source separation, mixing, and export are implemented. If no separated
background is supplied directly to `assemble_dub.py`, it retains soundtrack ducking
as a backward-compatible fallback.

New stages should follow the existing pattern: deterministic artifact paths, a
standalone script, schema-defined JSON handoffs, manifest status updates, and unit
tests for decision logic.
