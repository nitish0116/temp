"""Pure report transformations plus their small filesystem adapters."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path


BatchEntry = Mapping[str, object]


def validate_batch_report_name(value: str) -> str:
    """Return a safe Markdown filename that cannot escape the report folder."""

    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or path.parent != Path(".")
        or path.suffix.casefold() != ".md"
    ):
        raise ValueError(
            "batch report name must be a Markdown filename without directories"
        )
    return path.name


def markdown_code(value: object) -> str:
    """Return text safe for a fenced Markdown code block."""
    text = "" if value is None else str(value)
    return text.replace("```", "` ` `")


def _stage_totals(entries: Sequence[BatchEntry]) -> Counter[str]:
    """Combine per-file stage counts in their first-seen order."""
    totals: Counter[str] = Counter()
    for item in entries:
        stage_counts = item.get("stage_counts", {})
        if not isinstance(stage_counts, Mapping):
            continue
        for stage_name, count in stage_counts.items():
            totals[str(stage_name)] += int(count or 0)
    return totals


def _summary_header_lines(
    output_root: Path,
    source_root: Path,
    entries: Sequence[BatchEntry],
    generated_at: datetime,
    stage_totals: Counter[str],
) -> list[str]:
    """Build aggregate metadata and stage-table lines."""
    succeeded = sum(1 for item in entries if item["status"] == "success")
    failed = sum(1 for item in entries if item["status"] == "failed")
    total_changes = sum(int(item.get("changes", 0) or 0) for item in entries)
    total_elapsed = sum(
        float(item.get("elapsed_seconds", 0) or 0) for item in entries
    )
    lines = [
        "# Batch Cleanup Summary",
        "",
        f"- Generated: {generated_at.isoformat(timespec='seconds')}",
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
        lines.extend(
            f"| {stage_name} | {count} |"
            for stage_name, count in stage_totals.items()
        )
    else:
        lines.append("| — | 0 |")
    return lines


def _file_table_lines(entries: Sequence[BatchEntry]) -> list[str]:
    """Build the compact per-file result table."""
    lines = [
        "",
        "## Per-file results",
        "",
        "| File | Status | Changes | Time (s) | Output |",
        "|---|---|---:|---:|---|",
    ]
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
    return lines


def _change_lines(number: int, record: Mapping[str, object]) -> list[str]:
    """Render one detailed change record."""
    location = f"line {record.get('line', 0)}"
    stage = record.get("stage", "Unknown")
    reason = record.get("reason", "")
    confidence = record.get("confidence", "")
    applied = bool(record.get("applied", True))
    broken_word = record.get("broken_word")
    lines = [
        f"#### Change {number} — {stage} ({location})",
        "",
        f"- Reason: {reason}",
        f"- Confidence: {confidence}",
        f"- Applied: {'yes' if applied else 'no'}",
    ]
    if broken_word:
        lines.append(f"- Broken word: `{broken_word}`")
    lines.extend(
        [
            "",
            "Before:",
            "",
            "```text",
            markdown_code(record.get("before", "")),
            "```",
            "",
            "After:",
            "",
            "```text",
            markdown_code(record.get("after", "")),
            "```",
            "",
        ]
    )
    return lines


def _file_detail_lines(item: BatchEntry) -> list[str]:
    """Render status, stage counts, and change records for one file."""
    lines = [
        f"### {item['relative_path']}",
        "",
        f"Status: **{item['status']}**  ",
        f"Changes: **{item.get('changes', 0)}**",
        "",
    ]
    if item.get("error"):
        lines.extend(
            ["Error:", "", "```text", markdown_code(item["error"]), "```", ""]
        )
        return lines

    stage_counts = item.get("stage_counts", {})
    if isinstance(stage_counts, Mapping) and stage_counts:
        lines.extend(["Stage totals:", "", "| Stage | Changes |", "|---|---:|"])
        lines.extend(
            f"| {stage_name} | {count} |"
            for stage_name, count in stage_counts.items()
        )
        lines.append("")

    records = item.get("records", [])
    if not isinstance(records, Sequence) or not records:
        lines.extend(["No change records were logged.", ""])
        return lines
    for number, record in enumerate(records, 1):
        if isinstance(record, Mapping):
            lines.extend(_change_lines(number, record))
    return lines


def render_batch_summary(
    output_root: Path,
    *,
    source_root: Path,
    entries: Sequence[BatchEntry],
    generated_at: datetime | None = None,
) -> str:
    """Render an aggregate batch report without performing filesystem writes."""
    generated_at = generated_at or datetime.now()
    lines = _summary_header_lines(
        output_root,
        source_root,
        entries,
        generated_at,
        _stage_totals(entries),
    )
    lines.extend(_file_table_lines(entries))
    lines.extend(["", "## Detailed changes", ""])
    for item in entries:
        lines.extend(_file_detail_lines(item))

    return "\n".join(lines).rstrip() + "\n"


def write_batch_summary(
    output_root: Path,
    *,
    source_root: Path,
    entries: Sequence[BatchEntry],
    report_name: str = "batch_summary.md",
) -> Path:
    """Write one aggregate Markdown report for the entire batch run."""
    report_name = validate_batch_report_name(report_name)
    report_dir = output_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / report_name
    report_path.write_text(
        render_batch_summary(
            output_root,
            source_root=source_root,
            entries=entries,
        ),
        encoding="utf-8",
    )
    return report_path


def aggregate_glossary_candidates(
    entries: Sequence[BatchEntry],
) -> list[dict]:
    """Merge per-file vocabulary candidates without writing the JSON report."""
    combined: dict[str, dict] = {}
    for entry in entries:
        relative_path = str(entry.get("relative_path", ""))
        candidates = entry.get("glossary_candidates", [])
        if not isinstance(candidates, Sequence):
            continue
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            word = str(candidate.get("word", "")).strip()
            if not word:
                continue
            key = word.casefold()
            occurrences = int(candidate.get("occurrences", 0) or 0)
            aggregate = combined.setdefault(
                key,
                _new_candidate_aggregate(word, candidate),
            )
            _add_candidate_evidence(
                aggregate,
                candidate,
                relative_path=relative_path,
                occurrences=occurrences,
            )

    return sorted(
        combined.values(),
        key=lambda item: (-item["occurrences"], item["word"].casefold()),
    )


def _new_candidate_aggregate(
    word: str,
    candidate: Mapping[str, object],
) -> dict:
    """Create the stable master-report schema for one candidate."""
    return {
        "word": word,
        "occurrences": 0,
        "files": [],
        "suggested_correction": candidate.get("suggested_correction"),
        "edit_distance": candidate.get("edit_distance"),
        "confidence": candidate.get("confidence"),
        "classification": candidate.get("classification", "unknown"),
        "classification_confidence": candidate.get(
            "classification_confidence", 0.0
        ),
        "classification_basis": candidate.get(
            "classification_basis", "not classified"
        ),
        "status": "pending_review",
    }


def _add_candidate_evidence(
    aggregate: dict,
    candidate: Mapping[str, object],
    *,
    relative_path: str,
    occurrences: int,
) -> None:
    """Merge one per-file candidate into its case-insensitive aggregate."""
    aggregate["occurrences"] += occurrences
    aggregate["files"].append(
        {
            "file": relative_path,
            "occurrences": occurrences,
            "lines": list(candidate.get("lines", [])),
        }
    )
    current_confidence = aggregate.get("confidence")
    candidate_confidence = candidate.get("confidence")
    if candidate_confidence is not None and (
        current_confidence is None or candidate_confidence > current_confidence
    ):
        aggregate["suggested_correction"] = candidate.get("suggested_correction")
        aggregate["edit_distance"] = candidate.get("edit_distance")
        aggregate["confidence"] = candidate_confidence

    if aggregate.get("classification") == "unknown" and candidate.get(
        "classification"
    ) in {"noun", "adjective", "verb"}:
        aggregate["classification"] = candidate["classification"]
        aggregate["classification_confidence"] = candidate.get(
            "classification_confidence", 0.0
        )
        aggregate["classification_basis"] = candidate.get(
            "classification_basis", "not classified"
        )


def write_batch_glossary_candidates(
    output_root: Path,
    entries: Sequence[BatchEntry],
) -> Path:
    """Write the aggregated batch glossary candidate report."""
    values = aggregate_glossary_candidates(entries)
    report_dir = output_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "glossary_candidates.json"
    report_path.write_text(
        json.dumps(values, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report_path


def simplify_glossary_candidates(candidates: object) -> list[dict]:
    """Validate and project candidate data onto the compact review schema."""
    if not isinstance(candidates, list):
        raise ValueError("Master glossary candidate JSON must contain a list.")

    simplified = []
    for index, candidate in enumerate(candidates, 1):
        if not isinstance(candidate, dict):
            raise ValueError(f"Candidate {index} must be a JSON object.")
        simplified.append(
            {
                "word": candidate.get("word"),
                "occurrences": candidate.get("occurrences", 0),
                "suggested_correction": candidate.get("suggested_correction"),
            }
        )
    return simplified


def write_simplified_glossary_candidates(
    source: Path,
    output: Path | None = None,
) -> Path:
    """Read, validate, and write the compact glossary candidate report."""
    source = source.resolve()
    if not source.is_file():
        raise ValueError(f"Glossary candidate report not found: {source}")
    try:
        candidates = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {source}: line {exc.lineno}, column {exc.colno}"
        ) from exc

    simplified = simplify_glossary_candidates(candidates)
    target = (
        output.resolve()
        if output
        else source.with_name(f"{source.stem}_simplified.json")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(simplified, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target
