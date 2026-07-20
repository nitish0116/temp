"""Basic regression tests for the destructive OCR and structure bugs."""

from markdownCleaner.modules.cleanup.document import (
    DECORATIVE_SEPARATOR_LINE,
    DocumentCleanupStage,
)
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

def test_decorative_diamond_separator_is_removed():
    text = """Paragraph one.

◆◇◆◇◆

Paragraph two.
"""
    cleaned = DECORATIVE_SEPARATOR_LINE.sub("", text)
    assert "◆◇◆◇◆" not in cleaned
    assert "Paragraph one." in cleaned
    assert "Paragraph two." in cleaned


def test_plain_copyright_section_is_removed_until_next_narrative_heading():
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
    text = ("This is a sentence. " * 250).strip()
    chunks = DocumentCleanupStage._split_overlong_paragraph(text, max_chars=500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 550 for chunk in chunks)
    assert " ".join(chunks) == text
