"""
modules/core/stage.py

Base pipeline stage implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from ..core.logger import get_logger

# -------------------------------------------------------------
# Stage result
# -------------------------------------------------------------


@dataclass
class StageResult:
    """Describe the observable outcome of one pipeline stage.

    ``changes`` is the number reported by the stage, while ``success`` and
    ``error`` distinguish a clean run from a contained failure. The base
    executor adds ISO-formatted start and finish timestamps.

    Example:
        ``StageResult(stage="Unicode", changes=3)`` represents a successful
        stage that logged three transformations.
    """

    stage: str

    changes: int = 0

    success: bool = True

    error: str | None = None

    started: str | None = None

    finished: str | None = None


# -------------------------------------------------------------
# Base Stage
# -------------------------------------------------------------


class PipelineStage(ABC):
    """Provide the lifecycle shared by every cleanup stage.

    Subclasses declare ``name`` and, where applicable, ``config_section``.
    They may prepare resources in :meth:`initialize`, but implement their work
    in :meth:`process`. Callers should invoke :meth:`execute`, which applies the
    enabled flag, timestamps the run, updates context statistics, and converts
    exceptions into unsuccessful :class:`StageResult` objects.

    Stage workflow::

        execute(context)
          -> is_enabled()
          -> initialize(context)
          -> process(context)
          -> add timing and statistics
          -> StageResult

    Example:
        ``instance = PipelineStage(config)``
        Expected behavior: Provide the lifecycle shared by every cleanup stage.
    """

    name = "BaseStage"

    config_section = None

    def __init__(
        self,
        config,
    ):
        """Bind pipeline configuration and initialize the context reference.

        Example:
            ``instance = PipelineStage(config)``
            Expected behavior: Bind pipeline configuration and initialize the context reference.
        """

        self.config = config

        self.context = None

    # ---------------------------------------------------------

    def execute(
        self,
        context,
    ):
        """Run the complete stage lifecycle against a processing context.

        Disabled stages return a successful zero-change result immediately.
        Enabled stages initialize, process, timestamp, and publish statistics.
        Exceptions are logged and returned as failed results so the pipeline can
        finish its remaining stages and produce diagnostic reports.

        Example:
            ``result = instance.execute(context)``
            Expected behavior: Run the complete stage lifecycle against a processing context.
        """

        self.context = context

        if not self.is_enabled():

            return StageResult(
                stage=self.name,
                changes=0,
            )

        started = datetime.now().isoformat()

        try:

            self.initialize(context)

            result = self.process(context)

            result.started = started

            result.finished = datetime.now().isoformat()

            context.add_stat(
                self.name,
                result.changes,
            )

            return result

        except Exception as error:

            get_logger().exception(f"{self.name} failed.")

            result = StageResult(
                stage=self.name,
                success=False,
                error=str(error),
                started=started,
                finished=datetime.now().isoformat(),
            )

            context.add_stat(
                self.name,
                0,
            )

            return result

    # ---------------------------------------------------------

    def initialize(
        self,
        context,
    ):
        """Prepare context-dependent resources before processing.

        The default hook does nothing. Stages override it to construct
        processors, load dictionaries, or build indexes that need the active
        document context.

        Example:
            ``instance.initialize(context)``
            Expected behavior: Prepare context-dependent resources before processing.
        """

        pass

    # ---------------------------------------------------------

    @abstractmethod
    def process(
        self,
        context,
    ):
        """Transform or inspect the active context and return a stage result.

        Implementations may mutate editable segments and record each mutation,
        or operate report-only and leave the Markdown unchanged.

        Example:
            ``result = instance.process(context)``
            Expected behavior: Transform or inspect the active context and return a stage result.
        """

        raise NotImplementedError

        # ---------------------------------------------------------

    def is_enabled(
        self,
    ):
        """Return whether configuration enables this stage.

        A stage without ``config_section`` is always enabled. Otherwise the
        value comes from ``<config_section>.enabled`` and defaults to true.

        Example:
            ``result = instance.is_enabled()``
            Expected behavior: Return whether configuration enables this stage.
        """

        if self.config_section is None:

            return True

        return self.config.get(
            f"{self.config_section}.enabled",
            True,
        )

    # ---------------------------------------------------------

    def get_config(
        self,
        key,
        default=None,
    ):
        """Read a setting relative to this stage's configuration section.

        For a stage whose section is ``unicode``, ``get_config("enabled")`` is
        equivalent to ``config.get("unicode.enabled")``.

        Example:
            ``result = instance.get_config("section.option")``
            Expected behavior: Read a setting relative to this stage's configuration section.
        """

        if self.config_section is None:

            return self.config.get(
                key,
                default,
            )

        return self.config.get(
            f"{self.config_section}.{key}",
            default,
        )

    # ---------------------------------------------------------

    def record_change(
        self,
        *,
        segment,
        before,
        after,
        confidence,
        reason,
    ):
        """Append one auditable transformation to the shared change tracker.

        Location fields are inferred from the segment. ``before`` and ``after``
        should be the smallest useful excerpts, while ``reason`` explains why
        the change was considered safe and ``confidence`` quantifies certainty.

        Example:
            ``instance.record_change(segment=segment, before="teh", after="the", confidence=98.0, reason="Safe correction")``
            Expected behavior: Append one auditable transformation to the shared change tracker.
        """

        self.context.tracker.add(
            stage=self.name,
            block_index=getattr(
                segment,
                "block_index",
                0,
            ),
            segment_index=getattr(
                segment,
                "segment_index",
                0,
            ),
            line=getattr(
                segment,
                "start_line",
                getattr(
                    segment,
                    "line_number",
                    0,
                ),
            ),
            before=before,
            after=after,
            confidence=confidence,
            reason=reason,
        )

    # ---------------------------------------------------------

    def log(
        self,
        message,
    ):
        """Write an informational message prefixed with the stage name.

        Example:
            ``instance.log("Processing segment")``
            Expected behavior: Write an informational message prefixed with the stage name.
        """

        get_logger().info(f"[{self.name}] {message}")
