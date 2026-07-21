"""
modules/core/config.py

Configuration management for OCR cleanup pipeline.
"""

from __future__ import annotations


import os

from pathlib import Path


import yaml


class PipelineConfig:
    """Provide validated, nested access to pipeline configuration.

    Relative file paths are resolved against the YAML file's directory rather
    than the caller's working directory. Dot-separated keys keep stage code
    concise, for example ``config.get("symspell.confidence_threshold", 92)``.
    """

    def __init__(
        self,
        data=None,
        base_dir=None,
    ):
        """Initialize configuration data and its path-resolution base directory.

        Example:
            ``instance = PipelineConfig()``
            Expected behavior: Initialize configuration data and its path-resolution base directory.
        """

        self.data = data or {}
        self.base_dir = Path(base_dir).resolve() if base_dir else Path.cwd()

    # ---------------------------------------------------------

    @classmethod
    def load(
        cls,
        file_path,
    ):
        """Load YAML configuration and remember its directory for path lookup.

        Example:
            ``PipelineConfig.load("markdownCleaner/config.yaml")`` resolves
            relative data paths beside that configuration file.

        Raises:
            FileNotFoundError: If ``file_path`` does not exist.
        """

        path = Path(file_path)

        if not path.exists():

            raise FileNotFoundError(f"Config not found: {path}")

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:

            data = yaml.safe_load(file)

        return cls(data, base_dir=path.parent)

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
        """Return a top-level configuration section or an empty mapping.

        For example, ``config.section("unicode")`` returns all Unicode-stage
        settings without repeated dot-separated lookups.
        """

        return self.data.get(name, {})

    # ---------------------------------------------------------

    def set(
        self,
        key,
        value,
    ):
        """Create or replace a value addressed by a dot-separated key.

        ``config.set("paths.output_directory", "cleaned")`` creates missing
        intermediate dictionaries when necessary.

        Example:
            ``instance.set("section.option", "value")``
            Expected behavior: Create or replace a value addressed by a dot-separated key.
        """

        parts = key.split(".")

        target = self.data

        for part in parts[:-1]:

            if part not in target:

                target[part] = {}

            target = target[part]

        target[parts[-1]] = value

    def resolve_path(self, value):
        """Resolve a path relative to the configuration directory.

        Absolute paths are retained. Dictionary selectors such as
        ``builtin:en-82k`` and ``symspellpy`` are returned verbatim because they
        identify providers rather than files.

        Example:
            ``result = instance.resolve_path("value")``
            Expected behavior: Resolve a path relative to the configuration directory.
        """
        if value is None:
            return None
        text = str(value)
        if text.lower().startswith("builtin") or text.lower() == "symspellpy":
            return text
        path = Path(text)
        return str(path if path.is_absolute() else (self.base_dir / path).resolve())

    # ---------------------------------------------------------

    def validate(
        self,
    ):
        """Validate the minimum configuration shape required by the pipeline.

        Returns:
            ``True`` when the ``paths`` and ``backup`` sections exist.

        Raises:
            ValueError: If one or more required sections are missing.

        Example:
            ``result = instance.validate()``
            Expected behavior: Validate the minimum configuration shape required by the pipeline.
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
        """Return the underlying configuration mapping for serialization.

        Example:
            ``result = instance.dump()``
            Expected behavior: Return the underlying configuration mapping for serialization.
        """

        return self.data
