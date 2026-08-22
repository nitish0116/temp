"""Manage and invoke the isolated local-image environment and shared model cache."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]
PROJECT = WORKSPACE / "videoCreator"
ENVIRONMENT = WORKSPACE / "imageEnv"
REQUIREMENTS = PROJECT / "requirements-local-images.txt"
MARKER = ENVIRONMENT / ".video-creator-environment.json"
ACTIVE_FLAG = "VIDEO_CREATOR_IMAGE_ENV"
MODEL_ID = "Efficient-Large-Model/Sana_1600M_1024px_diffusers"
MODEL_REVISION = "ac0da2ff55fbe434795be0dce883042e4d49e2fc"
SANA_CONTROL_MODEL_ID = "ishan24/Sana_600M_1024px_ControlNetPlus_diffusers"
SANA_CONTROL_MODEL_REVISION = "c2c790efb0285f3d42dc6d7e73e58c80577cf447"
ANIME_MODEL_ID = "cagliostrolab/animagine-xl-3.1"
ANIME_MODEL_REVISION = "483f0c322568ed13697ed01dd0be07204746d12b"
IP_ADAPTER_MODEL_ID = "h94/IP-Adapter"
IP_ADAPTER_MODEL_REVISION = "9fa34f007c162daaf4b73f84609e414986991d44"
REVIEW_MODEL_ID = "HuggingFaceTB/SmolVLM2-2.2B-Instruct"
REVIEW_MODEL_REVISION = "482adb537c021c86670beed01cd58990d01e72e4"
TORCH_REQUIREMENTS = ("torch==2.11.0+cu128", "torchvision==0.26.0+cu128")
Runner = Callable[..., subprocess.CompletedProcess]


def environment_python(environment: Path = ENVIRONMENT) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def cache_root() -> Path:
    configured = os.environ.get("PYTHON_CACHE_HOME")
    return Path(configured).expanduser() if configured else WORKSPACE / ".model-cache"


def cache_environment() -> dict[str, str]:
    """Return process-local cache routing without persisting workstation paths."""
    root = cache_root().resolve()
    values = dict(os.environ)
    values.setdefault("PYTHON_CACHE_HOME", str(root))
    values.setdefault("HF_HOME", str(root / "huggingface"))
    values.setdefault("TORCH_HOME", str(root / "torch"))
    values.setdefault("TEMP", str(root / "tmp"))
    values.setdefault("TMP", str(root / "tmp"))
    for key in ("HF_HOME", "TORCH_HOME", "TEMP", "TMP"):
        Path(values[key]).mkdir(parents=True, exist_ok=True)
    return values


def requirements_fingerprint(path: Path = REQUIREMENTS) -> str:
    digest = hashlib.sha256(path.read_bytes())
    digest.update(f"{sys.version_info.major}.{sys.version_info.minor}".encode())
    digest.update("\n".join(TORCH_REQUIREMENTS).encode())
    return digest.hexdigest()


def environment_is_current(
    environment: Path = ENVIRONMENT, requirements: Path = REQUIREMENTS,
) -> bool:
    python = environment_python(environment)
    marker = environment / MARKER.name
    if not python.is_file() or not marker.is_file():
        return False
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return state.get("requirements_fingerprint") == requirements_fingerprint(requirements)


def ensure_image_environment(
    *, environment: Path = ENVIRONMENT, requirements: Path = REQUIREMENTS,
    runner: Runner = subprocess.run, offline: bool = False,
) -> Path:
    """Create once and refresh only when isolated requirements change."""
    python = environment_python(environment)
    if environment_is_current(environment, requirements):
        return python
    if offline:
        raise RuntimeError(
            "imageEnv is missing or stale; run `python -m video_creator.cli setup-local-images` online first"
        )
    if not python.is_file():
        runner([sys.executable, "-m", "venv", str(environment)], cwd=WORKSPACE, check=True)
    runner([
        str(python), "-m", "pip", "install", *TORCH_REQUIREMENTS,
        "--index-url", "https://download.pytorch.org/whl/cu128",
    ], cwd=WORKSPACE, check=True, env=cache_environment())
    runner([
        str(python), "-m", "pip", "install", "-r", str(requirements),
    ], cwd=WORKSPACE, check=True, env=cache_environment())
    runner([
        str(python), "-m", "pip", "install", "--no-build-isolation", "-e", str(PROJECT),
    ], cwd=WORKSPACE, check=True, env=cache_environment())
    marker = environment / MARKER.name
    marker.write_text(json.dumps({
        "schema_version": 1,
        "requirements": "videoCreator/requirements-local-images.txt",
        "requirements_fingerprint": requirements_fingerprint(requirements),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "torch_requirements": list(TORCH_REQUIREMENTS),
    }, indent=2) + "\n", encoding="utf-8")
    return python


def prefetch_models(*, python: Path, offline: bool, runner: Runner = subprocess.run) -> None:
    """Cache every pinned visual model once or verify all snapshots offline."""
    for model_id, revision, allow_patterns in (
        (MODEL_ID, MODEL_REVISION, None),
        (SANA_CONTROL_MODEL_ID, SANA_CONTROL_MODEL_REVISION, None),
        (ANIME_MODEL_ID, ANIME_MODEL_REVISION, None),
        (
            IP_ADAPTER_MODEL_ID, IP_ADAPTER_MODEL_REVISION,
            [
                "models/image_encoder/config.json",
                "models/image_encoder/model.safetensors",
                "sdxl_models/ip-adapter_sdxl_vit-h.bin",
            ],
        ),
        (REVIEW_MODEL_ID, REVIEW_MODEL_REVISION, None),
    ):
        command = [
            str(python), "-c",
            (
                "from huggingface_hub import snapshot_download; "
                f"snapshot_download({model_id!r}, revision={revision!r}, "
                f"allow_patterns={allow_patterns!r}, local_files_only={offline!r})"
            ),
        ]
        runner(command, cwd=WORKSPACE, check=True, env=cache_environment())


def setup_local_images(*, offline: bool = False, runner: Runner = subprocess.run) -> Path:
    python = ensure_image_environment(runner=runner, offline=offline)
    prefetch_models(python=python, offline=offline, runner=runner)
    return python


def run_local_images(arguments: Sequence[str], *, runner: Runner = subprocess.run) -> int:
    """Delegate image generation to imageEnv and return to the caller automatically."""
    offline = "--offline" in arguments
    python = setup_local_images(offline=offline, runner=runner)
    environment = cache_environment()
    environment[ACTIVE_FLAG] = "1"
    completed = runner(
        [str(python), "-m", "video_creator.cli", *arguments],
        cwd=PROJECT, env=environment, check=False,
    )
    return int(completed.returncode)
