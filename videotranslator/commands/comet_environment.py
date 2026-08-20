"""Create and invoke the isolated COMET machine-review environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]
ENVIRONMENT = WORKSPACE / "cometEnv"
REQUIREMENTS = WORKSPACE / "videotranslator" / "requirements" / "machine-review.txt"
MARKER = ENVIRONMENT / ".videotranslator-environment.json"
ACTIVE_FLAG = "VIDEOTRANSLATOR_COMET_ENV"
Runner = Callable[..., subprocess.CompletedProcess]


def environment_python(environment: Path = ENVIRONMENT) -> Path:
    """Return the platform-specific Python executable inside an environment."""
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def requirements_fingerprint(path: Path = REQUIREMENTS) -> str:
    """Bind environment readiness to requirements and the Python minor version."""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    digest.update(f"{sys.version_info.major}.{sys.version_info.minor}".encode())
    return digest.hexdigest()


def environment_is_current(
    environment: Path = ENVIRONMENT, requirements: Path = REQUIREMENTS,
) -> bool:
    """Return whether the interpreter and successful-install marker are current."""
    python = environment_python(environment)
    marker = environment / MARKER.name
    if not python.is_file() or not marker.is_file():
        return False
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return state.get("requirements_fingerprint") == requirements_fingerprint(requirements)


def ensure_comet_environment(
    *,
    environment: Path = ENVIRONMENT,
    requirements: Path = REQUIREMENTS,
    runner: Runner = subprocess.run,
    offline: bool = False,
) -> Path:
    """Create once and refresh only after the isolated requirements change."""
    python = environment_python(environment)
    if environment_is_current(environment, requirements):
        return python
    if offline:
        raise RuntimeError(
            "cometEnv is missing or stale; run `python -m videotranslator setup-comet-env` online first"
        )
    if not python.is_file():
        runner(
            [sys.executable, "-m", "venv", "--system-site-packages", str(environment)],
            cwd=WORKSPACE, check=True,
        )
    runner(
        [str(python), "-m", "pip", "install", "-r", str(requirements)],
        cwd=WORKSPACE, check=True,
    )
    marker = environment / MARKER.name
    marker.write_text(json.dumps({
        "schema_version": 1,
        "requirements": "videotranslator/requirements/machine-review.txt",
        "requirements_fingerprint": requirements_fingerprint(requirements),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
    }, indent=2) + "\n", encoding="utf-8")
    return python


def run_machine_review(
    arguments: Sequence[str], *, runner: Runner = subprocess.run,
) -> int:
    """Run qualification in cometEnv and return automatically to the caller."""
    offline = "--offline" in arguments
    python = ensure_comet_environment(runner=runner, offline=offline)
    child_environment = dict(os.environ)
    child_environment[ACTIVE_FLAG] = "1"
    completed = runner(
        [str(python), "-m", "videotranslator", "qualify-machine-review", *arguments],
        cwd=WORKSPACE, env=child_environment, check=False,
    )
    return int(completed.returncode)


def main(argv: list[str] | None = None) -> None:
    """Prepare cometEnv explicitly without running qualification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    python = ensure_comet_environment()
    print(f"COMET environment ready: {python.relative_to(WORKSPACE)}")
