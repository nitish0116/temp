# Web Novel to AI Video — Implementation Plan

## Objective

Build a resumable, autonomous pipeline that turns an authorized, cleaned
Markdown chapter into a finished 5–10 minute narrated visual video. The MVP
should produce stable intermediate artifacts, allow scene-level regeneration,
and prove story fidelity, character consistency, timing, cost, and render quality
before scaling to full chapters or novels.

## Autonomy contract

The installed pipeline—not a coding assistant—owns routine decisions from source
analysis through final QA. Each stage must provide deterministic defaults,
machine-readable validation, bounded retries, and automatic fallback behavior.
The normal successful path requires no editorial prompts or manual artifact
editing.

User interaction is limited to:

- declaring source rights and supplying credentials or unavailable inputs;
- optionally selecting or replacing major character reference images;
- resolving an exception only after automatic repair and fallback budgets are
  exhausted.

Entity merging, narration adaptation, scene design, shot selection, prompt
construction, candidate ranking, TTS, timing, audio mix, rendering, and QA are
automatic. Low-confidence results are regenerated or handled by a documented
fallback policy. The pipeline emits a concise exception report only when it
cannot continue safely; it never asks the user or a coding assistant to make a
routine production decision.

## Scope decisions

### MVP includes

- Clean Markdown input and project metadata.
- Source-linked narration adaptation.
- Character, location, continuity, scene, and storyboard artifacts.
- Automatically accepted reference images and 15–30 generated base panels.
- Scene-level narration audio and pronunciation overrides.
- Audio-derived alignment, SRT/WebVTT, and master timeline.
- Still-image pan, zoom, crop, crossfade, music ducking, and limited SFX.
- Scene previews and a 1080p H.264/AAC final MP4.
- Dependency hashes, resumability, validation reports, and review states.

### Deferred until the MVP passes

- Multi-chapter and multi-hour orchestration.
- Automated lip synchronization.
- AI video for routine shots.
- Unattended public publishing; the pipeline produces a validated local deliverable.
- Large asset libraries, distributed rendering, and cloud job scheduling.

## Architecture

Use a Python package with one module per stage and a thin pipeline orchestrator.
Every stage reads versioned artifacts, validates them, writes atomically, and
records input hashes, configuration, model/version, timestamps, and status.

```text
source -> analysis -> narration -> scenes -> storyboard -> prompts
       -> references/panels -> TTS -> alignment/subtitles
       -> timeline -> preview/render -> automatic QA -> deliverable
```

Suggested package boundaries:

```text
videoCreator/
  pyproject.toml
  video_creator/
    cli.py
    project.py
    schemas/
    stages/
      source.py
      analysis.py
      narration.py
      scenes.py
      storyboard.py
      prompts.py
      images.py
      tts.py
      alignment.py
      subtitles.py
      timeline.py
      render.py
      qa.py
    providers/
      llm.py
      image.py
      speech.py
    ffmpeg/
    cache.py
  tests/
  docs/
```

Provider interfaces should isolate model vendors from pipeline contracts. Start
with fixture providers so the complete workflow can be tested without network
calls or generation costs.

## Canonical artifact contracts

Define JSON Schema or typed model contracts before provider integration:

1. `project.json`: project ID, rights declaration, language, output profile,
   style configuration, and stage versions.
2. `source.json`: normalized chapters, stable source ranges, and source hash.
3. `entities.json`: characters, aliases, relationships, locations, factions,
   props, world rules, and continuity facts.
4. `narration.json`: narration IDs, exact source ranges, text, tone, speakers,
   pronunciation terms, and fidelity-review state.
5. `scenes.json`: location/time/mood, participants, event, continuity inputs,
   narration IDs, and estimated duration.
6. `shots.json`: scene/shot IDs, composition, action, focus, reference IDs,
   desired duration, motion, and reuse policy.
7. `assets.json`: prompts, negative prompts, provider/model/version, seed,
   reference hashes, candidates, approval, license, and cost.
8. `alignment.json`: audio, sentence and word timing with confidence.
9. `timeline.json`: ordered scene and shot intervals referencing approved assets.
10. `qa.json`: blocking failures, warnings, metrics, and review decisions.

Stable IDs should follow `ch001_s003`, `ch001_s003_n002`, and
`ch001_s003_sh004`. Never use filenames or array positions as identity.

## Delivery phases

### Phase 1 — Foundation and contracts

Deliver:

- Python package, CLI, configuration loading, structured logging, and tests.
- Deterministic project scaffolding beneath a chosen workspace directory.
- Typed schemas, artifact readers/writers, validation, atomic writes, and hashes.
- Stage manifest with `pending`, `running`, `generated`, `approved`, `rejected`,
  and `stale` states.
- Fixture project containing a short public-domain or owned story excerpt.

Exit criteria:

- Invalid IDs, missing references, overlapping ranges, and stale dependencies
  fail clearly.
- A no-model fixture run creates the complete directory and artifact skeleton.
- Re-running unchanged stages performs no work.

### Phase 2 — Source, analysis, narration, and scenes

Deliver:

- Markdown ingestion preserving chapters, dialogue, and scene breaks.
- Entity/world/continuity analysis behind an LLM provider interface.
- Spoken-narration adaptation with exact source-range lineage.
- Scene segmentation and duration estimates.
- Fidelity checks for uncovered source ranges, invented names/numbers, missing
  entities, and narration duplication.
- Human-readable narration and storyboard audit exports.

Automatic gate:

- Score narration and scene fidelity before paid image generation; retry failed
  units within budget and stop only on unresolved blocking defects.

Exit criteria:

- Every narration block maps to valid source ranges.
- Every source narrative range is included or explicitly excluded with reason.
- Entity references resolve to canonical IDs.
- Automatic QA can reject and regenerate one block or scene independently.

### Phase 3 — Visual bible, storyboard, and prompts

Deliver:

- Character/location/prop reference specifications and automatic ranking workflow.
- Shot planning with panel reuse and crop/motion variants.
- Deterministic prompt compiler using approved references and global style.
- Budget estimator enforcing panels-per-minute and candidate limits.
- Image provider interface, fixture generator, prompt/seed/reference provenance,
  candidate selection, and rejection/regeneration.

Interaction policy:

- Offer the user an optional major-character image choice with an automatic
  default and timeout-safe continuation.
- Automatically rank representative panels for identity, composition, source
  fit, and technical quality before bulk generation.

Exit criteria:

- Prompts cannot redefine locked identity traits silently.
- Every shot references an approved panel or has a blocking missing-asset state.
- Regenerating one panel invalidates only dependent shots and previews.

### Phase 4 — Narration, alignment, and subtitles

Deliver:

- Scene/block TTS provider with retry and pronunciation dictionary support.
- Loudness normalization and silence/missing-audio checks.
- TTS timestamp ingestion with forced-alignment fallback.
- Sentence/word timing contract plus SRT and WebVTT generation.
- Subtitle line length, duration, reading speed, ordering, and drift validation.

Exit criteria:

- Audio duration is authoritative for scene and final timing.
- A pronunciation fix regenerates only affected audio, alignment, subtitles,
  timeline intervals, and scene render.
- No missing narration, invalid cue timing, or uncovered audio interval exists.

### Phase 5 — Timeline, motion, audio mix, and rendering

Deliver:

- Master timeline compiler referencing—not duplicating—approved assets.
- Motion presets for slow zoom, pan, crop, reveal, and crossfade.
- Mood-to-music mapping, narration-aware ducking, and bounded SFX cues.
- FFmpeg scene renderer, preview renderer, validated concatenation, and final mux.
- 1080p/30 fps H.264 video, AAC audio, and separate subtitle deliverables.

Exit criteria:

- Timeline has continuous coverage with no black gaps or invalid overlaps.
- Scene renders can be resumed and concatenated without rebuilding valid scenes.
- Audio does not clip; narration remains intelligible over music/SFX.
- Output passes codec, resolution, frame-rate, duration, and stream checks.

### Phase 6 — MVP evaluation and scale decision

Run one automatically validated 5–10 minute segment and record:

- Source-to-narration fidelity defects.
- Character/location consistency defects.
- Panels generated, accepted, reused, and regenerated.
- Cost per finished minute and elapsed time per stage.
- Narration corrections and pronunciation failures.
- Subtitle/timing defects, black frames, audio clipping, and render retries.
- Reviewer time at each gate.

Scale only if there are no material story contradictions, no unresolved identity
defects, complete audio/visual coverage, acceptable pacing, and an affordable
cost per finished minute.

## Quality and release gates

The final render remains blocked unless:

- adaptation rights are recorded;
- narration, storyboard, and visual references pass automatic quality thresholds;
- source coverage and narration lineage pass;
- all timeline assets exist and pass automatic quality thresholds;
- character continuity checks have no blocking issue;
- TTS, alignment, subtitle, and timeline validation pass;
- narration loudness, music ducking, SFX peaks, and final encoding pass;
- preview and final automatic QA decisions are recorded.

Warnings may remain for intentional panel reuse or minor stylistic variation,
but story contradictions, identity drift, missing audio/visual coverage, timing
gaps, and unlicensed assets are blocking.

## Testing strategy

- Unit tests for IDs, hashes, schemas, dependency invalidation, subtitle layout,
  timeline math, prompt compilation, and FFmpeg command construction.
- Contract tests for every LLM, image, and speech provider response.
- Fixture-provider integration test covering the entire pipeline without network.
- Golden timeline/render tests using tiny generated images and audio.
- Failure tests for interrupted stages, corrupt assets, missing timestamps,
  rejected reviews, and partial regeneration.
- One short FFmpeg smoke render in CI where FFmpeg is available.

## Initial backlog

### Sprint 1 — Executable skeleton

1. Create package, CLI, configuration, and project scaffold command.
2. Define IDs and the first five schemas: project, source, entities, narration,
   and scenes.
3. Implement atomic artifact storage, content hashing, manifest state, and cache
   invalidation.
4. Add a small authorized/public-domain fixture and schema/lineage tests.
5. Implement `init`, `ingest`, `validate`, and `status` commands.

### Sprint 2 — Text planning proof

1. Add provider interfaces and deterministic fixture providers.
2. Implement entity analysis, narration adaptation, and scene segmentation.
3. Add source-coverage and unsupported-claim checks.
4. Export narration/storyboard audit documents and automatic accept/retry decisions.
5. Demonstrate selective regeneration after one narration edit.

### Sprint 3 — End-to-end media proof

1. Add fixture images and TTS, alignment, subtitle, and timeline stages.
2. Implement motion presets and a small FFmpeg scene renderer.
3. Add music ducking, final concatenation, and media QA.
4. Produce a 60–90 second fixture render before invoking paid providers.

## Immediate next action

Phase 1 source ingestion is implemented. The local Tanya prologue validates as
three source-linked dated sections while remaining untracked and release-blocked
under `unverified` rights. Draft story analysis is also implemented through a
provider contract and an offline extractive baseline. Its candidates retain
source offsets, require review, and cannot be used for release automatically.

Next, define approved entity/alias decisions and the narration and scene
contracts. Then add deterministic fixture providers for source-linked narration
adaptation and scene segmentation. Do not select production LLM, image, or TTS
vendors until those offline contracts and selective regeneration are proven.

Entity and setting decisions are now implemented. A generated review template
binds every candidate to the exact analysis fingerprint and source hash, requires
a named reviewer and timezone-qualified timestamp, and refuses incomplete or
pending decisions. Approved candidates require stable canonical IDs, names, and
entity kinds; rejected candidates remain explicit. The local Tanya workspace has
a pending template for six entity/event/concept candidates and three settings.

Next, resolve that template editorially, then implement narration blocks and
scene contracts against the approved canonical IDs. Alias choices such as whether
`God`, `Lord`, and `Creator` represent one canonical entity must not be inferred
silently by the pipeline.

A model-assisted planning review has now resolved the local draft. It treats
Tanya and Being X as characters,
records `God`, `Creator`, and `Lord` as Being X aliases, retains Malthus as a
historical reference, and classifies the Stanford Prison Experiment as an event.
The result is `reviewed_draft`, planning-usable, and non-release-usable under the
current contract. That mandatory human-approval behavior is now technical debt:
it must be replaced by source-bound automatic confidence and QA gates. Rights
status remains an independent release blocker.

The narration planning contract is also implemented. It verifies the manuscript
hash, requires reviewed canonical identities, groups the prologue into 14 bounded
source-range units, records referenced canonical IDs, and leaves all adapted text
empty with `pending_adaptation` status. Next, add a deterministic narration
provider fixture, adaptation fidelity checks, and scene segmentation tied to
these narration IDs.

Narration adaptation and scene contracts are now implemented. Provider responses
must cover all planned IDs exactly once; adapted blocks retain immutable source
ranges and hashes, declare canonical entities, preserve source-number evidence,
and stay non-release drafts. Fidelity checks reject unsupported numbers,
entities, empty text/tone, and unsafe compression. Scene segmentation covers
every validated narration ID exactly once, preserves ordering, does not cross
approved setting boundaries, and records estimated narration duration.

The local Tanya workspace now has a complete model-assisted narration draft for
all 14 bounded units. Every unit passed the implemented lineage, entity,
unsupported-number, and compression checks, with adaptation length ratios from
20.2% to 49.0%. The validated narration produced an eight-scene draft covering
every narration ID exactly once without crossing canonical setting boundaries.
The full workspace validator reports no issues, and the 13-test suite passes.

These artifacts remain local, model-assisted, non-release drafts because the
source rights are unresolved and the automatic promotion contract is not yet
implemented. Next, add an automatic scene-enrichment and QA contract that
derives event, mood, visual intent, confidence, and accept/retry decisions for
each of the eight scenes. It must promote passing artifacts without human or
coding-assistant guidance, route failures through bounded repair and fallback,
and produce an exception report only if those routes fail. Then prove selective
regeneration after automatically revising one narration unit.

Automatic scene enrichment and structural QA are now implemented. The offline
fallback derives source-bound story events, moods, and visual intent, records
per-check confidence and retry budgets, promotes complete results, and emits a
machine-readable exception report for incomplete provider output. All eight
local scenes were automatically accepted with no exceptions, the workspace
validator passes, and the 15-test suite passes. Rights remain independently
release-blocking.

Next, strengthen automatic QA from structural completeness to semantic and
continuity scoring, including setting-boundary detection, event-to-narration
support, and visual-intent/entity consistency. Then implement bounded provider
retry and deterministic fallback execution rather than only recording the retry
decision.

The stronger automatic gate is now implemented. Narration planning cannot group
paragraphs across ingested source sections, scene validation rejects any range
that crosses a canonical setting boundary, and enrichment scores narration
support plus setting/entity grounding. Provider failures and low scores receive
bounded retries followed by the deterministic fallback; only exhaustion creates
an exception report.

This gate detected a real historical-to-Tokyo boundary defect in the local draft.
The pipeline failed closed, replanned the affected narration boundaries,
regenerated downstream narration and scenes, and automatically accepted all
eight corrected scenes. The workspace validator now passes and the 18-test suite
passes. Next, prove dependency-aware selective regeneration so a corrected unit
invalidates and rebuilds only its downstream scenes and audit decisions.

Dependency-aware scene regeneration is now implemented. Every enriched scene
records a stable fingerprint of its scene contract, adapted narration, canonical
references, timing estimate, and enrichment-provider version. A rerun reuses a
previously accepted scene only when that complete fingerprint still matches;
changed dependencies regenerate only the affected scene. The artifact records
the exact reused and regenerated scene IDs for auditing.

The focused test changes one narration dependency and confirms that only its
scene calls the provider while the unaffected scene is preserved exactly. The
local workspace was upgraded once, regenerating all eight legacy scenes that
lacked fingerprints; its next unchanged run reused all eight and regenerated
none. Validation passes and the 19-test suite passes. Next, define the autonomous
storyboard shot-planning contract and carry these fingerprints into shot-level
selective regeneration.

Autonomous storyboard planning is now implemented. Automatically accepted
scenes produce bounded, ordered shots with setting and entity references,
composition, mood, motion preset, duration coverage, and dependency
fingerprints. Validation rejects missing scene coverage, sequence gaps, timing
drift, canonical-reference changes, or unaccepted shots. Prior shots are reused
only when their complete scene dependency still matches.

The local eight-scene draft produced 32 shots covering the full estimated
narration duration. Its immediate unchanged rerun reused all 32 shots and
regenerated none. Project validation passes and the 20-test suite passes. Next,
compile generation-ready image prompts and reference requirements from these
shots, keeping major-character image selection optional and every other visual
decision automatic.

Generation-ready image prompt compilation is now implemented. Each accepted
shot receives a positive prompt, bounded negative prompt, style, canonical
character references, and a dependency fingerprint. Canonical IDs are resolved
to display names; non-character entities never become character-reference
requirements. Major characters get a nonblocking `optional_user_override`
choice whose default action is automatic candidate generation and ranking.

The local storyboard produced 32 validated prompts and optional reference slots
for Tanya and Being X. Its unchanged rerun reused all 32 prompts and regenerated
none. Project validation passes and the 21-test suite passes. Next, implement the
image-provider contract, deterministic fixture images, automatic candidate
scoring, and default reference selection while preserving the optional override.

The image-provider contract and offline deterministic raster provider are now
implemented. Each prompt and default character reference produces a bounded
candidate set with reproducible seeds, technical/prompt-fit/continuity scores,
automatic winner selection, provenance, relative paths, and content hashes.
Project validation fails on missing, modified, unranked, or uncovered assets.

The local run generated 68 PNG candidates for 32 shots and two default character
references, then automatically selected all 34 required assets. No user choice
was required, and Tanya or Being X can still be overridden later. Workspace
validation passes and the 22-test suite passes. Next, add asset-level reuse and
provider retry/fallback so unchanged images incur no generation work and failed
production candidates recover without intervention.

Asset-level reuse and provider recovery are now implemented. An asset is reused
only when its dependency, provider, candidate count, files, hashes, and successful
generation provenance still match. Otherwise only that asset is regenerated.
Each candidate records every provider attempt; failures receive bounded retries
before the deterministic fallback runs automatically.

The local manifest was upgraded with provenance for all 68 candidates. Its next
run reused all 34 selected asset groups, regenerated none, and made no generation
calls. Corruption, zero-call reuse, and retry-to-fallback behavior are covered by
the 24-test passing suite. Next, implement autonomous narration-audio generation
with the same provider, retry, hash, selection, and selective-reuse contracts.

Offline production image generation is now integrated through the Apache-2.0
Sana 1.6B model at a pinned revision. It follows the translator lifecycle: a
workspace-root `imageEnv` is created once from a dependency fingerprint, commands
delegate to it automatically, and weights live in the shared ignored
`.model-cache`. Setup and cache verification have explicit offline behavior, and
production generation fails early instead of substituting fixture blobs.

The pinned model was cached and a real 1024-pixel CUDA smoke image was generated
offline on the local RTX PRO 4000. Next, generate and automatically rank the
canonical Tanya and Being X reference candidates before expanding generation to
all storyboard shots.

Character references can now run as an independent, selectively reusable stage
before shot generation. The first offline Sana run produced two candidates each
for Being X and Tanya in 27 seconds, but visual inspection exposed a semantic
grounding failure: Tanya appeared as an adult and Being X was not consistently
elderly. These candidates are diagnostic, not approved production references.
Next, compile source-evidenced visual character briefs and replace the synthetic
hash scorer with a cached local vision-language reviewer that can reject identity,
age, costume, and prompt-grounding failures automatically.

The Apache-2.0 SmolVLM2 2.2B reviewer is now pinned and cached alongside Sana in
the shared model cache. Ingestion retains a local normalized manuscript, prompt
compilation attaches bounded source evidence to each character, and semantic
review fails closed with per-candidate diagnostics and a retry-required state.

The first evidence-grounded retry correctly did not promote any references. It
also proved that raw prose is unsuitable as an image prompt: Sana rendered scene
layouts and pseudo-text instead of isolated character sheets. Next, distill the
evidence into a concise visual-only character brief before bounded regeneration;
raw excerpts remain reviewer evidence but must not be sent directly to Sana.

The visual-only brief compiler is now implemented. It extracts explicit nearby
age and presentation constraints while keeping raw excerpts exclusively in the
review context. The bounded retry produced clean character sheets: an elderly
male design for Being X and a toddler in plain period orphanage clothing for
Tanya. SmolVLM2 independently returned ACCEPT for all four candidates. Next,
persist reviewer-selected canonical reference files and feed their hashes into
shot prompt and selective-regeneration dependencies.

Reviewer-selected references are now promoted into stable
`references/characters` files with immutable hashes and provenance. Project
validation rejects missing, incomplete, or modified canonical references. Every
shot asset dependency incorporates only the hashes of characters that shot
actually references, so a changed design selectively invalidates affected shots.

The Tanya workspace promoted Being X and Tanya, upgraded the full asset manifest
with their hashes, and then reused all 34 asset groups on an unchanged rerun with
zero generation calls. The 29-test suite and full workspace validation pass.
Next, generate the first bounded batch of real Sana storyboard shots using these
canonical dependencies, then apply the same local semantic gate before expansion.

A four-shot offline Sana pilot and SmolVLM2 review stage are now implemented in
isolated paths. The pilot caught two prompt-contract defects through bounded
retries: missing character constraints initially produced an adult Tanya, and
adding age constraints corrected identity but revealed four camera-only variants
of the same portrait. A semantic-core diversity gate now marks such batches
`retry_required` even if the per-image reviewer accepts them.

Expansion to all 32 shots remains blocked. Next, enrich storyboard shots with
distinct source-bound actions, framing, environment changes, and narrative beats;
then rerun this same four-shot pilot without changing the acceptance policy.

Storyboard planning now distributes adapted narration sentences into distinct,
source-bound shot beats and validates uniqueness within each scene. The first
scene now progresses through first breath, fragmented awareness, infant-body
realization, orphanage reveal, and forced feeding. Contrastive prose is rewritten
to omit negated visual subjects; this selectively regenerated only the affected
orphanage shot and removed the incorrectly rendered train.

The four-shot pilot now passes semantic-core diversity, but development audit
still shows clothing and facial drift from the canonical Tanya reference. Full
expansion remains blocked until Sana generation consumes the canonical image as
visual conditioning rather than using its hash only as an invalidation key.

The project visual contract is now anime-style illustration. New projects store
this choice in their manifest; prompt compilation inherits it automatically,
character sheets use anime linework and cel shading, and negative prompts reject
photorealistic, live-action, and 3D-render drift. The Tanya workspace is pinned
to polished cinematic anime key art, so subsequent selective regeneration will
replace the earlier realism-oriented pilot assets.

The anime reference and pilot regeneration is complete. Sana 1.6B produced two
fresh anime candidates for both Being X and Tanya; SmolVLM2 accepted all four
and promoted candidate 02 for each character. The pinned Sana ControlNet then
generated four reference-conditioned anime pilot shots offline. All four passed
semantic review, narrative-diversity review, and the conditioning gate, so bulk
shot generation is now unblocked.

Production expansion now requires an accepted conditioned pilot. All 32 anime
storyboard shots were generated offline with the pinned Sana ControlNet, using
canonical character edges where applicable and neutral control for environment-
only shots. SmolVLM2 accepted all 32, the production review recorded no issues,
and workspace validation passes.

Offline narration generation is implemented with FFmpeg's bundled Flite voice,
48 kHz mono PCM output, and loudness normalization. All 14 adapted narration
blocks generated successfully on their first attempt (411.16 seconds total).
Each clip has a text/provider dependency, immutable hash, duration, retry history,
and selective-reuse contract.

Audio-authoritative alignment and subtitles are complete. The 411.16-second
narration was split into ordered cues capped at 42 characters, with continuous
non-overlapping coverage. A shared alignment artifact now drives validated SRT
and WebVTT deliverables, preventing timing drift between formats.

The master timeline and audio mix are complete. Thirty-two reviewed shots fill
the entire 411.16-second narration without gaps or overlaps, retain storyboard
motion presets, and reference immutable selected image hashes. The 14 normalized
clips are concatenated into a validated 48 kHz narration-first master mix.

The final rendering stage is complete. FFmpeg rendered 32 resumable 1080p/30 fps
H.264 motion segments, concatenated them without re-encoding, and muxed AAC
narration plus an English mov_text subtitle stream. The resulting local Tanya
prologue MP4 has an immutable recorded hash.

End-to-end encoded-media evaluation passes. The final file is 411.133 seconds,
H.264 1920x1080 at 30 fps, with AAC audio peaking at -2.5 dB and an embedded
mov_text subtitle stream; no sustained near-black frames were detected. The
technical pipeline is complete. Release remains deliberately blocked because
adaptation rights are still recorded as unverified.
