"""Translate an aligned source transcript while preserving every cue boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .auto_prepare_script import translate_target
    from .transcribe import srt_timestamp
except ImportError:
    from auto_prepare_script import translate_target
    from transcribe import srt_timestamp


def write_srt(path: Path, segments: list[dict]) -> None:
    """Write translated cue dictionaries as UTF-8 SRT."""
    content = "\n\n".join(
        f"{index}\n{srt_timestamp(item['start'])} --> {srt_timestamp(item['end'])}\n{item['text']}"
        for index, item in enumerate(segments, start=1)
    )
    path.write_text(content + "\n", encoding="utf-8")


def main() -> None:
    """Translate aligned JSON and write boundary-preserving JSON plus SRT."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--model", default="facebook/nllb-200-distilled-600M")
    parser.add_argument("--source-model-language")
    parser.add_argument("--target-model-language")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-srt", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    translated = translate_target(
        source, args.target_language, args.model, args.source_model_language,
        args.target_model_language, args.device,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_srt.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(translated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_srt(args.output_srt, translated["segments"])
    print(f"Translated {len(translated['segments'])} aligned cues to {args.target_language}")


if __name__ == "__main__":
    main()
