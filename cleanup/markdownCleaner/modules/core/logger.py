"""Application logging for the Markdown cleanup pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from threading import RLock

from rich.logging import RichHandler


LOG = logging.getLogger("ocr_cleanup")

_HANDLER_MARKER = "_markdown_cleaner_handler"
_configuration_lock = RLock()
_active_configuration: tuple[Path, int] | None = None


def _coerce_level(level: int | str) -> int:
    if isinstance(level, int):
        return level

    name = str(level).strip().upper()
    if name.isdecimal():
        return int(name)

    value = getattr(logging, name, None)
    if not isinstance(value, int):
        raise ValueError(f"Unknown logging level: {level!r}")
    return value


def _remove_managed_handlers() -> None:
    for handler in list(LOG.handlers):
        if not getattr(handler, _HANDLER_MARKER, False):
            continue
        LOG.removeHandler(handler)
        handler.close()


def initialize(
    log_folder: str | Path,
    level: int | str = logging.INFO,
    log_file: str | Path | None = None,
) -> logging.Logger:
    """Configure and return the shared application logger.

    Calling this function again with the same destination is idempotent.
    Supplying a different destination or level replaces only handlers managed
    by this module, so separately attached application handlers are preserved.

    Args:
        log_folder: Directory used by the legacy ``cleanup.log`` default.
        level: Standard integer level or a name such as ``"DEBUG"``.
        log_file: Optional explicit log path. A relative filename is placed
            beneath ``log_folder``; callers may pass an absolute path to keep
            configuration-file-relative path resolution unambiguous.
    """

    folder = Path(log_folder).resolve()
    target = Path(log_file) if log_file is not None else Path("cleanup.log")
    if not target.is_absolute():
        target = folder / target
    target = target.resolve()
    numeric_level = _coerce_level(level)
    configuration = (target, numeric_level)

    global _active_configuration
    with _configuration_lock:
        managed_handlers = [
            handler
            for handler in LOG.handlers
            if getattr(handler, _HANDLER_MARKER, False)
        ]
        if _active_configuration == configuration and managed_handlers:
            LOG.setLevel(numeric_level)
            return LOG

        _remove_managed_handlers()
        folder.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)

        LOG.setLevel(numeric_level)
        LOG.propagate = False

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )

        file_handler = logging.FileHandler(target, encoding="utf-8")
        file_handler.setFormatter(formatter)
        setattr(file_handler, _HANDLER_MARKER, True)

        console_handler = RichHandler(
            rich_tracebacks=True,
            show_path=False,
        )
        console_handler.setFormatter(formatter)
        setattr(console_handler, _HANDLER_MARKER, True)

        LOG.addHandler(file_handler)
        LOG.addHandler(console_handler)
        _active_configuration = configuration

    return LOG


def get_logger() -> logging.Logger:
    """Return the shared pipeline logger."""

    return LOG
