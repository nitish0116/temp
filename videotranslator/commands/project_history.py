"""Render the committed project handoff together with live Git evidence."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
DEFAULT_HANDOFF = PROJECT / "docs" / "project-handoff.md"


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


def main(argv: list[str] | None = None) -> None:
    """Print assistant-neutral project history for a human or coding agent."""
    parser = argparse.ArgumentParser(
        description="Show the durable project handoff plus live Git history",
    )
    parser.add_argument("--commits", type=int, default=8)
    args = parser.parse_args(argv)
    print(render_history(commit_count=args.commits))


if __name__ == "__main__":
    main()

