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

The unified requirements file includes diarization, vision, and expressive TTS.
XTTS-v2 model weights use the Coqui Public Model License and are enabled for this
non-commercial project. Transformers is pinned below 5 for XTTS compatibility.
The Windows environment uses matched PyTorch/TorchAudio CUDA 12.8 wheels; verify
installation with `torch.cuda.is_available()` before a long media run.

Detailed operations and project state:

- [Stage-by-stage CLI runbook](docs/cli.md)
- [Implementation history and known limitations](docs/implementation-history.md)
- [Future automatic quality approach](docs/future-approach.md)
- [Architecture](docs/architecture.md)
- [Code reference](docs/code-reference.md)

Copy `config/pipeline.example.json` for each video and adjust its paths, models, language,
and quality thresholds. Relative paths resolve from the configuration file.
`compute.device` defaults to `auto`: CUDA is selected when CUDA-enabled PyTorch
can access an NVIDIA GPU, otherwise every supported stage falls back to CPU.

## Run

For fully automatic subtitle creation, use the single-command workflow. It detects
the source language, uses CUDA when available, retries missing-speech recovery, and
promotes an SRT only when every QA gate passes:

```powershell
.\.venv\Scripts\python.exe -m videotranslator subtitles `
  "videotranslator\sample Data\episode.mp4" `
  --target-language en
```

Successful runs create `final.srt`. A run that exhausts all recovery profiles creates
`rejected.srt` and `subtitle-pipeline-report.json`, then exits with status 2. Resume is
automatic; pass `--force` to rebuild existing stages. Use `--offline` when all model
weights are already cached and internet access is unavailable.

```powershell
.\.venv\Scripts\python.exe -m videotranslator `
  videotranslator\config\pipeline.example.json run --through review
```

Run through selectable-subtitle export:

```powershell
.\.venv\Scripts\python.exe -m videotranslator `
  videotranslator\config\pipeline.example.json run --through subtitle_mux
```

Inspect status:

```powershell
.\.venv\Scripts\python.exe -m videotranslator `
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

- `commands/extract_audio.py`: normalize audio for speech recognition.
- `commands/separate_audio.py`: local Demucs two-stem separation into reusable vocals and
  accompaniment tracks.
- `commands/transcribe.py`: simple ad hoc transcription or direct translation.
- `commands/auto_prepare_script.py`: adaptive canonical transcription, translation, and automatic approval;
  source language is detected when omitted, target language defaults to `en`, and
  NLLB preserves every canonical source cue for all translated targets.
- `commands/generate_dub.py`: local Piper voice selection, model caching, clip generation,
  retries, duration measurement, and dub-manifest creation.
- `commands/assemble_dub.py`: cue alignment, overrun tempo fitting, automatic soundtrack
  ducking, native-tempo placement for constrained clips, final audio mixing,
  optional subtitle muxing, and MP4 export.
- `commands/diarize_speakers.py`: local WavLM speaker embeddings, automatic speaker-count
  selection, stable speaker IDs, pitch-style estimation, and distinct voice assignment.
- `commands/translate_constrained.py`: NLLB translation with per-voice duration budgets,
  bounded silence borrowing, duplicate-fragment cleanup, and automatic retries.
- `commands/synthesize_constrained.py`: assigned-voice Piper synthesis with edge-silence
  trimming, measured duration retries, and no post-processing tempo changes.
- `commands/align_active_speaker.py`: optional face tracking, lower-face motion scoring, and
  bounded visual-onset correction for multi-character scenes.
- `commands/qa_dubbing_pipeline.py`: strict automatic cross-stage QA for speech coverage,
  speaker identity, tempo, onset alignment, overlap, and visual confidence.
- `commands/qa_transcript.py`: blocking timing, readability, text-integrity, and source
  dialogue-coverage checks.
- `commands/burn_subtitles.py`: optional top-subtitle diagnostic render.
- `commands/mux_subtitles.py`: selectable English subtitle track without media re-encoding.
- `commands/qa_final.py`: automatic clip, timing, speaker, stream, duration, loudness, and
  stem-leakage checks with safe gain normalization when required.

Example automatic preparation:

```powershell
.\.venv\Scripts\python.exe videotranslator\commands\auto_prepare_script.py `
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

For difficult soundtracks, `commands/transcribe.py` supports relaxed Silero VAD and emits
word timestamps for later forced-alignment reconciliation. Run it against the
Demucs vocal stem without replacing the approved transcript:

```powershell
python commands/transcribe.py outputs/project/separation/vocals.wav --model large-v3 `
  --vad-threshold 0.25 --minimum-speech-ms 100 --minimum-silence-ms 300 `
  --speech-padding-ms 250 --no-speech-threshold 0.8 `
  -o outputs/project/step1-large-v3
```

This candidate must be reconciled with the existing transcript before promotion;
relaxed VAD can recover quiet lines but may also introduce false-positive speech.

## Missing-speech recovery

`commands/recover_missing_speech.py` treats pyannote turns and the strong Whisper transcript
as independent evidence rather than assuming the canonical cue list is complete.
It decodes uncovered vocal regions without VAD in one batched `large-v3` pass,
maps recovered words back to source time, retains strong-ASR words lost by CTC,
and merges contained duplicates without discarding their timing envelope.

```powershell
python commands/recover_missing_speech.py alignment/vocals.reconciled.json `
  diarization/diarization-report.json separation/vocals.wav `
  --strong-transcript step1-large-v3/vocals.json --model large-v3 `
  --output-transcript recovery/source.coverage-complete.json `
  --output-report recovery/recovery-report.json
```

The promoted transcript must pass source-evidence QA before translation. Coverage
is measured against both strong-ASR word events and diarized speech time; generated
clip coverage alone is not sufficient.

## Word-level forced alignment

`commands/force_align.py` applies a language-specific CTC acoustic model to the recovered
transcript, rebuilds cues from aligned word boundaries, and retains only reference
cues with no aligned-word evidence. The detected language automatically routes
English, French, German, Spanish, Hindi, Japanese, Chinese, Arabic, and Korean to
language-specific models. Unknown or low-confidence languages retain Whisper word
timestamps instead of using an incompatible model. `--model` overrides routing.

```powershell
python commands/force_align.py outputs/project/step1-large-v3/vocals.json `
  outputs/project/transcripts/source.json outputs/project/separation/vocals.wav `
  --output-transcript outputs/project/alignment/vocals.aligned.json `
  --output-reconciled outputs/project/alignment/vocals.reconciled.json `
  --output-report outputs/project/alignment/alignment-report.json
```

Use `--minimum-language-probability 0.5` to change the confidence threshold. The
report records the normalized language, selected mode/model, routing reason, CTC
success count, and number of cues that used Whisper timestamps.

Canonical cues are segmented at acoustic pauses, sentence-ending punctuation,
maximum duration/character limits, and any speaker identity already attached to
the words. The diarization stage performs a second word-level split at pyannote
speaker turns, preventing two characters from sharing one subtitle cue. CJK text
is reconstructed without inserting Latin-style spaces.

A conservative cleanup pass merges dangling continuation fragments and very short
unpunctuated fragments only when the neighboring cue has the same speaker, the
silence gap is small, and the combined cue remains within duration and character
limits. Complete short replies such as `No!` remain independent.

The reconciled output records `provenance` on every cue, distinguishing CTC-aligned
speech from short reference cues retained because no aligned word covered them.

## Dedicated speaker diarization

Step 3 uses `pyannote/speaker-diarization-community-1` rather than pitch-split
WavLM clustering. Install the optional backend, accept the model conditions on its
Hugging Face page, and expose a read-only token through `HF_TOKEN`:

```powershell
python -m pip install -r requirements.txt
$env:HF_TOKEN = "your-read-token"
python commands/diarize_pyannote.py outputs/project/alignment/vocals.reconciled.json `
  outputs/project/separation/vocals.wav `
  --output-script outputs/project/diarization/pyannote.assigned.json `
  --output-report outputs/project/diarization/pyannote.report.json
```

The token is read only from the environment and is never written to configuration,
logs, reports, or manifests. Audio is preloaded into memory so local inference does
not depend on TorchCodec's platform decoder.

## Persistent-speaker voice matching

`commands/match_speaker_voices.py` profiles each pyannote speaker and candidate Piper voice
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
python commands/translate_constrained.py outputs/project/voices.assigned.json `
  --target-language en --probe-dir outputs/project/probes `
  --output-script outputs/project/english.constrained.json `
  --output-report outputs/project/translation-report.json
```

The translated script preserves stable speaker and voice assignments. Every cue
includes its available time, estimated speech duration, ratio, character budget,
and retry decision for later synthesis and QA.

## Expressive cloned voices (optional, non-commercial)

XTTS-v2 is installed by the unified `requirements.txt`. Its model weights use
the Coqui Public Model License, so this backend is used only for this confirmed
non-commercial project. References are
selected automatically from clean pyannote turns in the isolated vocal stem;
pitch is not used to infer gender or select a voice.

```powershell
python commands/prepare_speaker_references.py separation/vocals.wav `
  diarization/diarization-report.json english.constrained.json -o references
python commands/synthesize_xtts.py english.constrained.json references/reference-report.json `
  -o synthesis-xtts --language en
```

XTTS preserves a persistent cloned voice per speaker. Clips that exceed their
speaking window fail QA instead of being accelerated. Use `--pilot-count 7` to
sample distinct speakers before a full render.

## Duration-constrained synthesis

Step 6 synthesizes the step-5 script with its persistent voice assignments and
measures the resulting WAV files. Quiet leading and trailing padding is trimmed
while internal pauses are preserved. Clips outside their recorded speaking window
are regenerated with Piper's native duration control, bounded by
`--minimum-length-scale`; unresolved clips fail the stage instead of being passed
to FFmpeg for tempo correction.

```powershell
python commands/synthesize_constrained.py outputs/project/english.constrained.json `
  -o outputs/project/synthesis --models-dir outputs/project/models `
  --minimum-length-scale 0.85 --tolerance 1.02
```

`synthesis-report.json` records every attempt and explicitly reports whether any
post-processing tempo was used. `dub-manifest.json` remains compatible with the
existing assembly stage.

Use `commands/assemble_dub.py --preserve-native-tempo` after constrained synthesis and
step-8 QA. This places the measured WAV clips without adding FFmpeg `atempo`
filters; the QA gate must already have verified that the clips do not overlap.

## Active-speaker and lip-motion alignment

Step 7 samples video around each dialogue cue, tracks visible faces, and measures
motion only in each face's lower region. In scenes with multiple detected faces,
the stage selects an active face only when its motion clearly dominates the other
tracks. The visual onset correction is capped at 250 ms and clamped against the
neighboring synthesized clips so it cannot create dialogue overlap.

```powershell
python -m pip install -r requirements.txt
python commands/align_active_speaker.py input.mp4 english.constrained.json `
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
python commands/qa_dubbing_pipeline.py english.constrained.json translation-report.json `
  active-speaker/dub-manifest.aligned.json synthesis/synthesis-report.json `
  active-speaker/active-speaker-report.json `
  -o qa/dubbing-pipeline-qa.json
```

The defaults require complete speech coverage, limit native rate to 1.20x and
visual correction to 250 ms, and require confident decisions for at least half of
detected multi-face cues. The report is an automatic pass/fail artifact defined by
`schemas/dubbing-pipeline-qa.schema.json`; failures stop the command with no manual
review path.
