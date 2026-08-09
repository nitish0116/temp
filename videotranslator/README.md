# Video Translator

This module converts a source-language video into a reviewed English transcript,
English subtitles, and eventually an English-dubbed video. Every automated run is
controlled by one JSON configuration and recorded in an output manifest.

For file-by-file implementation details, public helper behavior, failure modes, and
small usage examples, see [CODE_REFERENCE.md](CODE_REFERENCE.md).

## Pipeline

| Stage | Input | Output | State |
|---|---|---|---|
| 1. Ingest | Source video | Validated project configuration | Implemented |
| 2. Extract | Source video | Mono 16 kHz WAV | Implemented |
| 3. Translate and repair | WAV | Word-timed English JSON, SRT, decisions, and approval draft | Implemented |
| 4. QA | Transcript JSON | Timing issue report | Implemented |
| 5. Visual review | Video + SRT | Video with English text at the top | Implemented |
| 6. Approval draft | Transcript + QA report | Flagged editable English script | Implemented |
| 7. Script approval | Approval draft | Approved English script | Human step |
| 8. Voice generation | Approved timed script | One English voice clip per segment | Planned |
| 9. Voice alignment | Voice clips + timings | Duration-fitted dialogue track | Planned |
| 10. Audio mix | Dialogue + source audio/stems | English program audio | Planned |
| 11. Final export | Video + English audio + subtitles | English MP4 | Planned |

The human approval gate is intentional: translation mistakes become spoken errors
if text-to-speech starts before the English script has been corrected.

## Setup

FFmpeg must be on `PATH`. From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r videotranslator\requirements.txt
```

Copy `pipeline.example.json` for each video and edit its input, output, language,
model, and quality settings. Paths are resolved relative to the configuration file.

## Run the structured pipeline

From the repository root:

```powershell
.\.venv\Scripts\python.exe videotranslator\pipeline.py videotranslator\pipeline.example.json run --through review
```

Run through the selectable-subtitle export:

```powershell
.\.venv\Scripts\python.exe videotranslator\pipeline.py videotranslator\pipeline.example.json run --through subtitle_mux
```

Completed stages with existing artifacts are skipped. Use `--force` to rebuild them.
Inspect progress with:

```powershell
.\.venv\Scripts\python.exe videotranslator\pipeline.py videotranslator\pipeline.example.json status
```

## Output layout

```text
outputs/<project-id>/
├── manifest.json              # stage status, commands, and artifact paths
├── audio/<video>.wav          # normalized transcription audio
├── transcripts/<video>.auto.en.json
├── transcripts/<video>.auto.en.srt
├── transcripts/<video>.decisions.json
├── transcripts/<video>.approval-draft.json
├── qa/<video>.qa.json         # timing problems requiring attention
├── review/<video>.top-subs.mp4
└── final/<video>.english-subs.mp4
```

## Data contracts

JSON schemas live in `schemas/`:

- `pipeline-config.schema.json` defines project inputs and processing settings.
- `transcript.schema.json` defines the timed translation consumed by QA and dubbing.
- `approved-script.schema.json` defines the human-reviewed text allowed into TTS.
- `dub-manifest.schema.json` tracks every generated and aligned English voice clip.
- `manifest.schema.json` defines pipeline state and artifact provenance.

The transcript JSON is the handoff between speech translation and dubbing. Each
segment contains `start`, `end`, and `text`. The approved version must retain these
fields so later stages can generate and align English speech predictably.

## Quality gate

`qa_transcript.py` currently checks empty text, invalid duration, overlaps, and
segments longer than the configured threshold. A QA failure does not destroy the
translation; it identifies lines that must be split or corrected. Before dubbing:

1. Read the QA report.
2. Compare top English subtitles with the original bottom subtitles and speech.
3. Fix mistranslations, names, repetitions, and abnormal segment boundaries.
4. Mark the script approved in the future approval artifact.

## Existing standalone commands

The scripts remain independently usable:

- `extract_audio.py`: extract transcription-ready WAV audio.
- `transcribe.py`: create translated TXT, JSON, and SRT files.
- `qa_transcript.py`: inspect transcript timing.
- `create_approval_script.py`: create the editable, QA-annotated script draft.
- `auto_prepare_script.py`: re-transcribe with word timestamps, remove likely
  silence hallucinations, split oversized cues, and create a cleaner approval draft.

To automate timing decisions before approval:

```powershell
.\.venv\Scripts\python.exe videotranslator\auto_prepare_script.py `
  "videotranslator\sample Data\EP.1.v0.1639315485.720p.mp4" `
  --project-id noble-my-love-episode-1 --language ko --model small `
  -o videotranslator\outputs\auto-review
```
- `burn_subtitles.py`: create a top-subtitle review video.
- `mux_subtitles.py`: add a selectable English subtitle track without re-encoding.

## Dubbing design

The next implementation milestone is stage 7. It should consume only an approved
transcript, assign a stable segment ID, generate an audio file per segment, and
record provider, voice, duration, and retry metadata. Stage 8 will time-stretch or
pad clips within safe limits. Stage 9 should preserve background sound by using
source separation when possible; simply layering voices over intact dialogue will
leave Korean speech audible beneath the English dub.
