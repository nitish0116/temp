"""Shared state and synchronization for one cleanup pipeline execution."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import PipelineConfig
from .logger import get_logger
from ..markdown.markdown import BlockType, MarkdownDocument, MarkdownParser
from ..markdown.segmenter import MarkdownSegment
from ..report.change_log import ChangeLog


@dataclass(frozen=True, slots=True)
class ContextCheckpoint:
    """Snapshot of stage-mutable context state used for failure rollback."""

    markdown: str
    tracker_records: tuple[Any, ...]
    statistics: dict[str, Any]
    metadata: dict[str, Any]


class ProcessingContext:
    """Own the canonical Markdown model and shared state for one document.

    Paragraph processors edit :attr:`segments`. Whole-document processors use
    :meth:`replace_markdown`. :meth:`update_markdown` commits segment edits to
    the document model, while :meth:`checkpoint` and :meth:`restore` make a
    stage execution atomic when it fails.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.logger = get_logger()
        self.tracker = ChangeLog()

        self.source_file: str | None = None
        self.output_file: str | None = None
        self.original_markdown = ""
        self.current_markdown = ""

        self.document: MarkdownDocument | None = None
        self.segments: list[MarkdownSegment] = []
        self._parser = MarkdownParser()

        self.statistics: dict[str, Any] = {
            "started": datetime.now().isoformat(),
            "stages": {},
        }
        self.metadata: dict[str, Any] = {
            "version": "1.0",
            "source": None,
        }

    @property
    def total_changes(self) -> int:
        """Return the number of change records collected so far."""

        return self.tracker.total_changes()

    def load_markdown(self, file_path: str | Path) -> None:
        """Read, parse, and segment a UTF-8 Markdown source file."""

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(path)

        # ``utf-8-sig`` is identical to UTF-8 for normal files and removes a
        # leading BOM before structural Markdown classification.
        content = path.read_text(encoding="utf-8-sig")
        self.source_file = str(path)
        self.original_markdown = content
        self._load_document(content)

    def _load_document(self, markdown: str) -> None:
        """Replace the document model and derive synchronized segments."""

        self.document = self._parser.parse(markdown)
        self._create_segments()
        self.update_markdown()

    def _require_document(self) -> MarkdownDocument:
        if self.document is None:
            raise RuntimeError("No Markdown document has been loaded.")
        return self.document

    def _create_segments(self) -> None:
        """Create editable wrappers for paragraph blocks in document order."""

        document = self._require_document()
        self.segments = []

        for block_index, block in enumerate(document.blocks):
            if block.block_type is not BlockType.PARAGRAPH:
                continue

            self.segments.append(
                MarkdownSegment(
                    text=block.content,
                    current_text=block.content,
                    block_index=block_index,
                    segment_index=len(self.segments),
                    line_number=block.start_line,
                    start_line=block.start_line,
                    end_line=block.end_line,
                )
            )

    def update_markdown(self) -> None:
        """Commit segment edits to the document and rebuild canonical Markdown."""

        document = self._require_document()
        for segment in self.segments:
            document.blocks[segment.block_index].content = segment.current_text
        self.current_markdown = document.to_markdown()

    def replace_markdown(self, markdown: str) -> None:
        """Replace the complete working document and rebuild its segments."""

        self._load_document(markdown)

    def get_markdown(self) -> str:
        """Return canonical Markdown after committing pending segment edits."""

        self.update_markdown()
        return self.current_markdown

    def iter_segments(self) -> Iterator[MarkdownSegment]:
        """Yield editable Markdown segments in document order."""

        yield from self.segments

    def checkpoint(self) -> ContextCheckpoint:
        """Capture stage-mutable state after committing prior segment edits."""

        return ContextCheckpoint(
            markdown=self.get_markdown(),
            tracker_records=tuple(deepcopy(self.tracker.records)),
            statistics=deepcopy(self.statistics),
            metadata=deepcopy(self.metadata),
        )

    def restore(self, checkpoint: ContextCheckpoint) -> None:
        """Restore state captured before an unsuccessful stage execution."""

        self._load_document(checkpoint.markdown)
        self.tracker.records = list(checkpoint.tracker_records)
        self.statistics = deepcopy(checkpoint.statistics)
        self.metadata = deepcopy(checkpoint.metadata)

    def add_stat(self, stage: str, changes: int) -> None:
        """Store a stage's reported change count."""

        self.statistics["stages"][stage] = changes

    def increment(self, name: str, amount: int = 1) -> None:
        """Increment a named processing statistic."""

        self.statistics[name] = self.statistics.get(name, 0) + amount

    def finish(self) -> None:
        """Record the processing completion timestamp."""

        self.statistics["finished"] = datetime.now().isoformat()
