# Model weights and LLM inventory

Python packages are pinned in `requirements/`; model weights are separate runtime
assets and are not installed by `pip`. This inventory is the setup checklist for a
new workstation and the source of truth for models referenced by project code.

## Shared model storage

Keep downloaded weights in a cache directory rather than tracked files or run
outputs. Runtime code reads the environment variables, so each workstation can
use its own paths without changing code. Configure the matching profile once.

### Workstation A: shared `D:` caches

```powershell
$cacheRoot = "D:\PythonCaches"
[Environment]::SetEnvironmentVariable("PYTHON_CACHE_HOME", $cacheRoot, "User")
[Environment]::SetEnvironmentVariable("HF_HOME", "$cacheRoot\huggingface", "User")
[Environment]::SetEnvironmentVariable("TORCH_HOME", "$cacheRoot\torch", "User")
[Environment]::SetEnvironmentVariable("PIPER_MODELS_DIR", "$cacheRoot\piper\voices", "User")
[Environment]::SetEnvironmentVariable("TTS_HOME", "$cacheRoot\coqui-tts", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "D:\Ollama\Models", "User")
```

### Workstation B: repository-workspace caches

The `.model-cache` directory is ignored by Git.

```powershell
$cacheRoot = "C:\Users\z005537p\NitishWork\HM\temp\.model-cache"
[Environment]::SetEnvironmentVariable("PYTHON_CACHE_HOME", $cacheRoot, "User")
[Environment]::SetEnvironmentVariable("HF_HOME", "$cacheRoot\huggingface", "User")
[Environment]::SetEnvironmentVariable("TORCH_HOME", "$cacheRoot\torch", "User")
[Environment]::SetEnvironmentVariable("PIPER_MODELS_DIR", "$cacheRoot\piper\voices", "User")
[Environment]::SetEnvironmentVariable("TTS_HOME", "$cacheRoot\coqui-tts", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "$cacheRoot\ollama", "User")
```

Restart the terminal after setting persistent variables. `--offline` is valid only
after every model needed by the selected route has been downloaded and gated-model
access has been verified. If `PYTHON_CACHE_HOME` is unset, Windows falls back to
`%LOCALAPPDATA%\videotranslator\models`; other systems fall back to
`~/.cache/videotranslator/models`. Explicit workstation profiles are preferred.

## Subtitle creation models

| Model ID | Type and role | Requirement | Acquisition |
| --- | --- | --- | --- |
| `large-v3` | faster-whisper ASR and missing-speech recovery | Required by the canonical subtitle command | Downloaded through faster-whisper/Hugging Face |
| `medium`, `small` | Bounded ASR fallbacks for memory, timeout, or unsupported-GPU cases | Required for unattended fallback operation | Downloaded through faster-whisper/Hugging Face |
| `pyannote/speaker-diarization-community-1` | Speaker diarization and exclusive speech turns | Required by the canonical subtitle command | Hugging Face gated model; accept its terms and configure `HF_TOKEN` |
| `Qwen/Qwen2.5-0.5B-Instruct` | Current contextual primary translator | Required by the current canonical translation route | Hugging Face Transformers cache |
| `facebook/nllb-200-distilled-600M` | Direct multilingual fallback and legacy/constrained translation | Required for translation fallback and dubbing translation | Hugging Face Transformers cache |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Cross-language semantic similarity for translation agreement | Required when `--translation-agreement` is enabled | Hugging Face Transformers cache |
| `qwen3:1.7b` | Former Ollama independent translator | Diagnostic only: Step 22 rejected it for Japanese, Korean, and Mandarin | `ollama pull qwen3:1.7b` |
| `llama3.1:8b` | Stronger-model retry and rejected Step 23 baseline | Failed two reviewed station probes; do not use as release evidence | `ollama pull llama3.1:8b` |
| `qwen2.5:7b` | Qualified Ollama independent translator | Selected by Step 23; Apache 2.0 | `ollama pull qwen2.5:7b` |

`qwen3:1.7b` must not be treated as release-qualified merely because its files are
installed. Step 22 measured invalid-output rates of 100% for the Japanese probe,
75% for the Korean probe, and 35.09% for the complete Mandarin run.

Step 23 measured `qwen2.5:7b` on 60 bounded groups: 20/20 Japanese,
20/20 Korean, and 20/20 Mandarin groups passed both the target-only output contract
and reviewed semantic terms. The required `cute`, `Seoul`, and Treaty of
Shimonoseki references passed. The initial CPU qualification measured median
latency of 1.11-1.13 seconds per group and a 5.21-second worst case. With automatic
GPU offload enabled on Workstation B, Qwen used 4.75 GB VRAM, retained 60/60 probe
quality, and measured about 0.20-second warmed median latency. Ollama runs
headlessly from the local cache after download.

`--ollama-device auto` offloads when the installed Torch profile supports CUDA and
the GPU has at least 8 GiB VRAM. `cuda` explicitly permits offload below that
threshold; `cpu` forces `num_gpu=0`. This keeps both workstation profiles usable.

## Conditional alignment models

The force-alignment stage loads only the model routed for the detected source
language. Cache the languages expected on a new workstation; unsupported or
low-confidence languages retain Whisper timestamps instead.

| Source language | Hugging Face model ID |
| --- | --- |
| English | `facebook/wav2vec2-base-960h` |
| French | `facebook/wav2vec2-large-xlsr-53-french` |
| German | `facebook/wav2vec2-large-xlsr-53-german` |
| Spanish | `facebook/wav2vec2-large-xlsr-53-spanish` |
| Hindi | `jonatasgrosman/wav2vec2-large-xlsr-53-hindi` |
| Japanese | `jonatasgrosman/wav2vec2-large-xlsr-53-japanese` |
| Mandarin/Chinese | `jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn` |
| Arabic | `jonatasgrosman/wav2vec2-large-xlsr-53-arabic` |
| Korean | `kresnik/wav2vec2-large-xlsr-korean` |

## Dubbing and optional pipeline models

| Model ID or family | Role | Requirement | Acquisition |
| --- | --- | --- | --- |
| `htdemucs` | Vocal/background source separation | Required for the full dubbing route | Downloaded by Demucs into `TORCH_HOME` |
| `microsoft/wavlm-base-plus-sv` | Local speaker embeddings and clustering | Required by the non-pyannote speaker-clustering route | Hugging Face Transformers cache |
| Piper voice `.onnx` plus `.onnx.json` | Fast target-language speech synthesis | Required when Piper dubbing is selected | Selected per language from Piper's public voice index and stored in `PIPER_MODELS_DIR` |
| `tts_models/multilingual/multi-dataset/xtts_v2` | Expressive cross-lingual voice cloning | Optional, non-commercial route under the Coqui Public Model License | Downloaded by Coqui TTS into `TTS_HOME` |

Piper voice IDs are configuration and language dependent, so there is no single
universal voice model to preinstall. Record the chosen voice IDs in each project
configuration and prefetch them before an offline run.

## New-workstation readiness checklist

1. Run `videotranslator/install_dependencies.py` and let it select the Torch profile.
2. Install FFmpeg plus a shared Windows build for TorchCodec.
3. Configure the shared cache variables above and confirm every directory is writable.
4. Authenticate Hugging Face, accept pyannote terms, and download the gated model.
5. Prefetch `large-v3`, `medium`, and `small` for unattended ASR fallback.
6. Prefetch the primary, NLLB, semantic-similarity, and expected alignment models.
7. Install Ollama, set `OLLAMA_MODELS`, and pull only the agreement models selected by Step 23.
8. Prefetch Demucs and the selected Piper/XTTS assets when dubbing is required.
9. Run one offline smoke test; missing weights must fail before processing a full video.

Useful verification commands:

```powershell
D:\Git\Projects\.venv\Scripts\python.exe -m pip check
ollama list
nvidia-smi
D:\Git\Projects\.venv\Scripts\python.exe -m pytest videotranslator\tests -q
```

The package version, model ID, model license/terms, cache root, and qualification
status must be reviewed together whenever a model is replaced.

## Observed disk footprint on the current workstation

These are planning measurements from the current cache, not guaranteed download
sizes. Hugging Face directories can retain multiple formats or revisions.

| Cached assets | Observed footprint |
| --- | ---: |
| faster-whisper `large-v3`, `medium`, and `small` | about 4.76 GiB total |
| `Qwen/Qwen2.5-0.5B-Instruct` | about 0.93 GiB |
| `facebook/nllb-200-distilled-600M` | about 4.60 GiB in the current multi-format cache |
| Multilingual MiniLM | about 0.45 GiB |
| Installed Japanese, Mandarin, and Korean CTC aligners | about 5.93 GiB total |
| `microsoft/wavlm-base-plus-sv` | about 0.38 GiB |
| Ollama `qwen2.5:7b` plus `llama3.1:8b` | 8.94 GiB total in the current cache |

Allow additional headroom for pyannote component models, Demucs, selected Piper
voices, optional XTTS-v2, temporary downloads, and future Step 23 candidates.
