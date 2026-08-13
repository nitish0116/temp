# Video Translator CLI Runbook

Run commands from the `videotranslator` directory. PowerShell examples use the
repository virtual environment at `..\.venv`. Replace the input and output root
for a new project.

## 1. Installation

```powershell
..\.venv\Scripts\python.exe install_dependencies.py
```

The installer selects CPU, CUDA 12.6 for pre-Turing NVIDIA GPUs such as the GTX
1050 (`sm_61`), or CUDA 12.8 for Turing and newer GPUs. Pass `--profile` to make
the choice explicit. A direct `pip install -r requirements.txt` installs this
workstation's CUDA 12.6 default.

Install FFmpeg separately and ensure `ffmpeg` and `ffprobe` are on `PATH`.
Pyannote also needs a Hugging Face **read** token and accepted access to
`pyannote/speaker-diarization-community-1`. Store the token with the Hugging
Face CLI; never place it in configuration, source code, or shell history.

XTTS-v2 is included for this non-commercial project. Its downloaded model
weights use the Coqui Public Model License. Set agreement only when that license
is acceptable:

```powershell
$env:COQUI_TOS_AGREED = "1"
```

## 2. Quick legacy pipeline

The legacy orchestrator covers extraction, transcription, automatic script
preparation, Piper dubbing, and final QA. It does not yet orchestrate every
quality-stage command described below.

```powershell
..\.venv\Scripts\python.exe pipeline.py config\pipeline.example.json status
..\.venv\Scripts\python.exe pipeline.py config\pipeline.example.json run
```

Use the explicit commands below for the current high-quality pipeline.

## 3. Project variables

```powershell
$video = "sample Data\EP.1.v0.1639315485.720p.mp4"
$run = "outputs\my-project"
New-Item -ItemType Directory -Force $run | Out-Null
```

## 4. Extract and separate audio

```powershell
..\.venv\Scripts\python.exe commands/extract_audio.py $video -o "$run\source.wav"
..\.venv\Scripts\python.exe commands/separate_audio.py $video -o "$run\separation" `
  --model htdemucs --device auto --shifts 1
```

`--device auto` prefers CUDA and falls back to CPU. Use `--device cpu` only to
force CPU execution. An NVIDIA driver alone is insufficient: the environment
must contain a CUDA-enabled PyTorch build for CUDA to be reported as available.
The selected profile pins matched Torch and TorchAudio 2.11 wheels. Runtime
selection also checks the wheel's compiled architecture list, so an incompatible
CUDA wheel falls back to CPU instead of failing after model loading.

Outputs include `vocals.wav` and `accompaniment.wav`. Downstream speech stages
use the vocal stem; final assembly uses the accompaniment.

## 5. Strong multilingual transcription

Omit `--language` to detect the input language automatically.

```powershell
..\.venv\Scripts\python.exe commands/transcribe.py "$run\separation\vocals.wav" `
  --model large-v3 -o "$run\transcription"
..\.venv\Scripts\python.exe commands/qa_transcript.py `
  "$run\transcription\vocals.json" -o "$run\transcription\qa.json" `
  --source-transcript "$run\transcription\source.json" `
  --minimum-duration 0.5 --maximum-duration 12 `
  --maximum-characters 84 --maximum-line-characters 42 `
  --maximum-lines 2 --maximum-characters-per-second 20
```

## 6. Forced alignment

`commands/force_align.py` automatically selects a model for English, French, German,
Spanish, Hindi, Japanese, Chinese, Arabic, or Korean. Unsupported and
low-confidence languages safely retain Whisper word timestamps. Pass `--model`
to override the automatic route.

```powershell
..\.venv\Scripts\python.exe commands/force_align.py `
  "$run\transcription\vocals.json" "$run\reference.json" `
  "$run\separation\vocals.wav" `
  --output-transcript "$run\alignment\aligned.json" `
  --output-reconciled "$run\alignment\reconciled.json" `
  --output-report "$run\alignment\report.json"
```

## 7. Dedicated speaker diarization

```powershell
..\.venv\Scripts\python.exe commands/diarize_pyannote.py `
  "$run\alignment\reconciled.json" "$run\separation\vocals.wav" `
  --output-script "$run\diarization\speakers.json" `
  --output-report "$run\diarization\report.json" `
  --maximum-speakers 10
```

Speaker IDs are persistent within one video. Pitch is not treated as gender.

## 8. Recover missing speech

```powershell
..\.venv\Scripts\python.exe commands/recover_missing_speech.py `
  "$run\diarization\speakers.json" "$run\diarization\report.json" `
  "$run\separation\vocals.wav" `
  --strong-transcript "$run\transcription\vocals.json" `
  --model large-v3 `
  --output-transcript "$run\recovery\source.complete.json" `
  --output-report "$run\recovery\coverage-report.json"
```

This stage decodes uncovered diarized regions without VAD and retains strong-ASR
words that forced alignment lost.

## 9. Piper voice matching and constrained translation

```powershell
..\.venv\Scripts\python.exe commands/match_speaker_voices.py `
  "$run\recovery\source.complete.json" "$run\diarization\report.json" `
  "$run\separation\vocals.wav" `
  --voice en_GB-alba-medium --voice en_GB-aru-medium `
  --models-dir "$run\models" --probe-dir "$run\voice-probes" `
  --output-script "$run\voices\assigned.json" `
  --output-report "$run\voices\report.json"

..\.venv\Scripts\python.exe commands/translate_constrained.py `
  "$run\voices\assigned.json" --target-language en `
  --probe-dir "$run\voice-probes" `
  --output-script "$run\translation\english.json" `
  --output-report "$run\translation\report.json"
```

## 10A. Piper synthesis

```powershell
..\.venv\Scripts\python.exe commands/synthesize_constrained.py `
  "$run\translation\english.json" -o "$run\synthesis-piper" `
  --models-dir "$run\models" --project-id my-project
```

Piper is fast and commercial-friendly, but its voices are less expressive.

## 10B. XTTS-v2 expressive synthesis

First select clean reference turns automatically:

```powershell
..\.venv\Scripts\python.exe commands/prepare_speaker_references.py `
  "$run\separation\vocals.wav" "$run\diarization\report.json" `
  "$run\translation\english.json" -o "$run\xtts-references"
```

Generate a speaker-diverse pilot or the complete script:

```powershell
..\.venv\Scripts\python.exe commands/synthesize_xtts.py `
  "$run\translation\english.json" "$run\xtts-references\reference-report.json" `
  -o "$run\xtts-pilot" --language en --pilot-count 7

..\.venv\Scripts\python.exe commands/synthesize_xtts.py `
  "$run\translation\english.json" "$run\xtts-references\reference-report.json" `
  -o "$run\synthesis-xtts" --language en
```

The command resumes existing accepted WAV files. Do not use a very large
`--tolerance` as a final-quality solution: it retains speech but can create
overlaps. Failed cues should instead return to translation correction.

## 11. Active-speaker alignment and cross-stage QA

```powershell
..\.venv\Scripts\python.exe commands/align_active_speaker.py $video `
  "$run\translation\english.json" "$run\synthesis-xtts\dub-manifest.json" `
  --output-manifest "$run\active-speaker\manifest.json" `
  --output-report "$run\active-speaker\report.json"

..\.venv\Scripts\python.exe commands/qa_dubbing_pipeline.py `
  "$run\translation\english.json" "$run\translation\report.json" `
  "$run\active-speaker\manifest.json" "$run\synthesis-xtts\synthesis-report.json" `
  "$run\active-speaker\report.json" `
  --strong-transcript "$run\transcription\vocals.json" `
  --diarization-report "$run\diarization\report.json" `
  -o "$run\qa\dubbing.json"
```

## 12. Assemble and inspect

Only assemble after QA passes.

```powershell
..\.venv\Scripts\python.exe commands/assemble_dub.py $video `
  "$run\active-speaker\manifest.json" `
  -o "$run\final\english-dubbed.mp4" `
  --background "$run\separation\accompaniment.wav" `
  --preserve-native-tempo
```

For subtitle timing inspection:

```powershell
..\.venv\Scripts\python.exe commands/burn_subtitles.py $video subtitles.srt `
  -o "$run\final\subtitle-timing.mp4"
```

## 13. Command discovery

Every stage exposes `--help`, for example:

```powershell
..\.venv\Scripts\python.exe commands/recover_missing_speech.py --help
..\.venv\Scripts\python.exe commands/synthesize_xtts.py --help
..\.venv\Scripts\python.exe commands/qa_dubbing_pipeline.py --help
```
## 14. Automatic subtitle creation

Use this command when the required output is a translated SRT rather than a full dub:

```powershell
..\.venv\Scripts\python.exe -m videotranslator subtitles "input.mp4" `
  --target-language en `
  --device auto
```

The command automatically extracts audio, detects the spoken language, transcribes
with word timestamps, force-aligns supported languages, runs speaker diarization,
retries uncovered speech with three increasingly permissive profiles, translates,
repairs readability, and applies independent coverage QA. `final.srt` exists only on
a passing run. Failed runs retain the best candidate as `rejected.srt` with a complete
`subtitle-pipeline-report.json`; no user review decision is required.

For unattended execution, export `HF_TOKEN` in the scheduler's environment. The
workflow fails immediately with an actionable message if diarization needs the token
and none is available. Unwritable Hugging Face, Torch, and Matplotlib caches are
replaced with directories below the output folder. Speech recovery automatically
steps down through bounded model/device combinations after an error or timeout, and
records those decisions under `automatic_fallbacks` in the pipeline report.

Useful options:

- `-o outputs/my-video` selects the artifact directory.
- `--source-language ja` overrides automatic source-language detection.
- `--maximum-attempts 1|2|3` limits recovery cost.
- `--recovery-timeout-seconds 1800` limits each recovery model/device attempt.
- `--offline` prevents model metadata network checks when weights are cached.
- `--force` rebuilds all stages instead of resuming existing artifacts.

## 15. Headless incremental canonical reprocessing

Use `reprocess-subtitles` when transcription, alignment, diarization, recovery,
and translation already exist. It executes canonical migration, display mapping,
bounded repair, independent QA, and validated SRT/ASS export only. The report
records reused/executed stages, before/after metrics, resume artifacts, and the
cheapest justified upstream rerun.

Preflight checks validate readable JSON, equal source/target segment counts,
writable output, at least 100 MB free space by default, and existing resumable
artifacts. `--minimum-free-mb` changes the disk threshold.

Exit codes:

- `0`: all QA gates passed and `passed.srt`/`passed.ass` were created.
- `1`: unexpected runtime failure.
- `2`: QA rejected the result; `rejected.srt`/`rejected.ass` and reports remain.
- `3`: preflight prerequisite failure; expensive processing did not start.
