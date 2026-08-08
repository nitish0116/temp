"""Configuration management for the Markdown cleanup pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


_MISSING = object()
_BOOLEAN_KEYS = (
    "backup.enabled",
    "cleanup.enabled",
    "cleanup.remove_picture_ocr",
    "cleanup.remove_front_matter",
    "cleanup.remove_promotional_tail",
    "cleanup.remove_publisher_tail",
    "cleanup.remove_glossary_footnotes",
    "cleanup.remove_footnotes",
    "cleanup.strip_markdown_emphasis",
    "cleanup.report_ocr_noise",
    "tts_validation.enabled",
    "unicode.enabled",
    "unicode.fixes.ligatures",
    "unicode.fixes.invisible_characters",
    "unicode.fixes.whitespace",
    "unicode.fixes.punctuation",
    "regex.enabled",
    "symspell.enabled",
    "symspell.auto_protect_proper_nouns",
    "symspell.wordfreq_enabled",
    "vocabulary_candidates.enabled",
    "report.enabled",
    "report.export_json",
    "report.export_summary",
    "report.include_low_confidence",
    "mutation.report_only",
    "page_artifacts.enabled",
    "contextual_real_words.enabled",
    "context_validator.enabled",
    "context_validator.local_files_only",
    "classifier_dataset.enabled",
)
_CORRECTION_KEYS = (
    "zero_to_o",
    "one_to_l",
    "five_to_s",
    "eight_to_b",
    "broken_words",
    "broken_hyphen_words",
    "repeated_characters",
)
_MAPPING_SECTIONS = (
    "paths",
    "backup",
    "cleanup",
    "tts_validation",
    "unicode",
    "regex",
    "symspell",
    "vocabulary_candidates",
    "report",
    "logging",
    "mutation",
    "page_artifacts",
    "contextual_real_words",
    "context_validator",
    "classifier_dataset",
)


def require_bool(value: Any, label: str) -> bool:
    """Return a real boolean or reject ambiguous truthy configuration text."""

    if isinstance(value, bool):
        return value
    raise ValueError(f"{label} must be true or false, not {value!r}")


class PipelineConfig:
    """Provide validated, nested access to pipeline configuration.

    :meth:`resolve_path` resolves relative paths against the YAML file's
    directory rather than the caller's working directory. Dot-separated keys
    keep stage code concise, for example
    ``config.get("symspell.confidence_threshold", 92)``.
    """

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        base_dir: str | Path | None = None,
    ) -> None:
        if data is not None and not isinstance(data, dict):
            raise TypeError("Configuration data must be a mapping.")

        self.data: dict[str, Any] = data if data is not None else {}
        self.base_dir = Path(base_dir).resolve() if base_dir else Path.cwd()

    @classmethod
    def load(cls, file_path: str | Path) -> "PipelineConfig":
        """Load YAML configuration and remember its directory for path lookup."""

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Config not found: {path}")

        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("Configuration root must be a mapping.")

        return cls(data, base_dir=path.parent)

    def get(self, key: str, default: Any = None) -> Any:
        """Return a value addressed by a dot-separated key."""

        value: Any = self.data
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Return a strictly typed boolean addressed by ``key``."""

        return require_bool(self.get(key, default), key)

    def section(self, name: str) -> dict[str, Any]:
        """Return a top-level mapping, or an empty mapping when malformed."""

        value = self.data.get(name)
        return value if isinstance(value, dict) else {}

    def set(self, key: str, value: Any) -> None:
        """Create or replace a value addressed by a dot-separated key."""

        parts = key.split(".")
        target = self.data
        for part in parts[:-1]:
            child = target.get(part)
            if not isinstance(child, dict):
                child = {}
                target[part] = child
            target = child
        target[parts[-1]] = value

    def resolve_path(self, value: str | Path | None) -> str | None:
        """Resolve a filesystem path relative to the configuration directory.

        Named dictionary providers are returned verbatim because they are
        selectors rather than file paths.
        """

        if value is None:
            return None

        text = str(value)
        selector = text.casefold()
        if selector in {
            "builtin",
            "builtin:en",
            "builtin:en-82k",
            "symspellpy",
        }:
            return text

        path = Path(text)
        return str(path if path.is_absolute() else (self.base_dir / path).resolve())

    def validate(self) -> bool:
        """Validate the minimum configuration shape required by the pipeline."""

        required = ("paths", "backup")
        missing = [name for name in required if name not in self.data]
        if missing:
            raise ValueError("Missing configuration sections: " + ", ".join(missing))

        malformed = [
            name
            for name in _MAPPING_SECTIONS
            if name in self.data and not isinstance(self.data.get(name), dict)
        ]
        if malformed:
            raise ValueError(
                "Configuration sections must be mappings: " + ", ".join(malformed)
            )

        for key in _BOOLEAN_KEYS:
            value = self.get(key, _MISSING)
            if value is not _MISSING:
                require_bool(value, key)
        for name in _CORRECTION_KEYS:
            key = f"regex.corrections.{name}"
            value = self.get(key, _MISSING)
            if value is _MISSING:
                continue
            if isinstance(value, dict):
                value = value.get("enabled", True)
                key += ".enabled"
            require_bool(value, key)

        output_directory = self.get("paths.output_directory")
        if not isinstance(output_directory, (str, Path)) or not str(
            output_directory
        ).strip():
            raise ValueError(
                "paths.output_directory must be a non-empty filesystem path."
            )

        if self.get_bool("backup.enabled", True):
            backup_directory = self.get("backup.directory")
            if not isinstance(backup_directory, (str, Path)) or not str(
                backup_directory
            ).strip():
                raise ValueError(
                    "backup.directory must be a non-empty filesystem path "
                    "when backups are enabled."
                )

        minimum_confidence = float(
            self.get("mutation.minimum_confidence", 0.0)
        )
        if not 0.0 <= minimum_confidence <= 100.0:
            raise ValueError(
                "mutation.minimum_confidence must be between 0 and 100."
            )

        picture_mode = str(self.get("cleanup.picture_ocr_mode", "safe")).casefold()
        if picture_mode not in {"safe", "keep", "remove"}:
            raise ValueError("cleanup.picture_ocr_mode must be safe, keep, or remove.")

        artifact_mode = str(
            self.get("page_artifacts.mode", "report_only")
        ).casefold()
        if artifact_mode not in {"report_only", "remove"}:
            raise ValueError(
                "page_artifacts.mode must be report_only or remove."
            )
        for key, default, minimum in (
            ("page_artifacts.minimum_occurrences", 3, 2),
            ("page_artifacts.minimum_line_gap", 12, 1),
            ("page_artifacts.maximum_length", 80, 1),
            ("page_artifacts.report_limit", 100, 0),
            ("contextual_real_words.report_limit", 200, 0),
        ):
            if int(self.get(key, default)) < minimum:
                raise ValueError(f"{key} must be at least {minimum}.")
        if float(self.get("symspell.dehyphenation_zipf_margin", 0.5)) < 0:
            raise ValueError(
                "symspell.dehyphenation_zipf_margin cannot be negative."
            )
        for key, default, minimum in (
            ("context_validator.batch_size", 16, 1),
            ("context_validator.max_length", 128, 16),
            ("context_validator.context_characters", 600, 80),
        ):
            if int(self.get(key, default)) < minimum:
                raise ValueError(f"{key} must be at least {minimum}.")
        if float(self.get("context_validator.merge_margin", 0.35)) < 0:
            raise ValueError(
                "context_validator.merge_margin cannot be negative."
            )
        validator_device = str(
            self.get("context_validator.device", "auto")
        ).strip().casefold()
        if validator_device not in {"auto", "cpu", "cuda"}:
            raise ValueError(
                "context_validator.device must be auto, cpu, or cuda."
            )

        return True

    def apply_environment(self) -> None:
        """Apply supported environment-variable overrides."""

        output_dir = os.getenv("OCR_OUTPUT_DIR")
        if output_dir:
            self.set("paths.output_directory", output_dir)

    def dump(self) -> dict[str, Any]:
        """Return the underlying configuration mapping for serialization."""

        return self.data
