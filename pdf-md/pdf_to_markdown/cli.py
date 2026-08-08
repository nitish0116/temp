"""Command-line interface for PDF-to-Markdown conversion."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import sys

from .converter import (
    ConversionOptions,
    PDFToMarkdownConverter,
    SUPPORTED_EXTENSIONS,
    parse_page_spec,
    readable_output_name,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "output"


def build_parser() -> argparse.ArgumentParser:
    """Build and return the command-line argument parser.

    Returns:
        A parser configured for single-file and batch PDF/EPUB conversion.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Convert a PDF/EPUB file or a folder of documents into structured Markdown."
        )
    )
    parser.add_argument(
        "input", type=Path, help="PDF/EPUB file or folder containing documents"
    )
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("-r", "--recursive", action="store_true")
    parser.add_argument("--pages", help="1-based page selection, for example: 1-10,15")
    parser.add_argument(
        "--layout",
        choices=("fast", "smart"),
        default="fast",
        help="Fast extraction (default) or slower neural layout analysis",
    )
    parser.add_argument(
        "--ocr",
        choices=("auto", "off", "force"),
        default="auto",
        help=(
            "OCR policy; force enables smart layout, while auto retries smart "
            "mode if fast extraction is empty"
        ),
    )
    parser.add_argument("--ocr-language", default="eng")
    parser.add_argument(
        "--keep-header",
        action="store_true",
        help="Preserve detected PDF headers in smart layout mode",
    )
    parser.add_argument(
        "--keep-footer",
        action="store_true",
        help="Preserve detected PDF footers in smart layout mode",
    )
    parser.add_argument(
        "--images", choices=("ignore", "write", "embed"), default="ignore"
    )
    parser.add_argument("--image-format", choices=("png", "jpg"), default="png")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument(
        "--file-workers",
        type=int,
        default=4,
        help=(
            "Maximum documents converted concurrently in separate processes "
            "for folder input (default: 4; use 1 for sequential conversion)"
        ),
    )
    return parser


def _document_files(root: Path, recursive: bool) -> list[Path]:
    """Discover supported documents beneath a directory.

    Args:
        root: Directory in which to search.
        recursive: Whether to include documents in nested directories.

    Returns:
        Sorted PDF and EPUB paths found under ``root``.
    """
    iterator = root.rglob("*") if recursive else root.glob("*")
    return sorted(
        path
        for path in iterator
        if path.is_file() and path.suffix.casefold() in SUPPORTED_EXTENSIONS
    )


def _unique_name(name: str, used: set[str]) -> str:
    """Reserve a case-insensitively unique filename for one output directory.

    Args:
        name: Preferred output filename.
        used: Case-folded names already reserved in the directory.

    Returns:
        The original name or a collision-safe variant with a numeric suffix.
    """
    path = Path(name)
    candidate = path.name
    number = 2
    while candidate.casefold() in used:
        candidate = f"{path.stem} ({number}){path.suffix}"
        number += 1
    used.add(candidate.casefold())
    return candidate


def _conversion_options(args: argparse.Namespace) -> ConversionOptions:
    """Translate validated CLI arguments into converter settings."""
    try:
        pages = parse_page_spec(args.pages)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return ConversionOptions(
        layout_mode=args.layout,
        ocr_mode=args.ocr,
        ocr_language=args.ocr_language,
        header=args.keep_header,
        footer=args.keep_footer,
        image_mode=args.images,
        image_format=args.image_format,
        pages=pages,
        show_progress=not args.quiet,
    )


def _sources(source: Path, recursive: bool) -> tuple[list[Path], Path]:
    """Return documents to convert and the root used for relative paths."""
    if source.is_file():
        return [source], source.parent
    return _document_files(source, recursive), source


def _planned_targets(
    files: list[Path], source_root: Path, output_root: Path
) -> list[tuple[Path, Path, Path]]:
    """Precompute deterministic, collision-safe targets before dispatch."""
    used_by_directory: dict[Path, set[str]] = {}
    planned: list[tuple[Path, Path, Path]] = []
    for document in files:
        relative = document.relative_to(source_root)
        target_dir = output_root / relative.parent
        used = used_by_directory.setdefault(target_dir, set())
        target = target_dir / _unique_name(readable_output_name(document), used)
        planned.append((document, relative, target))
    return planned


def _convert_document_worker(
    document: Path, target: Path, options: ConversionOptions
):
    """Convert one document in an isolated file-worker process."""
    return PDFToMarkdownConverter(options).convert(document, target)


def _convert_documents(
    files: list[Path],
    source_root: Path,
    output_root: Path,
    converter: PDFToMarkdownConverter,
    continue_on_error: bool,
    file_workers: int = 1,
    options: ConversionOptions | None = None,
) -> int:
    """Convert a document batch, preserving subdirectories and unique names."""
    planned = _planned_targets(files, source_root, output_root)
    if file_workers > 1 and len(planned) > 1:
        if options is None:
            raise ValueError("options are required for parallel conversion")
        return _convert_documents_parallel(
            planned, options, continue_on_error, file_workers
        )

    failures = 0
    for index, (document, relative, target) in enumerate(planned, 1):
        print(f"[{index}/{len(files)}] {relative}")
        try:
            result = converter.convert(document, target)
            print(
                f"  Output: {result.markdown} "
                f"({result.pages} pages, {result.characters:,} characters)"
            )
        except Exception as exc:
            failures += 1
            print(f"  ERROR: {exc}", file=sys.stderr)
            if not continue_on_error:
                return 2

    print(f"Completed: {len(files) - failures} succeeded, {failures} failed")
    return 0 if failures == 0 else 2


def _convert_documents_parallel(
    planned: list[tuple[Path, Path, Path]],
    options: ConversionOptions,
    continue_on_error: bool,
    file_workers: int,
) -> int:
    """Convert a planned document batch in up to four worker processes."""
    worker_count = min(file_workers, len(planned), 4)
    print(
        f"Converting {len(planned)} documents with "
        f"{worker_count} file processes"
    )
    failures = 0
    completed = 0
    stop_after_failure = False

    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        future_to_item = {
            executor.submit(_convert_document_worker, document, target, options): (
                index,
                relative,
                target,
            )
            for index, (document, relative, target) in enumerate(planned, 1)
        }
        for future in as_completed(future_to_item):
            index, relative, _target = future_to_item[future]
            print(f"[{index}/{len(planned)}] {relative}")
            try:
                result = future.result()
                completed += 1
                print(
                    f"  Output: {result.markdown} "
                    f"({result.pages} pages, {result.characters:,} characters)"
                )
            except Exception as exc:
                failures += 1
                print(f"  ERROR: {exc}", file=sys.stderr)
                if not continue_on_error:
                    stop_after_failure = True
                    for pending in future_to_item:
                        pending.cancel()
                    break

    if stop_after_failure:
        return 2
    print(f"Completed: {completed} succeeded, {failures} failed")
    return 0 if failures == 0 else 2


def main(argv: list[str] | None = None) -> int:
    """Run PDF/EPUB conversion from command-line arguments.

    Args:
        argv: Optional argument list. When omitted, arguments come from
            ``sys.argv``.

    Returns:
        Process exit code: zero for success, one when no documents are found,
        or two when conversion fails.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    source = args.input.resolve()
    output_root = args.output.resolve()
    if not source.exists():
        parser.error(f"Input path not found: {source}")

    try:
        options = _conversion_options(args)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    if args.file_workers < 1 or args.file_workers > 4:
        parser.error("--file-workers must be between 1 and 4")
    converter = PDFToMarkdownConverter(options)

    files, source_root = _sources(source, args.recursive)
    if not files:
        print(f"No PDF or EPUB files found in: {source}", file=sys.stderr)
        return 1

    return _convert_documents(
        files,
        source_root,
        output_root,
        converter,
        args.continue_on_error,
        args.file_workers,
        options,
    )


if __name__ == "__main__":
    raise SystemExit(main())
