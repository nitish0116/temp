"""Show or update the durable handoff for any project in this repository."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
HANDOFF_RELATIVE = Path("docs/project-handoff.md")


def git_output(arguments: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=REPOSITORY, check=True,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        return f"Git information unavailable: {type(error).__name__}"
    return result.stdout.strip() or "(no output)"


def discover_project(explicit: str | None, cwd: Path) -> Path:
    if explicit:
        project = (REPOSITORY / explicit).resolve()
        if project.parent != REPOSITORY or not project.is_dir():
            raise ValueError(f"project must be a repository-root directory: {explicit}")
        return project

    resolved_cwd = cwd.resolve()
    for candidate in (resolved_cwd, *resolved_cwd.parents):
        if candidate.parent == REPOSITORY and (candidate / HANDOFF_RELATIVE).is_file():
            return candidate
        if candidate == REPOSITORY:
            break

    branch = git_output(["branch", "--show-current"])
    branch_project = REPOSITORY / branch
    if branch_project.is_dir() and (branch_project / HANDOFF_RELATIVE).is_file():
        return branch_project

    projects = sorted(
        path.parent.parent for path in REPOSITORY.glob(f"*/{HANDOFF_RELATIVE.as_posix()}")
    )
    if len(projects) == 1:
        return projects[0]
    names = ", ".join(path.name for path in projects) or "none"
    raise ValueError(f"cannot infer active project; use --project. Available: {names}")


def validate_handoff(content: str) -> None:
    first_line = content.splitlines()[0].strip() if content else ""
    if not first_line.startswith("# ") or not first_line.casefold().endswith("project handoff"):
        raise ValueError("handoff must start with '# <Project> project handoff'")
    if "\x00" in content:
        raise ValueError("handoff must be plain UTF-8 Markdown")


def update_handoff(project: Path, source: Path) -> Path:
    if not source.is_file():
        raise FileNotFoundError(f"handoff update not found: {source}")
    content = source.read_text(encoding="utf-8").strip()
    validate_handoff(content)
    destination = project / HANDOFF_RELATIVE
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(f"{content}\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def render_history(project: Path, commit_count: int) -> str:
    handoff = project / HANDOFF_RELATIVE
    if not handoff.is_file():
        raise FileNotFoundError(f"project handoff not found: {handoff.relative_to(REPOSITORY)}")
    durable = handoff.read_text(encoding="utf-8").strip()
    pathspec = project.relative_to(REPOSITORY).as_posix()
    status = git_output(["status", "-sb", "--", pathspec])
    commits = git_output([
        "log", f"-{commit_count}", "--date=short", "--format=%h %ad %s", "--", pathspec,
    ])
    unpushed = git_output(["log", "--oneline", "@{upstream}..HEAD", "--", pathspec])
    return (
        f"{durable}\n\n# Live repository evidence\n\n"
        f"Project: `{pathspec}`\n\n## Status\n\n```text\n{status}\n```\n\n"
        f"## Recent project commits\n\n```text\n{commits}\n```\n\n"
        f"## Project commits not in upstream\n\n```text\n{unpushed}\n```\n"
    )


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Show or update a project handoff")
    parser.add_argument("--project", help="repository-root project directory")
    parser.add_argument("--commits", type=int, default=8)
    parser.add_argument("--update-from", type=Path, metavar="MARKDOWN")
    args = parser.parse_args(argv)
    if args.commits < 1:
        parser.error("--commits must be positive")
    project = discover_project(args.project, Path.cwd())
    if args.update_from:
        print(f"Updated project handoff: {update_handoff(project, args.update_from)}")
    else:
        print(render_history(project, args.commits))


if __name__ == "__main__":
    main()
