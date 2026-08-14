---
name: project-history
description: Load and maintain the repository's durable assistant handoff. Use when the user says /history, asks to resume work, changes systems or coding assistants, requests project status, or asks to record context for a future assistant.
---

# Project History

Run this from the repository root:

```powershell
D:\Git\Projects\.venv\Scripts\python.exe -m videotranslator history
```

Use the printed handoff, Git status, and recent commits to summarize:

1. the last verified outcome;
2. current blockers and deliberate rejections;
3. uncommitted or unpushed work;
4. the next documented action.

Read files linked by `videotranslator/docs/project-handoff.md` only when needed.
Prefer current code, tests, and Git evidence over stale prose.

When recording a new handoff, update `videotranslator/docs/project-handoff.md`
with durable decisions, verification results, blockers, and one concrete next
step. Keep it concise. Never include raw conversations, secrets, credentials,
personal data, or bulky logs. Acknowledge that unexported assistant transcripts
cannot be recovered from the repository.

