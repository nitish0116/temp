# Video Translator project handoff

Last durable update: 2026-08-16. Run `python -m videotranslator history` for the
current branch, working tree, and recent commit IDs rather than copying a possibly
stale commit hash from this document.

## Outcome and current state

The project produces structurally valid draft subtitles with resumable ASR,
alignment, diarization, contextual translation, timing repair, provenance, and
blocking QA. It does not yet produce release-qualified subtitles reliably.

Steps 19 through 24 are in the tree:

- reviewed semantic references block known translation defects;
- independent translation agreement validates output contracts and semantic evidence;
- unresolved disagreements can use a bounded stronger-model retry;
- unhealthy agreement backends fail closed and roll back tentative promotions;
- the Japanese, Korean, and Mandarin sample runs were qualified and rejected;
- Step 23 selected `qwen2.5:7b` as the independent agreement backend;
- Step 24 adds opt-in SeamlessM4T-v2 speech-to-English evidence (not the unattended default).

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

Step 24 speech-to-English evidence is implemented and opt-in via
`--speech-translation`. Workstation A prefetched `facebook/seamless-m4t-v2-large`
into its workspace-relative `.model-cache`. The GTX 1050 (4 GiB) cannot hold the
checkpoint; automatic
mode loads it on CPU. A reviewed-defect probe produced independent English for
every probed group, but did not recover `cute`, `Seoul`, or Treaty of Shimonoseki.
Do not enable the route by default. Full coverage needs a ≥10 GiB GPU (Workstation
B). Record: `docs/speech-translation-qualification.md`.

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

Step 24 full coverage completed on the 16 GiB GPU: 367/367 groups returned audio
English, but `cute`, `Seoul`, and Shimonoseki remained non-diagnosable. Step 25
now has a repeatable native-protocol dedicated-MT benchmark. MADLAD-400 3B passed
`cute` and `Seoul` but rendered Shimonoseki as “Treaty of Macau”; NLLB-200 3.3B
preserved `Seoul` but rendered the other fixtures as “lovely” and “Customs
Treaty.” Both remain rejected and the production default is unchanged.

The next action is to qualify a stronger source-grounded dedicated MT candidate
or a general terminology-aware mechanism, then run full three-sample coverage
only after all bounded reviewed fixtures pass.

Step 26 is now wired behind `--multi-route-adjudication`. It runs Seamless,
MADLAD, and Ollama sequentially, writes dedicated-MT and adjudication evidence,
and adds unresolved adjudication to the final promotion gate. A bounded
`qwen2.5:7b` protocol-3 probe verified `cute` but left the compound
Dalsan-ri/Seoul line and Shimonoseki unresolved; notably, its reason named the
correct Treaty of Shimonoseki while its status remained unresolved. This is a
safe rejection and evidence that the 7B adjudicator is not stable enough.

Qwen3 14B Q4_K_M was downloaded into the workspace-relative Ollama cache and
fully offloaded its 9,646,353,939-byte runtime allocation to the 16 GiB GPU. Five
uncached protocol-3 trials per reviewed fixture passed `cute` and Shimonoseki
10/10, but failed the compound Dalsan-ri/Seoul line 5/5: the model consistently
marked a translation verified while omitting the opening Dalsan-ri clause. It is
not release-qualified and the production default remains unchanged. Next, add a
model-independent source-clause and named-entity coverage gate, then repeat the
bounded qualification before any full-episode run. Evidence:
`docs/multi-route-adjudication-qwen3-14b-qualification.json`.

That gate is now implemented and requalified. Across five fresh trials per
fixture, it accepted the ten correct `cute` and Shimonoseki responses, blocked
all five incomplete Dalsan-ri/Seoul responses as `source_clause_omission`, and
accepted no reviewed defect. The next Step 26 action is broader cached sample
coverage with counts for accepted, unresolved, and falsely accepted groups.

Valid three-route coverage is now complete across 367 cached sample groups.
MADLAD returned 367/367 candidates; Qwen3 14B plus the coverage gates accepted
348 and retained 19 unresolved: ten model-declared ambiguities, eight integrity
blocks, and one invalid JSON response. The reviewed `cute` and Shimonoseki cases
were accepted correctly, and Dalsan-ri/Seoul was blocked correctly. The accepted
population is not fully human-labelled, so no broader zero-error claim is made.
All three samples remain non-promotable. Proceed to the Step 27 bounded review
artifact for the 19 unresolved groups and a stratified audit of accepted groups.

Step 27 has started. Three portable manifests under
`videotranslator/outputs/step27-bounded-review` contain all 19 unresolved groups
and 19 verified-readable padded mono WAV clips. Every item binds pending review
to the source media hash, clip hash, time region, source text, evidence package,
adjudication model, and protocol through a versioned approval key. Integrity-
rejected model proposals are retained as review evidence rather than silently
discarded. No human decision has been applied, so promotion remains blocked.
Next, implement approval-key-validated decision ingestion and generate the
stratified audit sample from the 348 automatically accepted groups.

Decision ingestion now validates approval keys against the current document and
evidence, requires reviewer identity plus a timezone-qualified timestamp, and
re-runs deterministic integrity checks before recording `human_verified`
provenance. A separate accepted audit under
`videotranslator/outputs/step27-accepted-audit` contains 24 deterministic
early/middle/late groups (eight per sample) and 24 verified-readable hashed WAV
clips. The 19 unresolved items and 24 accepted audit items are both pending real
human decisions; no generated subtitle has been promoted.

Step 27 review schema v2 is being developed on `videoTranslator` because the
project owner does not speak the sample source languages. It replaces ambiguous
`human_verified` decisions with capability-aware `bilingual_verified`,
`target_language_reviewed`, and `unable_to_verify` states. Only bilingual review
can resolve semantic disagreements; English-only review remains useful evidence
but cannot promote a subtitle.

Step 27A automatic review has started. A model-independent fail-closed gate now
requires reference-free quality, agreement from at least two independent routes,
round-trip semantic preservation, deterministic integrity checks, and a named
passing adversarial calibration. Successful output is `machine_verified`, not
human verification. It is not yet wired to promotion. Next, implement and
qualify the COMETKiwi adapter against reviewed and deliberately corrupted
multilingual fixtures before running it over the 19 unresolved groups. The lazy
adapter is now implemented with shared-cache, offline, batch, device, and
reference-free contract enforcement; model installation, gated-weight prefetch,
and real-score qualification remain pending.

The `qualify-machine-review` CLI now evaluates four reviewed Korean/Mandarin
fixtures and thirteen adversarial corruptions, writes a versioned report, and
fails closed. Offline contract tests pass, but no real COMET score has been
recorded. Next, install `requirements/machine-review.txt`, accept the gated model
terms, prefetch into the configured cache, and run qualification before choosing
or changing any score threshold.

The first real setup attempt on 2026-08-20 installed COMET, then restored the
primary environment after its legacy constraints downgraded NumPy, Protobuf, and
TorchMetrics below pipeline requirements. The full suite still passes. Hugging
Face authentication is valid, but model download returned 403 because this
account has not been granted access to `Unbabel/wmt22-cometkiwi-da`. Run COMET in
a dedicated environment after accepting the model terms. No real qualification
report was produced and machine review remains non-promoting.

The agreed forward architecture is audio-only. OCR and burned-subtitle extraction
are excluded as required, optional, and fallback evidence paths. Step 24 added
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

- Each workstation resolves `.venv` and `.model-cache` relative to its own
  repository workspace root; tracked files must not store absolute host paths.
- Workstation A has the NVIDIA Pascal/GTX, Torch CUDA 12.6 profile.
- Workstation B has an RTX PRO 4000
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

The current workstation suite reports `189 passed, 3 skipped`. The three skips
require cached release artifacts absent from this checkout. The maintainability
scan now excludes the ignored workspace-local `.venv`; targeted translation,
agreement, and speech-translation tests also pass.

Before changing code, run:

```powershell
git status -sb
python -m videotranslator history
python -m pytest videotranslator\tests -q
python -m videotranslator qualify-speech-translation --help
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
- `docs/speech-translation-qualification.md` — Step 24 prefetch, VRAM, latency, and probe evidence
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
