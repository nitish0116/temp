"""Input discovery and output-path resolution for audio conversion."""

from __future__ import annotations

from pathlib import Path
import re


AudioTarget = tuple[Path, Path, str]


def default_input_path(script_path: Path) -> Path:
    """Return the only Markdown file beside *script_path*.

    An explicit input is required when the directory contains zero or multiple
    Markdown files.
    """
    matches = sorted(script_path.parent.glob("*.md"))
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(
        "Specify an input markdown path when the folder contains multiple .md files."
    )


def clean_stem(path: Path) -> str:
    """Return a human-readable, filesystem-safe title derived from a filename."""
    stem = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", path.stem)
    stem = stem.replace("_", " ")
    stem = re.sub(r"\s*-\s*", " - ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -_.")
    stem = re.sub(r"[<>:\"/\\|?*]", "", stem).strip(" .")

    volume = re.search(r"\b(?:volume|vol\.?|book)\s*0*(\d+)\b", stem, re.IGNORECASE)
    if volume:
        title = stem[: volume.start()].strip(" -_.")
        if title:
            stem = f"{title} Volume {int(volume.group(1))}"

    deduplicated: list[str] = []
    for token in stem.split():
        if not deduplicated or deduplicated[-1].casefold() != token.casefold():
            deduplicated.append(token)
    return " ".join(deduplicated) or "converted"


def source_output_stem(path: Path) -> str:
    """Return the unmodified source stem used by default output naming."""
    return path.stem or "converted"


def collect_input_paths(input_path: Path) -> list[Path]:
    """Collect sorted, non-recursive Markdown inputs from a file or directory."""
    if input_path.is_dir():
        matches = sorted(path for path in input_path.glob("*.md") if path.is_file())
        if not matches:
            raise SystemExit(f"No .md files were found in: {input_path}")
        return matches
    if input_path.is_file():
        return [input_path]
    raise SystemExit(f"Input path was not found: {input_path}")


def default_output_path(input_path: Path, extension: str) -> Path:
    """Return the default output beside an input, preserving its source stem."""
    return (input_path.parent / f"{source_output_stem(input_path)}{extension}").resolve()


def resolve_conversion_targets(
    input_value: str | None,
    output_value: str | None,
    script_path: Path,
    *,
    allowed_extensions: frozenset[str],
    default_extension: str = ".mp3",
) -> list[AudioTarget]:
    """Resolve file or folder conversion arguments into normalized targets.

    Batch conversions always use *default_extension*.  A single-file output may
    select any suffix in *allowed_extensions*.
    """
    input_path = (
        Path(input_value).expanduser().resolve()
        if input_value
        else default_input_path(script_path)
    )
    input_paths = collect_input_paths(input_path)

    raw_output: Path | None = None
    if output_value:
        raw_output = Path(output_value).expanduser()
        if not raw_output.is_absolute():
            base = input_path if input_path.is_dir() else input_path.parent
            raw_output = base / raw_output
        raw_output = raw_output.resolve()

    if len(input_paths) > 1:
        output_directory = input_path if raw_output is None else raw_output
        if output_directory.suffix:
            raise SystemExit(
                "When converting a folder, output_path must be a directory, not a file."
            )
        return [
            (
                source,
                (output_directory / f"{source_output_stem(source)}{default_extension}").resolve(),
                default_extension,
            )
            for source in input_paths
        ]

    source = input_paths[0]
    if raw_output is None:
        output = default_output_path(source, default_extension)
    elif raw_output.suffix:
        output = raw_output
    else:
        output = (raw_output / f"{source_output_stem(source)}{default_extension}").resolve()

    extension = output.suffix.casefold()
    if extension not in allowed_extensions:
        choices = " or ".join(sorted(allowed_extensions))
        raise SystemExit(f"Output path must end in {choices}.")
    return [(source, output, extension)]
