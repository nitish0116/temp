# Web Novel to AI Video — Implementation Plan

## Objective

Build a resumable pipeline that turns an authorized, cleaned Markdown chapter
into a reviewable 5–10 minute narrated visual video. The MVP should produce
stable intermediate artifacts, allow scene-level regeneration, and prove story
fidelity, character consistency, timing, cost, and render quality before scaling
to full chapters or novels.

## Scope decisions

### MVP includes

- Clean Markdown input and project metadata.
- Source-linked narration adaptation.
- Character, location, continuity, scene, and storyboard artifacts.
- Approved reference images and 15–30 generated base panels.
- Scene-level narration audio and pronunciation overrides.
- Audio-derived alignment, SRT/WebVTT, and master timeline.
- Still-image pan, zoom, crop, crossfade, music ducking, and limited SFX.
- Scene previews and a 1080p H.264/AAC final MP4.
- Dependency hashes, resumability, validation reports, and review states.

### Deferred until the MVP passes

- Multi-chapter and multi-hour orchestration.
- Automated lip synchronization.
- AI video for routine shots.
- Fully automatic reference-image approval or final publishing.
- Large asset libraries, distributed rendering, and cloud job scheduling.

## Architecture

Use a Python package with one module per stage and a thin pipeline orchestrator.
Every stage reads versioned artifacts, validates them, writes atomically, and
records input hashes, configuration, model/version, timestamps, and status.

```text
source -> analysis -> narration -> scenes -> storyboard -> prompts
       -> references/panels -> TTS -> alignment/subtitles
       -> timeline -> preview/render -> QA/review
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
- Human-readable narration and storyboard review exports.

Review gate:

- Approve narration and scene breakdown before any paid image generation.

Exit criteria:

- Every narration block maps to valid source ranges.
- Every source narrative range is included or explicitly excluded with reason.
- Entity references resolve to canonical IDs.
- A reviewer can reject and regenerate one block or scene independently.

### Phase 3 — Visual bible, storyboard, and prompts

Deliver:

- Character/location/prop reference specifications and approval workflow.
- Shot planning with panel reuse and crop/motion variants.
- Deterministic prompt compiler using approved references and global style.
- Budget estimator enforcing panels-per-minute and candidate limits.
- Image provider interface, fixture generator, prompt/seed/reference provenance,
  candidate selection, and rejection/regeneration.

Review gates:

- Approve major character and recurring-location references.
- Approve 10–20 representative panels before bulk generation.

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

Run one approved 5–10 minute segment and record:

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
- narration/storyboard and visual references are approved;
- source coverage and narration lineage pass;
- all timeline assets exist and are approved;
- character continuity checks have no blocking issue;
- TTS, alignment, subtitle, and timeline validation pass;
- narration loudness, music ducking, SFX peaks, and final encoding pass;
- the preview and final review decisions are recorded.

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
4. Export narration/storyboard review documents and accept/reject decisions.
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

A model-assisted planning review has now resolved the local draft without
misrepresenting it as human approval. It treats Tanya and Being X as characters,
records `God`, `Creator`, and `Lord` as Being X aliases, retains Malthus as a
historical reference, and classifies the Stanford Prison Experiment as an event.
The result is `reviewed_draft`, planning-usable, non-release-usable, and still
requires human approval.

The narration planning contract is also implemented. It verifies the manuscript
hash, requires reviewed canonical identities, groups the prologue into 14 bounded
source-range units, records referenced canonical IDs, and leaves all adapted text
empty with `pending_adaptation` status. Next, add a deterministic narration
provider fixture, adaptation fidelity checks, and scene segmentation tied to
these narration IDs.
