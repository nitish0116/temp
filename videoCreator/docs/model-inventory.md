# Local model inventory

## Image generation

- Model: `Efficient-Large-Model/Sana_1600M_1024px_diffusers`
- Revision: `ac0da2ff55fbe434795be0dce883042e4d49e2fc`
- License: Apache-2.0
- Purpose: offline 1024-pixel image and character-reference generation
- Environment: workspace-root `imageEnv` (ignored by Git)
- Weights: workspace-root `.model-cache/huggingface` (ignored by Git)

Run `python -m video_creator.cli setup-local-images` from `videoCreator` once
while online. Later runs automatically delegate Sana work to `imageEnv`; shell
activation is unnecessary. Use `setup-local-images --offline` to verify that the
environment and pinned model revision are available without network access.

The dependency fingerprint includes the Python minor version, pinned PyTorch
profile, and `requirements-local-images.txt`. The environment is rebuilt only
when that fingerprint changes. `PYTHON_CACHE_HOME` can relocate the shared cache;
the manager also routes Hugging Face and PyTorch caches beneath it.

The deterministic fixture provider is retained for tests only. Production Sana
generation fails early if CUDA, dependencies, or cached weights are unavailable;
it does not silently replace production output with fixture blobs.
