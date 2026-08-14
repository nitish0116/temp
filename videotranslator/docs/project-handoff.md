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

Step 23 probe qualification completed on 2026-08-14. `llama3.1:8b` failed two
routine semantic probes. The selected `qwen2.5:7b` replacement passed 20/20
Japanese, 20/20 Korean, and 20/20 Mandarin output-contract and semantic-term
checks, including `cute`, `Seoul`, and Treaty of Shimonoseki. The CLI default now
uses it. Model-specific agreement cache keys invalidate only agreement evidence.

The next action is to restore or regenerate the three Step 22 cached sample
outputs, rerun their episode-wide agreement stage with `qwen2.5:7b`, and repeat
the deterministic 72-cue review. Those three canonical artifacts are not all
present on this workstation, so the release rerun has not started.

Targeted ASR recovery remains necessary where the source transcript itself is
wrong, especially the Mandarin Treaty of Shimonoseki group.

## Environment and model setup

Two workstation profiles are supported through user-level environment variables:

- Workstation A uses `D:\Git\Projects\.venv`, `D:\PythonCaches`, and
  `D:\Ollama\Models`; it has the NVIDIA Pascal/GTX, Torch CUDA 12.6 profile.
- Workstation B uses `videotranslator\.venv` and the ignored
  `C:\Users\z005537p\NitishWork\HM\temp\.model-cache`; it has an RTX PRO 4000
  Blackwell Laptop GPU, 16 GB VRAM, about 65 GB RAM, and Torch CUDA 12.8.
- Workstation B has Ollama `0.32.11`; `qwen2.5:7b` and `llama3.1:8b` occupy
  8.94 GiB total.

Do not replace one profile's paths with the other's. Runtime code prefers the
active workstation's environment and uses an OS-local fallback only when no cache
root is configured.

Read `docs/model-inventory.md` before configuring another workstation. Tokens and
credentials are intentionally not committed; configure Hugging Face access again
on the destination system.

## Verification baseline

The current workstation suite reports `172 passed, 5 skipped`. The five skips
require cached release artifacts absent from this checkout. The maintainability
scan now excludes the ignored workspace-local `.venv`; targeted translation and
agreement tests also pass (`48 passed`).

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
