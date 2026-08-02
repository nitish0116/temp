"""Focused contracts for the refactored command-line application layer."""

from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from markdownCleaner import runner
from markdownCleaner.cli import _run_single_file, build_parser, main
from markdownCleaner.commands.batch import (
    process_batch_file,
    run_batch,
    safe_report_name,
    write_batch_reports,
)
from markdownCleaner.commands.execution import configured_output_root
from markdownCleaner.commands.reports import (
    render_batch_summary,
    simplify_glossary_candidates,
)
from markdownCleaner.commands.review import review_action_count, review_request
from markdownCleaner.modules.core.config import PipelineConfig
from markdownCleaner.modules.report.exporter import ReportOptions


def test_parser_and_review_request_preserve_existing_flags():
    """Keep parsing separate while retaining all review command semantics."""
    args = build_parser().parse_args(
        [
            "--config",
            "custom.yaml",
            "--reject-words",
            "offense",
            "humor",
            "--rejected-file",
            "rejected.json",
        ]
    )

    request = review_request(args)

    assert review_action_count(args) == 1
    assert request is not None
    assert list(request.words) == ["offense", "humor"]
    assert request.explicit_file == Path("rejected.json")
    assert request.config_key == "vocabulary_candidates.rejected"
    assert request.label == "Rejected words"


def test_parser_rejects_batch_report_path_traversal():
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["books", "--batch-report-name", "../outside.md"]
        )


def test_summary_renderer_is_pure_and_accepts_a_fixed_generation_time(tmp_path):
    """Render deterministic report text before the filesystem adapter writes it."""
    output_root = tmp_path / "not-created"
    entries = [
        {
            "relative_path": "book.md",
            "status": "success",
            "changes": 1,
            "elapsed_seconds": 0.5,
            "stage_counts": {"BrokenWords": 1},
            "records": [
                {
                    "stage": "BrokenWords",
                    "line": 4,
                    "reason": "OCR broken word merge",
                    "confidence": 85.0,
                    "broken_word": "ener gy",
                    "before": "inner ```ener gy```",
                    "after": "inner energy",
                }
            ],
            "output": "cleaned/book.md",
        }
    ]

    report = render_batch_summary(
        output_root,
        source_root=tmp_path / "source",
        entries=entries,
        generated_at=datetime(2026, 7, 31, 10, 30, 0),
    )

    assert "Generated: 2026-07-31T10:30:00" in report
    assert "| BrokenWords | 1 |" in report
    assert "- Broken word: `ener gy`" in report
    assert "inner ` ` `ener gy` ` `" in report
    assert not output_root.exists()


def test_simplified_report_projection_does_not_mutate_master_schema():
    """Keep classification in master data but omit it from the compact contract."""
    candidates = [
        {
            "word": "sitrep",
            "occurrences": 12,
            "suggested_correction": "strep",
            "classification": "noun",
            "files": [{"file": "book.md"}],
        }
    ]

    simplified = simplify_glossary_candidates(candidates)

    assert simplified == [
        {
            "word": "sitrep",
            "occurrences": 12,
            "suggested_correction": "strep",
        }
    ]
    assert candidates[0]["classification"] == "noun"


def test_batch_file_processing_normalizes_unexpected_exceptions(tmp_path):
    """Turn a pipeline exception into the same stable batch-report entry schema."""
    source = tmp_path / "book.md"

    def fail(*_args, **_kwargs):
        raise RuntimeError("dictionary unavailable")

    result = process_batch_file(
        source,
        relative=Path("book.md"),
        config=tmp_path / "config.yaml",
        target_dir=tmp_path / "output",
        output_name="Book - Cleaned.md",
        report_dir=Path("reports/Book"),
        run_one=fail,
    )

    assert not result.succeeded
    assert result.changes == 0
    assert result.entry == {
        "relative_path": "book.md",
        "status": "failed",
        "changes": 0,
        "elapsed_seconds": 0,
        "stage_counts": {},
        "records": [],
        "glossary_candidates": [],
        "output": None,
        "error": "dictionary unavailable",
    }


def test_batch_file_filters_low_confidence_records_for_aggregate_report(tmp_path):
    def run_one(*_args, **_kwargs):
        return (
            {
                "stages": [],
                "output": {"markdown": tmp_path / "cleaned.md"},
                "elapsed_seconds": 0.1,
            },
            2,
            [
                {"stage": "RegexOCR", "confidence": 99.0, "reason": "safe"},
                {"stage": "TTSValidation", "confidence": 10.0, "reason": "review"},
            ],
            [],
        )

    result = process_batch_file(
        tmp_path / "book.md",
        relative=Path("book.md"),
        config=tmp_path / "config.yaml",
        target_dir=tmp_path / "output",
        output_name="Book - Cleaned.md",
        report_dir=Path("reports/Book"),
        run_one=run_one,
        minimum_record_confidence=85.0,
    )

    assert result.changes == 2
    assert result.entry["changes"] == 1
    assert result.entry["stage_counts"] == {"RegexOCR": 1}
    assert result.entry["records"] == [
        {"stage": "RegexOCR", "confidence": 99.0, "reason": "safe"}
    ]


def test_disabled_batch_reports_do_not_call_writers(tmp_path):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("disabled report writer was called")

    paths = write_batch_reports(
        tmp_path,
        source_root=tmp_path / "source",
        entries=[],
        report_name="batch.md",
        write_summary=unexpected,
        write_candidates=unexpected,
        options=ReportOptions(enabled=False),
    )

    assert paths == (None, None)


def test_colliding_output_names_get_distinct_report_directories():
    first = safe_report_name(
        Path("Book [Kobo].md"),
        "Book - Cleaned.md",
    )
    second = safe_report_name(
        Path("Book [Yen Press].md"),
        "Book - Cleaned (2).md",
    )

    assert first == Path("reports/Book")
    assert second == Path("reports/Book (2)")


def test_batch_default_output_is_relative_to_configuration(tmp_path):
    config = PipelineConfig(
        {"paths": {"output_directory": "cleaned"}},
        base_dir=tmp_path / "configuration",
    )

    output = configured_output_root(
        tmp_path / "configuration" / "config.yaml",
        pipeline_factory=lambda _path: SimpleNamespace(config=config),
    )

    assert output == (tmp_path / "configuration" / "cleaned").resolve()


def test_recursive_folder_run_excludes_output_and_backup_trees(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "books"
    output = source / "output"
    backup = source / "backup"
    (output / "reports").mkdir(parents=True)
    (backup / "timestamp").mkdir(parents=True)
    (source / "book.md").write_text("Story.", encoding="utf-8")
    (output / "book - Cleaned.md").write_text("Old output.", encoding="utf-8")
    (output / "reports" / "summary.md").write_text(
        "# Old report",
        encoding="utf-8",
    )
    (backup / "timestamp" / "book.md").write_text(
        "Old backup.",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "paths:\n"
        "  output_directory: books/output\n"
        "backup:\n"
        "  enabled: true\n"
        "  directory: books/backup\n",
        encoding="utf-8",
    )
    captured: list[Path] = []

    def capture(_source, *, files, **_kwargs):
        captured.extend(files)
        return 0

    monkeypatch.setattr("markdownCleaner.cli.run_batch", capture)

    exit_code = main(
        [str(source), "--config", str(config), "--recursive"]
    )

    assert exit_code == 0
    assert captured == [source / "book.md"]


def test_folder_discovery_keeps_source_when_output_is_its_parent(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "books"
    source.mkdir()
    book = source / "book.md"
    book.write_text("Story.", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "paths:\n"
        "  output_directory: .\n"
        "backup:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )
    captured: list[Path] = []

    def capture(_source, *, files, **_kwargs):
        captured.extend(files)
        return 0

    monkeypatch.setattr("markdownCleaner.cli.run_batch", capture)

    assert main([str(source), "--config", str(config), "--recursive"]) == 0
    assert captured == [book]


def test_legacy_runner_supports_direct_script_execution():
    package_root = Path(__file__).resolve().parents[2]

    completed = subprocess.run(
        [sys.executable, "markdownCleaner/runner.py", "--help"],
        cwd=package_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Clean OCR/PDF-extracted novel Markdown" in completed.stdout


def test_single_file_boundary_returns_two_without_traceback(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise OSError("output is unavailable")

    monkeypatch.setattr("markdownCleaner.cli._run_one", fail)

    exit_code = _run_single_file(
        Path("book.md"),
        config=Path("config.yaml"),
        output_root=None,
    )

    assert exit_code == 2
    assert "ERROR: book.md: output is unavailable" in capsys.readouterr().err


def test_folder_setup_boundary_returns_two_without_traceback(
    tmp_path,
    monkeypatch,
    capsys,
):
    source = tmp_path / "books"
    source.mkdir()
    (source / "book.md").write_text("Story.", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("{}\n", encoding="utf-8")

    def fail(*_args, **_kwargs):
        raise ValueError("invalid output configuration")

    monkeypatch.setattr("markdownCleaner.cli.configured_output_root", fail)

    exit_code = main([str(source), "--config", str(config)])

    assert exit_code == 2
    assert "invalid output configuration" in capsys.readouterr().err


def test_batch_report_boundary_returns_two_without_traceback(
    tmp_path,
    monkeypatch,
    capsys,
):
    source = tmp_path / "books"
    source.mkdir()
    (source / "book.md").write_text("Story.", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        "paths:\n"
        "  output_directory: output\n"
        "backup:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )

    def fail(*_args, **_kwargs):
        raise OSError("aggregate report is unavailable")

    monkeypatch.setattr("markdownCleaner.cli.run_batch", fail)

    exit_code = main([str(source), "--config", str(config)])

    assert exit_code == 2
    assert "aggregate report is unavailable" in capsys.readouterr().err


def test_review_action_reports_null_configured_target(
    tmp_path,
    capsys,
):
    config = tmp_path / "config.yaml"
    config.write_text(
        "paths:\n"
        "  output_directory: output\n"
        "backup:\n"
        "  enabled: false\n"
        "symspell:\n"
        "  glossary: null\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--config",
                str(config),
                "--approve-words",
                "Arthur Leywin",
            ]
        )

    assert exc_info.value.code == 2
    assert "symspell.glossary cannot be null" in capsys.readouterr().err


def test_batch_stops_once_and_writes_each_aggregate_report_once(tmp_path):
    """Centralize early-stop finalization instead of duplicating report writes."""
    source = tmp_path / "source"
    files = [source / "one.md", source / "two.md"]
    calls: list[str] = []
    captured_entries: list[list[dict]] = []

    def run_one(file, **_kwargs):
        calls.append(file.name)
        return (
            {
                "stages": [SimpleNamespace(stage="Regex", changes=1)],
                "output": {"markdown": tmp_path / "cleaned.md"},
                "elapsed_seconds": 0.25,
                "pipeline_error": "Pipeline stage failure(s): Regex: boom",
            },
            1,
            [],
            [],
        )

    def write_summary(output_root, *, source_root, entries, report_name):
        assert source_root == source
        assert report_name == "batch.md"
        captured_entries.append(list(entries))
        return output_root / "reports" / report_name

    def write_candidates(output_root, entries):
        captured_entries.append(list(entries))
        return output_root / "reports" / "glossary_candidates.json"

    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_batch(
        source,
        files=files,
        config=tmp_path / "config.yaml",
        output_root=tmp_path / "output",
        continue_on_error=False,
        report_name="batch.md",
        run_one=run_one,
        write_summary=write_summary,
        write_candidates=write_candidates,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert calls == ["one.md"]
    assert len(captured_entries) == 2
    assert captured_entries[0] == captured_entries[1]
    assert captured_entries[0][0]["changes"] == 1
    assert "Batch summary:" in stdout.getvalue()
    assert "ERROR:" in stderr.getvalue()


def test_legacy_runner_delegates_to_the_canonical_cli(monkeypatch):
    """Remove the runner's hard-coded sample while retaining its callable API."""
    monkeypatch.setattr(runner, "cli_main", lambda argv: 7)

    assert runner.main(["book.md"]) == 7


def test_broken_word_review_cli_runs_without_a_cleaning_input(
    tmp_path,
    monkeypatch,
    capsys,
):
    config = tmp_path / "config.yaml"
    config.write_text(
        "paths:\n"
        "  output_directory: output\n"
        "backup:\n"
        "  enabled: false\n"
        "symspell:\n"
        "  broken_word_decisions: data/decisions.json\n",
        encoding="utf-8",
    )
    library = tmp_path / "library"
    library.mkdir()
    captured: dict[str, object] = {}

    def run(library_path, **kwargs):
        captured["library"] = library_path
        captured.update(kwargs)
        return {
            "files": 1,
            "decided": 2,
            "accepted": 1,
            "rejected": 1,
            "ambiguous": 3,
            "cache_hits": 0,
            "cache_misses": 1,
            "cache_removed": 0,
        }

    monkeypatch.setattr("markdownCleaner.cli.run_review_pipeline", run)

    exit_code = main(
        [
            "--config",
            str(config),
            "--build-broken-word-review",
            str(library),
        ]
    )

    assert exit_code == 0
    assert captured["library"] == library
    assert captured["main_output"] == tmp_path / "data/broken_word_review.json"
    assert captured["ambiguous_output"] == (
        tmp_path / "data/broken_word_review_ambiguous.json"
    )
    assert captured["cache_path"] == (
        tmp_path / "data/.broken_word_review_cache.json.gz"
    )
    output = capsys.readouterr().out
    assert "1 accepted, 1 rejected, 3 ambiguous" in output
