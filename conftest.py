"""Workspace-wide pytest safeguards for Windows Test Explorer runs."""

from __future__ import annotations

import os
from pathlib import Path


def pytest_configure(config) -> None:
    """Give every pytest process an isolated, workspace-local temp directory."""
    if config.option.basetemp is None:
        temp_root = Path(__file__).parent / ".pytest-runs"
        temp_root.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(temp_root / f"run-{os.getpid()}")
