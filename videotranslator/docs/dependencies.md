# Dependency reference

## Installation profiles

From the repository root, use the hardware-aware installer:

```powershell
D:\Git\Projects\.venv\Scripts\python.exe videotranslator\install_dependencies.py
```

It selects one Torch profile (`cpu`, `cu126`, or `cu128`) and then installs
`requirements/common.txt`. Override detection with `--profile`; use `--dry-run`
to inspect commands. For development, install `requirements/dev.txt` after the
Torch profile.

## Direct dependencies

| Requirement | Import | Purpose |
| --- | --- | --- |
| `faster-whisper` | `faster_whisper` | ASR and recovery |
| `transformers`, `sentencepiece` | `transformers` | contextual/NLLB translation |
| `torch`, `torchaudio`, `torchcodec` | same | model execution and media decoding |
| `pyannote.audio`, `huggingface-hub` | `pyannote`, `huggingface_hub` | diarization and authentication |
| `numpy`, `scipy`, `soundfile`, `librosa` | same | audio arrays, signal processing, WAV I/O |
| `scikit-learn` | `sklearn` | speaker clustering |
| `demucs` | same | source separation |
| `opencv-python-headless` | `cv2` | active-speaker analysis |
| `piper-tts` | `piper` | local voices |
| `coqui-tts` | `TTS` | optional XTTS-v2 cloning |
| `truststore` | `truststore` | OS certificate store support |

FFmpeg is an external executable. Standard stages require `ffmpeg` and `ffprobe`
on `PATH`; TorchCodec on Windows also needs a shared build containing the FFmpeg
DLLs. The runtime discovers `FFMPEG_SHARED_HOME` or the Winget Gyan shared package.
Ollama is a separate external service used only by translation agreement; its
required pulls and qualification status are listed in the model inventory.

`pytest` is test-only and belongs in `requirements/dev.txt`. Pillow and MoviePy
are not imported by this project and are intentionally not pinned. In a shared
environment, `pip check` can report conflicts belonging to other projects; verify
that any reported distribution is part of this table before changing this file.
After dependency changes, run:

```powershell
D:\Git\Projects\.venv\Scripts\python.exe -m pip check
D:\Git\Projects\.venv\Scripts\python.exe -m pytest videotranslator\tests -q
```

Packages imported by project code are declared directly even when another package
currently installs them transitively. This keeps clean installs reproducible.

## Runtime model requirements

Installing these Python distributions does not install every model used by the
pipeline. The complete model IDs, gated-access requirements, cache variables,
Ollama pulls, conditional language aligners, and new-workstation checklist are in
[model-inventory.md](model-inventory.md).
