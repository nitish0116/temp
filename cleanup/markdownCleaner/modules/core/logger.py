from pathlib import Path
import logging

from rich.logging import RichHandler


LOG = logging.getLogger("ocr_cleanup")


def initialize(
    log_folder,
    level=logging.INFO,
):
    """
    Initialize application logger.
    """

    if LOG.handlers:
        return LOG

    Path(log_folder).mkdir(
        parents=True,
        exist_ok=True,
    )

    LOG.setLevel(level)
    LOG.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )

    file_handler = logging.FileHandler(
        Path(log_folder) / "cleanup.log",
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
    )
    console_handler.setFormatter(formatter)

    LOG.addHandler(file_handler)
    LOG.addHandler(console_handler)

    return LOG


def get_logger():
    """
    Return the shared pipeline logger.
    """
    return LOG