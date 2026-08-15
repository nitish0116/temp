"""Tests for Step 25 dedicated text-MT qualification."""

from videotranslator.commands.dedicated_mt import DedicatedMTTranslator, MADLAD_MODEL, NLLB_MODEL
from videotranslator.commands.qualify_text_translation import cache_file


def test_dedicated_mt_selects_native_protocol_without_loading():
    assert DedicatedMTTranslator(MADLAD_MODEL, "cpu").protocol == "madlad"
    assert DedicatedMTTranslator(NLLB_MODEL, "cpu").protocol == "nllb"


def test_dedicated_mt_cache_is_model_language_and_text_specific(tmp_path):
    first = cache_file(tmp_path, MADLAD_MODEL, "ko", "참귀엽죠?")
    assert first == cache_file(tmp_path, MADLAD_MODEL, "ko", "참귀엽죠?")
    assert first != cache_file(tmp_path, NLLB_MODEL, "ko", "참귀엽죠?")
    assert first != cache_file(tmp_path, MADLAD_MODEL, "zh", "참귀엽죠?")
    assert first.parent == tmp_path
