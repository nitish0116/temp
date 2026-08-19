"""Select an available compute device consistently across pipeline stages."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import torch


MINIMUM_OLLAMA_GPU_MEMORY_BYTES = 8 * 1024**3
MINIMUM_SEAMLESS_GPU_MEMORY_BYTES = 10 * 1024**3
T = TypeVar("T")


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


def ollama_gpu_available(
    requested: str | None = "auto",
    minimum_memory_bytes: int = MINIMUM_OLLAMA_GPU_MEMORY_BYTES,
) -> bool:
    """Select Ollama GPU offload when CUDA is supported and VRAM is reasonable.

    Automatic mode requires at least 8 GiB of VRAM. Explicit ``cuda`` bypasses
    the memory threshold but still requires a CUDA architecture supported by the
    installed Torch profile; explicit ``cpu`` always disables offload.
    """
    choice = (requested or "auto").lower()
    if choice == "cpu":
        return False
    selected = resolve_device(choice)
    if selected != "cuda":
        return False
    if choice == "cuda":
        return True
    return torch.cuda.get_device_properties(0).total_memory >= minimum_memory_bytes


def large_model_cuda_available(
    requested: str | None = "auto",
    minimum_memory_bytes: int = MINIMUM_SEAMLESS_GPU_MEMORY_BYTES,
) -> bool:
    """Use CUDA for multi-gigabyte models only when VRAM can hold them.

    Example:: a 4 GiB GTX 1050 stays on CPU for SeamlessM4T-v2, while a 16 GiB
    RTX PRO 4000 loads the same checkpoint on GPU.
    """
    return ollama_gpu_available(requested, minimum_memory_bytes=minimum_memory_bytes)


def whisper_compute_type(device: str) -> str:
    """Return a stable faster-whisper precision for the selected device."""
    if device != "cuda":
        return "int8"
    return "int8_float32" if torch.cuda.get_device_capability() < (7, 0) else "float16"


def torch_dtype_for_device(device: str):
    """Return a GPU-first floating type, using float32 only on CPU.

    Example:: an Ampere GPU uses bfloat16, Pascal uses float16, and CPU stays
    on float32 so SeamlessM4T does not silently quantize on the fallback path.
    """
    if device != "cuda":
        return torch.float32
    return torch.bfloat16 if torch.cuda.get_device_capability() >= (8, 0) else torch.float16


def is_cuda_out_of_memory(error: BaseException) -> bool:
    """Detect CUDA allocation failures across Torch exception types.

    Example:: both ``torch.cuda.OutOfMemoryError`` and a RuntimeError whose
    message contains ``out of memory`` request a CPU fallback.
    """
    if isinstance(error, getattr(torch.cuda, "OutOfMemoryError", ())):
        return True
    return "out of memory" in str(error).casefold()


def release_cuda_cache() -> None:
    """Free cached CUDA blocks after an explicit model move or unload.

    Example:: calling this after ``model.to("cpu")`` makes the next GPU load
    less likely to fail on leftover allocator fragments.
    """
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def run_preferring_cuda(
    operation: Callable[[str], T],
    requested: str | None = "auto",
) -> tuple[T, str]:
    """Run ``operation(device)`` on CUDA when supported, otherwise on CPU.

    Example:: a SeamlessM4T load that raises CUDA OOM retries once on CPU and
    returns the CPU result plus ``\"cpu\"``.
    """
    preferred = resolve_device(requested)
    try:
        return operation(preferred), preferred
    except (RuntimeError, torch.cuda.OutOfMemoryError) as error:
        if preferred != "cuda" or not is_cuda_out_of_memory(error):
            raise
        release_cuda_cache()
        return operation("cpu"), "cpu"
