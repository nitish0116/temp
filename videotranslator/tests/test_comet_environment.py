"""Tests for one-time setup and subprocess switching of cometEnv."""

import json
from types import SimpleNamespace

import pytest

from videotranslator.commands.comet_environment import (
    ACTIVE_FLAG,
    ensure_comet_environment,
    environment_is_current,
    environment_python,
    run_machine_review,
)


def test_environment_python_is_platform_specific(tmp_path):
    path = environment_python(tmp_path / "cometEnv")
    assert path.name in {"python", "python.exe"}


def test_environment_is_created_once_and_refreshed_on_requirement_change(tmp_path):
    environment = tmp_path / "cometEnv"
    requirements = tmp_path / "machine-review.txt"
    requirements.write_text("unbabel-comet==2.2.7\n", encoding="utf-8")
    calls = []

    def runner(command, **kwargs):
        """Simulate venv creation and dependency installation."""
        calls.append((command, kwargs))
        if command[1:3] == ["-m", "venv"]:
            python = environment_python(environment)
            python.parent.mkdir(parents=True)
            python.write_text("fixture", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    python = ensure_comet_environment(
        environment=environment, requirements=requirements, runner=runner,
    )
    assert python.is_file() and len(calls) == 2
    assert "--system-site-packages" in calls[0][0]
    assert environment_is_current(environment, requirements)
    ensure_comet_environment(
        environment=environment, requirements=requirements, runner=runner,
    )
    assert len(calls) == 2
    requirements.write_text("unbabel-comet==2.2.8\n", encoding="utf-8")
    ensure_comet_environment(
        environment=environment, requirements=requirements, runner=runner,
    )
    assert len(calls) == 3
    marker = json.loads((environment / ".videotranslator-environment.json").read_text())
    assert marker["requirements"].endswith("machine-review.txt")


def test_offline_setup_fails_before_running_pip(tmp_path):
    with pytest.raises(RuntimeError, match="setup-comet-env"):
        ensure_comet_environment(
            environment=tmp_path / "cometEnv",
            requirements=tmp_path / "missing.txt",
            offline=True,
        )


def test_run_uses_isolated_python_and_marks_child(monkeypatch, tmp_path):
    isolated = tmp_path / "cometEnv" / "Scripts" / "python.exe"
    calls = []

    def runner(command, **kwargs):
        """Record the isolated qualification subprocess."""
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(
        "videotranslator.commands.comet_environment.ensure_comet_environment",
        lambda **kwargs: isolated,
    )
    code = run_machine_review(["--offline"], runner=runner)
    assert code == 7
    assert calls[0][0][:4] == [
        str(isolated), "-m", "videotranslator", "qualify-machine-review",
    ]
    assert calls[0][1]["env"][ACTIVE_FLAG] == "1"
