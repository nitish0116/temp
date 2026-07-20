"""Optional report-only validation of cleaned text for TTS consumption."""

from __future__ import annotations

import re

from ..core.stage import PipelineStage, StageResult


RAW_AMPERSAND = re.compile(
    r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-f]+;)",
    re.I,
)


class TTSValidationStage(PipelineStage):
    """Report unsafe TTS chunks without modifying the cleaned Markdown."""

    name = "TTSValidation"
    config_section = "tts_validation"

    @staticmethod
    def validation_chunks(text: str, chunk_size: int = 2600) -> list[str]:
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
