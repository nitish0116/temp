"""Select an available compute device consistently across pipeline stages."""

from __future__ import annotations

import torch


def resolve_device(requested: str | None = "auto") -> str:
    """Prefer CUDA for ``auto`` and otherwise return CPU."""
    choice = (requested or "auto").lower()
    if choice not in {"auto", "cuda", "cpu"}:
        raise ValueError(f"Unsupported device {requested!r}; use auto, cuda, or cpu")
    if choice == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but CUDA-enabled PyTorch is unavailable")
        return "cuda"
    return "cuda" if choice == "auto" and torch.cuda.is_available() else "cpu"


def whisper_compute_type(device: str) -> str:
    """Return a stable faster-whisper precision for the selected device."""
    return "float16" if device == "cuda" else "int8"
