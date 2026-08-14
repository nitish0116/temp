# Repository assistant handoff

For work involving `videotranslator`, read
`videotranslator/docs/project-handoff.md` before planning changes. Treat current
code, tests, and Git state as authoritative when they differ from the handoff.

When the user sends `/history`, asks to resume prior work, or asks for project
status, run:

```powershell
D:\Git\Projects\.venv\Scripts\python.exe -m videotranslator history
```

Summarize its output, then continue from the documented next step unless the user
changes direction.

After material work, update the handoff when the user requests a handoff, history
update, or commit. Record durable outcomes, decisions, verification, blockers, and
the next action. Do not copy raw assistant transcripts, credentials, tokens,
personal data, model responses containing private input, or large command logs.
Never claim access to another assistant product's conversation history unless the
user has explicitly exported that history into the workspace.

