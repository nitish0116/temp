"""Basic regression tests for the destructive OCR and structure bugs."""
from markdownCleaner.modules.cleanup.document import DocumentCleanupStage
from markdownCleaner.modules.regex.constants import OCR_CHARACTER_REPLACEMENTS


def test_unsafe_rn_replacement_is_disabled():
    assert "rn" not in OCR_CHARACTER_REPLACEMENTS


def test_paragraph_wrap_reconstruction():
    source = "A curious\n\nsight appeared.\n\nA new paragraph."
    result = DocumentCleanupStage._reconstruct_paragraphs(source)
    assert "A curious sight appeared." in result
    assert "sight appeared.\n\nA new paragraph." in result


def test_heading_normalization():
    source = "### <u>Chapter 1 | The Beginning</u>"
    assert DocumentCleanupStage._normalize_headings(source) == "# Chapter 1: The Beginning"
