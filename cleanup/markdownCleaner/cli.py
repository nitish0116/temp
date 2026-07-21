"""Command-line interface for Markdown cleanup and glossary approval.

Typical calls::

    python -m markdownCleaner.cli novel.md
    python -m markdownCleaner.cli novel.md --output cleaned
    python -m markdownCleaner.cli books --recursive --continue-on-error
    python -m markdownCleaner.cli --approve-words sitrep noncoms
    python -m markdownCleaner.cli --learn-words sitrep noncoms
    python -m markdownCleaner.cli --reject-words offense humor

The first two forms process one file, the third performs a folder batch, and
the final form updates the configured glossary without running cleanup.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from markdownCleaner.pipeline import OCRPipeline
from markdownCleaner.modules.report.exporter import meaningful_output_name
from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.symspell.vocabulary import (
    merge_approved_words,
    merge_learned_words,
    merge_rejected_words,
)


def _markdown_files(root: Path, recursive: bool) -> list[Path]:
    """Return sorted Markdown files found below ``root``.

    Args:
        root: Directory to search.
        recursive: Search all descendants when true; otherwise inspect only
            direct children.

    Example:
        ``_markdown_files(Path("books"), recursive=True)`` finds Markdown in
        ``books`` and each volume subdirectory.
    """
    iterator = root.rglob("*.md") if recursive else root.glob("*.md")
    return sorted(path for path in iterator if path.is_file())


def _safe_report_name(relative_file: Path) -> Path:
    """Create a collision-free report folder while retaining folder context."""
    readable = Path(meaningful_output_name(relative_file)).stem
    readable = readable.removesuffix(" - Cleaned")
    return Path("reports") / readable


def _unique_batch_output_name(filename: str, used_names: set[str]) -> str:
    """Return a case-insensitively unique filename for one output directory.

    The supplied set is updated in place. For example, requesting ``Book.md``
    twice returns ``Book.md`` and then ``Book (2).md``.
    """
    path = Path(filename)
    candidate = path.name
    number = 2
    while candidate.casefold() in used_names:
        candidate = f"{path.stem} ({number}){path.suffix}"
        number += 1
    used_names.add(candidate.casefold())
    return candidate


def _run_one(
    source: Path,
    *,
    config: Path,
    output_directory: Path | None,
    output_name: str | None = None,
    report_subdirectory: Path | str = "reports",
) -> tuple[dict, int, list[dict]]:
    """Run the configured pipeline for one Markdown source.

    Args:
        source: Markdown file to clean.
        config: YAML configuration file used to construct the pipeline.
        output_directory: Optional destination overriding the configured path.
        output_name: Optional filename, primarily used to avoid batch clashes.
        report_subdirectory: Report location relative to the output directory.

    Returns:
        A tuple containing the pipeline result mapping, total logged change
        count, and serializable change records. Failed stages are summarized in
        ``result["pipeline_error"]`` so batch callers can apply their policy.
    """
    pipeline = OCRPipeline(config)
    result = pipeline.run(
        source,
        output_directory=output_directory,
        output_name=output_name,
        report_subdirectory=report_subdirectory,
    )
    records = [asdict(record) for record in pipeline.context.tracker.records]
    failed_stages = [stage for stage in result["stages"] if not stage.success]
    if failed_stages:
        details = "; ".join(f"{stage.stage}: {stage.error}" for stage in failed_stages)
        result["pipeline_error"] = f"Pipeline stage failure(s): {details}"
    return result, pipeline.context.total_changes, records


def _md_code(value: object) -> str:
    """Return text safe for a fenced Markdown code block."""
    text = "" if value is None else str(value)
    # Avoid accidentally closing our own fence.
    return text.replace("```", "` ` `")


def _write_batch_summary(
    output_root: Path,
    *,
    source_root: Path,
    entries: list[dict],
    report_name: str = "batch_summary.md",
) -> Path:
    """Write one aggregate Markdown report for the entire batch run."""
    report_dir = output_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / report_name

    succeeded = sum(1 for item in entries if item["status"] == "success")
    failed = sum(1 for item in entries if item["status"] == "failed")
    total_changes = sum(item.get("changes", 0) for item in entries)
    total_elapsed = sum(float(item.get("elapsed_seconds", 0) or 0) for item in entries)

    stage_totals: Counter[str] = Counter()
    for item in entries:
        for stage_name, count in item.get("stage_counts", {}).items():
            stage_totals[stage_name] += int(count or 0)

    lines: list[str] = [
        "# Batch Cleanup Summary",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Input root: `{source_root}`",
        f"- Output root: `{output_root}`",
        f"- Files discovered: {len(entries)}",
        f"- Succeeded: {succeeded}",
        f"- Failed: {failed}",
        f"- Total changes logged: {total_changes}",
        f"- Total pipeline time: {total_elapsed:.2f} seconds",
        "",
        "## Aggregate changes by stage",
        "",
        "| Stage | Changes |",
        "|---|---:|",
    ]

    if stage_totals:
        for stage_name, count in stage_totals.items():
            lines.append(f"| {stage_name} | {count} |")
    else:
        lines.append("| — | 0 |")

    lines.extend(
        [
            "",
            "## Per-file results",
            "",
            "| File | Status | Changes | Time (s) | Output |",
            "|---|---|---:|---:|---|",
        ]
    )

    for item in entries:
        output = item.get("output") or "—"
        error = item.get("error")
        status = item["status"]
        if error:
            status = f"{status}: {str(error).replace('|', '/')}"
        lines.append(
            f"| `{item['relative_path']}` | {status} | {item.get('changes', 0)} | "
            f"{float(item.get('elapsed_seconds', 0) or 0):.2f} | `{output}` |"
        )

    lines.extend(["", "## Detailed changes", ""])

    for item in entries:
        lines.append(f"### {item['relative_path']}")
        lines.append("")
        lines.append(f"Status: **{item['status']}**  ")
        lines.append(f"Changes: **{item.get('changes', 0)}**")
        lines.append("")

        if item.get("error"):
            lines.extend(["Error:", "", "```text", _md_code(item["error"]), "```", ""])
            continue

        stage_counts = item.get("stage_counts", {})
        if stage_counts:
            lines.extend(["Stage totals:", "", "| Stage | Changes |", "|---|---:|"])
            for stage_name, count in stage_counts.items():
                lines.append(f"| {stage_name} | {count} |")
            lines.append("")

        records = item.get("records", [])
        if not records:
            lines.extend(["No change records were logged.", ""])
            continue

        for number, record in enumerate(records, 1):
            location = f"line {record.get('line', 0)}"
            stage = record.get("stage", "Unknown")
            reason = record.get("reason", "")
            confidence = record.get("confidence", "")
            lines.append(f"#### Change {number} — {stage} ({location})")
            lines.append("")
            lines.append(f"- Reason: {reason}")
            lines.append(f"- Confidence: {confidence}")
            lines.append("")
            lines.append("Before:")
            lines.append("")
            lines.append("```text")
            lines.append(_md_code(record.get("before", "")))
            lines.append("```")
            lines.append("")
            lines.append("After:")
            lines.append("")
            lines.append("```text")
            lines.append(_md_code(record.get("after", "")))
            lines.append("```")
            lines.append("")

    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report_path


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for all supported CLI call signatures.

    Supported forms::

        markdownCleaner INPUT.md [--output DIR] [--config FILE]
        markdownCleaner INPUT_DIR [--recursive] [--continue-on-error]
        markdownCleaner --approve-words WORD [WORD ...] [--glossary-file FILE]
        markdownCleaner --learn-words WORD [WORD ...] [--learned-file FILE]
        markdownCleaner --reject-words WORD [WORD ...] [--rejected-file FILE]

    ``python -m markdownCleaner.cli`` can replace ``markdownCleaner`` when the
    project is run directly from source.
    """
    parser = argparse.ArgumentParser(
        description="Clean OCR/PDF-extracted novel Markdown for TTS. Input may be a file or folder.",
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        help="Input Markdown file or a folder containing .md files",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output directory. Defaults to paths.output_directory from config.yaml",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="When input is a folder, process .md files in all subfolders",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("config.yaml"),
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--batch-report-name",
        default="batch_summary.md",
        help="Filename for the combined batch report (default: batch_summary.md)",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue processing remaining files if one file fails",
    )
    parser.add_argument(
        "--approve-words",
        nargs="+",
        metavar="WORD",
        help="Explicitly add reviewed terms to custom_words.json, then exit",
    )
    parser.add_argument(
        "--glossary-file",
        type=Path,
        default=None,
        help="Glossary to update with --approve-words (defaults to symspell.glossary)",
    )
    parser.add_argument(
        "--learn-words",
        nargs="+",
        metavar="WORD",
        help="Safely add reviewed terms to learned_words.json, then exit",
    )
    parser.add_argument(
        "--learned-file",
        type=Path,
        default=None,
        help="File to update with --learn-words (defaults to symspell.learned)",
    )
    parser.add_argument(
        "--reject-words",
        nargs="+",
        metavar="WORD",
        help="Suppress reviewed terms from future glossary candidate reports",
    )
    parser.add_argument(
        "--rejected-file",
        type=Path,
        default=None,
        help=(
            "File to update with --reject-words "
            "(defaults to vocabulary_candidates.rejected)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Execute the requested CLI workflow and return a process exit code.

    Args:
        argv: Arguments without the executable name. Passing ``None`` reads
            ``sys.argv``; passing a list supports programmatic calls such as
            ``main(["book.md", "--output", "cleaned"])``.

    Workflow:
        1. Resolve and validate the configuration.
        2. Approve glossary words and exit, if requested.
        3. Run one file directly, or discover and process a folder batch.
        4. Write per-file reports plus an aggregate report for folder runs.

    Returns:
        ``0`` on success, ``1`` when a folder contains no Markdown, and ``2``
        when one or more pipeline stages or files fail.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    config = args.config.resolve()
    if not config.exists():
        parser.error(f"Config file not found: {config}")

    review_actions = [args.approve_words, args.learn_words, args.reject_words]
    if sum(bool(action) for action in review_actions) > 1:
        parser.error(
            "--approve-words, --learn-words, and --reject-words are mutually exclusive"
        )

    if any(review_actions):
        loaded_config = PipelineConfig.load(config)
        learning = bool(args.learn_words)
        rejecting = bool(args.reject_words)
        explicit_file = (
            args.rejected_file
            if rejecting
            else args.learned_file if learning else args.glossary_file
        )
        config_key = (
            "vocabulary_candidates.rejected"
            if rejecting
            else "symspell.learned" if learning else "symspell.glossary"
        )
        default_file = (
            "data/rejected_words.json"
            if rejecting
            else "data/learned_words.json" if learning else "data/custom_words.json"
        )
        target = (
            explicit_file.resolve()
            if explicit_file
            else Path(
                loaded_config.resolve_path(loaded_config.get(config_key, default_file))
            )
        )
        try:
            added = (
                merge_rejected_words(target, args.reject_words)
                if rejecting
                else (
                    merge_learned_words(target, args.learn_words)
                    if learning
                    else merge_approved_words(target, args.approve_words)
                )
            )
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        label = (
            "Rejected words"
            if rejecting
            else "Learned words" if learning else "Glossary"
        )
        print(f"{label}: {target}")
        print(f"Added {len(added)} term(s): {', '.join(added) if added else 'none'}")
        return 0

    if args.input is None:
        parser.error("input is required unless a word-review command is used")
    source = args.input.resolve()
    output_root = args.output.resolve() if args.output else None

    if not source.exists():
        parser.error(f"Input path not found: {source}")

    # Single-file mode keeps the familiar reports/ directory.
    if source.is_file():
        if source.suffix.lower() != ".md":
            parser.error(f"Input file must be Markdown (.md): {source}")
        result, changes, _records = _run_one(
            source,
            config=config,
            output_directory=output_root,
        )
        print(f"\nClean Markdown: {result['output']['markdown']}")
        print(f"Changes logged: {changes}")
        if result.get("pipeline_error"):
            print(f"ERROR: {result['pipeline_error']}", file=sys.stderr)
            return 2
        return 0

    # Folder mode.
    files = _markdown_files(source, args.recursive)
    if not files:
        print(f"No Markdown files found in: {source}", file=sys.stderr)
        return 1

    # Resolve the configured output root once so relative folder structure can
    # be preserved across all files.
    if output_root is None:
        probe = OCRPipeline(config)
        output_root = Path(
            probe.config.get("paths.output_directory", "output")
        ).resolve()

    succeeded = 0
    failed = 0
    total_changes = 0
    batch_entries: list[dict] = []
    used_output_names: dict[Path, set[str]] = {}

    print(f"Found {len(files)} Markdown file(s).")
    for index, file in enumerate(files, 1):
        relative = file.relative_to(source)
        target_dir = output_root / relative.parent
        report_dir = _safe_report_name(relative)
        directory_names = used_output_names.setdefault(target_dir, set())
        output_name = _unique_batch_output_name(
            meaningful_output_name(file),
            directory_names,
        )

        print(f"\n[{index}/{len(files)}] {relative}")
        try:
            result, changes, records = _run_one(
                file,
                config=config,
                output_directory=target_dir,
                output_name=output_name,
                report_subdirectory=report_dir,
            )
            total_changes += changes
            stage_counts = {stage.stage: stage.changes for stage in result["stages"]}
            pipeline_error = result.get("pipeline_error")
            if pipeline_error:
                failed += 1
                batch_entries.append(
                    {
                        "relative_path": str(relative),
                        "status": "failed",
                        "changes": changes,
                        "elapsed_seconds": result.get("elapsed_seconds", 0),
                        "stage_counts": stage_counts,
                        "records": records,
                        "output": str(result["output"]["markdown"]),
                        "error": pipeline_error,
                    }
                )
                print(f"ERROR: {file}: {pipeline_error}", file=sys.stderr)
                if not args.continue_on_error:
                    summary_path = _write_batch_summary(
                        output_root,
                        source_root=source,
                        entries=batch_entries,
                        report_name=args.batch_report_name,
                    )
                    print(f"Batch summary: {summary_path}")
                    return 2
            else:
                succeeded += 1
                batch_entries.append(
                    {
                        "relative_path": str(relative),
                        "status": "success",
                        "changes": changes,
                        "elapsed_seconds": result.get("elapsed_seconds", 0),
                        "stage_counts": stage_counts,
                        "records": records,
                        "output": str(result["output"]["markdown"]),
                    }
                )
                print(f"Output: {result['output']['markdown']}")
        except Exception as exc:  # CLI boundary: report error and decide policy.
            failed += 1
            batch_entries.append(
                {
                    "relative_path": str(relative),
                    "status": "failed",
                    "changes": 0,
                    "elapsed_seconds": 0,
                    "stage_counts": {},
                    "records": [],
                    "output": None,
                    "error": str(exc),
                }
            )
            print(f"ERROR: {file}: {exc}", file=sys.stderr)
            if not args.continue_on_error:
                summary_path = _write_batch_summary(
                    output_root,
                    source_root=source,
                    entries=batch_entries,
                    report_name=args.batch_report_name,
                )
                print(f"Batch summary: {summary_path}")
                return 2

    summary_path = _write_batch_summary(
        output_root,
        source_root=source,
        entries=batch_entries,
        report_name=args.batch_report_name,
    )

    print("\nBatch completed")
    print(f"Succeeded: {succeeded}")
    print(f"Failed: {failed}")
    print(f"Total changes logged: {total_changes}")
    print(f"Output directory: {output_root}")
    print(f"Batch summary: {summary_path}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
