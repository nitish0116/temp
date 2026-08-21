# Local model inventory

## Long-context scene generation

- Model: `Efficient-Large-Model/Sana_1600M_1024px_diffusers`
- Revision: `ac0da2ff55fbe434795be0dce883042e4d49e2fc`
- License: Apache-2.0
- Purpose: cached long-context Sana components and unconditioned 1024-pixel generation
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

The autonomous production route uses the compatible 600M Sana ControlNet checkpoint
below for conditioned scenes. Both Sana pipelines support an explicit 300-token
prompt window. The deterministic fixture provider is retained for tests only.

## Anime character references

- Model: `cagliostrolab/animagine-xl-3.1`
- Revision: `483f0c322568ed13697ed01dd0be07204746d12b`
- License: CreativeML Open RAIL++-M
- Purpose: single-view canonical anime character portraits

Animagine uses SDXL's CLIP encoder. Reference prompts are compiled conservatively,
validated again with the actual tokenizer, and must fit its 77-token context.
Anatomy, multi-view, and nonhuman exclusions live in the separate negative prompt
so they cannot be silently truncated from the positive prompt.

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
  character references. Only the face/hair region is placed into the control map,
  avoiding full-body pose copying. Scene prompts use up to 300 tokens. The manifest
  records conditioning as a hard expansion gate.

## Kokoro narration

- Model: `hexgrad/Kokoro-82M`
- Revision: `fbba31e67ad83eb66394c926627e99d35abeb087`
- License: Apache-2.0
- Runtime: ignored workspace-root `audioEnv`; weights: shared `.model-cache/huggingface`
- Voice: `af_bella` at 0.92 speed
- Setup: `python -m video_creator.cli setup-local-audio` once online, then
  `--offline` verifies the complete model, voice, tokenizer, and runtime cache.
