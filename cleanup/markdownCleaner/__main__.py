"""Allow ``python -m markdownCleaner`` to run the canonical CLI."""

from markdownCleaner.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
