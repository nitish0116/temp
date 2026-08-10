# Video Translator

This module converts source-language video into an automatically approved English
script, subtitles, speaker-aware English dubbing, and a final mixed video. Runs are
controlled by JSON configuration and recorded in a manifest.

See [docs/code-reference.md](docs/code-reference.md) for file-level APIs and
[docs/architecture.md](docs/architecture.md) for the module layout and data flow.

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
| Final QA | Dubbed MP4 + manifests + stems | Automatic pass/fail report | Implemented |

No user review is required. The primary model independently creates a
source-language transcript and a target-language translation. If either fails confidence,
rejection, or timing thresholds, a stronger fallback model retries the media. The
best passing candidate is approved automatically; if none passes, the pipeline
stops before TTS.

## Setup

FFmpeg must be on `PATH`. From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r videotranslator\requirements.txt
```

Copy `config/pipeline.example.json` for each video and adjust its paths, models, language,
and quality thresholds. Relative paths resolve from the configuration file.

## Run

```powershell
.\.venv\Scripts\python.exe videotranslator\pipeline.py `
  videotranslator\config\pipeline.example.json run --through review
```

Run through selectable-subtitle export:

```powershell
.\.venv\Scripts\python.exe videotranslator\pipeline.py `
  videotranslator\config\pipeline.example.json run --through subtitle_mux
```

Inspect status:

```powershell
.\.venv\Scripts\python.exe videotranslator\pipeline.py `
  videotranslator\config\pipeline.example.json status
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
- `auto_prepare_script.py`: adaptive canonical transcription, translation, and automatic approval;
  source language is detected when omitted, target language defaults to `en`, and
  NLLB preserves every canonical source cue for all translated targets.
- `generate_dub.py`: local Piper voice selection, model caching, clip generation,
  retries, duration measurement, and dub-manifest creation.
- `assemble_dub.py`: cue alignment, overrun tempo fitting, automatic soundtrack
  ducking, native-tempo placement for constrained clips, final audio mixing,
  optional subtitle muxing, and MP4 export.
- `diarize_speakers.py`: local WavLM speaker embeddings, automatic speaker-count
  selection, stable speaker IDs, pitch-style estimation, and distinct voice assignment.
- `translate_constrained.py`: NLLB translation with per-voice duration budgets,
  bounded silence borrowing, duplicate-fragment cleanup, and automatic retries.
- `synthesize_constrained.py`: assigned-voice Piper synthesis with edge-silence
  trimming, measured duration retries, and no post-processing tempo changes.
- `align_active_speaker.py`: optional face tracking, lower-face motion scoring, and
  bounded visual-onset correction for multi-character scenes.
- `qa_dubbing_pipeline.py`: strict automatic cross-stage QA for speech coverage,
  speaker identity, tempo, onset alignment, overlap, and visual confidence.
- `qa_transcript.py`: deterministic timing checks.
- `burn_subtitles.py`: optional top-subtitle diagnostic render.
- `mux_subtitles.py`: selectable English subtitle track without media re-encoding.
- `qa_final.py`: automatic clip, timing, speaker, stream, duration, loudness, and
  stem-leakage checks with safe gain normalization when required.

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
signal; high and low pitch groups cannot be collapsed into the same acoustic cluster.
The code does not claim a person's gender from audio. Voice generation then
produces one cached target-language WAV per segment. Assembly fits clips that
overrun their cue windows, places every clip on the source timeline, and ducks the
mixes the English speech with the Demucs accompaniment stem. The isolated original
vocal stem is retained as an artifact but excluded from the translated export.

Translation uses one detected-language transcript as the canonical timing source.
The multilingual coverage gate requires all target cues and timestamps to match it,
and repeated decoder clauses are removed automatically. Assembly may borrow silence
before the next cue and applies bounded slowdown to avoid unnaturally short lines.

Run the complete pipeline with:

```powershell
python pipeline.py config/pipeline.example.json run --through final_qa
```

For a non-English target, set `translation.target_language`. Common ISO language
codes are mapped to local NLLB codes; uncommon languages can supply
`source_model_language` and `target_model_language`. Piper chooses a matching voice
automatically, or `dubbing.voice` can name one explicitly. Translation and synthesis
remain local after public model files are downloaded.

## Strong isolated-vocal transcription

For difficult soundtracks, `transcribe.py` supports relaxed Silero VAD and emits
word timestamps for later forced-alignment reconciliation. Run it against the
Demucs vocal stem without replacing the approved transcript:

```powershell
python transcribe.py outputs/project/separation/vocals.wav --model large-v3 `
  --vad-threshold 0.25 --minimum-speech-ms 100 --minimum-silence-ms 300 `
  --speech-padding-ms 250 --no-speech-threshold 0.8 `
  -o outputs/project/step1-large-v3
```

This candidate must be reconciled with the existing transcript before promotion;
relaxed VAD can recover quiet lines but may also introduce false-positive speech.

## Missing-speech recovery

`recover_missing_speech.py` treats pyannote turns and the strong Whisper transcript
as independent evidence rather than assuming the canonical cue list is complete.
It decodes uncovered vocal regions without VAD in one batched `large-v3` pass,
maps recovered words back to source time, retains strong-ASR words lost by CTC,
and merges contained duplicates without discarding their timing envelope.

```powershell
python recover_missing_speech.py alignment/vocals.reconciled.json `
  diarization/diarization-report.json separation/vocals.wav `
  --strong-transcript step1-large-v3/vocals.json --model large-v3 `
  --output-transcript recovery/source.coverage-complete.json `
  --output-report recovery/recovery-report.json
```

The promoted transcript must pass source-evidence QA before translation. Coverage
is measured against both strong-ASR word events and diarized speech time; generated
clip coverage alone is not sufficient.

## Word-level forced alignment

`force_align.py` applies a language-specific CTC acoustic model to the recovered
transcript, rebuilds cues from aligned word boundaries, and retains only reference
cues with no aligned-word evidence. The source language selects a known alignment
model when available; `--model` accepts another Hugging Face CTC model for other
languages.

```powershell
python force_align.py outputs/project/step1-large-v3/vocals.json `
  outputs/project/transcripts/source.json outputs/project/separation/vocals.wav `
  --output-transcript outputs/project/alignment/vocals.aligned.json `
  --output-reconciled outputs/project/alignment/vocals.reconciled.json `
  --output-report outputs/project/alignment/alignment-report.json
```

The reconciled output records `provenance` on every cue, distinguishing CTC-aligned
speech from short reference cues retained because no aligned word covered them.

## Dedicated speaker diarization

Step 3 uses `pyannote/speaker-diarization-community-1` rather than pitch-split
WavLM clustering. Install the optional backend, accept the model conditions on its
Hugging Face page, and expose a read-only token through `HF_TOKEN`:

```powershell
python -m pip install -r requirements/diarization.txt
$env:HF_TOKEN = "your-read-token"
python diarize_pyannote.py outputs/project/alignment/vocals.reconciled.json `
  outputs/project/separation/vocals.wav `
  --output-script outputs/project/diarization/pyannote.assigned.json `
  --output-report outputs/project/diarization/pyannote.report.json
```

The token is read only from the environment and is never written to configuration,
logs, reports, or manifests. Audio is preloaded into memory so local inference does
not depend on TorchCodec's platform decoder.

## Persistent-speaker voice matching

`match_speaker_voices.py` profiles each pyannote speaker and candidate Piper voice
using pitch, pitch range, spectral centroid, spectral bandwidth, and energy range.
A Hungarian assignment selects the globally closest unique voice for every speaker.
Pitch is the strongest acoustic constraint but is not used alone, and the report
explicitly records `gender_inference: false`.

Candidate voice probe clips are cached. For target languages without a built-in
neutral probe sentence, provide `--probe-text` in that language.

## Duration-constrained translation

Step 5 translates the speaker-assigned source script before TTS. It calibrates the
estimated speaking rate from each assigned voice's cached probe, permits at most a
bounded amount of trailing silence, and retries translations that exceed their
speaking-window budget. Adjacent same-speaker alignment fragments are merged only
when their normalized text contains one another. The command fails when any line
is still estimated to require excessive tempo adjustment.

```powershell
python translate_constrained.py outputs/project/voices.assigned.json `
  --target-language en --probe-dir outputs/project/probes `
  --output-script outputs/project/english.constrained.json `
  --output-report outputs/project/translation-report.json
```

The translated script preserves stable speaker and voice assignments. Every cue
includes its available time, estimated speech duration, ratio, character budget,
and retry decision for later synthesis and QA.

## Duration-constrained synthesis

Step 6 synthesizes the step-5 script with its persistent voice assignments and
measures the resulting WAV files. Quiet leading and trailing padding is trimmed
while internal pauses are preserved. Clips outside their recorded speaking window
are regenerated with Piper's native duration control, bounded by
`--minimum-length-scale`; unresolved clips fail the stage instead of being passed
to FFmpeg for tempo correction.

```powershell
python synthesize_constrained.py outputs/project/english.constrained.json `
  -o outputs/project/synthesis --models-dir outputs/project/models `
  --minimum-length-scale 0.85 --tolerance 1.02
```

`synthesis-report.json` records every attempt and explicitly reports whether any
post-processing tempo was used. `dub-manifest.json` remains compatible with the
existing assembly stage.

Use `assemble_dub.py --preserve-native-tempo` after constrained synthesis and
step-8 QA. This places the measured WAV clips without adding FFmpeg `atempo`
filters; the QA gate must already have verified that the clips do not overlap.

## Active-speaker and lip-motion alignment

Step 7 samples video around each dialogue cue, tracks visible faces, and measures
motion only in each face's lower region. In scenes with multiple detected faces,
the stage selects an active face only when its motion clearly dominates the other
tracks. The visual onset correction is capped at 250 ms and clamped against the
neighboring synthesized clips so it cannot create dialogue overlap.

```powershell
python -m pip install -r requirements/vision.txt
python align_active_speaker.py input.mp4 english.constrained.json `
  synthesis/dub-manifest.json `
  --output-manifest active-speaker/dub-manifest.aligned.json `
  --output-report active-speaker/active-speaker-report.json
```

Ambiguous multi-face scenes are automatically left unchanged and recorded in the
report; the tool never guesses a visible speaker or requests manual review. This
optional local implementation uses OpenCV and does not upload video frames.

## Cross-stage dubbing QA

Step 8 blocks assembly when any canonical speech clip is missing, a persistent
speaker changes voice, native TTS rate exceeds its limit, post-processing tempo is
reported, visual onset correction is excessive, generated dialogue overlaps, or
multi-face alignment confidence is below the configured threshold.

```powershell
python qa_dubbing_pipeline.py english.constrained.json translation-report.json `
  active-speaker/dub-manifest.aligned.json synthesis/synthesis-report.json `
  active-speaker/active-speaker-report.json `
  -o qa/dubbing-pipeline-qa.json
```

The defaults require complete speech coverage, limit native rate to 1.20x and
visual correction to 250 ms, and require confident decisions for at least half of
detected multi-face cues. The report is an automatic pass/fail artifact defined by
`schemas/dubbing-pipeline-qa.schema.json`; failures stop the command with no manual
review path.
