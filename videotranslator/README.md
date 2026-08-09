# Video Translator

This module converts source-language video into an automatically approved English
script, subtitles, speaker-aware English dubbing, and a final mixed video. Runs are
controlled by JSON configuration and recorded in a manifest.

See [CODE_REFERENCE.md](CODE_REFERENCE.md) for file-level APIs, examples, and errors.

## Pipeline

| Stage | Input | Output | State |
|---|---|---|---|
| Ingest | Source video | Validated configuration | Implemented |
| Extract | Video | Mono 16 kHz WAV | Implemented |
| Source separation | Source soundtrack | Vocals + accompaniment | Implemented |
| Adaptive translation | WAV | Source and English transcripts | Implemented |
| Automatic approval | Model candidates | Approved timed English script | Implemented |
| QA | English transcript | Timing report | Implemented |
| Speaker diarization | Source audio + script | Stable speakers and distinct voices | Implemented |
| Diagnostic render | Video + SRT | Optional top-subtitle video | Implemented |
| Subtitle mux | Video + SRT | Selectable English subtitles | Implemented |
| Voice generation | Approved script | Local target-language clips | Implemented |
| Alignment | Voice clips | Timed dialogue track | Implemented |
| Audio mix | Dialogue + source audio | English program audio | Implemented |
| Export | Video + English audio | English MP4 | Implemented |

No user review is required. The primary model independently creates a
source-language transcript and an English translation. If either fails confidence,
rejection, or timing thresholds, a stronger fallback model retries the media. The
best passing candidate is approved automatically; if none passes, the pipeline
stops before TTS.

## Setup

FFmpeg must be on `PATH`. From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r videotranslator\requirements.txt
```

Copy `pipeline.example.json` for each video and adjust its paths, models, language,
and quality thresholds. Relative paths resolve from the configuration file.

## Run

```powershell
.\.venv\Scripts\python.exe videotranslator\pipeline.py `
  videotranslator\pipeline.example.json run --through review
```

Run through selectable-subtitle export:

```powershell
.\.venv\Scripts\python.exe videotranslator\pipeline.py `
  videotranslator\pipeline.example.json run --through subtitle_mux
```

Inspect status:

```powershell
.\.venv\Scripts\python.exe videotranslator\pipeline.py `
  videotranslator\pipeline.example.json status
```

Completed stages with existing artifacts are skipped. Add `--force` to rebuild.

## Output layout

```text
outputs/<project-id>/
|-- manifest.json
|-- audio/<video>.wav
|-- transcripts/<video>.source.json
|-- transcripts/<video>.auto.en.json
|-- transcripts/<video>.auto.en.srt
|-- transcripts/<video>.decisions.json
|-- transcripts/<video>.approved.json
|-- diarization/<video>.assigned.json
|-- diarization/<video>.speakers.json
|-- qa/<video>.qa.json
|-- review/<video>.top-subs.mp4
`-- final/<video>.english-subs.mp4
```

## Automatic quality gate

The gate evaluates source and English passes independently:

- accepted and rejected cue counts;
- low-confidence cue ratio;
- silence hallucinations;
- invalid, overlapping, or oversized timing;
- primary versus fallback candidate score.

Thresholds live under `quality` in the pipeline configuration. Approval metadata
records `automatic-quality-gate`, the timestamp, and selected model. The decision
report preserves metrics for every attempted candidate.

## Data contracts

Schemas under `schemas/` define:

- pipeline configuration;
- source and English transcripts;
- automatically approved scripts;
- pipeline manifests;
- future generated dub clips.

The approved script is the only input accepted by the TTS stage. Stable
segment IDs connect generated speech to timing and decision provenance.

## Standalone tools

- `extract_audio.py`: normalize audio for speech recognition.
- `separate_audio.py`: local Demucs two-stem separation into reusable vocals and
  accompaniment tracks.
- `transcribe.py`: simple ad hoc transcription or direct translation.
- `auto_prepare_script.py`: adaptive dual-pass translation and automatic approval;
  source language is detected when omitted, and target language defaults to `en`.
- `generate_dub.py`: local Piper voice selection, model caching, clip generation,
  retries, duration measurement, and dub-manifest creation.
- `assemble_dub.py`: cue alignment, overrun tempo fitting, automatic soundtrack
  ducking, final audio mixing, optional subtitle muxing, and MP4 export.
- `diarize_speakers.py`: local WavLM speaker embeddings, automatic speaker-count
  selection, stable speaker IDs, pitch-style estimation, and distinct voice assignment.
- `qa_transcript.py`: deterministic timing checks.
- `burn_subtitles.py`: optional top-subtitle diagnostic render.
- `mux_subtitles.py`: selectable English subtitle track without media re-encoding.

Example automatic preparation:

```powershell
.\.venv\Scripts\python.exe videotranslator\auto_prepare_script.py `
  "videotranslator\sample Data\EP.1.v0.1639315485.720p.mp4" `
  --project-id noble-my-love-episode-1 --language ko `
  --model small --fallback-model medium `
  -o videotranslator\outputs\auto-review
```

## Dubbing design

Speaker diarization runs before voice generation, so recurring acoustic speakers
keep stable IDs and distinct Piper voices. Pitch is used only as a broad voice-style
signal; the code does not claim a person's gender from audio. Voice generation then
produces one cached target-language WAV per segment. Assembly fits clips that
overrun their cue windows, places every clip on the source timeline, and ducks the
mixes the English speech with the Demucs accompaniment stem. The isolated original
vocal stem is retained as an artifact but excluded from the translated export.

Run the complete pipeline with:

```powershell
python pipeline.py pipeline.example.json run --through assemble
```

For a non-English target, set `translation.target_language`. Common ISO language
codes are mapped to local NLLB codes; uncommon languages can supply
`source_model_language` and `target_model_language`. Piper chooses a matching voice
automatically, or `dubbing.voice` can name one explicitly. Translation and synthesis
remain local after public model files are downloaded.
