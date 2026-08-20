# Repository assistant handoff

For work involving `videotranslator`, read
`videotranslator/docs/project-handoff.md` before planning changes. Treat current
code, tests, and Git state as authoritative when they differ from the handoff.

When the user sends `/history`, asks to resume prior work, or asks for project
status, run:

```text
python -m videotranslator history
```

Use the Python command from the active environment (`python3` where applicable);
do not assume a workstation-specific environment path.

Summarize its output, then continue from the documented next step unless the user
changes direction.

After material work, update the handoff when the user requests a handoff, history
update, or commit. Record durable outcomes, decisions, verification, blockers, and
the next action. Do not copy raw assistant transcripts, credentials, tokens,
personal data, model responses containing private input, or large command logs.
Never claim access to another assistant product's conversation history unless the
user has explicitly exported that history into the workspace.

A human can install a prepared complete handoff with:

```text
python -m videotranslator history --update-from path/to/handoff-next.md
```

## Commit messages

Every commit must use a concise subject line followed by a blank line and a
bulleted body describing the material changes. Do not create subject-only
commits.

## Local model environments and caches

All future local model integrations must follow the `videotranslator` lifecycle:

- keep conflicting model dependencies in a dedicated ignored environment at the
  workspace root;
- create the environment once and refresh it only when its requirements
  fingerprint or Python minor version changes;
- delegate model work to that interpreter automatically without shell activation;
- store downloaded weights in the shared ignored `.model-cache` hierarchy, honoring
  `PYTHON_CACHE_HOME`, `HF_HOME`, and framework-specific cache variables;
- separate package installation from model prefetch, record the model ID and license,
  and support an offline mode that fails early when the environment or weights are
  missing;
- never hardcode a workstation-specific absolute cache path or commit model weights.
