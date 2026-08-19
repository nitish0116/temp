# Contributing

Thank you for helping improve the HM Document-to-Audio Toolkit. Contributions to
the document, narration, video, reporting, and Video Translator components are
welcome.

## Before starting

- Search existing issues before opening a duplicate.
- Open an issue before a large architectural change so scope and compatibility
  can be discussed.
- Do not upload copyrighted source documents, credentials, model caches, or
  generated media.
- Report security vulnerabilities privately according to
  [SECURITY.md](SECURITY.md).

## Development setup

Use Python 3.10 or later on Windows, create a virtual environment, and install the
requirements for the component you are changing. The root [README](README.md)
contains the current setup commands and FFmpeg requirements.

Create a focused branch from the latest default branch:

```powershell
git fetch origin
git switch -c fix/short-description origin/main
```

## Making changes

- Keep each change focused and avoid unrelated formatting rewrites.
- Preserve compatibility with documented PowerShell and Python entry points.
- Keep generated outputs, media, virtual environments, and model caches out of
  Git. Never use `git add --force` to include ignored media.
- Store portable paths in committed configuration and documentation; do not
  commit workstation-specific absolute paths.
- Add or update tests for behavior changes.
- Update the relevant component README when commands or user-visible behavior
  change.

## Testing

Run the full suite when practical:

```powershell
python -m pytest
```

During development, a focused component suite is acceptable, but describe both
the focused and full verification performed in the pull request.

## Pull requests

- Use a clear title and explain the problem, solution, and tradeoffs.
- Link related issues.
- List verification commands and their results.
- Call out breaking changes, new dependencies, network access, model downloads,
  or changes that affect large files.
- Confirm that you have the right to contribute the submitted code and content.

By contributing, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

