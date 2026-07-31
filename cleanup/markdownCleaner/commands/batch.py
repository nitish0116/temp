"""Folder discovery and batch cleanup orchestration."""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from markdownCleaner.modules.report.exporter import (
    ReportOptions,
    meaningful_output_name,
)


RunOne = Callable[..., tuple[dict, int, list[dict], list[dict]]]
WriteSummary = Callable[..., Path]
WriteCandidates = Callable[[Path, list[dict]], Path]


def markdown_files(
    root: Path,
    recursive: bool,
    *,
    excluded_roots: tuple[Path, ...] = (),
) -> list[Path]:
    """Return source Markdown while excluding generated artifact trees."""

    excluded = tuple(path.resolve() for path in excluded_roots)
    iterator = root.rglob("*.md") if recursive else root.glob("*.md")
    return sorted(
        path
        for path in iterator
        if path.is_file()
        and not any(
            path.resolve().is_relative_to(excluded_root)
            for excluded_root in excluded
        )
    )


def safe_report_name(
    relative_file: Path,
    output_name: str | None = None,
) -> Path:
    """Derive a readable report folder from the unique output filename."""

    readable = Path(output_name or meaningful_output_name(relative_file)).stem
    readable = re.sub(
        r" - Cleaned(?= \(\d+\)$|$)",
        "",
        readable,
        flags=re.IGNORECASE,
    )
    return Path("reports") / readable


def unique_batch_output_name(filename: str, used_names: set[str]) -> str:
    """Return a case-insensitively unique filename and update ``used_names``."""
    path = Path(filename)
    candidate = path.name
    number = 2
    while candidate.casefold() in used_names:
        candidate = f"{path.stem} ({number}){path.suffix}"
        number += 1
    used_names.add(candidate.casefold())
    return candidate


@dataclass(frozen=True, slots=True)
class BatchFileResult:
    """Normalized outcome for one file in a folder batch."""

    entry: dict
    changes: int
    output: str | None
    error: str | None

    @property
    def succeeded(self) -> bool:
        """Whether the pipeline completed without stage or boundary failures."""
        return self.entry["status"] == "success"


def process_batch_file(
    file: Path,
    *,
    relative: Path,
    config: Path,
    target_dir: Path,
    output_name: str,
    report_dir: Path,
    run_one: RunOne,
    minimum_record_confidence: float | None = None,
) -> BatchFileResult:
    """Run one batch file and normalize stage failures and exceptions."""
    try:
        result, changes, records, candidates = run_one(
            file,
            config=config,
            output_directory=target_dir,
            output_name=output_name,
            report_subdirectory=report_dir,
        )
    except Exception as exc:  # CLI boundary: retain the failure in batch reports.
        error = str(exc)
        return BatchFileResult(
            entry={
                "relative_path": str(relative),
                "status": "failed",
                "changes": 0,
                "elapsed_seconds": 0,
                "stage_counts": {},
                "records": [],
                "glossary_candidates": [],
                "output": None,
                "error": error,
            },
            changes=0,
            output=None,
            error=error,
        )

    report_changes = changes
    if minimum_record_confidence is not None:
        records = [
            record
            for record in records
            if float(record.get("confidence", 0.0))
            >= minimum_record_confidence
        ]
        report_changes = len(records)
        stage_counts = dict(
            Counter(str(record.get("stage", "Unknown")) for record in records)
        )
    else:
        stage_counts = {
            stage.stage: stage.changes for stage in result["stages"]
        }
    output = str(result["output"]["markdown"])
    error = result.get("pipeline_error")
    entry = {
        "relative_path": str(relative),
        "status": "failed" if error else "success",
        "changes": report_changes,
        "elapsed_seconds": result.get("elapsed_seconds", 0),
        "stage_counts": stage_counts,
        "records": records,
        "glossary_candidates": candidates,
        "output": output,
    }
    if error:
        entry["error"] = error
    return BatchFileResult(
        entry=entry,
        changes=changes,
        output=output,
        error=error,
    )


def write_batch_reports(
    output_root: Path,
    *,
    source_root: Path,
    entries: list[dict],
    report_name: str,
    write_summary: WriteSummary,
    write_candidates: WriteCandidates,
    options: ReportOptions | None = None,
) -> tuple[Path | None, Path | None]:
    """Write the aggregate artifacts enabled by the active report options."""
    active_options = options or ReportOptions()
    if not active_options.enabled:
        return None, None

    summary_path = (
        write_summary(
            output_root,
            source_root=source_root,
            entries=entries,
            report_name=report_name,
        )
        if active_options.export_summary
        else None
    )
    candidates_path = write_candidates(output_root, entries)
    return summary_path, candidates_path


def run_batch(
    source: Path,
    *,
    files: list[Path],
    config: Path,
    output_root: Path,
    continue_on_error: bool,
    report_name: str,
    run_one: RunOne,
    write_summary: WriteSummary,
    write_candidates: WriteCandidates,
    report_options: ReportOptions | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Process a discovered folder batch and return its CLI exit code."""
    active_report_options = report_options or ReportOptions()
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    succeeded = 0
    failed = 0
    total_changes = 0
    entries: list[dict] = []
    used_output_names: dict[Path, set[str]] = {}
    stopped_early = False

    print(f"Found {len(files)} Markdown file(s).", file=stdout)
    for index, file in enumerate(files, 1):
        relative = file.relative_to(source)
        target_dir = output_root / relative.parent
        directory_names = used_output_names.setdefault(target_dir, set())
        output_name = unique_batch_output_name(
            meaningful_output_name(file),
            directory_names,
        )
        report_dir = safe_report_name(relative, output_name)

        print(f"\n[{index}/{len(files)}] {relative}", file=stdout)
        item = process_batch_file(
            file,
            relative=relative,
            config=config,
            target_dir=target_dir,
            output_name=output_name,
            report_dir=report_dir,
            run_one=run_one,
            minimum_record_confidence=(
                None
                if active_report_options.include_low_confidence
                else active_report_options.review_threshold
            ),
        )
        entries.append(item.entry)
        total_changes += item.changes
        if item.succeeded:
            succeeded += 1
            print(f"Output: {item.output}", file=stdout)
            continue

        failed += 1
        print(f"ERROR: {file}: {item.error}", file=stderr)
        if not continue_on_error:
            stopped_early = True
            break

    summary_path, candidates_path = write_batch_reports(
        output_root,
        source_root=source,
        entries=entries,
        report_name=report_name,
        write_summary=write_summary,
        write_candidates=write_candidates,
        options=active_report_options,
    )
    if stopped_early:
        if summary_path is not None:
            print(f"Batch summary: {summary_path}", file=stdout)
        if candidates_path is not None:
            print(f"Batch glossary candidates: {candidates_path}", file=stdout)
        return 2

    print("\nBatch completed", file=stdout)
    print(f"Succeeded: {succeeded}", file=stdout)
    print(f"Failed: {failed}", file=stdout)
    print(f"Total changes logged: {total_changes}", file=stdout)
    print(f"Output directory: {output_root}", file=stdout)
    if summary_path is not None:
        print(f"Batch summary: {summary_path}", file=stdout)
    if candidates_path is not None:
        print(f"Batch glossary candidates: {candidates_path}", file=stdout)
    return 0 if failed == 0 else 2
