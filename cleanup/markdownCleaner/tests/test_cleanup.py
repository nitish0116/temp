"""Basic regression tests for the destructive OCR and structure bugs."""

from markdownCleaner.modules.cleanup.document import (
    DECORATIVE_SEPARATOR_LINE,
    DocumentCleanupStage,
)
from markdownCleaner.modules.regex.constants import OCR_CHARACTER_REPLACEMENTS
from markdownCleaner.modules.report.exporter import meaningful_output_name
from markdownCleaner.cli import (
    _unique_batch_output_name,
    _write_batch_glossary_candidates,
    _write_simplified_glossary_candidates,
)
from markdownCleaner.modules.cleanup.tts_validation import TTSValidationStage
from markdownCleaner.modules.symspell.vocabulary import (
    VocabularyCandidateStage,
    classify_candidate,
    merge_approved_words,
    merge_learned_words,
    merge_rejected_words,
)


def test_glossary_candidate_classification_is_conservative():
    """Classify strong noun/adjective forms and leave ambiguous terms unknown."""
    assert classify_candidate("noncoms", [("the", "were")])[0] == "noun"
    assert classify_candidate("armored", [("an", "vehicle")])[0] == "adjective"
    assert classify_candidate("armored", [("they", "the")])[0] == "verb"
    assert classify_candidate("mobilized", [("they", "the")])[0] == "verb"
    assert classify_candidate("Degurechaff", [("Captain", "spoke")])[0] == "noun"
    assert classify_candidate("sitrep")[0] == "unknown"


def test_output_filename_is_readable_and_drops_release_tags():
    """Ensure output names retain the title while discarding release tags."""
    source = (
        "The Saga of Tanya the Evil - Volume 13 " "[Yen Press][Kobo_LNWNCentral].md"
    )
    assert meaningful_output_name(source) == (
        "The Saga of Tanya the Evil - Volume 13 - Cleaned.md"
    )


def test_output_filename_does_not_accumulate_clean_suffixes():
    """Ensure repeated cleanup runs do not accumulate cleanup suffixes."""
    source = "Hell_Mode_-_Volume_01__clean.md"
    assert meaningful_output_name(source) == "Hell Mode - Volume 01 - Cleaned.md"


def test_meaningful_batch_names_remain_collision_safe():
    """Ensure colliding batch outputs receive a readable numeric suffix."""
    used: set[str] = set()
    name = "Book - Volume 1 - Cleaned.md"
    assert _unique_batch_output_name(name, used) == name
    assert _unique_batch_output_name(name, used) == "Book - Volume 1 - Cleaned (2).md"


def test_batch_glossary_candidates_merge_words_and_preserve_sources(tmp_path):
    """Aggregate duplicate candidates while retaining per-file evidence."""
    import json

    entries = [
        {
            "relative_path": "Volume 1.md",
            "glossary_candidates": [
                {
                    "word": "Degurechaff",
                    "occurrences": 3,
                    "lines": [4, 8, 12],
                    "suggested_correction": "degrease",
                    "edit_distance": 2,
                    "confidence": 70.0,
                }
            ],
        },
        {
            "relative_path": "Volume 2.md",
            "glossary_candidates": [
                {
                    "word": "DEGURECHAFF",
                    "occurrences": 5,
                    "lines": [2, 9],
                    "suggested_correction": None,
                    "edit_distance": None,
                    "confidence": None,
                },
                {
                    "word": "noncoms",
                    "occurrences": 4,
                    "lines": [20, 21],
                    "suggested_correction": "noncom",
                    "edit_distance": 1,
                    "confidence": 95.0,
                },
            ],
        },
    ]

    path = _write_batch_glossary_candidates(tmp_path, entries)
    values = json.loads(path.read_text(encoding="utf-8"))

    assert path == tmp_path / "reports" / "glossary_candidates.json"
    assert [item["word"] for item in values] == ["Degurechaff", "noncoms"]
    assert values[0]["occurrences"] == 8
    assert values[0]["status"] == "pending_review"
    assert [source["file"] for source in values[0]["files"]] == [
        "Volume 1.md",
        "Volume 2.md",
    ]


def test_cli_writes_simplified_glossary_candidate_report(tmp_path):
    """Expose a compact review report through both helper and CLI workflows."""
    import json
    from markdownCleaner.cli import main

    source = tmp_path / "glossary_candidates.json"
    source.write_text(
        json.dumps(
            [
                {
                    "word": "sitrep",
                    "occurrences": 12,
                    "suggested_correction": "strep",
                    "classification": "noun",
                    "files": [{"file": "book.md"}],
                }
            ]
        ),
        encoding="utf-8",
    )
    default_output = _write_simplified_glossary_candidates(source)
    assert json.loads(default_output.read_text(encoding="utf-8")) == [
        {
            "word": "sitrep",
            "occurrences": 12,
            "suggested_correction": "strep",
        }
    ]

    explicit_output = tmp_path / "review.json"
    assert (
        main(
            [
                "--simplify-candidates",
                str(source),
                "--simplified-output",
                str(explicit_output),
            ]
        )
        == 0
    )
    assert explicit_output.exists()


def test_change_log_timestamps_are_timezone_aware_utc():
    """Record changes with non-deprecated, timezone-aware UTC timestamps."""
    from datetime import UTC, datetime
    from markdownCleaner.modules.report.change_log import ChangeLog

    log = ChangeLog()
    log.add(
        stage="RegexOCR",
        block_index=0,
        segment_index=0,
        line=1,
        before="teh",
        after="the",
        confidence=98.0,
        reason="Safe correction",
    )

    timestamp = datetime.fromisoformat(log.records[0].timestamp)
    assert timestamp.utcoffset() == UTC.utcoffset(timestamp)


def test_bounded_bare_and_blockquoted_glossary_footnotes_are_removed():
    """Remove bounded glossary notes in plain and blockquoted forms."""
    text = """Narrative ending.

1 **Heimat** A homeland or native country.

> 2 **Mage** A practitioner of magic.
> Continued explanation.

# Appendix

Content that must remain.
"""
    cleaned, removed = DocumentCleanupStage._remove_bounded_glossary_footnotes(text)
    assert removed == 2
    assert "**Heimat**" not in cleaned
    assert "**Mage**" not in cleaned
    assert "# Appendix" in cleaned
    assert "Content that must remain." in cleaned


def test_ordinary_numbered_prose_is_not_treated_as_glossary_footnote():
    """Preserve numbered prose that does not match a glossary note."""
    text = "1. This is an ordinary numbered instruction.\n"
    cleaned, removed = DocumentCleanupStage._remove_bounded_glossary_footnotes(text)
    assert removed == 0
    assert cleaned == text


def test_generic_newsletter_tail_is_removed():
    """Remove a publisher newsletter solicitation from the document tail."""
    text = """# Epilogue

The story ends.

# Sign Up for the Yen Press Newsletter
Visit yenpress.com/newsletter for updates.
"""
    cleaned, kind = DocumentCleanupStage._remove_generic_publisher_tail(text)
    assert kind == "publisher signup/newsletter tail"
    assert "The story ends." in cleaned
    assert "Sign Up" not in cleaned


def test_trailing_numbered_contents_appendix_is_removed():
    """Remove a numbered table-of-contents appendix at the document tail."""
    text = """# Epilogue

The story ends.

1. Cover
2. Insert
3. Chapter 1
4. Afterword
"""
    cleaned, kind = DocumentCleanupStage._remove_generic_publisher_tail(text)
    assert kind == "numbered contents appendix"
    assert "The story ends." in cleaned
    assert "1. Cover" not in cleaned


def test_ocr_noise_detection_is_conservative_and_report_only():
    """Report obvious OCR noise without modifying surrounding prose."""
    text = """Normal narrative text remains here.
bcdfghjklmnpqrstvwxyz
The year was 1928.
"""
    findings = DocumentCleanupStage._find_conservative_ocr_noise(text)
    assert len(findings) == 1
    assert findings[0][1] == "bcdfghjklmnpqrstvwxyz"
    assert "Normal narrative text remains here." in text
    assert "The year was 1928." in text


def test_tts_validation_reports_xml_ampersand_and_low_alpha():
    """Report unsafe XML characters and chunks with too little speech text."""
    assert TTSValidationStage.issues("& < >", minimum_alpha=4) == [
        "LOW_ALPHA(0)",
        "XML_BRACKETS",
        "RAW_AMPERSAND",
    ]
    assert TTSValidationStage.issues("Safe narration text.") == []


def test_explicit_glossary_approval_merges_without_duplicates(tmp_path):
    """Merge approved terms without case-insensitive glossary duplicates."""
    glossary = tmp_path / "custom_words.json"
    glossary.write_text('["sitrep", "Ainz Ooal Gown"]\n', encoding="utf-8")

    added = merge_approved_words(
        glossary,
        ["SITREP", "noncoms", "Degurechaff"],
    )

    import json

    values = json.loads(glossary.read_text(encoding="utf-8"))
    assert added == ["noncoms", "Degurechaff"]
    assert sum(word.casefold() == "sitrep" for word in values) == 1
    assert "noncoms" in values
    assert "Degurechaff" in values
    assert "Ainz Ooal Gown" in values


def test_learned_words_use_readable_structured_format(tmp_path):
    """Write learned terms with instructions, sorting, and safe deduplication."""
    import json

    learned = tmp_path / "learned_words.json"
    learned.write_text('["sitrep", "Hmph"]\n', encoding="utf-8")

    added = merge_learned_words(learned, ["SITREP", "noncoms"])
    data = json.loads(learned.read_text(encoding="utf-8"))

    assert added == ["noncoms"]
    assert "--learn-words" in data["_description"]
    assert data["words"] == ["Hmph", "noncoms", "sitrep"]


def test_dictionary_loads_structured_learned_words(tmp_path):
    """Load only the words list from the structured learned-word object."""
    from markdownCleaner.modules.symspell.dictionary import DictionaryManager

    learned = tmp_path / "learned_words.json"
    learned.write_text(
        '{"_description": "instructions", "words": ["sitrep", "noncoms"]}\n',
        encoding="utf-8",
    )
    manager = DictionaryManager(learned_path=learned)
    manager.load()

    assert manager.is_protected("sitrep")
    assert manager.is_protected("noncoms")
    assert not manager.contains("_description")


def test_cli_can_update_learned_words_without_manual_json_editing(tmp_path):
    """Provide a safe CLI workflow for adding reviewed learned terms."""
    import json
    from markdownCleaner.cli import main

    learned = tmp_path / "learned_words.json"
    config = tmp_path / "config.yaml"
    config.write_text("paths: {}\nbackup: {}\n", encoding="utf-8")

    result = main(
        [
            "--config",
            str(config),
            "--learn-words",
            "sitrep",
            "noncoms",
            "--learned-file",
            str(learned),
        ]
    )

    assert result == 0
    assert json.loads(learned.read_text(encoding="utf-8"))["words"] == [
        "noncoms",
        "sitrep",
    ]


def test_cli_can_persistently_reject_glossary_candidates(tmp_path):
    """Store rejected candidates through the CLI without protecting them."""
    import json
    from markdownCleaner.cli import main

    rejected = tmp_path / "rejected_words.json"
    config = tmp_path / "config.yaml"
    config.write_text("paths: {}\nbackup: {}\n", encoding="utf-8")

    result = main(
        [
            "--config",
            str(config),
            "--reject-words",
            "offense",
            "humor",
            "grueling",
            "labor",
            "practiced",
            "armored",
            "afterward",
            "--rejected-file",
            str(rejected),
        ]
    )

    assert result == 0
    assert json.loads(rejected.read_text(encoding="utf-8"))["words"] == [
        "afterward",
        "armored",
        "grueling",
        "humor",
        "labor",
        "offense",
        "practiced",
    ]


def test_vocabulary_candidates_are_discovered_without_mutating_glossary(tmp_path):
    """Discover repeated unknown terms without silently changing the glossary."""
    from markdownCleaner.modules.core.config import PipelineConfig
    from markdownCleaner.modules.core.context import ProcessingContext

    dictionary = tmp_path / "freq.txt"
    dictionary.write_text("the 10000000\nreported 9000000\n", encoding="utf-8")
    glossary = tmp_path / "custom_words.json"
    glossary.write_text("[]\n", encoding="utf-8")
    source = tmp_path / "sample.md"
    source.write_text(
        "Degurechaff reported. Degurechaff replied. Degurechaff nodded.",
        encoding="utf-8",
    )
    config = PipelineConfig(
        {
            "paths": {"output_directory": str(tmp_path / "out")},
            "backup": {"enabled": False},
            "symspell": {
                "dictionary": str(dictionary),
                "glossary": str(glossary),
                "max_edit_distance": 1,
            },
            "vocabulary_candidates": {
                "enabled": True,
                "minimum_occurrences": 3,
                "report_limit": 20,
            },
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)

    result = VocabularyCandidateStage(config).execute(context)

    assert result.success
    assert any(
        item["word"] == "Degurechaff"
        for item in context.metadata["glossary_candidates"]
    )
    assert glossary.read_text(encoding="utf-8") == "[]\n"

    rejected = tmp_path / "rejected_words.json"
    merge_rejected_words(rejected, ["Degurechaff"])
    config.set("vocabulary_candidates.rejected", str(rejected))
    rejected_context = ProcessingContext(config)
    rejected_context.load_markdown(source)

    rejected_result = VocabularyCandidateStage(config).execute(rejected_context)

    assert rejected_result.success
    assert rejected_context.metadata["glossary_candidates"] == []


def test_unsafe_rn_replacement_is_disabled():
    """Ensure the ambiguous OCR replacement from 'rn' remains disabled."""
    assert "rn" not in OCR_CHARACTER_REPLACEMENTS


def test_paragraph_wrap_reconstruction():
    """Join wrapped fragments while retaining genuine paragraph boundaries."""
    source = "A curious\n\nsight appeared.\n\nA new paragraph."
    result = DocumentCleanupStage._reconstruct_paragraphs(source)
    assert "A curious sight appeared." in result
    assert "sight appeared.\n\nA new paragraph." in result


def test_heading_normalization():
    """Normalize converter-specific heading markup into standard Markdown."""
    source = "### <u>Chapter 1 | The Beginning</u>"
    assert (
        DocumentCleanupStage._normalize_headings(source) == "# Chapter 1: The Beginning"
    )


def test_markdown_emphasis_removed_for_tts():
    """Strip Markdown emphasis markers before text-to-speech processing."""
    source = "_Yggdrasil_, _Ba-ding!_, _because_, **bold**, __strong__"
    result = DocumentCleanupStage._strip_markdown_emphasis(source)
    assert result == "Yggdrasil, Ba-ding!, because, bold, strong"


def test_internal_underscores_are_preserved():
    """Preserve underscores that form part of identifiers rather than emphasis."""
    source = "file_name and snake_case"
    assert DocumentCleanupStage._strip_markdown_emphasis(source) == source


def test_legacy_markup_cleanup_is_merged_into_document_stage():
    """Confirm legacy markup rules are handled by the unified document stage."""
    from markdownCleaner.modules.cleanup.document import (
        HTML_BREAK,
        UNDERLINE_TAG,
        HTML_COMMENT,
    )

    source = "Hello<br>world <u>name</u><!-- empty -->"
    result = HTML_BREAK.sub("\n", source)
    result = UNDERLINE_TAG.sub("", result)
    result = HTML_COMMENT.sub("", result)
    assert result == "Hello\nworld name"


def test_symspell_one_edit_confidence_can_reach_safe_threshold():
    """Allow a strong one-edit spelling candidate to reach the safe threshold."""
    from markdownCleaner.modules.symspell.candidate import CorrectionCandidate

    candidate = CorrectionCandidate("retum", "return", 1, 1_000_000)
    assert candidate.calculate_confidence() >= 92


def test_symspell_two_edit_candidate_stays_below_safe_threshold():
    """Keep a riskier two-edit spelling candidate below the safe threshold."""
    from markdownCleaner.modules.symspell.candidate import CorrectionCandidate

    candidate = CorrectionCandidate("abcdef", "abxyef", 2, 10_000_000)
    assert candidate.calculate_confidence() < 92


def test_safe_symspell_corrects_typo_and_preserves_protected_name(tmp_path):
    """Correct a safe typo without altering a protected proper name."""
    from markdownCleaner.modules.core.config import PipelineConfig
    from markdownCleaner.modules.core.context import ProcessingContext
    from markdownCleaner.modules.symspell.stage import SymSpellStage

    dictionary = tmp_path / "freq.txt"
    dictionary.write_text("because 10000000\nreturn 9000000\n", encoding="utf-8")
    glossary = tmp_path / "glossary.json"
    glossary.write_text('["Yggdrasil"]', encoding="utf-8")
    source = tmp_path / "sample.md"
    source.write_text("becuse Yggdrasil", encoding="utf-8")

    config = PipelineConfig(
        {
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
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)
    result = SymSpellStage(config).execute(context)
    assert result.success
    assert result.changes == 1
    assert context.get_markdown().strip() == "because Yggdrasil"


def test_symspell_does_not_convert_regular_plural_to_singular(tmp_path):
    """Prevent SymSpell from replacing an ordinary plural with its singular."""
    from markdownCleaner.modules.core.config import PipelineConfig
    from markdownCleaner.modules.core.context import ProcessingContext
    from markdownCleaner.modules.symspell.stage import SymSpellStage

    dictionary = tmp_path / "freq.txt"
    dictionary.write_text("noncom 10000000\n", encoding="utf-8")
    source = tmp_path / "sample.md"
    source.write_text("The noncoms submitted their reports.", encoding="utf-8")
    config = PipelineConfig(
        {
            "paths": {"output_directory": str(tmp_path / "out")},
            "backup": {"enabled": False},
            "symspell": {
                "enabled": True,
                "dictionary": str(dictionary),
                "max_edit_distance": 2,
                "max_auto_edit_distance": 1,
                "confidence_threshold": 92,
                "minimum_word_length": 4,
                "minimum_candidate_frequency": 1000,
                "ambiguity_margin": 2,
                "auto_protect_proper_nouns": False,
            },
        }
    )
    context = ProcessingContext(config)
    context.load_markdown(source)

    result = SymSpellStage(config).execute(context)

    assert result.success
    assert result.changes == 0
    assert "noncoms" in context.get_markdown()


def test_false_atx_heading_is_demoted_and_context_rejoined():
    """Demote a false ATX heading and rejoin it to its narrative context."""
    source = 'And\n\n## then—\n\n"A scream?"'
    normalized = DocumentCleanupStage._normalize_headings(source)
    assert "## then—" not in normalized
    result = DocumentCleanupStage._reconstruct_paragraphs(normalized)
    assert "And then—\n\n“A scream?”" in result or 'And then—\n\n"A scream?"' in result


def test_plain_underlined_chapter_heading_is_promoted():
    """Promote a plain underlined chapter label to a structural heading."""
    # In the real source, legacy <u> tags are removed just before heading normalization.
    source = "Chapter 2 | The Floor Guardians"
    assert (
        DocumentCleanupStage._normalize_headings(source)
        == "# Chapter 2: The Floor Guardians"
    )


def test_nonstructural_converter_headings_are_demoted():
    """Demote converter-created headings that are actually narrative text."""
    source = "## Carne.\n\n## 0:00:38…\n\n## “A message maybe?”"
    result = DocumentCleanupStage._normalize_headings(source)
    assert result == "Carne.\n\n0:00:38…\n\n“A message maybe?”"


def test_duplicate_structural_headings_are_collapsed():
    """Collapse adjacent duplicate structural headings into one heading."""
    source = "Epilogue\n### Epilogue"
    assert DocumentCleanupStage._normalize_headings(source) == "# Epilogue"


def test_afterword_is_hard_back_matter_cutoff(tmp_path):
    """Treat an Afterword heading as a definitive back-matter cutoff."""
    from markdownCleaner.modules.core.config import PipelineConfig
    from markdownCleaner.modules.core.context import ProcessingContext

    source = tmp_path / "novel.md"
    source.write_text(
        "# Epilogue\n\nThe actual ending.\n\nCharacter Profiles\nSecret data.\nn\n<u>Afterword</u>\n\nThanks for reading.",
        encoding="utf-8",
    )
    config = PipelineConfig(
        {
            "paths": {"output_directory": str(tmp_path / "out")},
            "backup": {"enabled": False},
            "cleanup": {
                "remove_picture_ocr": True,
                "remove_front_matter": False,
                "remove_back_matter": True,
                "remove_footnotes": True,
                "strip_markdown_emphasis": True,
            },
        }
    )
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
    """Discover Markdown inputs consistently for both files and folders."""
    from markdownCleaner.cli import _markdown_files

    (tmp_path / "one.md").write_text("one", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "two.md").write_text("two", encoding="utf-8")
    (nested / "ignore.txt").write_text("x", encoding="utf-8")

    assert [p.name for p in _markdown_files(tmp_path, recursive=False)] == ["one.md"]
    assert [p.name for p in _markdown_files(tmp_path, recursive=True)] == [
        "two.md",
        "one.md",
    ] or [p.name for p in _markdown_files(tmp_path, recursive=True)] == [
        "one.md",
        "two.md",
    ]


def test_ocr_misspelled_aferword_is_back_matter_cutoff():
    """Recognize the common OCR misspelling of Afterword as back matter."""
    from markdownCleaner.modules.cleanup.document import BACK_MATTER_HEADING

    assert BACK_MATTER_HEADING.search("# _<u>Aferword</u>_\n")


def test_character_profiles_are_not_back_matter_cutoff():
    """Avoid treating character profiles as the general back-matter cutoff."""
    from markdownCleaner.modules.cleanup.document import BACK_MATTER_HEADING

    assert BACK_MATTER_HEADING.search("Character Profiles\n") is None
    assert BACK_MATTER_HEADING.search("OVERLORD Character Profiles\n") is None
    assert BACK_MATTER_HEADING.search("# _<u>Aferword</u>_\n")


def test_story_heading_is_promoted():
    """Promote a plain story label to a standard Markdown heading."""
    source = "Story 1 | Enri’s Hectc, Eventul Life"
    assert (
        DocumentCleanupStage._normalize_headings(source)
        == "# Story 1: Enri’s Hectc, Eventul Life"
    )


def test_character_profiles_heading_is_promoted():
    """Promote a plain character-profiles label to a structural heading."""
    assert (
        DocumentCleanupStage._normalize_headings("Character Profiles")
        == "# Character Profiles"
    )


def test_profile_picture_ocr_is_preserved_until_afterword():
    """Preserve readable profile image text while removing the Afterword tail."""
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
    """Rejoin an em-dash continuation incorrectly parsed as a heading."""
    source = "And then—\n\n## —something hard stopped it."
    normalized = DocumentCleanupStage._normalize_headings(source)
    result = DocumentCleanupStage._reconstruct_paragraphs(normalized)
    assert result == "And then—something hard stopped it."


def test_profile_data_line_is_not_promoted_to_section_heading():
    """Keep profile record fields from becoming structural headings."""
    source = "Character Profiles 29\nCharacter 24+\nENTOMA"
    result = DocumentCleanupStage._normalize_headings(source)
    assert not result.startswith("# Character Profiles")


def test_start_heading_finds_plain_story_with_separator_but_not_toc_item():
    """Find a real story heading without selecting its contents entry."""
    from markdownCleaner.modules.cleanup.document import START_HEADING

    assert START_HEADING.search("<u>Story 1 | A Day Inside Nazarick</u>")
    assert (
        START_HEADING.search("<u>Story 1 A Day Inside Nazarick Story 2 Other</u>")
        is None
    )


def test_profile_reconstruction_preserves_internal_lines():
    """Preserve line structure inside character-profile records."""
    source = "# Character Profiles\n\nCharacter 1\nALICE\nHero\n\nBiography text."
    result = DocumentCleanupStage._reconstruct_paragraphs(source)
    assert "Character 1\nALICE\nHero" in result


def test_sequence_independent_section_removal_preserves_following_content():
    """Remove a named section without discarding unrelated following content."""
    source = "# Chapter 1: Start\n\nBody A.\n\n# Afterword\n\nRemove me.\n\n# Appendix\n\nKeep me."
    result, removed = DocumentCleanupStage._remove_named_sections(source, ["Afterword"])
    assert "Remove me." not in result
    assert "# Appendix" in result
    assert "Keep me." in result
    assert removed == ["Afterword"]


def test_unknown_existing_heading_is_preserved():
    """Preserve an existing heading even when its title is not predefined."""
    source = "## World Building Notes\n\nUseful content."
    result = DocumentCleanupStage._normalize_headings(source)
    assert result.startswith("## World Building Notes")


def test_readable_picture_ocr_is_preserved_without_profile_sequence():
    """Preserve readable image OCR even outside a character-profile sequence."""
    source = "<!-- Start of picture text -->Map of the Northern Kingdom<br>Capital City<br>River Gate<!-- End of picture text -->"
    result, removed, preserved = DocumentCleanupStage._filter_picture_ocr(
        source, mode="safe"
    )
    assert preserved == 1
    assert removed == 0
    assert "Northern Kingdom" in result


def test_gibberish_picture_ocr_is_removed_in_safe_mode():
    """Remove clearly nonsensical image OCR during conservative cleanup."""
    source = "<!-- Start of picture text -->\\ \\ / } i : ~~ 2) sl Pe yr XS ny P \\Y H f ; Z<!-- End of picture text -->"
    result, removed, preserved = DocumentCleanupStage._filter_picture_ocr(
        source, mode="safe"
    )
    assert removed == 1
    assert preserved == 0


def test_structured_record_block_preserves_line_structure():
    """Preserve individual lines in a structured appendix record."""
    source = "# Appendix\n\nName: Alice\nClass: Mage\nLevel: 20"
    result = DocumentCleanupStage._reconstruct_paragraphs(source)
    assert "Name: Alice\nClass: Mage\nLevel: 20" in result


def test_content_before_first_narrative_heading_is_never_truncated_by_sequence():
    """Retain custom content appearing before the first narrative heading."""
    source = "Preface-like custom content.\n\n# Custom Section\n\nKeep this.\n\n# Chapter 1: Start\n\nStory."
    normalized = DocumentCleanupStage._normalize_headings(source)
    assert "Preface-like custom content." in normalized
    assert "# Custom Section" in normalized
    assert "Keep this." in normalized


def test_explicit_section_removal_works_when_section_is_not_last():
    """Remove a named middle section while preserving later sections."""
    source = "# Chapter 1: A\n\nOne.\n\n# Afterword\n\nRemove.\n\n# Character Profiles\n\nKeep profile.\n\n# Appendix\n\nKeep appendix."
    result, _ = DocumentCleanupStage._remove_named_sections(source, ["Afterword"])
    assert "Remove." not in result
    assert "Keep profile." in result
    assert "Keep appendix." in result


def test_batch_summary_combines_files_stage_totals_and_change_records(tmp_path):
    """Aggregate file outcomes, stage totals, and changes in the batch summary."""
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


def test_decorative_diamond_separator_is_removed():
    """Remove a standalone decorative diamond separator between paragraphs."""
    text = """Paragraph one.

◆◇◆◇◆

Paragraph two.
"""
    cleaned = DECORATIVE_SEPARATOR_LINE.sub("", text)
    assert "◆◇◆◇◆" not in cleaned

    wrapped = "Before.\n**◆◇◆◇◆,\nAfter."
    assert "◆" not in DECORATIVE_SEPARATOR_LINE.sub("", wrapped)
    assert "Paragraph one." in cleaned
    assert "Paragraph two." in cleaned


def test_plain_copyright_section_is_removed_until_next_narrative_heading():
    """Remove plain copyright front matter up to the narrative heading."""
    text = """Copyright
Example Book
This book is a work of fiction.
Yen Press, LLC supports the right to free expression.

Contents
Cover
Copyright
Chapter 0: Prologue

Story begins here.
"""
    cleaned = DocumentCleanupStage._remove_local_metadata(text)
    assert "Copyright" not in cleaned
    assert "Yen Press" not in cleaned
    assert "Story begins here." in cleaned


def test_metadata_heavy_overlord_style_prefix_is_removed():
    """Remove a noisy metadata-heavy prefix from an Overlord-style source."""
    text = """vy SS a 4 D > random cover OCR
Illustration by so-bin OVERLORD2 Kugane Maruyama
Begin Reading Table of Contents Insert Yen Newsleter Copyright Page
Yen Press, LLC supports the right to free expression and the value of copyright.
The scanning, uploading, and distribution of this book without permission is a theft.
OVERLORD Volume 2: The Dark Warrior
Kugane Maruyama | Illustration by so-bin

#### Prologue

Real story text.
"""
    cleaned = DocumentCleanupStage._remove_leading_front_matter(text)
    assert cleaned.lstrip().startswith("#### Prologue")
    assert "vy SS" not in cleaned
    assert "Begin Reading" not in cleaned
    assert "Real story text." in cleaned


def test_front_matter_skips_concatenated_toc_heading_and_starts_at_real_chapter():
    """Skip concatenated contents text and begin at the actual chapter."""
    text = """Copyright
Publisher metadata
Contents
Chapter 6 Intro Chapter 7 Attack Preparations
Chapter 8 The Six Arms
random picture OCR

Chapter 6 | Disturbance in the Royal Capital: Introduction

Real story.
"""
    cleaned = DocumentCleanupStage._remove_leading_front_matter(text)
    assert cleaned.lstrip().startswith("Chapter 6 | Disturbance")
    assert "Chapter 6 Intro Chapter 7" not in cleaned
    assert "random picture OCR" not in cleaned


def test_picture_ocr_character_profiles_marker_becomes_excludable_heading():
    """Convert an image-OCR profile marker into a removable heading."""
    text = """Story ending.

<!-- Start of picture text -->
OVERLORD<br>Character Profiles<br>noise
<!-- End of picture text -->

Profile prose that should be removed.

# Bonus Short Stories

Keep this.
"""
    filtered, _, _ = DocumentCleanupStage._filter_picture_ocr(
        text,
        mode="safe",
        excluded_sections=["Character Profiles"],
    )
    cleaned, removed = DocumentCleanupStage._remove_named_sections(
        filtered,
        ["Character Profiles"],
    )
    assert "Profile prose that should be removed." not in cleaned
    assert "# Bonus Short Stories" in cleaned
    assert "Keep this." in cleaned
    assert removed


def test_series_prefixed_character_profiles_heading_is_excluded():
    """Exclude character-profile headings prefixed by the series title."""
    text = """# Epilogue

Ending.

OVERLORD Character Profiles

Profile data.

# Bonus Short Stories

Keep this.
"""
    cleaned, removed = DocumentCleanupStage._remove_named_sections(
        text,
        ["Character Profiles"],
    )
    assert "Profile data." not in cleaned
    assert "Bonus Short Stories" in cleaned
    assert "Keep this." in cleaned
    assert removed


def test_next_volume_coming_soon_promotional_tail_is_removed():
    """Remove a next-volume promotion from the document tail."""
    text = """# Epilogue

Actual ending.

# Volume 7: The Next Book
Illustration by Someone
Coming soon from Publisher

# Prologue
Preview content.
"""
    cleaned = DocumentCleanupStage._remove_promotional_tail(text)
    assert "Actual ending." in cleaned
    assert "Volume 7" not in cleaned
    assert "Preview content." not in cleaned


def test_picture_ocr_with_one_heading_line_and_many_noise_lines_is_removed():
    """Remove image text dominated by OCR noise despite one heading."""
    block = """<!-- Start of picture text -->
=.
-
. =
SS f gz -> <n (
-
fx
.
a
f
.Ss
x ted
Le - ZAM
ge y
J we
Chapter2 Journey
<!-- End of picture text -->"""
    filtered, removed, preserved = DocumentCleanupStage._filter_picture_ocr(
        block,
        mode="safe",
        excluded_sections=[],
    )
    assert "Chapter2 Journey" not in filtered
    assert removed == 1
    assert preserved == 0


def test_tanya_explicit_chapter_marker_wins_over_contents_entries():
    """Prefer Tanya's explicit chapter marker over contents entries."""
    text = """Copyright
Publisher
Contents
Chapter 0: Prologue
Chapter I: End of the Beginning
Afterword
[chapter] 0 Prologue
REAL STORY
"""
    cleaned = DocumentCleanupStage._remove_leading_front_matter(text)
    assert cleaned.startswith("[chapter] 0 Prologue")
    assert "Contents" not in cleaned
    assert "REAL STORY" in cleaned


def test_tanya_volume_13_full_copyright_and_contents_prefix_is_removed():
    """Remove Tanya volume 13 copyright metadata and contents prefix."""
    text = """Copyright
The Saga of Tanya the Evil, Vol. 13
Carlo Zen
Translation by James Balzer
Cover art by Shinobu Shinotsuki
This book is a work of fiction. Names, characters, places, and incidents are the
product of the author's imagination or are used fictitiously.
YOJO SENKI Vol. 13 Dum Spiro, Spero JO
©Carlo Zen 2023
First published in Japan in 2023 by KADOKAWA CORPORATION, Tokyo.
English translation © 2024 by Yen Press, LLC
Yen Press, LLC supports the right to free expression and the value of copyright.
The scanning, uploading, and distribution of this book without permission is a
theft of the author's intellectual property.
```
material from the book, please contact the publisher.
```
Yen On
150 West 30th Street, 19th Floor
New York, NY 10001
First Yen On Edition: December 2024
Library of Congress Cataloging-in-Publication Data
```
ISBNs: 979-8-8554-0287-2 (paperback) 979-8-8554-0288-9 (ebook)
```
E3-20241126-JV-NF-ORI
Contents
Cover
Insert
Title Page
Copyright
Chapter 0: Prologue
Chapter I: End of the Beginning
Chapter II: House of Cards
Afterword
Yen Newsletter
[chapter] 0 Prologue
JANUARY 15, UNIFIED YEAR 1928, THE GENERAL STAFF OFFICE
Real story text.
"""
    cleaned = DocumentCleanupStage._remove_leading_front_matter(text)

    assert cleaned.startswith("[chapter] 0 Prologue")
    assert "The Saga of Tanya the Evil, Vol. 13" not in cleaned
    assert "Yen Press, LLC" not in cleaned
    assert "ISBNs:" not in cleaned
    assert "\nContents\n" not in cleaned
    assert "Chapter I: End of the Beginning" not in cleaned
    assert "Real story text." in cleaned


def test_overlong_reconstructed_paragraphs_are_split_without_losing_text():
    """Split overlong paragraphs without losing narrative text."""
    text = ("This is a sentence. " * 250).strip()
    chunks = DocumentCleanupStage._split_overlong_paragraph(text, max_chars=500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 550 for chunk in chunks)
    assert " ".join(chunks) == text
