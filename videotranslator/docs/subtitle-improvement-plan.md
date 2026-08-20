# Subtitle translation and quality improvement plan

## Purpose

This is the authoritative, ordered plan for improving subtitle creation and
translation. It combines the findings from the first complete episode run with
the principles from the original architecture proposal, now consolidated here.

The central architectural decision is that structured source-language timed text
is the canonical hand-off between ASR, translation, subtitle export, TTS, and
dubbing. SRT and ASS are exports, not internal source data. Work proceeds one
verified step at a time and reuses existing artifacts unless metrics prove that
an upstream stage must be rerun.

The workflow must remain deterministic and headless. It may produce `final.srt`
only after QA passes. Otherwise, it retains `rejected.srt`, reports, provenance,
and a nonzero exit code without requiring coding-assistant intervention.

## Regression baseline

Source video:

`sample Data/Duty First, Kiss Later (2026) Episode 1 English Subbed - MyAsiantv.mp4`

Baseline report:

`outputs/duty-first-kiss-later-episode-1-subtitles/subtitle-pipeline-report.json`

The pipeline rejected all three profiles and selected attempt 2, `balanced`.

| Metric | Baseline | Required |
| --- | ---: | ---: |
| Subtitle cues | 443 | N/A |
| Source event coverage | 99.72% | 98% |
| Source time coverage | 91.32% | 95% |
| Diarized turn coverage | 89.61% | 90% |
| Diarized time coverage | 91.29% | 90% |
| Cues shorter than 0.5 seconds | 19 | 0 |
| Cues above 20 characters/second | 25 | 0 |
| Cues longer than 12 seconds | 1 | 0 |
| Longest cue | 35.756 seconds | 12 seconds maximum |
| Maximum reading speed | 73.17 characters/second | 20 maximum |

Almost every speech event was found. The immediate failures are timing,
readability, source-time coverage, and a small diarized-turn deficit. Translation
quality also has an architectural risk because the current implementation
translates cues independently and drops useful metadata.

## Required representations

The pipeline must preserve three distinct representations:

1. **Raw ASR**: direct recognition output, confidence, model, language detection,
   raw segments, and word timestamps.
2. **Clean semantic transcript**: normalized source text grouped into complete
   utterances or thoughts, independent of display cue boundaries.
3. **Canonical timed text**: source timing, word timing, speaker identity,
   semantic-group membership, source/target text, confidence, and provenance.

A canonical segment requires stable IDs and should support at least:

```json
{
  "id": "cue-0021",
  "semantic_group_id": "group-0012",
  "source_cue_ids": [21, 22],
  "start": 75.2,
  "end": 79.3,
  "source_text": "Source-language utterance",
  "translated_text": "Target-language utterance",
  "speaker": "speaker_1",
  "words": [],
  "confidence": {},
  "provenance": []
}
```

Schema changes must be versioned. CLI and JSON consumers must either migrate
together or retain a compatibility adapter.

## Target pipeline

```text
Video/audio
  -> speech detection
  -> raw word-level source-language ASR
  -> direct speech-to-target translation (independent audio evidence)
  -> forced alignment and targeted ASR recovery
  -> speaker diarization and reconciliation
  -> clean semantic transcript
  -> canonical source-language timed text
  -> dedicated source-text machine translation
  -> multi-route, source-grounded contextual adjudication
  -> verified or explicitly unresolved semantic groups
  -> bounded human approval for unresolved groups
  -> mapping into display subtitle cues
  -> timing and readability optimization
  -> independent QA and promotion gate
  -> SRT/ASS export
  -> optional TTS, audio validation, and dubbing from approved canonical data
```

ASR segments are timing evidence, not final subtitle boundaries. Subtitle cues
are display units, not necessarily translation units.

The general pipeline is audio-only. Burned-subtitle detection and OCR are
explicitly out of scope and must not become required, optional, or fallback
evidence paths.

## Ordered implementation steps

### 1. Freeze the regression baseline

- Preserve the selected attempt and its reports.
- Create a compact, non-copyrighted fixture representing long, short, fast,
  overlapping, missing-coverage, multi-cue sentence, and speaker-turn cases.
- Store baseline metrics in test metadata.

Exit: tests reproduce the known failures without models, network, or GPU.

### 2. Define and version the canonical timed-text schema

- Define raw-ASR, clean-transcript, semantic-group, canonical-cue, and translated
  fields.
- Require stable IDs, source mappings, word timing, speaker, confidence, and
  provenance.
- Add schema validation and compatibility adapters for existing JSON artifacts.
- Keep credentials and machine-specific paths out of portable data.

Exit: representative old and new artifacts validate and round-trip without losing
text, timing, speaker, or provenance.

### 3. Preserve metadata through every stage

- Stop translation and repair from reducing a segment to only start/end/text.
- Propagate IDs, source cue IDs, semantic group ID, speaker, words, confidence,
  and provenance.
- Define which stage owns each field and which fields are immutable.

Exit: an automated lineage test traces every exported cue back to source words
and a speaker or an explicit unknown-speaker value.

### 4. Build the clean semantic transcript

- Normalize raw ASR without changing meaning.
- Join sentence continuations split by ASR or subtitle boundaries.
- Split genuine multi-sentence ASR spans.
- Use punctuation, pauses, speaker changes, word timestamps, and language-aware
  rules; never join across an incompatible speaker boundary.
- Preserve the original raw text and mapping for audit.

Exit: semantic groups represent coherent utterances and retain complete mappings
to raw ASR and canonical timing.

### 5. Add contextual semantic-group translation

- Translate semantic groups rather than individual display cues.
- Provide a bounded context window, initially three preceding and three following
  groups, while requesting output only for the current group.
- Preserve names, numbers, terminology, tone, and speaker context.
- Translate directly from the detected source language to the target language
  when the selected model supports the pair; do not require English as a pivot.
- Cache translations by source text, language pair, model, prompt/configuration,
  and context signature.

Exit: multi-cue sentences translate as one thought, direct language pairs work,
and repeated headless runs are deterministic or explicitly versioned.

### 6. Add translation-integrity QA and retry

- Check completeness, semantic similarity, names, numbers, repetitions,
  hallucinations, punctuation, and source/target information density.
- Retry a failed group with constrained instructions or an approved fallback
  model.
- If retry fails, re-segment the semantic group and translate its subgroups.
- Never delete source meaning or lower thresholds merely to pass.

Exit: every semantic group has a passing translation or a machine-readable
rejection reason.

### 7. Map translated groups into display cues

- Treat the complete semantic group as the translation unit and its constituent
  timing windows as subtitle-layout inputs.
- Segment translated text at target-language grammatical boundaries.
- Allocate text across source timing using word density, pauses, speaker turns,
  available duration, line length, and reading speed.
- Keep a reversible mapping from target cues to semantic and source groups.

Exit: no translated sentence is arbitrarily broken by the old source cue count,
and all translated text is mapped exactly once.

### 8. Add comprehensive timing regression tests

Cover long-cue splitting, short-cue extension/merge, reading speed, line length,
overlap, invalid duration, chronological order, text preservation, source and
diarization coverage, idempotence, and bounded convergence.

Exit: tests fail for the intended reasons before each repair rule is implemented.

### 9. Split excessively long cues

Split cues above 12 seconds using word pauses, sentence punctuation, clause
punctuation, then balanced target-language boundaries. Retain all text and stay
inside the semantic group's supported timing envelope.

Exit: the 35.756-second baseline cue is repaired without overlap or text loss.

### 10. Repair very short cues

- First extend into verified adjacent silence.
- Otherwise merge with a compatible neighbor when speaker, semantic group, gap,
  duration, line length, and reading speed remain valid.
- Never consume another speaker's time or reorder dialogue.
- Report objectively irreparable cues.

Exit: every safely repairable sub-0.5-second cue passes.

### 11. Optimize reading speed and layout

- Borrow verified silence, split at target-language boundaries, and rebalance
  adjacent cues within one semantic group.
- Enforce duration, characters per second, maximum characters, maximum two lines,
  and language-aware line breaking.
- Request a meaning-preserving constrained translation only after timing and
  segmentation cannot fit the text.

Exit: no cue exceeds 20 characters per second or layout limits without a recorded
irreparable reason.

### 12. Preserve complete source speech envelopes

Use VAD, aligned words, neighboring source events, and diarization turns so
recovered cues cover supported speech rather than only recognized word interiors.
Apply aggressive recovery only to independently supported speech. Reject likely
music, noise, low-confidence repetition, and hallucination.

Exit: source event coverage is at least 98% and source time coverage at least 95%
without overlaps or material precision loss.

### 13. Reconcile unmatched diarization turns

Attach a small unmatched turn only when the temporal gap is bounded, no cue
conflicts, speaker continuity is compatible, and independent speech evidence
supports it. Preserve unknown or overlapping speakers explicitly rather than
inventing an identity.

Exit: diarized turn and time coverage are both at least 90%.

### 14. Add bounded iterative optimization and provenance

Run segmentation, splitting, extension, merging, and speed/layout optimization in
a stable order. Repeat only while an objective score improves, with a small pass
limit. Record rule, cue IDs, old/new values, reason, and score effect.

Exit: execution converges, is idempotent, and every mutation is explainable.

### 15. Export SRT and ASS from canonical data

- Generate SRT and optional ASS only after canonical target cues pass validation.
- Keep export formatting out of translation and timing logic.
- Preserve speaker/style metadata in ASS where supported.
- Validate exported timestamps, ordering, encoding, and content against canonical
  data.

Exit: export/import comparison detects no lost text or timing changes within the
format's precision.

### 16. Use canonical timed text for TTS and dubbing

Provide translated text, timing window, speaker, semantic group, and provenance
to voice selection and synthesis. Maintain one persistent voice per speaker,
enforce non-overlap, and use constrained regeneration rather than arbitrary speed
changes when speech does not fit.

Exit: subtitle and dubbing paths consume the same approved canonical artifact.

### 17. Reprocess existing episode artifacts incrementally

Migrate and reuse existing transcription, alignment, diarization, recovery, and
translation artifacts. Run the cheapest changed stages first. Rerun translation
for changed semantic groups only; rerun ASR only when canonical source evidence
is demonstrably missing.

Exit: the new report compares every metric with the frozen baseline and records
the reason for each expensive rerun.

### 18. Complete headless orchestration and documentation

- Add every new stage, schema version, cache key, timeout, retry, and exit code to
  the orchestrator and CLI documentation.
- Preflight credentials, writable caches, model availability, disk space, GPU
  compatibility, and resumable artifacts.
- Record automatic fallbacks without secrets.
- Retain rejected candidates and never promote them as final.

Exit: a clean scheduled process can finish or fail safely with sufficient
machine-readable diagnostics and no coding-assistant decision.

### 19. Add reviewed semantic-reference promotion gates

- Accept an optional versioned sidecar containing reviewed timestamps, required
  terms, and forbidden terms.
- Resolve each timestamp to its complete semantic group rather than checking only
  one display cue.
- Write an auditable report for every attempt and block `final.srt` when a cue is
  missing, a required term is absent, or a forbidden substitution is present.
- Run the existing three-sample review manifest through the executable gate.

Status: implemented. The gate correctly rejects the confirmed `cute`/`freak`,
`Seoul`/`Seattle`, and Treaty of Shimonoseki failures.

Exit: independently reviewed dialogue can participate in unattended promotion
without hard-coded episode logic.

### 20. Add independent translation-agreement QA

- Generate an independent direct translation with the approved fallback backend;
  do not ask the primary model to grade its own answer.
- Compare translations at semantic-group granularity using named entities,
  numbers, polarity/negation, information density, and multilingual semantic
  similarity.
- Treat lexical variation as valid when meaning agrees; flag material disagreement
  rather than requiring identical wording.
- Cache the independent candidate and agreement evidence by source text, language
  pair, models, and QA protocol version.

Exit: the three verified semantic failures are detected without supplying their
manual reference sidecar, while valid paraphrases remain accepted.

Status: implemented, but the configured `qwen3:1.7b` independent backend did not
qualify. Output-contract checks reject source-language echoes, prompt leakage, and
model chatter before promotion. A bounded health probe stops an unhealthy backend
without generating an entire episode.

### 21. Retry semantic disagreements with a stronger model

- Retry only failed semantic groups with a stronger translation-capable model and
  explicit preservation constraints.
- Provide bounded surrounding context, detected names/numbers, both candidate
  translations, and the disagreement reasons without instructing the model to
  copy either candidate blindly.
- Re-run independent agreement and deterministic integrity QA after retry.
- Reject an unresolved group; never select a candidate only because it is fluent
  or shorter.

Exit: every promoted semantic group either passes the first independent agreement
check or has a recorded stronger-model retry that passes the same gate.

Status: implemented. Stronger-model retries are cached and bounded. They are not
launched when the independent backend itself fails health checks, because that
would turn a backend failure into an episode-sized retry job.

### 22. Rerun and qualify all three sample videos

- Invalidate only translation and downstream caches affected by the new QA
  protocol; reuse accepted ASR/alignment/diarization artifacts initially.
- Rerun the Japanese, Korean, and Mandarin samples from semantic translation
  through export.
- Repeat the deterministic 24-cue review for each output and compare all available
  burned English references.
- If corrupted source-language ASR is the cause, mark the affected groups for
  targeted ASR/alignment recovery before translating them again.

Exit: structural QA passes, no verified semantic-reference defect remains, the
independent agreement report passes every group, and the 72-cue review finds no
material error. Only then are the sample subtitles considered reasonably usable.

Status: executed on 2026-08-14; exit criteria not met. Structural QA passes, but
all three subtitle artifacts are rejected by independent-backend health checks:

| Sample | Independent evidence | Agreement result |
| --- | --- | --- |
| Duty First, Kiss Later | 8/8 probe candidates invalid (100%) | 269/269 groups blocked |
| Korean Episode 1 | 6/8 probe candidates invalid (75%) | 130/130 groups blocked |
| Linglong's Ferry Episode 24 | 40/114 candidates invalid (35.09%) | 114/114 groups blocked |

No rejected run retains `final.srt`. The prior 72-cue findings and semantic
reference defects remain controlling evidence. A new promotion review is deferred
until a replacement independent backend passes the three language probes.

### 23. Qualify and replace the independent translation backend

- Use `llama3.1:8b` as the first already-available baseline, then evaluate other
  translation-capable models only if it misses the resource or quality gates.
- Run bounded Japanese, Korean, and Mandarin probes before any episode-wide run.
- Require valid target-language-only output for at least 95% of probe groups; no
  prompt leakage, source echo, empty response, or model commentary is promotable.
- Require all reviewed names, places, numbers, polarity, and semantic references to
  pass, including `cute`, `Seoul`, and Treaty of Shimonoseki fixtures.
- Measure peak RAM/VRAM, median and worst group latency, model size, cache location,
  license/terms, and offline behavior on the target workstation.
- Record the selected model and installation command in `docs/model-inventory.md`,
  change the CLI default only after qualification, and invalidate agreement caches
  without discarding accepted ASR, alignment, diarization, or primary translations.
- Rerun Step 22 and the 72-cue review only after all three probes pass.

Exit: one independent model passes all three language probes, stays within the
documented workstation resource budget, runs headlessly from shared caches, and
allows Step 22 to be repeated without weakening any QA threshold.

Status: probe qualification completed on 2026-08-14. `llama3.1:8b` passed the
three mandatory semantic references but failed two routine semantic probes
(`station` in Korean and Mandarin). `qwen2.5:7b` passed 20/20 Japanese, 20/20
Korean, and 20/20 Mandarin output-contract and reviewed-term checks. Median group
latency was 1.11-1.13 seconds and worst latency was 5.21 seconds on CPU. Automatic
GPU offload was then enabled for supported GPUs with at least 8 GiB VRAM. On the
RTX PRO 4000, Qwen used 4.75 GB VRAM, retained 60/60 probe quality, and achieved
about 0.20-second warmed median latency. The Apache-2.0 model runs headlessly from
the workspace cache, and the CLI default now selects it. The next action is to
restore the three cached sample outputs, rerun Step 22, and repeat the 72-cue
review; those artifacts are not all present in this checkout.

### 24. Add independent direct speech-to-English evidence

- Add SeamlessM4T-v2 as a bounded audio-to-English route operating on the same
  speech regions used by canonical ASR.
- Preserve Whisper `large-v3` as the source-language transcript and timing route;
  do not replace aligned source evidence with translated speech output.
- Cache direct speech translations by audio-region hash, language pair, model,
  decoding configuration, and protocol version.
- Compare direct speech translation with text-derived translation at semantic-group
  granularity. A corrupt source transcript must be diagnosable because the audio
  route does not consume Whisper text.
- Measure language coverage, VRAM/RAM, latency, offline behavior, license, and
  Japanese/Korean/Mandarin performance before enabling the route by default.

Exit: every sample group has independent audio-derived English evidence or an
explicit unsupported/failed status; source-ASR defects no longer force every
translation candidate to share the same wrong input.

Status: implemented as an opt-in route and probe-qualified on Workstation A.
`--speech-translation` collects SeamlessM4T-v2 English evidence for every
semantic-group audio window, caches it by audio-region hash, and compares it with
the text-derived translation. It does not replace Whisper source text or primary
translations. Workstation A (GTX 1050 4 GiB) prefetched the CC-BY-NC checkpoint
into its workspace-relative `.model-cache` and falls back to CPU. A reviewed-defect probe produced
independent English for `cute`, `Seoul`, and Treaty of Shimonoseki groups, but
did not recover the required terms. Full three-sample coverage and default
enablement remain blocked until a ≥10 GiB GPU run recovers those terms or Step 26
adjudicates this evidence. See `docs/speech-translation-qualification.md`.

### 25. Replace the release primary text translator

- Remove `Qwen/Qwen2.5-0.5B-Instruct` from release-qualified translation; retain it
  only as a diagnostic or constrained fallback until removal is safe.
- Benchmark dedicated MT candidates, starting with MADLAD-400 3B and NLLB-200
  3.3B, on the reviewed Japanese, Korean, and Mandarin fixtures.
- Require names, numbers, polarity, information density, and the `cute`, `Seoul`,
  and Treaty of Shimonoseki references to pass without prompt-specific episode
  logic.
- Run large models sequentially and unload inactive models so the 16 GiB GPU is
  not occupied by ASR, direct speech translation, MT, and Ollama simultaneously.
- Select by fixture quality first, then resource use and latency. Do not promote a
  model merely because it improves aggregate similarity.

Exit: one dedicated text MT model passes all reviewed language fixtures and
outperforms the 0.5B primary without weakening integrity or provenance gates.

Status: in progress. The native-protocol benchmark command is implemented.
MADLAD-400 3B passed `cute` and `Seoul` but produced “Treaty of Macau”;
NLLB-200 3.3B preserved `Seoul` but produced “lovely” and “Customs Treaty.” Both
candidates remain rejected, so the exit criterion is not met. See
`docs/text-translation-qualification.md`.

### 26. Replace pairwise agreement with multi-route adjudication

- Compare three independent evidence routes: direct speech-to-English, dedicated
  translation of source ASR, and contextual Qwen translation/adjudication.
- Give the adjudicator bounded source context, candidate translations, detected
  names/numbers, speaker information, and disagreement reasons. It may return a
  corrected candidate or `unresolved`; it must never be forced to choose.
- Reposition `qwen2.5:7b` as contextual verification rather than the sole
  independent translator.
- Retire `llama3.1:8b` as the stronger retry because it failed Step 23 semantic
  qualification. Qualify a stronger replacement, beginning with a quantized
  Qwen3 14B if it fits the workstation budget.
- Accept lexical paraphrases while continuing to block material differences in
  meaning, names, numbers, and polarity.

Exit: every automatically accepted group has source-grounded support from
independent audio and text routes; disputed groups remain explicitly unresolved.

Status: in progress. The opt-in pipeline now collects Seamless speech evidence,
MADLAD text-MT evidence, and the primary translation sequentially, unloads the
large in-process models, and submits a strict source-grounded JSON request to
`qwen2.5:7b`. Invalid or unresolved responses retain the original and block
promotion. Protocol-3 probes verify `cute` but leave the compound Dalsan-ri/Seoul
line and Shimonoseki unresolved, even when the model's reason identifies the
correct treaty. Qwen3 14B was then run through five uncached trials per reviewed
fixture with full GPU offload. It passed `cute` and Shimonoseki in all ten trials,
but in all five compound-line trials it marked a translation verified after
omitting the opening Dalsan-ri clause. It is therefore not release-qualified.
The next action is a model-independent source-clause and named-entity coverage
gate that rejects such false verification before broader qualification; do not
tune fixture-specific answers into the prompt. See
`docs/multi-route-adjudication-qwen3-14b-qualification.json`.

The coverage gate is now implemented. A fresh five-trial rerun accepted all ten
correct `cute` and Shimonoseki responses and blocked all five incomplete
Dalsan-ri/Seoul responses as `source_clause_omission`; it accepted no known-bad
response. Qwen3 14B remains unable to resolve every reviewed fixture, but its
observed false verification now fails closed. Next, run broader cached sample
coverage and measure accepted, unresolved, and falsely accepted groups before
closing Step 26.

Three-sample coverage is complete with valid three-route evidence. MADLAD
produced 367/367 dedicated candidates; Qwen3 14B plus deterministic integrity
gates accepted 348 groups and retained 19 as unresolved. Those 19 comprise ten
model-declared ambiguities, eight integrity-gate blocks, and one invalid JSON
response. The reviewed `cute` and Shimonoseki regressions were correctly
accepted, while the incomplete Dalsan-ri/Seoul result was correctly blocked.
Zero false accepts applies only to those reviewed regressions: the other accepted
groups do not all have human semantic labels. Every sample therefore remains
non-promotable. Next, create the Step 27 bounded review artifact for the 19
unresolved groups and draw a stratified audit sample from accepted groups before
changing defaults.

### 27. Add durable bounded human resolution

- Emit a compact review artifact for unresolved groups containing the audio clip,
  source ASR, all English candidates, surrounding dialogue, speaker, names,
  numbers, confidence, and proposed correction.
- Store approvals as versioned provenance tied to the source/audio hash and model
  protocol; never encode approval as an untracked conversation or blanket bypass.
- Support terminal states `multi_route_consensus`, `adjudicator_verified`,
  `reviewed_reference_verified`, `bilingual_verified`, and `unresolved`.
- Record reviewer language capabilities. An English-only reviewer may submit
  `target_language_reviewed` or `unable_to_verify`, but neither state resolves a
  semantic disagreement or permits promotion.
- Permit `final.srt` only when structural QA passes and no group is unresolved.
- Invalidate only affected approvals when source text, audio regions, translation
  models, or QA protocol changes.

Exit: a reviewer can resolve only the bounded disagreement set, rerun promotion,
and produce an auditable `final.srt` without weakening unattended QA.

Status: in progress. Portable pending-review manifests now exist for all 19
unresolved groups (10 Japanese, 3 Korean, and 6 Mandarin) under
`videotranslator/outputs/step27-bounded-review`. Each item contains source and
context, all evidence candidates, the rejected proposal when available,
timestamps, speaker/confidence fields, observable names/numbers, a padded mono
audio clip, media and clip hashes, and a versioned approval key. No review
decision has been applied. Next, implement validated decision ingestion and draw
the stratified semantic audit sample from accepted groups.

Approval-key-validated decision ingestion is now implemented: it rejects stale
evidence, mismatched keys, unsupported states, missing reviewer identity or
timezone, and corrected translations that fail deterministic integrity checks.
The accepted-group audit is also generated under
`videotranslator/outputs/step27-accepted-audit`: eight deterministic
early/middle/late groups per sample, 24 total, each with the same portable hashed
evidence and a verified-readable audio clip. Both the 19-item correction set and
24-item semantic audit remain pending human decisions.

Reviewer-capability enforcement is now in development on the `videoTranslator`
branch. Review schema v2 binds source and output languages into each approval
key. Only a reviewer attesting capability in both languages may submit
`bilingual_verified`; target-language-only review and `unable_to_verify` are
durable, explicitly non-promoting decisions.

#### 27A. Add calibrated automatic review

- Score source/translation pairs with a reference-free quality estimator.
- Require agreement from at least two independent translation routes, semantic
  round-trip preservation, and all deterministic integrity gates.
- Calibrate thresholds against reviewed correct fixtures and adversarial
  corruptions; any known critical defect that passes blocks activation.
- Record successful results as `machine_verified`, never as human or bilingual
  approval, and retain `unresolved` whenever any gate fails.

Status: in progress. The model-independent policy, candidate-review contract,
fail-closed adversarial calibration, and lazy COMETKiwi adapter are implemented.
The adapter uses the shared model cache, supports offline lookup, rejects
reference-based models and malformed scores, and remains optional. It is not
connected to subtitle promotion. Next, install and prefetch the gated model,
then qualify it against the reviewed multilingual fixtures plus omission,
entity, number, polarity, and plausible-mistranslation mutations.

The repeatable `qualify-machine-review` command is now implemented with four
reviewed Korean/Mandarin fixtures and thirteen critical-error mutations. It
writes auditable evidence, exits nonzero when any accepted translation scores
below threshold or any corruption escapes both deterministic and learned gates,
and remains disconnected from promotion. Next, install the optional COMET
runtime, accept and prefetch the gated weights, and record the first real-score
report; threshold tuning must use those results rather than assumptions.

The first real setup attempt on 2026-08-20 confirmed that the account is
authenticated but does not have access to the gated
`Unbabel/wmt22-cometkiwi-da` repository. COMET 2.2.7 also requests dependency
downgrades incompatible with the main pipeline, so qualification must run in a
dedicated environment. The primary environment was restored and its full suite
passes. No real score or qualification claim was produced.

After gated access was granted, the managed `cometEnv` downloaded the checkpoint
and completed CPU qualification. The model is rejected at the 0.85 threshold:
it scored the reviewed Mandarin treaty translation 0.3946 and failed to block
`lovely` for Korean `cute` (0.8632) and `Seattle` for `Seoul` (0.8587). The
versioned report is `docs/machine-review-qualification.json`. Do not lower the
threshold: no single threshold separates all reviewed good and bad cases. Next,
add explicit terminology/entity gates or qualify a stronger estimator before
running the 19 unresolved groups.

Step 27B terminology and entity consensus gates are now implemented under
machine-review protocol 2. Source-triggered reviewed rules block required-term
omissions and forbidden substitutions, while proper names independently present
in at least two routes must survive the selected translation. The cached offline
rerun now passes all three Korean fixtures and blocks the previous `lovely` and
`Seattle` escapes. Overall qualification remains rejected because the reviewed
Mandarin treaty translation still scores 0.3946. Next, qualify a stronger
reference-free estimator with the same protocol-2 fixture set.

### 28. Produce and validate dubbing only from approved subtitles

- Freeze approved canonical text and speaker assignments before TTS begins.
- Benchmark CosyVoice 3 and Chatterbox Multilingual against the existing Piper and
  XTTS routes for content accuracy, cross-lingual speaker similarity, licensing,
  resource use, and duration control.
- Synthesize one speaker-consistent clip per approved semantic/display unit, fit
  delivery through bounded rate and text adjustments, and preserve pauses rather
  than applying excessive time stretching.
- Back-transcribe every synthesized clip and compare it with the approved English
  text; block omissions, repetitions, hallucinations, names, and number errors.
- Mix speech with the separated accompaniment, validate loudness and clipping,
  then mux the final video. Treat visual lip synchronization as a later optional
  post-process, never as evidence that audio content is correct.

Exit: the dubbed video preserves approved meaning, speakers, timing, intelligible
audio, and auditable lineage from source speech through `final.srt` to each clip.

Status: planned; blocked until Step 27 produces an approved `final.srt`.

## Current usability assessment

The pipeline currently creates **structurally usable draft subtitles**: timing,
coverage, cue layout, provenance, deterministic caching, headless operation, and
export are working. It does **not yet create reliably usable final subtitles**.
The three-sample review found material meaning, place-name, and ASR errors despite
all structural checks passing. Step 19 prevents known reviewed errors from being
promoted, but it cannot detect unknown errors in new dialogue. Steps 20 and 21
fail closed, and Step 23 fixed independent-backend health, but the 2026-08-15
Japanese rerun still left 55-60 unresolved groups. Step 24 adds independent
speech-to-English evidence so a corrupt Whisper transcript is no longer the only
English source. The remaining release blocker is weak primary text translation
and the lack of multi-route adjudication plus bounded human resolution.
Steps 25-27 address those gaps without OCR.

## Definition of done

The fixture suite and episode run must satisfy all applicable requirements:

- source text is represented exactly once in clean semantic groups;
- every target group maps to source group and cue IDs;
- speakers, words, confidence, and provenance survive downstream stages;
- contextual translation passes integrity QA;
- reviewed semantic references pass when a sidecar is supplied;
- independent translation agreement passes every semantic group;
- unresolved semantic disagreements block promotion;
- no overlap or invalid timestamp exists;
- no cue is shorter than 0.5 seconds unless objectively irreparable;
- no cue exceeds 12 seconds, 20 characters per second, or layout limits;
- source event coverage is at least 98%;
- source time coverage is at least 95%;
- diarized turn and time coverage are at least 90%;
- SRT/ASS exports match approved canonical data;
- every fallback and mutation is recorded;
- rejection never produces or retains `final.srt`.
- the three-sample 72-cue semantic review contains no material error.

## Implementation discipline

- Complete and verify one numbered step before beginning the next.
- Prefer deterministic local transformations over another model invocation.
- Never translate display cues independently when they share a semantic group.
- Never weaken QA merely to make a run pass.
- Preserve prior artifacts until replacements pass QA.
- Keep credentials in environment variables and out of source, logs, and reports.
- Keep model/device fallbacks bounded and record the selected configuration.
