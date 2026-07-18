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
    """
    Result returned by every stage.
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
    """
    Abstract base class for every cleanup stage.
    """

    name = "BaseStage"

    config_section = None

    def __init__(
        self,
        config,
    ):

        self.config = config

        self.context = None

    # ---------------------------------------------------------

    def execute(
        self,
        context,
    ):
        """
        Execute the stage with common
        timing, statistics and error handling.
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

            get_logger().exception(
    f"{self.name} failed."
)
            
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
        """
        Optional initialization hook.
        """

        pass

    # ---------------------------------------------------------

    @abstractmethod
    def process(
        self,
        context,
    ):
        """
        Stage implementation.
        """

        raise NotImplementedError

        # ---------------------------------------------------------

    def is_enabled(
        self,
    ):
        """
        Returns whether the stage is enabled
        in config.yaml.
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
        """
        Convenience wrapper around PipelineConfig.
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
        """
        Record a correction in the shared
        change log.
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
        """
        Console logger.
        """

        get_logger().info(f"[{self.name}] {message}")