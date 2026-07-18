"""
modules/core/config.py

Configuration management for OCR cleanup pipeline.
"""

from __future__ import annotations


import os

from pathlib import Path


import yaml


class PipelineConfig:
    """
    Handles pipeline configuration.
    """

    def __init__(
        self,
        data=None,
    ):

        self.data = data or {}

    # ---------------------------------------------------------

    @classmethod
    def load(
        cls,
        file_path,
    ):
        """
        Load YAML configuration.
        """

        path = Path(file_path)

        if not path.exists():

            raise FileNotFoundError(f"Config not found: {path}")

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:

            data = yaml.safe_load(file)

        return cls(data)

    # ---------------------------------------------------------

    def get(
        self,
        key,
        default=None,
    ):
        """
        Access nested configuration.

        Example:

        config.get(
            "symspell.confidence_threshold"
        )

        """

        parts = key.split(".")

        value = self.data

        for part in parts:

            if not isinstance(
                value,
                dict,
            ):

                return default

            if part not in value:

                return default

            value = value[part]

        return value

    # ---------------------------------------------------------

    def section(
        self,
        name,
    ):
        """
        Return configuration section.
        """

        return self.data.get(name, {})

    # ---------------------------------------------------------

    def set(
        self,
        key,
        value,
    ):
        """
        Update nested configuration.
        """

        parts = key.split(".")

        target = self.data

        for part in parts[:-1]:

            if part not in target:

                target[part] = {}

            target = target[part]

        target[parts[-1]] = value

    # ---------------------------------------------------------

    def validate(
        self,
    ):
        """
        Validate required sections.
        """

        required = [
            "paths",
            "backup",
        ]
        missing = [item for item in required if item not in self.data]

        if missing:

            raise ValueError("Missing configuration sections: " + ", ".join(missing))

        return True

    # ---------------------------------------------------------

    def apply_environment(
        self,
    ):
        """
        Override values using environment variables.

        Example:

        OCR_OUTPUT_DIR=/data/output

        """

        output_dir = os.getenv("OCR_OUTPUT_DIR")

        if output_dir:

            self.set(
                "paths.output_directory",
                output_dir,
            )

    # ---------------------------------------------------------

    def dump(
        self,
    ):
        """
        Return raw configuration.
        """

        return self.data
