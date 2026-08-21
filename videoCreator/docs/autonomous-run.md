# Autonomous pipeline command

Run or resume an entire manuscript with one command:

```powershell
python -m video_creator.cli run workspaces/novel-part-01 manuscripts/part-01.md `
  --series-library workspaces/novel-series `
  --rights-status authorized `
  --offline
```

Run the command from `videoCreator`. Omit `--offline` on the first run so the
managed `imageEnv` and `audioEnv` can be created and the pinned models can be
cached. Later runs can use `--offline` and fail early if an environment or model
is unavailable.

The command initializes the workspace when needed and then owns analysis,
narration, scene enrichment, storyboard planning, prompt compilation, character
references, the shot pilot, production images, semantic review, narration audio,
subtitles, the timeline, rendering, validation, and final QA. Re-running the same
command resumes from accepted stage artifacts. A failure writes
`reports/run-report.json` with one exception and a concrete rerun action.

## Split manuscripts and recurring characters

Give every part of the same novel a different workspace and the same
`--series-library` directory:

```powershell
python -m video_creator.cli run workspaces/novel-part-02 manuscripts/part-02.md `
  --series-library workspaces/novel-series `
  --rights-status authorized `
  --offline
```

Accepted canonical references are copied into the series library. Later parts
match characters by canonical ID and name, copy the hash-validated image into the
new workspace, and run semantic review against the current manuscript evidence.
Only new characters are generated. Known series characters are seeded into
analysis even when their name appears only once in a short manuscript.

Do not point two manuscript parts at the same workspace. If a workspace already
contains a different source hash, the command stops and instructs the operator to
use a new workspace with the same series library.
