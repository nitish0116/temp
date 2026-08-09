# Video Translator Code Reference

This document describes the implementation of the `videotranslator` module. See
`README.md` for installation, pipeline stages, and operator instructions.

## Architecture

The module is a collection of small command-line programs coordinated by
`pipeline.py`. Each stage communicates through files rather than in-memory state:

```text
source video
  -> extracted WAV
  -> separate source transcript and English translation
  -> candidate scoring, fallback retry, and automatic approval
  -> QA report
  -> review video / selectable-subtitle video
  -> approved script
  -> local target-language TTS clips and dub manifest
  -> future alignment, mixing, and export stages
```

`manifest.json` records commands, timestamps, status, and output paths. This makes
an interrupted run resumable and provides provenance for every generated artifact.

## `pipeline.py`

The main orchestrator. It does not contain media-processing logic; it builds and
runs commands for the specialized scripts.

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

## `diarize_speakers.py`

Performs local acoustic speaker clustering before TTS.

- `segment_audio(...)` extracts each approved timing window.
- `speaker_embeddings(...)` produces normalized local WavLM speaker x-vectors.
- `choose_clusters(...)` selects speaker count with penalized cosine silhouette.
- `estimate_voice_style(...)` derives a broad high/low/neutral pitch style.
- `assign_voices(...)` consistently assigns distinct Piper voices to clusters.
- `diarize(...)` writes stable `speaker-NN` and `voice` values into every segment.

The pitch style is an acoustic matching hint, not a gender assertion. Recurring
clusters keep the same voice throughout the video, and the report records cluster
size, pitch estimate, selected voice, method, and silhouette score.

## Configuration and schemas

`pipeline.example.json` documents a complete project configuration. JSON Schema
files under `schemas/` define boundaries between stages:

- `pipeline-config.schema.json`: source, output, translation, quality, and dubbing
  settings.
- `transcript.schema.json`: detected language and timed English cues.
- `approved-script.schema.json`: automatically approved text and decision state.
- `dub-manifest.schema.json`: future generated voice clips and alignment metadata.
- `manifest.schema.json`: stage lifecycle, commands, errors, and artifact paths.

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

Planned stages are already represented in configuration and manifest contracts:

1. `align`: pad or safely time-stretch clips to their allotted windows.
2. `mix`: combine target dialogue with separated source ambience and music.
3. `export`: mux target audio, optional original audio, and subtitles into the
   final video.

New stages should follow the existing pattern: deterministic artifact paths, a
standalone script, schema-defined JSON handoffs, manifest status updates, and unit
tests for decision logic.
