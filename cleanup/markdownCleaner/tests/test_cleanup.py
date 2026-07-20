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
        "# Epilogue\n\nThe actual ending.\n\nCharacter Profiles\nSecret data.\nn\n<u>Afterword</u>\n\nThanks for reading.",
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
    assert "Character Profiles" in cleaned


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



def test_ocr_misspelled_aferword_is_back_matter_cutoff():
    from markdownCleaner.modules.cleanup.document import BACK_MATTER_HEADING
    assert BACK_MATTER_HEADING.search("# _<u>Aferword</u>_\n")


def test_character_profiles_are_not_back_matter_cutoff():
    from markdownCleaner.modules.cleanup.document import BACK_MATTER_HEADING
    assert BACK_MATTER_HEADING.search("Character Profiles\n") is None
    assert BACK_MATTER_HEADING.search("OVERLORD Character Profiles\n") is None
    assert BACK_MATTER_HEADING.search("# _<u>Aferword</u>_\n")


def test_story_heading_is_promoted():
    source = "Story 1 | Enri’s Hectc, Eventul Life"
    assert DocumentCleanupStage._normalize_headings(source) == "# Story 1: Enri’s Hectc, Eventul Life"


def test_character_profiles_heading_is_promoted():
    assert DocumentCleanupStage._normalize_headings("Character Profiles") == "# Character Profiles"


def test_profile_picture_ocr_is_preserved_until_afterword():
    source = (
        "<!-- Start of picture text -->cover noise<!-- End of picture text -->\n"
        "<u>Story 1 | Main Story</u>\n\nBody.\n\n"
        "<!-- Start of picture text -->OVERLORD<br>Character Profiles<!-- End of picture text -->\n"
        "<!-- Start of picture text -->Character 1<br>ALICE<br>Hero<!-- End of picture text -->\n"
        "# _<u>Aferword</u>_\nAfterword text."
    )
    filtered, removed, preserved = DocumentCleanupStage._filter_picture_ocr(source)
    assert removed == 1
    assert preserved == 2
    assert "cover noise" not in filtered
    assert "# Character Profiles" in filtered
    assert "ALICE" in filtered
    assert "Afterword text." in filtered  # cutoff happens in the next document step


def test_false_heading_em_dash_continuation_rejoins():
    source = "And then—\n\n## —something hard stopped it."
    normalized = DocumentCleanupStage._normalize_headings(source)
    result = DocumentCleanupStage._reconstruct_paragraphs(normalized)
    assert result == "And then—something hard stopped it."


def test_profile_data_line_is_not_promoted_to_section_heading():
    source = "Character Profiles 29\nCharacter 24+\nENTOMA"
    result = DocumentCleanupStage._normalize_headings(source)
    assert not result.startswith("# Character Profiles")


def test_start_heading_finds_plain_story_with_separator_but_not_toc_item():
    from markdownCleaner.modules.cleanup.document import START_HEADING
    assert START_HEADING.search("<u>Story 1 | A Day Inside Nazarick</u>")
    assert START_HEADING.search("<u>Story 1 A Day Inside Nazarick Story 2 Other</u>") is None


def test_profile_reconstruction_preserves_internal_lines():
    source = "# Character Profiles\n\nCharacter 1\nALICE\nHero\n\nBiography text."
    result = DocumentCleanupStage._reconstruct_paragraphs(source)
    assert "Character 1\nALICE\nHero" in result


def test_sequence_independent_section_removal_preserves_following_content():
    source = "# Chapter 1: Start\n\nBody A.\n\n# Afterword\n\nRemove me.\n\n# Appendix\n\nKeep me."
    result, removed = DocumentCleanupStage._remove_named_sections(source, ["Afterword"])
    assert "Remove me." not in result
    assert "# Appendix" in result
    assert "Keep me." in result
    assert removed == ["Afterword"]


def test_unknown_existing_heading_is_preserved():
    source = "## World Building Notes\n\nUseful content."
    result = DocumentCleanupStage._normalize_headings(source)
    assert result.startswith("## World Building Notes")


def test_readable_picture_ocr_is_preserved_without_profile_sequence():
    source = "<!-- Start of picture text -->Map of the Northern Kingdom<br>Capital City<br>River Gate<!-- End of picture text -->"
    result, removed, preserved = DocumentCleanupStage._filter_picture_ocr(source, mode="safe")
    assert preserved == 1
    assert removed == 0
    assert "Northern Kingdom" in result


def test_gibberish_picture_ocr_is_removed_in_safe_mode():
    source = "<!-- Start of picture text -->\\ \\ / } i : ~~ 2) sl Pe yr XS ny P \\Y H f ; Z<!-- End of picture text -->"
    result, removed, preserved = DocumentCleanupStage._filter_picture_ocr(source, mode="safe")
    assert removed == 1
    assert preserved == 0


def test_structured_record_block_preserves_line_structure():
    source = "# Appendix\n\nName: Alice\nClass: Mage\nLevel: 20"
    result = DocumentCleanupStage._reconstruct_paragraphs(source)
    assert "Name: Alice\nClass: Mage\nLevel: 20" in result


def test_content_before_first_narrative_heading_is_never_truncated_by_sequence():
    source = "Preface-like custom content.\n\n# Custom Section\n\nKeep this.\n\n# Chapter 1: Start\n\nStory."
    normalized = DocumentCleanupStage._normalize_headings(source)
    assert "Preface-like custom content." in normalized
    assert "# Custom Section" in normalized
    assert "Keep this." in normalized


def test_explicit_section_removal_works_when_section_is_not_last():
    source = "# Chapter 1: A\n\nOne.\n\n# Afterword\n\nRemove.\n\n# Character Profiles\n\nKeep profile.\n\n# Appendix\n\nKeep appendix."
    result, _ = DocumentCleanupStage._remove_named_sections(source, ["Afterword"])
    assert "Remove." not in result
    assert "Keep profile." in result
    assert "Keep appendix." in result


def test_batch_summary_combines_files_stage_totals_and_change_records(tmp_path):
    from markdownCleaner.cli import _write_batch_summary

    entries = [
        {
            "relative_path": "a.md",
            "status": "success",
            "changes": 2,
            "elapsed_seconds": 1.25,
            "stage_counts": {"DocumentCleanup": 1, "Unicode": 1},
            "records": [
                {
                    "stage": "DocumentCleanup",
                    "line": 10,
                    "reason": "Normalized heading",
                    "confidence": 99.0,
                    "before": "Chapter 1 | Start",
                    "after": "# Chapter 1: Start",
                },
                {
                    "stage": "Unicode",
                    "line": 20,
                    "reason": "Normalized punctuation",
                    "confidence": 100.0,
                    "before": "A—B",
                    "after": "A-B",
                },
            ],
            "output": str(tmp_path / "a_clean.md"),
        },
        {
            "relative_path": "sub/b.md",
            "status": "success",
            "changes": 1,
            "elapsed_seconds": 0.75,
            "stage_counts": {"DocumentCleanup": 1, "Unicode": 0},
            "records": [
                {
                    "stage": "DocumentCleanup",
                    "line": 3,
                    "reason": "Joined wrapped paragraph",
                    "confidence": 96.0,
                    "before": "split text",
                    "after": "joined text",
                }
            ],
            "output": str(tmp_path / "sub" / "b_clean.md"),
        },
    ]

    report = _write_batch_summary(
        tmp_path,
        source_root=tmp_path / "input",
        entries=entries,
    )
    text = report.read_text(encoding="utf-8")

    assert "Files discovered: 2" in text
    assert "Total changes logged: 3" in text
    assert "| DocumentCleanup | 2 |" in text
    assert "| Unicode | 1 |" in text
    assert "### a.md" in text
    assert "### sub/b.md" in text
    assert "Chapter 1 | Start" in text
    assert "joined text" in text
