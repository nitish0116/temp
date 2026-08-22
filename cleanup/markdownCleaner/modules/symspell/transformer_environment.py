"""Portable environment and cache paths for transformer OCR validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import MutableMapping


WORKSPACE = Path(__file__).resolve().parents[4]


def shared_cache_root(env: MutableMapping[str, str]) -> Path:
    """Use this workstation's configured cache or the ignored workspace cache."""

    configured = env.get("PYTHON_CACHE_HOME")
    return Path(configured).expanduser() if configured else WORKSPACE / ".model-cache"


def prepare_transformer_environment(
    env: MutableMapping[str, str] | None = None,
) -> MutableMapping[str, str]:
    """Point Hugging Face, Torch, and temporary files at one shared cache."""

    target = os.environ if env is None else env
    root = shared_cache_root(target)
    target.setdefault("PYTHON_CACHE_HOME", str(root))
    target.setdefault("HF_HOME", str(root / "huggingface"))
    target.setdefault("HF_HUB_CACHE", str(Path(target["HF_HOME"]) / "hub"))
    target.setdefault(
        "HUGGINGFACE_HUB_CACHE", str(Path(target["HF_HOME"]) / "hub")
    )
    target.setdefault("TORCH_HOME", str(root / "torch"))
    target.setdefault("TEMP", str(root / "tmp"))
    target.setdefault("TMP", str(root / "tmp"))
    return target


__all__ = ["prepare_transformer_environment", "shared_cache_root"]
