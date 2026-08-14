"""Render the committed project handoff together with live Git evidence."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
DEFAULT_HANDOFF = PROJECT / "docs" / "project-handoff.md"
HANDOFF_HEADING = "# Video Translator project handoff"


def git_output(repository: Path, arguments: list[str]) -> str:
    """Return one read-only Git query or a concise unavailable marker.

    Example:: ``git_output(root, ["status", "-sb"])`` returns the current branch
    and working-tree summary without modifying repository state.
    """
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=repository, check=True,
            capture_output=True, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        return f"Git information unavailable: {type(error).__name__}"
    return result.stdout.strip() or "(no output)"


def render_history(
    repository: Path = REPOSITORY,
    handoff: Path = DEFAULT_HANDOFF,
    commit_count: int = 8,
) -> str:
    """Combine durable handoff prose with current status and recent commits.

    Example:: a newly cloned workstation sees the same committed decisions plus
    its own branch, uncommitted changes, and unpushed commit evidence.
    """
    if commit_count < 1:
        raise ValueError("commit_count must be positive")
    if not handoff.is_file():
        raise FileNotFoundError(f"Project handoff not found: {handoff}")
    durable = handoff.read_text(encoding="utf-8").strip()
    status = git_output(repository, ["status", "-sb"])
    commits = git_output(
        repository,
        ["log", f"-{commit_count}", "--date=short", "--format=%h %ad %s"],
    )
    unpushed = git_output(
        repository, ["log", "--oneline", "@{upstream}..HEAD"],
    )
    return (
        f"{durable}\n\n"
        "# Live repository evidence\n\n"
        f"## Status\n\n```text\n{status}\n```\n\n"
        f"## Recent commits\n\n```text\n{commits}\n```\n\n"
        f"## Commits not in upstream\n\n```text\n{unpushed}\n```\n"
    )


def update_handoff(
    source: Path,
    destination: Path = DEFAULT_HANDOFF,
) -> Path:
    """Validate and atomically install a complete project handoff document.

    Example:: ``update_handoff(Path("handoff-next.md"))`` replaces the tracked
    handoff only when the source is non-empty and starts with the required title.
    """
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError("update source must be different from the tracked handoff")
    if not source.is_file():
        raise FileNotFoundError(f"Handoff update not found: {source}")
    content = source.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError("handoff update must not be empty")
    if content.splitlines()[0].strip() != HANDOFF_HEADING:
        raise ValueError(f"handoff update must start with: {HANDOFF_HEADING}")
    if "\x00" in content:
        raise ValueError("handoff update must be plain UTF-8 Markdown")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(f"{content}\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def main(argv: list[str] | None = None) -> None:
    """Display project history or install a validated replacement handoff."""
    parser = argparse.ArgumentParser(
        description="Show or update the durable, assistant-neutral project handoff",
    )
    parser.add_argument("--commits", type=int, default=8)
    parser.add_argument(
        "--update-from",
        type=Path,
        metavar="MARKDOWN",
        help="replace the tracked handoff with a complete validated Markdown file",
    )
    args = parser.parse_args(argv)
    if args.update_from is not None:
        updated = update_handoff(args.update_from)
        print(f"Updated project handoff: {updated}")
        return
    print(render_history(commit_count=args.commits))


if __name__ == "__main__":
    main()
