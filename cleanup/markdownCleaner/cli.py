from __future__ import annotations

import argparse
from pathlib import Path

from markdownCleaner.pipeline import OCRPipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean OCR/PDF-extracted novel Markdown for TTS")
    parser.add_argument("input", type=Path, help="Input Markdown file")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.yaml"))
    args = parser.parse_args()

    if not args.input.exists():
        parser.error(f"Input file not found: {args.input}")

    pipeline = OCRPipeline(args.config)
    result = pipeline.run(args.input)
    print(f"\nClean Markdown: {result['output']['markdown']}")
    print(f"Changes logged: {pipeline.context.total_changes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
