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

## Semantic visual review

- Model: `HuggingFaceTB/SmolVLM2-2.2B-Instruct`
- Revision: `482adb537c021c86670beed01cd58990d01e72e4`
- License: Apache-2.0
- Purpose: compare generated references with source-evidenced visual briefs
- Environment and cache: shared `imageEnv` and `.model-cache/huggingface`

The setup command prefetches both Sana and SmolVLM2. Offline setup succeeds only
when all exact revisions are present, ensuring the automatic reviewer cannot be
silently skipped when internet access is unavailable.

## Sana ControlNet reference conditioning

- Model: `ishan24/Sana_600M_1024px_ControlNetPlus_diffusers`
- Revision: `c2c790efb0285f3d42dc6d7e73e58c80577cf447`
- Runtime: shared `imageEnv`; weights: shared ignored `.model-cache/huggingface`
- Purpose: condition shot generation on edge maps derived from promoted canonical
  character references. The manifest records conditioning as a hard expansion gate.
