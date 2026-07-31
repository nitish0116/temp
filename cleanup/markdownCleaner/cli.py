"""Command-line interface for Markdown cleanup and glossary approval.

Typical calls::

    python -m markdownCleaner.cli novel.md
    python -m markdownCleaner.cli novel.md --output cleaned
    python -m markdownCleaner.cli books --recursive --continue-on-error
    python -m markdownCleaner.cli --approve-words sitrep noncoms
    python -m markdownCleaner.cli --learn-words sitrep noncoms
    python -m markdownCleaner.cli --reject-words offense humor

The public helpers in this module remain compatibility façades.  Cohesive
implementations live in :mod:`markdownCleaner.commands` so report transforms
and command workflows can be tested without invoking the entire CLI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from markdownCleaner.commands.batch import (
    markdown_files,
    run_batch,
    safe_report_name,
    unique_batch_output_name,
)
from markdownCleaner.commands.execution import (
    configured_output_root,
    execute_pipeline,
)
from markdownCleaner.commands.parser import build_parser as _build_parser
from markdownCleaner.commands.reports import (
    markdown_code,
    write_batch_glossary_candidates,
    write_batch_summary,
    write_simplified_glossary_candidates,
)
from markdownCleaner.commands.review import (
    ReviewRequest,
    apply_review_request,
    review_action_count,
    review_request,
)
from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.report.exporter import ReportOptions
from markdownCleaner.pipeline import OCRPipeline


def _markdown_files(
    root: Path,
    recursive: bool,
    *,
    excluded_roots: tuple[Path, ...] = (),
) -> list[Path]:
    """Return sorted Markdown files found below ``root``."""
    return markdown_files(
        root,
        recursive,
        excluded_roots=excluded_roots,
    )


def _safe_report_name(relative_file: Path) -> Path:
    """Create a collision-free report folder while retaining folder context."""
    return safe_report_name(relative_file)


def _unique_batch_output_name(filename: str, used_names: set[str]) -> str:
    """Return a case-insensitively unique output filename."""
    return unique_batch_output_name(filename, used_names)


def _run_one(
    source: Path,
    *,
    config: Path,
    output_directory: Path | None,
    output_name: str | None = None,
    report_subdirectory: Path | str = "reports",
) -> tuple[dict, int, list[dict], list[dict]]:
    """Run one Markdown source using the historical tuple return contract."""
    return execute_pipeline(
        source,
        config=config,
        output_directory=output_directory,
        output_name=output_name,
        report_subdirectory=report_subdirectory,
        pipeline_factory=OCRPipeline,
    ).as_legacy_tuple()


def _md_code(value: object) -> str:
    """Return text safe for a fenced Markdown code block."""
    return markdown_code(value)


def _write_batch_summary(
    output_root: Path,
    *,
    source_root: Path,
    entries: list[dict],
    report_name: str = "batch_summary.md",
) -> Path:
    """Write one aggregate Markdown report for the entire batch run."""
    return write_batch_summary(
        output_root,
        source_root=source_root,
        entries=entries,
        report_name=report_name,
    )


def _write_batch_glossary_candidates(
    output_root: Path,
    entries: list[dict],
) -> Path:
    """Aggregate and write per-file vocabulary candidates."""
    return write_batch_glossary_candidates(output_root, entries)


def _write_simplified_glossary_candidates(
    source: Path,
    output: Path | None = None,
) -> Path:
    """Write a compact candidate report containing review essentials."""
    return write_simplified_glossary_candidates(source, output)


def build_parser() -> argparse.ArgumentParser:
    """Build the parser for every supported CLI call signature."""
    return _build_parser(Path(__file__).with_name("config.yaml"))


def _run_single_file(source: Path, *, config: Path, output_root: Path | None) -> int:
    """Execute and print the single-file workflow."""
    try:
        result, changes, _records, _candidates = _run_one(
            source,
            config=config,
            output_directory=output_root,
        )
    except Exception as exc:  # CLI boundary: present a stable failure contract.
        print(f"ERROR: {source}: {exc}", file=sys.stderr)
        return 2

    print(f"\nClean Markdown: {result['output']['markdown']}")
    print(f"Changes logged: {changes}")
    if result.get("pipeline_error"):
        print(f"ERROR: {result['pipeline_error']}", file=sys.stderr)
        return 2
    return 0


def _run_review_action(
    request: ReviewRequest,
    *,
    config: Path,
    parser: argparse.ArgumentParser,
) -> int:
    """Persist one reviewed-word action and print its outcome."""
    try:
        result = apply_review_request(request, config)
    except Exception as exc:
        parser.error(str(exc))
    print(f"{result.label}: {result.target}")
    added = ", ".join(result.added) if result.added else "none"
    print(f"Added {len(result.added)} term(s): {added}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Execute the requested CLI workflow and return a process exit code.

    ``0`` means success, ``1`` means a folder contained no Markdown, and ``2``
    means at least one file or pipeline stage failed.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.simplify_candidates:
        try:
            target = _write_simplified_glossary_candidates(
                args.simplify_candidates,
                args.simplified_output,
            )
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(f"Simplified glossary candidates: {target}")
        return 0
    if args.simplified_output:
        parser.error("--simplified-output requires --simplify-candidates")

    config = args.config.resolve()
    if not config.exists():
        parser.error(f"Config file not found: {config}")

    if review_action_count(args) > 1:
        parser.error(
            "--approve-words, --learn-words, and --reject-words are mutually exclusive"
        )
    requested_review = review_request(args)
    if requested_review:
        return _run_review_action(
            requested_review,
            config=config,
            parser=parser,
        )

    if args.input is None:
        parser.error("input is required unless a word-review command is used")
    source = args.input.resolve()
    output_root = args.output.resolve() if args.output else None
    if not source.exists():
        parser.error(f"Input path not found: {source}")

    if source.is_file():
        if source.suffix.lower() != ".md":
            parser.error(f"Input file must be Markdown (.md): {source}")
        return _run_single_file(source, config=config, output_root=output_root)

    try:
        if output_root is None:
            output_root = configured_output_root(
                config,
                pipeline_factory=OCRPipeline,
            )
        loaded_config = PipelineConfig.load(config)
        loaded_config.validate()
        report_options = ReportOptions.from_config(loaded_config)
        artifact_roots = [output_root]
        if loaded_config.get_bool("backup.enabled", True):
            configured_backup = loaded_config.resolve_path(
                loaded_config.get("backup.directory", "backup")
            )
            if configured_backup is None:
                raise ValueError("backup.directory cannot be null")
            artifact_roots.append(Path(configured_backup))
        if any(path.resolve() == source for path in artifact_roots):
            raise ValueError(
                "output and backup directories must not equal the input folder"
            )
        excluded_roots = [
            path
            for path in artifact_roots
            if path.resolve().is_relative_to(source)
        ]
    except Exception as exc:  # CLI boundary: normalize folder setup failures.
        print(f"ERROR: {config}: {exc}", file=sys.stderr)
        return 2
    files = _markdown_files(
        source,
        args.recursive,
        excluded_roots=tuple(excluded_roots),
    )
    if not files:
        print(f"No Markdown files found in: {source}", file=sys.stderr)
        return 1

    try:
        return run_batch(
            source,
            files=files,
            config=config,
            output_root=output_root,
            continue_on_error=args.continue_on_error,
            report_name=args.batch_report_name,
            run_one=_run_one,
            write_summary=_write_batch_summary,
            write_candidates=_write_batch_glossary_candidates,
            report_options=report_options,
        )
    except Exception as exc:
        # Aggregate report creation happens after per-file processing, so keep
        # failures at the same stable CLI boundary as pipeline exceptions.
        print(f"ERROR: {source}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
