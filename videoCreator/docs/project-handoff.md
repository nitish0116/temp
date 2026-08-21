# Video Creator project handoff

Last durable update: 2026-08-21. Run
`python scripts/project_history.py --project videoCreator` from the repository
root for current Git evidence.

## Verified outcome

The Tanya prologue pipeline now produces a complete offline anime-style video
with locally cached models and automatic environment delegation. The latest
render is 550.45 seconds, H.264 1920x1080 at 30 fps, with AAC narration and an
embedded subtitle stream. Final QA passes with no sustained black frames.

All 32 storyboard images were regenerated with Animagine XL 3.1 and the standard
SDXL IP-Adapter. Character shots use cropped canonical identity references;
environment-only shots use a zero-strength neutral adapter embedding. SmolVLM2
auto-accepted all 32 structured scene checks. Kokoro narration uses `af_bella`
at 0.92 speed, measures 121.1 spoken words per minute, and includes 48 sentence
pauses plus eight scene pauses. Safe framing contains each square illustration
over a blurred widescreen extension.

The latest verification is 48 passing tests plus successful bytecode compilation.
Generated media, model weights, and dedicated `imageEnv`/`audioEnv` environments
remain ignored and local.

The CLI now provides one resumable `run` command for the complete pipeline. A
shared series library preserves accepted canonical character images across
separate manuscript workspaces, seeds known characters even on a single mention,
and generates only previously unseen characters. The coordinator stays in
`imageEnv` and delegates only speech synthesis to `audioEnv`.

## Durable decisions

- Normal operation must require minimal user intervention. The installed
  pipeline owns routine decisions and asks only for unavailable inputs, optional
  major-character image selection, or exhausted exception handling.
- Visuals are cinematic anime-style illustrations generated with locally cached
  open-source models; new model integrations follow the repository environment
  and cache lifecycle in `AGENTS.md`.
- Character continuity is grounded with canonical reference images, while shot
  prompts remain source-bound and scene-specific.
- Every commit uses a concise subject, a blank line, and a bulleted body.
- Project handoffs live at `<project>/docs/project-handoff.md` and are accessed
  through the repository-wide `scripts/project_history.py` command.

## Blockers and limitations

- Source adaptation rights remain unverified, so the final QA report deliberately
  marks the render `blocked_rights`; it must not be treated as release-ready.
- SmolVLM2 sometimes emits inaccurate optional rationale even when its mandatory
  structured character, setting, and action fields pass. Automated review needs
  stronger grounding before its prose can be trusted as evidence.
- Automated entity typing remains conservative only at the contract level: the
  contextual policy now separates characters, fictional locations,
  organizations, concepts, and noise, and merges source-supported character
  aliases. Its output remains model-assisted and non-release-usable.

The first TBAtE run exposed a classification cascade: 30 capitalized phrases
were treated as characters and 15 identity images were attached to a landscape
pilot shot. The fix filters sentence-start noise, recognizes explicit fictional
place and organization contexts, merges Arthur/Art, Alice/Mother, and Reynolds
Leywin/Reynolds, links characters at the shot-sentence level, and conditions on
at most one primary identity. Provider retry failures now retain the underlying
exception. The polluted local workspace and series catalog were preserved in
`*-invalid-entity-classification-20260821` backup directories.

The clean TBAtE rerun then exposed one 49-character hyphenated subtitle token.
Subtitle chunking now splits oversized tokens preferentially at hyphens and uses
a hard 42-character boundary only when necessary. The resumed run reused images,
reviews, and audio, executed only subtitles through evaluation, and passed final
QA. Its 2491.033-second 1080p video has AAC audio, embedded subtitles, no black
frames, and an authorized `release_ready` status.

## Git and local output state

The active branch is `videoCreator`. Commit `4a823dde` completed the offline
production-image fixes. The final ignored render is stored beneath
`videoCreator/workspaces/tanya-prologue/renders/final/`. Use live Git evidence
from the history command rather than relying on these identifiers indefinitely.

## Next action

Inspect the completed first TBAtE video for editorial quality, then run the next
manuscript part with a new workspace and the same series library. Confirm the
three established character images report `series_reuse` and only new identities
are generated.
