# Video Translator project handoff

Last durable update: 2026-08-15. Run `python -m videotranslator history` for the
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

The Japanese Step 22 rerun completed on Workstation B with `qwen2.5:7b`. Audio,
274/274 aligned ASR segments, 7-speaker diarization, and all three recovery
profiles completed. Qwen remained healthy (1.4-2.8% invalid candidates), but the
profiles retained 55, 55, and 60 unresolved agreement groups. The run correctly
produced `rejected.srt` and retained no `final.srt`.

The next action is to regenerate and rerun the Korean sample with its reviewed
`cute` and `Seoul` references, then Mandarin with the Treaty of Shimonoseki
reference. Repeat the deterministic 72-cue review only if all three pass. Secure
downloads on Windows corporate networks now use the OS trust store in both
Pyannote diarization and missing-speech recovery.

The agreed forward architecture is audio-only. OCR and burned-subtitle extraction
are excluded as required, optional, and fallback evidence paths. Step 24 adds
independent direct speech-to-English evidence with SeamlessM4T-v2 while retaining
Whisper `large-v3` for source transcription and timing. Step 25 benchmarks a
dedicated primary text translator, beginning with MADLAD-400 3B and NLLB-200 3.3B,
and removes Qwen2.5 0.5B from release-qualified translation. Step 26 combines the
speech, text-MT, and contextual-Qwen routes in source-grounded adjudication, with
Qwen3 14B as the first stronger retry candidate to qualify. Step 27 adds durable,
bounded human approval for only the remaining unresolved groups. `final.srt` is
permitted only after all groups are resolved and structural QA passes. Step 28
then benchmarks and validates dubbing from that approved subtitle artifact.

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
- Ollama device policy defaults to automatic GPU offload with at least 8 GiB VRAM.
  Workstation B loads Qwen fully in 4.75 GB VRAM and measured about 0.20-second
  warmed median probe latency; explicit `cuda` and `cpu` overrides remain available.

Do not replace one profile's paths with the other's. Runtime code prefers the
active workstation's environment and uses an OS-local fallback only when no cache
root is configured.

Read `docs/model-inventory.md` before configuring another workstation. Tokens and
credentials are intentionally not committed; configure Hugging Face access again
on the destination system.

## Verification baseline

The current workstation suite reports `173 passed, 5 skipped`. The five skips
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
