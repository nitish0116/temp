"""Select an available compute device consistently across pipeline stages."""

from __future__ import annotations

import torch


def parse_cuda_architecture(architecture: str) -> tuple[int, int]:
    """Convert Torch architecture names such as ``sm_61`` into ``(6, 1)``."""
    digits = architecture.removeprefix("sm_")
    return int(digits[:-1]), int(digits[-1])


def cuda_architecture_supported() -> bool:
    """Return whether this Torch wheel targets the installed GPU generation."""
    if not torch.cuda.is_available():
        return False
    capability = torch.cuda.get_device_capability()
    compiled = [
        parse_cuda_architecture(arch)
        for arch in torch.cuda.get_arch_list()
        if arch.startswith("sm_")
    ]
    return bool(compiled) and capability >= min(compiled)


def resolve_device(requested: str | None = "auto") -> str:
    """Prefer CUDA only when the installed Torch wheel supports the GPU."""
    choice = (requested or "auto").lower()
    if choice not in {"auto", "cuda", "cpu"}:
        raise ValueError(f"Unsupported device {requested!r}; use auto, cuda, or cpu")
    if choice == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but CUDA-enabled PyTorch is unavailable")
        if not cuda_architecture_supported():
            capability = ".".join(map(str, torch.cuda.get_device_capability()))
            raise RuntimeError(
                f"CUDA was requested, but this PyTorch wheel does not support "
                f"GPU compute capability {capability}"
            )
        return "cuda"
    return "cuda" if choice == "auto" and cuda_architecture_supported() else "cpu"


def whisper_compute_type(device: str) -> str:
    """Return a stable faster-whisper precision for the selected device."""
    if device != "cuda":
        return "int8"
    return "int8_float32" if torch.cuda.get_device_capability() < (7, 0) else "float16"
