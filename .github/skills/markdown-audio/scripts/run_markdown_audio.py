#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def preferred_python(workspace_root: Path) -> str:
    venv_python = workspace_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def main() -> int:
    workspace_root = Path(__file__).resolve().parents[4]
    script_path = workspace_root / "md-audio" / "md_to_audio.py"
    if not script_path.exists():
        raise SystemExit(f"Converter script not found: {script_path}")

    command = [preferred_python(workspace_root), str(script_path), *sys.argv[1:]]
    completed = subprocess.run(command)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())