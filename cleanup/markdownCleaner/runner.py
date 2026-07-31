"""Backward-compatible script entry point for the Markdown cleaner CLI."""

from __future__ import annotations

import sys
from pathlib import Path


if __package__ in {None, ""}:
    # Direct execution places only the package directory on sys.path. Add its
    # parent so the same absolute imports used by module execution still work.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from markdownCleaner.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    """Delegate to the canonical CLI instead of a hard-coded sample document."""
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
