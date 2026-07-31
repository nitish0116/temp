"""Base lifecycle for cleanup pipeline stages."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from .config import PipelineConfig
from .context import ContextCheckpoint, ProcessingContext
from .logger import get_logger
from .processor import SegmentProcessor
from ..markdown.segmenter import MarkdownSegment, process_editable_spans


@dataclass
class StageResult:
    """Describe the observable outcome of one pipeline stage."""

    stage: str
    changes: int = 0
    success: bool = True
    error: str | None = None
    started: str | None = None
    finished: str | None = None


class PipelineStage(ABC):
    """Provide enabled checks, synchronization, rollback, and reporting.

    Segment-oriented stages may edit ``segment.current_text``. Whole-document
    stages use :meth:`ProcessingContext.replace_markdown`. Successful work is
    committed at the stage boundary; unsuccessful or exceptional work is
    rolled back so later stages cannot publish partial mutations.
    """

    name = "BaseStage"
    config_section: str | None = None

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.context: ProcessingContext | None = None

    def execute(self, context: ProcessingContext) -> StageResult:
        """Run this stage atomically against a processing context."""

        self.context = context
        if not self.is_enabled():
            return StageResult(stage=self.name, changes=0)

        started = datetime.now().isoformat()
        checkpoint: ContextCheckpoint | None = None

        try:
            checkpoint = context.checkpoint()
            self.initialize(context)
            result = self.process(context)

            if result.success:
                context.update_markdown()
            else:
                context.restore(checkpoint)
                result.changes = 0

            result.started = started
            result.finished = datetime.now().isoformat()
            context.add_stat(self.name, result.changes)
            return result

        except Exception as error:
            logger = get_logger()
            logger.exception("%s failed.", self.name)

            if checkpoint is not None:
                try:
                    context.restore(checkpoint)
                except Exception:
                    logger.exception("%s rollback failed.", self.name)

            result = StageResult(
                stage=self.name,
                success=False,
                error=str(error),
                started=started,
                finished=datetime.now().isoformat(),
            )
            context.add_stat(self.name, 0)
            return result

    def initialize(self, context: ProcessingContext) -> None:
        """Prepare context-dependent resources before processing."""

    @abstractmethod
    def process(self, context: ProcessingContext) -> StageResult:
        """Transform or inspect the active context and return a result."""

        raise NotImplementedError

    def is_enabled(self) -> bool:
        """Return whether configuration enables this stage."""

        if self.config_section is None:
            return True
        return self.config.get_bool(
            f"{self.config_section}.enabled",
            True,
        )

    def get_config(self, key: str, default: Any = None) -> Any:
        """Read a setting relative to this stage's configuration section."""

        if self.config_section is None:
            return self.config.get(key, default)
        return self.config.get(f"{self.config_section}.{key}", default)

    def record_change(
        self,
        *,
        segment: Any,
        before: str,
        after: str,
        confidence: float,
        reason: str,
        broken_word: str | None = None,
    ) -> None:
        """Append one auditable transformation to the shared change tracker."""

        if self.context is None:
            raise RuntimeError("Stage has not been bound to a processing context.")

        self.context.tracker.add(
            stage=self.name,
            block_index=getattr(segment, "block_index", 0),
            segment_index=getattr(segment, "segment_index", 0),
            line=getattr(
                segment,
                "start_line",
                getattr(segment, "line_number", 0),
            ),
            before=before,
            after=after,
            confidence=confidence,
            reason=reason,
            broken_word=broken_word,
        )

    def log(self, message: str) -> None:
        """Write an informational message prefixed with the stage name."""

        get_logger().info("[%s] %s", self.name, message)


class SegmentProcessingStage(PipelineStage):
    """Run an ordered processor collection over every editable segment.

    Subclasses only construct their processor list and optionally enable the
    empty-segment guard. Change counts are derived consistently from the shared
    audit tracker.
    """

    skip_empty_segments = False

    def __init__(self, config: PipelineConfig) -> None:
        super().__init__(config)
        self.processors: list[SegmentProcessor] = []
        self._processor_context: ProcessingContext | None = None

    @abstractmethod
    def build_processors(
        self,
        context: ProcessingContext,
    ) -> Iterable[SegmentProcessor]:
        """Construct processors in their required execution order."""

        raise NotImplementedError

    def initialize(self, context: ProcessingContext) -> None:
        self.processors = list(self.build_processors(context))
        self._processor_context = context

    def should_process_segment(self, segment: MarkdownSegment) -> bool:
        """Return whether processors should visit this segment."""

        return not (
            self.skip_empty_segments and not segment.current_text.strip()
        )

    def process(self, context: ProcessingContext) -> StageResult:
        """Run each processor over eligible segments and report audit growth."""

        if self._processor_context is not context:
            self.initialize(context)

        start_changes = context.total_changes
        for segment in context.iter_segments():
            if not self.should_process_segment(segment):
                continue
            self._process_segment(segment)

        return StageResult(
            stage=self.name,
            changes=context.total_changes - start_changes,
        )

    def _process_segment(self, segment: MarkdownSegment) -> None:
        """Process editable spans while preserving inline Markdown literals."""

        def process(editable: MarkdownSegment) -> None:
            for processor in self.processors:
                processor.process(editable)

        process_editable_spans(segment, process)
