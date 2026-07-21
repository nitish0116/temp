"""Optional report-only validation of cleaned text for TTS consumption."""

from __future__ import annotations

import re

from ..core.stage import PipelineStage, StageResult


RAW_AMPERSAND = re.compile(
    r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-f]+;)",
    re.I,
)


class TTSValidationStage(PipelineStage):
    """Inspect final cleaned text for likely TTS/SSML boundary problems.

    This final, optional stage is report-only. It divides Markdown into bounded
    speech-sized chunks and flags insufficient alphabetic content, raw XML angle
    brackets, unescaped ampersands, and unbalanced curly quotation marks. XML or
    SSML escaping remains the responsibility of ``md_to_audio.py``.

    Workflow::

        cleaned Markdown -> paragraph chunks -> issue-code checks
        -> bounded audit records -> unchanged Markdown

    Example::

        from unittest.mock import Mock
        from markdownCleaner.modules.core.config import PipelineConfig

        config = PipelineConfig({"tts_validation": {"minimum_alpha": 4}})
        context = Mock(
            current_markdown="R&D <draft>",
            original_markdown="R&D <draft>",
        )
        result = TTSValidationStage(config).process(context)
        assert result.changes == 1
        context.tracker.add.assert_called_once()

    ``context.current_markdown`` remains ``"R&D <draft>"`` after validation.
    """

    name = "TTSValidation"
    config_section = "tts_validation"

    @staticmethod
    def validation_chunks(text: str, chunk_size: int = 2600) -> list[str]:
        """Split Markdown into bounded paragraph chunks for TTS validation.

        Heading markers are removed, blank paragraphs are ignored, and oversized
        paragraphs prefer a nearby word boundary. For example,
        ``validation_chunks("# Dawn\n\nThe story begins.")`` returns
        ``["Dawn", "The story begins."]``.
        """
        chunks: list[str] = []
        for paragraph in re.split(r"\n\s*\n+", text):
            value = re.sub(r"(?m)^\s*#{1,6}\s*", "", paragraph).strip()
            if not value:
                continue
            while len(value) > chunk_size:
                cut = value.rfind(" ", 0, chunk_size)
                if cut < chunk_size // 2:
                    cut = chunk_size
                chunks.append(value[:cut].strip())
                value = value[cut:].strip()
            if value:
                chunks.append(value)
        return chunks

    @staticmethod
    def issues(chunk: str, minimum_alpha: int = 4) -> list[str]:
        """Return stable TTS-safety issue codes detected in one chunk.

        Example:
            ``issues("R&D <draft>")`` reports ``XML_BRACKETS`` and
            ``RAW_AMPERSAND``. Already escaped ``&amp;`` is not reported as raw.
        """
        found: list[str] = []
        alpha = sum(char.isalpha() for char in chunk)
        if alpha < minimum_alpha:
            found.append(f"LOW_ALPHA({alpha})")
        if "<" in chunk or ">" in chunk:
            found.append("XML_BRACKETS")
        if RAW_AMPERSAND.search(chunk):
            found.append("RAW_AMPERSAND")
        if chunk.count("\u201c") != chunk.count("\u201d"):
            found.append("UNBALANCED_CURLY_QUOTES")
        return found

    def process(self, context) -> StageResult:
        """Record bounded TTS warnings without modifying Markdown.

        The result's ``changes`` count represents findings rather than edits.
        Each audit record contains a shortened chunk preview and all applicable
        issue codes so ``md_to_audio.py`` can skip or escape it appropriately.

        Example::

            from unittest.mock import Mock
            from markdownCleaner.modules.core.config import PipelineConfig

            config = PipelineConfig(
                {"tts_validation": {"chunk_size": 2600, "report_limit": 10}}
            )
            context = Mock(
                current_markdown="Safe narration.\n\nR&D <draft>",
                original_markdown="",
            )
            result = TTSValidationStage(config).process(context)
            assert result.changes == 1
            assert context.current_markdown == "Safe narration.\n\nR&D <draft>"

        Setting ``report_limit`` to zero returns a zero-finding result without
        adding tracker records.
        """
        text = context.current_markdown or context.original_markdown
        chunk_size = int(self.get_config("chunk_size", 2600))
        minimum_alpha = int(self.get_config("minimum_alpha", 4))
        limit = int(self.get_config("report_limit", 200))
        findings = 0
        if limit <= 0:
            return StageResult(stage=self.name, changes=0)
        for index, chunk in enumerate(self.validation_chunks(text, chunk_size), 1):
            issues = self.issues(chunk, minimum_alpha)
            if not issues:
                continue
            context.tracker.add(
                stage=self.name,
                block_index=-1,
                segment_index=index - 1,
                line=0,
                before=chunk[:500],
                after=chunk[:500],
                confidence=0.0,
                reason="Report only; TTS issue(s): " + ", ".join(issues),
            )
            findings += 1
            if findings >= limit:
                break
        return StageResult(stage=self.name, changes=findings)
