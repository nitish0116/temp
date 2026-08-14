# Configuration guide

## Choose an entry point

The current automatic subtitle workflow uses CLI flags:

```powershell
python -m videotranslator subtitles VIDEO --target-language en
```

The full translation/dubbing orchestrator uses JSON configuration and records
resumable stage state in `manifest.json`:

```powershell
python -m videotranslator CONFIG run --through review
```

Start with `config/pipeline.example.json`. Paths resolve relative to the config
file. Keep tokens and machine-specific cache paths in environment variables.

## Minimal configuration

```json
{
  "$schema": "../schemas/pipeline-config.schema.json",
  "project_id": "episode-01",
  "input_video": "../sample Data/episode-01.mp4",
  "output_root": "../outputs/episode-01",
  "translation": {
    "model": "small",
    "target_language": "en"
  }
}
```

The four shown project/path/model fields are required. Unknown keys are rejected
by the schema, preventing misspelled settings from being silently ignored.

## Settings

| Section | Key | Meaning |
| --- | --- | --- |
| root | `project_id` | Stable ID used in manifests and reports. |
| root | `input_video` | Source media path relative to the config. |
| root | `output_root` | One resumable artifact directory per project. |
| `translation` | `source_language` | Source code or `null` for detection. |
| `translation` | `target_language` | Output code; default `en`. |
| `translation` | `model`, `fallback_model` | Primary ASR and stronger retry models. |
| `translation` | `translation_model` | Hugging Face translation model ID. |
| `compute` | `device` | `auto`, `cpu`, or `cuda`; prefer `auto`. |
| `quality` | `maximum_segment_duration` | Hard cue ceiling in seconds. |
| `quality` | `minimum_subtitle_duration` | Minimum readable cue duration. |
| `quality` | `maximum_subtitle_characters` | Maximum total cue characters. |
| `quality` | `maximum_subtitle_line_characters` | Maximum characters per line. |
| `quality` | `maximum_subtitle_lines` | Normally two. |
| `quality` | `maximum_subtitle_characters_per_second` | Reading-speed ceiling. |
| `quality` | `minimum_source_event_coverage` | Required source-event fraction. |
| `quality` | `minimum_source_time_coverage` | Required source-time fraction. |
| `diarization` | `maximum_speakers` | Clustering upper bound, at least two. |
| `separation` | `model`, `device`, `shifts` | Demucs controls. |
| `dubbing` | `enabled`, `provider`, `voice` | Speech-synthesis selection. |
| `dubbing` | `source_volume`, `dub_volume` | Linear mix gains from 0 through 2. |
| `dubbing` | `minimum_dialogue_occupancy`, `minimum_tempo` | Assembly limits. |

Quality fractions use `0.0` through `1.0`. Complete constraints are authoritative
in `schemas/pipeline-config.schema.json`.

## Environment

```powershell
$cacheRoot = [Environment]::GetEnvironmentVariable("PYTHON_CACHE_HOME", "User")
$env:PYTHON_CACHE_HOME = $cacheRoot
$env:HF_HOME = [Environment]::GetEnvironmentVariable("HF_HOME", "User")
$env:TORCH_HOME = [Environment]::GetEnvironmentVariable("TORCH_HOME", "User")
$env:PIPER_MODELS_DIR = [Environment]::GetEnvironmentVariable("PIPER_MODELS_DIR", "User")
$env:HF_TOKEN = "<read-token>"
```

The Hugging Face token is needed for gated Pyannote models. Cache directories must
be writable. Select the appropriate workstation profile in
[model-inventory.md](model-inventory.md) first. Generated artifacts remain under
`output_root`.

## Validate and run

VS Code uses `$schema` to report invalid keys and types. Run and inspect state with:

```powershell
python -m videotranslator videotranslator\config\pipeline.example.json run --through review
python -m videotranslator videotranslator\config\pipeline.example.json status
```
