"""Single-file pipeline execution helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from markdownCleaner.pipeline import OCRPipeline


PipelineFactory = Callable[[Path], OCRPipeline]


@dataclass(frozen=True, slots=True)
class PipelineExecution:
    """Serializable information retained from one pipeline invocation."""

    result: dict
    changes: int
    records: list[dict]
    glossary_candidates: list[dict]

    def as_legacy_tuple(self) -> tuple[dict, int, list[dict], list[dict]]:
        """Return the tuple historically exposed by ``cli._run_one``."""
        return (
            self.result,
            self.changes,
            self.records,
            self.glossary_candidates,
        )


def execute_pipeline(
    source: Path,
    *,
    config: Path,
    output_directory: Path | None,
    output_name: str | None = None,
    report_subdirectory: Path | str = "reports",
    pipeline_factory: PipelineFactory = OCRPipeline,
) -> PipelineExecution:
    """Run the configured pipeline and collect its CLI-facing diagnostics."""
    pipeline = pipeline_factory(config)
    result = pipeline.run(
        source,
        output_directory=output_directory,
        output_name=output_name,
        report_subdirectory=report_subdirectory,
    )
    records = [asdict(record) for record in pipeline.context.tracker.records]
    candidates = list(pipeline.context.metadata.get("glossary_candidates", []))
    failed_stages = [stage for stage in result["stages"] if not stage.success]
    if failed_stages:
        details = "; ".join(
            f"{stage.stage}: {stage.error}" for stage in failed_stages
        )
        result["pipeline_error"] = f"Pipeline stage failure(s): {details}"
    return PipelineExecution(
        result=result,
        changes=pipeline.context.total_changes,
        records=records,
        glossary_candidates=candidates,
    )


def configured_output_root(
    config: Path,
    *,
    pipeline_factory: PipelineFactory = OCRPipeline,
) -> Path:
    """Resolve the effective batch output directory, including env overrides."""
    pipeline = pipeline_factory(config)
    configured = pipeline.config.get("paths.output_directory", "output")
    resolved = pipeline.config.resolve_path(configured)
    if resolved is None:
        raise ValueError("paths.output_directory cannot be null")
    return Path(resolved)
