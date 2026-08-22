# Markdown Cleaner project handoff

Last durable update: 2026-08-22.

## Current state

The optional `distilbert/distilroberta-base` boundary validator is enabled and
uses a portable shared-cache lifecycle. Runtime cache resolution honors the
active workstation's `PYTHON_CACHE_HOME`, `HF_HOME`, and `TORCH_HOME`, falling
back to the ignored workspace-root `.model-cache`; tracked code contains no
workstation-specific cache path.

`markdownCleaner/install-transformer.ps1` manages the ignored workspace-root
`ocrTransformerEnv`. It refreshes the environment only when the transformer
requirements fingerprint or Python minor version changes, separates dependency
installation from model prefetch, records the model ID and Apache-2.0 license,
and supports early offline verification.

Device policy is GPU-first. `context_validator.device: auto` selects CUDA when
PyTorch reports it available and falls back to CPU only when CUDA is unavailable
or automatic CUDA model transfer fails. Explicit `cuda` remains fail-closed.
Startup logs report the selected device and GPU name. Missing cached weights may
fail open when `context_validator.fail_open` is enabled.

## Verification

- Focused context-validator suite: 15 passed.
- PowerShell installer syntax: valid.
- Offline cached-model probe on the current workstation selected `cuda`, and
  the first model parameter was resident on `cuda:0`.
- `git diff --check`: passed (line-ending notices only).

## Next action

Run one representative Markdown cleanup and confirm the log reports
`Context validator model loaded on CUDA (...)`; compare SymSpell elapsed time
and reviewed boundary decisions with the prior CPU run.
