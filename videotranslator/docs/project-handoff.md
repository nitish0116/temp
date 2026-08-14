# Video Translator project handoff

Last durable update: 2026-08-14. Run `python -m videotranslator history` for the
current branch, working tree, and recent commit IDs rather than copying a possibly
stale commit hash from this document.

## Outcome and current state

The project produces structurally valid draft subtitles with resumable ASR,
alignment, diarization, contextual translation, timing repair, provenance, and
blocking QA. It does not yet produce release-qualified subtitles reliably.

Steps 19 through 22 are implemented:

- reviewed semantic references block known translation defects;
- independent translation agreement validates output contracts and semantic evidence;
- unresolved disagreements can use a bounded stronger-model retry;
- unhealthy agreement backends fail closed and roll back tentative promotions;
- the Japanese, Korean, and Mandarin sample runs were qualified and rejected.

Step 22 measurements:

| Sample | Independent backend result | Pipeline result |
| --- | --- | --- |
| Duty First, Kiss Later | 8/8 probe candidates invalid | rejected; structural QA passed |
| Korean Episode 1 | 6/8 probe candidates invalid | rejected; structural QA passed |
| Linglong's Ferry Episode 24 | 40/114 candidates invalid | rejected; structural QA passed |

Rejected runs retain diagnostic `rejected.srt` files and do not retain
`final.srt`. The subtitles are structurally usable drafts, not publishable final
subtitles.

## Current blocker and next action

`qwen3:1.7b` is not a release-qualified independent translator. The next planned
work is Step 23 in `docs/subtitle-improvement-plan.md`:

1. probe `llama3.1:8b` as the first already-installed baseline;
2. require at least 95% target-language-only valid output across bounded Japanese,
   Korean, and Mandarin probes;
3. require the `cute`, `Seoul`, and Treaty of Shimonoseki references to pass;
4. record latency, RAM/VRAM, disk, offline behavior, license, and cache location;
5. select a replacement only if it passes without weakening QA;
6. rerun Step 22 and the deterministic 72-cue review.

Do not start another episode-wide agreement run until the three probes pass.
Targeted ASR recovery remains necessary where the source transcript itself is
wrong, especially the Mandarin Treaty of Shimonoseki group.

## Environment and model setup

The paths below describe the current workstation; they are not required paths for
another system. On a new system, choose local equivalents and activate that
system's Python environment before running project commands.

- Shared virtual environment: `D:\Git\Projects\.venv`
- Shared cache root: `D:\PythonCaches`
- Hugging Face cache: `D:\PythonCaches\huggingface`
- Ollama models: `D:\Ollama\Models`
- Current workstation profile: NVIDIA Pascal/GTX, Torch CUDA 12.6 profile
- `transformers==4.57.6` was reinstalled after one corrupted package file was found

Read `docs/model-inventory.md` before configuring another workstation. Tokens and
credentials are intentionally not committed; configure Hugging Face access again
on the destination system.

## Verification baseline

The last complete repository suite reported `170 passed, 3 skipped`. The skips are
the cached release smoke checks that require promoted `final.srt` files; rejected
runs deliberately do not have those files. Documentation maintainability checks
also passed after the model inventory was added.

Before changing code, run:

```powershell
git status -sb
python -m videotranslator history
python -m pytest videotranslator\tests -q
```

## Portable handoff workflow

After activating the destination system's Python environment, display the handoff
with `python -m videotranslator history`. To install a prepared complete update,
run `python -m videotranslator history --update-from path/to/handoff-next.md`,
review the resulting Git diff, and commit it. The replacement document must begin
with `# Video Translator project handoff`. A coding assistant may instead edit this
tracked file directly when asked to update the handoff.

## Authoritative references

- `docs/subtitle-improvement-plan.md` — completed work and next numbered step
- `docs/three-sample-release-review.md` — semantic release evidence
- `docs/model-inventory.md` — models, caches, disk planning, and setup
- `docs/architecture.md` — component boundaries
- `docs/schemas.md` and `docs/configuration.md` — artifact and configuration contracts

## What this handoff cannot preserve

Git preserves committed code, documentation, decisions, and test evidence. It does
not preserve private Codex/Copilot/other assistant chats, local model caches,
virtual environments, ignored outputs, credentials, or uncommitted files. If a
conversation contains a durable decision, summarize that decision here before
switching systems; do not commit the raw transcript.
