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

The latest verification is 40 passing tests plus successful bytecode compilation.
Generated media, model weights, and dedicated `imageEnv`/`audioEnv` environments
remain ignored and local.

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
- The current CLI exposes individual stages but no single autonomous run/resume
  command, so an operator or coding assistant still sequences the pipeline.

## Git and local output state

The active branch is `videoCreator`. Commit `4a823dde` completed the offline
production-image fixes. The final ignored render is stored beneath
`videoCreator/workspaces/tanya-prologue/renders/final/`. Use live Git evidence
from the history command rather than relying on these identifiers indefinitely.

## Next action

Implement a one-command `video-creator run <workspace> <manuscript> --offline`
orchestrator. It must discover completed stages, resume selectively, delegate to
managed model environments, apply bounded retries and fallbacks, run final QA,
and emit one concise exception report without requiring coding-assistant decisions.
