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


def test_markdown_emphasis_removed_for_tts():
    source = "_Yggdrasil_, _Ba-ding!_, _because_, **bold**, __strong__"
    result = DocumentCleanupStage._strip_markdown_emphasis(source)
    assert result == "Yggdrasil, Ba-ding!, because, bold, strong"

def test_internal_underscores_are_preserved():
    source = "file_name and snake_case"
    assert DocumentCleanupStage._strip_markdown_emphasis(source) == source


def test_legacy_markup_cleanup_is_merged_into_document_stage():
    from markdownCleaner.modules.cleanup.document import HTML_BREAK, UNDERLINE_TAG, HTML_COMMENT
    source = "Hello<br>world <u>name</u><!-- empty -->"
    result = HTML_BREAK.sub("\n", source)
    result = UNDERLINE_TAG.sub("", result)
    result = HTML_COMMENT.sub("", result)
    assert result == "Hello\nworld name"


def test_symspell_one_edit_confidence_can_reach_safe_threshold():
    from markdownCleaner.modules.symspell.candidate import CorrectionCandidate
    candidate = CorrectionCandidate("retum", "return", 1, 1_000_000)
    assert candidate.calculate_confidence() >= 92


def test_symspell_two_edit_candidate_stays_below_safe_threshold():
    from markdownCleaner.modules.symspell.candidate import CorrectionCandidate
    candidate = CorrectionCandidate("abcdef", "abxyef", 2, 10_000_000)
    assert candidate.calculate_confidence() < 92


def test_safe_symspell_corrects_typo_and_preserves_protected_name(tmp_path):
    from markdownCleaner.modules.core.config import PipelineConfig
    from markdownCleaner.modules.core.context import ProcessingContext
    from markdownCleaner.modules.symspell.stage import SymSpellStage

    dictionary = tmp_path / "freq.txt"
    dictionary.write_text("because 10000000\nreturn 9000000\n", encoding="utf-8")
    glossary = tmp_path / "glossary.json"
    glossary.write_text('["Yggdrasil"]', encoding="utf-8")
    source = tmp_path / "sample.md"
    source.write_text("becuse Yggdrasil", encoding="utf-8")

    config = PipelineConfig({
        "paths": {"output_directory": str(tmp_path / "out")},
        "backup": {"enabled": False},
        "symspell": {
            "enabled": True,
            "dictionary": str(dictionary),
            "glossary": str(glossary),
            "max_edit_distance": 2,
            "max_auto_edit_distance": 1,
            "confidence_threshold": 92,
            "minimum_word_length": 4,
            "minimum_candidate_frequency": 1000,
            "ambiguity_margin": 2,
            "auto_protect_proper_nouns": True,
            "proper_noun_min_occurrences": 2,
        },
    })
    context = ProcessingContext(config)
    context.load_markdown(source)
    result = SymSpellStage(config).execute(context)
    assert result.success
    assert result.changes == 1
    assert context.get_markdown().strip() == "because Yggdrasil"


def test_false_atx_heading_is_demoted_and_context_rejoined():
    source = "And\n\n## then—\n\n\"A scream?\""
    normalized = DocumentCleanupStage._normalize_headings(source)
    assert "## then—" not in normalized
    result = DocumentCleanupStage._reconstruct_paragraphs(normalized)
    assert 'And then—\n\n“A scream?”' in result or 'And then—\n\n"A scream?"' in result


def test_plain_underlined_chapter_heading_is_promoted():
    # In the real source, legacy <u> tags are removed just before heading normalization.
    source = "Chapter 2 | The Floor Guardians"
    assert DocumentCleanupStage._normalize_headings(source) == "# Chapter 2: The Floor Guardians"


def test_nonstructural_converter_headings_are_demoted():
    source = "## Carne.\n\n## 0:00:38…\n\n## “A message maybe?”"
    result = DocumentCleanupStage._normalize_headings(source)
    assert result == "Carne.\n\n0:00:38…\n\n“A message maybe?”"


def test_duplicate_structural_headings_are_collapsed():
    source = "Epilogue\n### Epilogue"
    assert DocumentCleanupStage._normalize_headings(source) == "# Epilogue"



def test_afterword_is_hard_back_matter_cutoff(tmp_path):
    from markdownCleaner.modules.core.config import PipelineConfig
    from markdownCleaner.modules.core.context import ProcessingContext

    source = tmp_path / "novel.md"
    source.write_text(
        "# Epilogue\n\nThe actual ending.\n\n<u>Afterword</u>\n\nThanks for reading.\n\nCharacter Profiles\nSecret data.",
        encoding="utf-8",
    )
    config = PipelineConfig({
        "paths": {"output_directory": str(tmp_path / "out")},
        "backup": {"enabled": False},
        "cleanup": {
            "remove_picture_ocr": True,
            "remove_front_matter": False,
            "remove_back_matter": True,
            "remove_footnotes": True,
            "strip_markdown_emphasis": True,
        },
    })
    context = ProcessingContext(config)
    context.load_markdown(source)
    result = DocumentCleanupStage(config).execute(context)
    assert result.success
    cleaned = context.get_markdown()
    assert "The actual ending." in cleaned
    assert "Afterword" not in cleaned
    assert "Thanks for reading." not in cleaned
    assert "Character Profiles" not in cleaned


def test_cli_folder_file_discovery(tmp_path):
    from markdownCleaner.cli import _markdown_files

    (tmp_path / "one.md").write_text("one", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "two.md").write_text("two", encoding="utf-8")
    (nested / "ignore.txt").write_text("x", encoding="utf-8")

    assert [p.name for p in _markdown_files(tmp_path, recursive=False)] == ["one.md"]
    assert [p.name for p in _markdown_files(tmp_path, recursive=True)] == ["two.md", "one.md"] or \
           [p.name for p in _markdown_files(tmp_path, recursive=True)] == ["one.md", "two.md"]



def test_ocr_misspelled_aferword_and_character_profiles_are_back_matter_cutoffs():
    from markdownCleaner.modules.cleanup.document import BACK_MATTER_HEADING
    assert BACK_MATTER_HEADING.search("# _<u>Aferword</u>_\n")
    assert BACK_MATTER_HEADING.search("OVERLORD Character Profiles\n")
