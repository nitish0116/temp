"""Manage the isolated Kokoro environment and pinned local model cache."""
from __future__ import annotations
import hashlib, json, os, subprocess, sys
from collections.abc import Callable, Sequence
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
PROJECT = WORKSPACE / "videoCreator"
ENVIRONMENT = WORKSPACE / "audioEnv"
REQUIREMENTS = PROJECT / "requirements-local-audio.txt"
MARKER = ENVIRONMENT / ".video-creator-audio-environment.json"
ACTIVE_FLAG = "VIDEO_CREATOR_AUDIO_ENV"
MODEL_ID = "hexgrad/Kokoro-82M"
MODEL_REVISION = "fbba31e67ad83eb66394c926627e99d35abeb087"
Runner = Callable[..., subprocess.CompletedProcess]

def environment_python(environment: Path = ENVIRONMENT) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

def cache_environment() -> dict[str, str]:
    root = WORKSPACE / ".model-cache"; values = dict(os.environ)
    values.setdefault("HF_HOME", str(root / "huggingface")); values.setdefault("PYTHON_CACHE_HOME", str(root))
    values.setdefault("TEMP", str(root / "tmp")); values.setdefault("TMP", str(root / "tmp"))
    for key in ("HF_HOME", "TEMP", "TMP"): Path(values[key]).mkdir(parents=True, exist_ok=True)
    return values

def requirements_fingerprint() -> str:
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()); digest.update(f"{sys.version_info.major}.{sys.version_info.minor}".encode())
    return digest.hexdigest()

def environment_is_current(environment: Path = ENVIRONMENT) -> bool:
    if not environment_python(environment).is_file() or not (environment / MARKER.name).is_file(): return False
    try: state = json.loads((environment / MARKER.name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return False
    return state.get("requirements_fingerprint") == requirements_fingerprint()

def ensure_audio_environment(*, offline: bool = False, runner: Runner = subprocess.run) -> Path:
    python = environment_python()
    if environment_is_current(): return python
    if offline: raise RuntimeError("audioEnv is missing or stale; run setup-local-audio online once")
    if not python.is_file(): runner([sys.executable, "-m", "venv", str(ENVIRONMENT)], cwd=WORKSPACE, check=True)
    runner([str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)], cwd=WORKSPACE, check=True, env=cache_environment())
    runner([str(python), "-m", "pip", "install", "--no-build-isolation", "-e", str(PROJECT)], cwd=WORKSPACE, check=True, env=cache_environment())
    MARKER.write_text(json.dumps({"schema_version": 1, "requirements": "videoCreator/requirements-local-audio.txt",
        "requirements_fingerprint": requirements_fingerprint(), "python_version": f"{sys.version_info.major}.{sys.version_info.minor}"}, indent=2) + "\n", encoding="utf-8")
    return python

def setup_local_audio(*, offline: bool = False, runner: Runner = subprocess.run) -> Path:
    python = ensure_audio_environment(offline=offline, runner=runner)
    code = ("from huggingface_hub import snapshot_download; "
            f"snapshot_download({MODEL_ID!r}, revision={MODEL_REVISION!r}, local_files_only={offline!r})")
    runner([str(python), "-c", code], cwd=WORKSPACE, check=True, env=cache_environment()); return python

def run_local_audio(arguments: Sequence[str], *, runner: Runner = subprocess.run) -> int:
    python = setup_local_audio(offline="--offline" in arguments, runner=runner); environment = cache_environment()
    environment[ACTIVE_FLAG] = "1"
    return int(runner([str(python), "-m", "video_creator.cli", *arguments], cwd=PROJECT, env=environment, check=False).returncode)
