#!/usr/bin/env python3
"""Generate a deterministic Git-tracked manifest for external media files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


MEDIA_EXTENSIONS = frozenset({".mp3", ".mp4"})
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 digest of *path*.

    Reading a Files On-Demand placeholder may download the complete file.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def availability_from_attributes(attributes: int) -> str:
    """Translate Windows cloud-file attributes into a portable label."""
    cloud_only_bits = (
        FILE_ATTRIBUTE_OFFLINE
        | FILE_ATTRIBUTE_RECALL_ON_OPEN
        | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    )
    return "online-only" if attributes & cloud_only_bits else "local"


def build_manifest(
    source: Path,
    *,
    root_label: str = "OneDrive/Library",
    include_hashes: bool = False,
) -> dict:
    """Return a deterministic manifest for MP3 and MP4 files below *source*."""
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise NotADirectoryError(source)

    entries = []
    for path in source.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue

        stat = path.stat()
        attributes = getattr(stat, "st_file_attributes", 0)
        entry = {
            "path": path.relative_to(source).as_posix(),
            "type": path.suffix.lower().lstrip("."),
            "size_bytes": stat.st_size,
            "modified_utc": datetime.fromtimestamp(
                stat.st_mtime, timezone.utc
            ).isoformat(timespec="seconds"),
            "availability": availability_from_attributes(attributes),
        }
        if include_hashes:
            entry["sha256"] = sha256_file(path)
        entries.append(entry)

    entries.sort(key=lambda item: (item["path"].casefold(), item["path"]))
    counts = {
        extension.lstrip("."): sum(
            entry["type"] == extension.lstrip(".") for entry in entries
        )
        for extension in sorted(MEDIA_EXTENSIONS)
    }
    return {
        "schema_version": 1,
        "media_root": root_label,
        "hashes_included": include_hashes,
        "summary": {
            "file_count": len(entries),
            "total_size_bytes": sum(entry["size_bytes"] for entry in entries),
            "by_type": counts,
        },
        "files": entries,
    }


def serialized_manifest(manifest: dict) -> str:
    """Serialize a manifest with stable formatting and a final newline."""
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        help="external media root, for example D:\\OneDrive\\Library",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("media-manifest.json"),
        help="manifest output path (default: media-manifest.json)",
    )
    parser.add_argument(
        "--root-label",
        default="OneDrive/Library",
        help="portable root label stored in the manifest",
    )
    parser.add_argument(
        "--sha256",
        action="store_true",
        help="hash every file; cloud-only files may be fully downloaded",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit with status 1 when the existing manifest is outdated",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Generate or check the configured media manifest."""
    args = build_parser().parse_args(argv)
    manifest_text = serialized_manifest(
        build_manifest(
            args.source,
            root_label=args.root_label,
            include_hashes=args.sha256,
        )
    )
    output = args.output.expanduser().resolve()

    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != manifest_text:
            print(f"Media manifest is outdated: {output}", file=sys.stderr)
            return 1
        print(f"Media manifest is current: {output}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(manifest_text, encoding="utf-8")
    os.replace(temporary, output)
    summary = json.loads(manifest_text)["summary"]
    print(
        f"Wrote {output}: {summary['file_count']} files, "
        f"{summary['total_size_bytes']} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
