"""Command-line interface for single-file and folder batch cleanup."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from markdownCleaner.pipeline import OCRPipeline


def _markdown_files(root: Path, recursive: bool) -> list[Path]:
    iterator = root.rglob("*.md") if recursive else root.glob("*.md")
    return sorted(path for path in iterator if path.is_file())


def _safe_report_name(relative_file: Path) -> Path:
    """Create a collision-free report folder while retaining folder context."""
    return Path("reports") / relative_file.stem.replace(" ", "_")


def _run_one(
    source: Path,
    *,
    config: Path,
    output_directory: Path | None,
    output_name: str | None = None,
    report_subdirectory: Path | str = "reports",
) -> tuple[dict, int]:
    pipeline = OCRPipeline(config)
    result = pipeline.run(
        source,
        output_directory=output_directory,
        output_name=output_name,
        report_subdirectory=report_subdirectory,
    )
    failed_stages = [stage for stage in result["stages"] if not stage.success]
    if failed_stages:
        details = "; ".join(f"{stage.stage}: {stage.error}" for stage in failed_stages)
        raise RuntimeError(f"Pipeline stage failure(s): {details}")
    return result, pipeline.context.total_changes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clean OCR/PDF-extracted novel Markdown for TTS. Input may be a file or folder.",
    )
    parser.add_argument("input", type=Path, help="Input Markdown file or a folder containing .md files")
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output directory. Defaults to paths.output_directory from config.yaml",
    )
    parser.add_argument(
        "-r", "--recursive",
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
        "--continue-on-error",
        action="store_true",
        help="Continue processing remaining files if one file fails",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    source = args.input.resolve()
    config = args.config.resolve()
    output_root = args.output.resolve() if args.output else None

    if not source.exists():
        parser.error(f"Input path not found: {source}")
    if not config.exists():
        parser.error(f"Config file not found: {config}")

    # Single-file mode keeps the familiar reports/ directory.
    if source.is_file():
        if source.suffix.lower() != ".md":
            parser.error(f"Input file must be Markdown (.md): {source}")
        result, changes = _run_one(
            source,
            config=config,
            output_directory=output_root,
        )
        print(f"\nClean Markdown: {result['output']['markdown']}")
        print(f"Changes logged: {changes}")
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
        output_root = Path(probe.config.get("paths.output_directory", "output")).resolve()

    succeeded = 0
    failed = 0
    total_changes = 0

    print(f"Found {len(files)} Markdown file(s).")
    for index, file in enumerate(files, 1):
        relative = file.relative_to(source)
        target_dir = output_root / relative.parent
        report_dir = _safe_report_name(relative)
        output_name = file.stem.replace(" ", "_") + "_clean.md"

        print(f"\n[{index}/{len(files)}] {relative}")
        try:
            result, changes = _run_one(
                file,
                config=config,
                output_directory=target_dir,
                output_name=output_name,
                report_subdirectory=report_dir,
            )
            succeeded += 1
            total_changes += changes
            print(f"Output: {result['output']['markdown']}")
        except Exception as exc:  # CLI boundary: report error and decide policy.
            failed += 1
            print(f"ERROR: {file}: {exc}", file=sys.stderr)
            if not args.continue_on_error:
                return 2

    print("\nBatch completed")
    print(f"Succeeded: {succeeded}")
    print(f"Failed: {failed}")
    print(f"Total changes logged: {total_changes}")
    print(f"Output directory: {output_root}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
