"""Argument parser construction for the Markdown cleaner command."""

from __future__ import annotations

import argparse
from pathlib import Path

from .reports import validate_batch_report_name


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def _batch_report_name(value: str) -> str:
    try:
        return validate_batch_report_name(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser(default_config: Path = DEFAULT_CONFIG) -> argparse.ArgumentParser:
    """Build the parser for cleanup, batch, and glossary-review workflows."""
    parser = argparse.ArgumentParser(
        description=(
            "Clean OCR/PDF-extracted novel Markdown for TTS. "
            "Input may be a file or folder."
        ),
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
        default=default_config,
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--batch-report-name",
        type=_batch_report_name,
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
    parser.add_argument(
        "--simplify-candidates",
        type=Path,
        metavar="MASTER_JSON",
        help=(
            "Write a simplified glossary candidate JSON containing only word, "
            "occurrences, and suggested_correction, then exit"
        ),
    )
    parser.add_argument(
        "--simplified-output",
        type=Path,
        default=None,
        help="Optional destination used with --simplify-candidates",
    )
    parser.add_argument(
        "--build-broken-word-review",
        type=Path,
        metavar="LIBRARY",
        help=(
            "Scan a Markdown library with a portable cache and create "
            "classified broken-word review records, then exit"
        ),
    )
    parser.add_argument(
        "--broken-word-review-output",
        type=Path,
        default=None,
        help="Main high-confidence broken-word review JSON",
    )
    parser.add_argument(
        "--broken-word-ambiguous-output",
        type=Path,
        default=None,
        help="Separate JSON for unresolved broken-word candidates",
    )
    parser.add_argument(
        "--broken-word-review-cache",
        type=Path,
        default=None,
        help="Portable incremental scan cache (.json or .json.gz)",
    )
    parser.add_argument(
        "--rebuild-broken-word-cache",
        action="store_true",
        help="Discard the broken-word cache and rescan every Markdown file",
    )
    parser.add_argument(
        "--max-broken-word-contexts",
        type=int,
        default=3,
        help="Maximum example contexts retained per candidate (default: 3)",
    )
    parser.add_argument(
        "--promote-broken-word-review",
        type=Path,
        metavar="REVIEW_JSON",
        help=(
            "Copy accepted/rejected candidates from a review JSON into the "
            "configured decision store, then exit"
        ),
    )
    parser.add_argument(
        "--broken-word-decisions-file",
        type=Path,
        default=None,
        help=(
            "Decision store used with --promote-broken-word-review; defaults "
            "to symspell.broken_word_decisions"
        ),
    )
    parser.add_argument(
        "--import-broken-word-candidates",
        type=Path,
        metavar="REVIEW_JSON",
        help=(
            "Import accepted/review proposals and corpus rejections into the "
            "non-authoritative transformer candidate store, then exit"
        ),
    )
    parser.add_argument(
        "--ocr-boundary-candidate-file",
        type=Path,
        default=None,
        help=(
            "Candidate store used with --import-broken-word-candidates; "
            "defaults to context_validator.candidate_file"
        ),
    )
    return parser
