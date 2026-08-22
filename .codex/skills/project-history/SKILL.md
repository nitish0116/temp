---
name: project-history
description: Load and maintain the repository's durable assistant handoff. Use when the user says /history, asks to resume work, changes systems or coding assistants, requests project status, or asks to record context for a future assistant.
---

# Project History

Activate the environment configured for the current workstation, then run this
from the repository root:

```text
python scripts/project_history.py
```

Use `python3` instead when that is the active environment's Python command. Do
not assume a drive letter, username, shell, or virtual-environment location.

Treat the repository workspace root (the directory containing `.git`, currently
named `temp`) as the root for every path created or recorded during a session.
Create project outputs, model caches, temporary staging, reports, and generated
configuration only beneath that root, using workspace-relative paths wherever a
file or document stores the path. Never write a workstation-specific absolute
path into tracked files. Before finishing each session, inspect changed tracked
files for drive-letter, username, home-directory, or other machine-specific
paths. An absolute path may be used only when the user explicitly requests an
external location; record that exception as workstation-local, not portable
project history.

The command discovers the project from the working directory or current branch.
When discovery is ambiguous, pass `--project <directory>`. Handoffs live at
`<project>/docs/project-handoff.md`; this convention applies to every project in
the repository, not only `videotranslator`.

Use the printed handoff, Git status, and recent commits to summarize:

1. the last verified outcome;
2. current blockers and deliberate rejections;
3. uncommitted or unpushed work;
4. the next documented action.

Read files linked by the selected project handoff only when needed.
Prefer current code, tests, and Git evidence over stale prose.

When recording a new handoff, update `<project>/docs/project-handoff.md`
directly, or prepare a complete replacement and run:

```text
python scripts/project_history.py --project <directory> --update-from path/to/handoff-next.md
```

The replacement must start with a level-one heading ending in `project handoff`.
Record durable decisions, verification results, blockers, and one concrete next step.
Keep it concise. Never include raw conversations, secrets, credentials, personal
data, or bulky logs. Acknowledge that unexported assistant transcripts cannot be
recovered from the repository. Run the display command again to review the result.
