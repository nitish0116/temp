"""Install Video Translator dependencies for the detected GPU architecture."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PROFILES = {"cpu", "cu126", "cu128"}


def parse_compute_capability(output: str) -> tuple[int, int] | None:
    """Return the first NVIDIA compute capability reported by ``nvidia-smi``."""
    match = re.search(r"(?:^|,)\s*(\d+)\.(\d+)\s*$", output.splitlines()[0]) if output.strip() else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def detect_compute_capability() -> tuple[int, int] | None:
    """Detect the first NVIDIA GPU without importing an existing Torch build."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,compute_cap", "--format=csv,noheader"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return parse_compute_capability(result.stdout)


def select_profile(capability: tuple[int, int] | None) -> str:
    """Map hardware to the official PyTorch 2.11 wheel family."""
    if capability is None:
        return "cpu"
    return "cu126" if capability < (7, 5) else "cu128"


def install(profile: str, dry_run: bool = False) -> list[list[str]]:
    """Install the selected Torch profile before the shared dependency set."""
    root = Path(__file__).resolve().parent
    profile_file = root / "requirements" / f"torch-{profile}.txt"
    commands = [
        [sys.executable, "-m", "pip", "install", "-r", str(profile_file)],
        [sys.executable, "-m", "pip", "install", "-r", str(root / "requirements" / "common.txt")],
    ]
    if not dry_run:
        for command in commands:
            subprocess.run(command, check=True)
    return commands


def main() -> None:
    """Detect hardware and install, or print, the matching dependency profile.

    Example:: ``python videotranslator/install_dependencies.py --dry-run``
    prints the two pip commands without modifying the environment.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["auto", *sorted(PROFILES)], default="auto")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    capability = detect_compute_capability()
    profile = select_profile(capability) if args.profile == "auto" else args.profile
    detected = "none" if capability is None else f"{capability[0]}.{capability[1]}"
    print(f"Detected NVIDIA compute capability: {detected}")
    print(f"Selected PyTorch profile: {profile}")
    commands = install(profile, args.dry_run)
    if args.dry_run:
        for command in commands:
            print(subprocess.list2cmdline(command))


if __name__ == "__main__":
    main()
