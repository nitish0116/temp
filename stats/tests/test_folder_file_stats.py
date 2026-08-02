"""Tests for the folder statistics command-line utility."""

from __future__ import annotations

from pathlib import Path

import pytest

import folder_file_stats as stats


def test_help_renders_without_percent_formatting_error(capsys) -> None:
    """Argparse help should render successfully and describe both filters."""
    with pytest.raises(SystemExit) as exc_info:
        stats.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--skip-below-master-avg" in output
    assert "5 percent" in output
    assert "tolerance" in output


def test_resolve_report_options_normalizes_paths_and_suffix(tmp_path: Path) -> None:
    """CLI paths become absolute and output names receive an HTA suffix."""
    parser = stats._build_argument_parser()
    args = parser.parse_args([str(tmp_path), "-o", str(tmp_path / "reports" / "summary")])

    options = stats._resolve_report_options(args)

    assert options.root_path == str(tmp_path.resolve())
    assert options.output_path == str((tmp_path / "reports" / "summary.hta").resolve())
    assert options.skip_below_master_avg is False


def test_main_writes_report_and_creates_output_parent(tmp_path: Path) -> None:
    """A small directory tree should produce a readable HTA report."""
    source = tmp_path / "source"
    child = source / "child"
    child.mkdir(parents=True)
    (source / "root.txt").write_text("root", encoding="utf-8")
    (child / "nested.txt").write_text("nested", encoding="utf-8")
    output = tmp_path / "reports" / "folder-summary.hta"

    exit_code = stats.main([str(source), "-o", str(output)])

    assert exit_code == 0
    report = output.read_text(encoding="utf-8")
    assert "Total folders:</strong> 2" in report
    assert "Total files:</strong> 2" in report
    assert str(source.resolve()) in report


def test_main_rejects_non_directory(tmp_path: Path, capsys) -> None:
    """A missing input path should return a failure without writing output."""
    missing = tmp_path / "missing"

    exit_code = stats.main([str(missing)])

    assert exit_code == 1
    assert "is not a valid directory" in capsys.readouterr().out
