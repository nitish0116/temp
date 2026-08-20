"""Command-line interface for deterministic video-creator projects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifacts import read_json
from .project import (
    RIGHTS_STATES, analyze_project_source, ingest_project_source,
    approve_project_analysis, initialize_project, validate_project,
    plan_project_narration, write_analysis_review_template,
)


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    root = argparse.ArgumentParser(prog="video-creator")
    commands = root.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init", help="Create a project workspace")
    initialize.add_argument("workspace", type=Path)
    initialize.add_argument("--project-id", required=True)
    initialize.add_argument("--title", required=True)
    initialize.add_argument("--rights-status", choices=sorted(RIGHTS_STATES), default="unverified")
    ingest = commands.add_parser("ingest", help="Ingest a Markdown manuscript")
    ingest.add_argument("workspace", type=Path)
    ingest.add_argument("manuscript", type=Path)
    analyze = commands.add_parser("analyze", help="Create draft story/entity candidates")
    analyze.add_argument("workspace", type=Path)
    analyze.add_argument("manuscript", type=Path)
    template = commands.add_parser("analysis-review-template", help="Create entity review decisions")
    template.add_argument("workspace", type=Path)
    template.add_argument("output", type=Path)
    approve = commands.add_parser("approve-analysis", help="Apply complete entity decisions")
    approve.add_argument("workspace", type=Path)
    approve.add_argument("decisions", type=Path)
    narration = commands.add_parser("plan-narration", help="Create bounded adaptation units")
    narration.add_argument("workspace", type=Path)
    narration.add_argument("manuscript", type=Path)
    narration.add_argument("--maximum-source-characters", type=int, default=2400)
    validate = commands.add_parser("validate", help="Validate project artifacts")
    validate.add_argument("workspace", type=Path)
    status = commands.add_parser("status", help="Show project stage states")
    status.add_argument("workspace", type=Path)
    return root


def main(argv: list[str] | None = None) -> None:
    """Run one video-creator command."""
    args = parser().parse_args(argv)
    if args.command == "init":
        result = initialize_project(args.workspace, args.project_id, args.title, args.rights_status)
    elif args.command == "ingest":
        result = ingest_project_source(args.workspace, args.manuscript)
    elif args.command == "analyze":
        result = analyze_project_source(args.workspace, args.manuscript)
    elif args.command == "analysis-review-template":
        result = write_analysis_review_template(args.workspace, args.output)
    elif args.command == "approve-analysis":
        result = approve_project_analysis(args.workspace, args.decisions)
    elif args.command == "plan-narration":
        result = plan_project_narration(
            args.workspace, args.manuscript,
            maximum_source_characters=args.maximum_source_characters,
        )
    elif args.command == "validate":
        issues = validate_project(args.workspace)
        result = {"passed": not issues, "issues": issues}
    else:
        manifest = read_json(args.workspace / "project.json")
        result = {
            "project_id": manifest["project_id"], "rights": manifest["rights"],
            "stages": manifest["stages"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "validate" and not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
