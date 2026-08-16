"""Tests for Step 25 dedicated text-MT qualification."""

import json

from videotranslator.commands.dedicated_mt import DedicatedMTTranslator, MADLAD_MODEL, NLLB_MODEL, collect_dedicated_mt_evidence
from videotranslator.commands.qualify_text_translation import cache_file
from videotranslator.tests.test_translation_agreement import translated_document


def test_dedicated_mt_selects_native_protocol_without_loading():
    assert DedicatedMTTranslator(MADLAD_MODEL, "cpu").protocol == "madlad"
    assert DedicatedMTTranslator(NLLB_MODEL, "cpu").protocol == "nllb"


def test_dedicated_mt_cache_is_model_language_and_text_specific(tmp_path):
    first = cache_file(tmp_path, MADLAD_MODEL, "ko", "참귀엽죠?")
    assert first == cache_file(tmp_path, MADLAD_MODEL, "ko", "참귀엽죠?")
    assert first != cache_file(tmp_path, NLLB_MODEL, "ko", "참귀엽죠?")
    assert first != cache_file(tmp_path, MADLAD_MODEL, "zh", "참귀엽죠?")
    assert first.parent == tmp_path


def test_dedicated_evidence_is_cached_without_replacing_primary(tmp_path):
    calls = []
    translate = lambda text, source, target: calls.append(text) or "Was there a place in Seoul?"
    output, candidates, report = collect_dedicated_mt_evidence(
        translated_document(), translate, model="fixture", cache_directory=tmp_path,
    )
    assert report["passed"] is True and candidates["group-1"].endswith("Seoul?")
    assert output["segments"][0]["translated_text"].startswith("Seattle")
    collect_dedicated_mt_evidence(translated_document(), translate, model="fixture", cache_directory=tmp_path)
    assert len(calls) == 1


def test_dedicated_evidence_accepts_legacy_text_cache(tmp_path):
    document = translated_document()
    collect_dedicated_mt_evidence(
        document, lambda text, source, target: "Legacy cached translation",
        model="fixture", cache_directory=tmp_path,
    )
    cache = next(tmp_path.glob("*.json"))
    cache.write_text(json.dumps({"text": "Legacy cached translation"}), encoding="utf-8")
    output, candidates, report = collect_dedicated_mt_evidence(
        document, lambda *args: (_ for _ in ()).throw(AssertionError("cache miss")),
        model="fixture", cache_directory=tmp_path,
    )
    assert report["passed"] is True
    assert report["checks"][0]["cache_hit"] is True
    assert candidates["group-1"] == "Legacy cached translation"
    assert output["segments"][0]["metadata"]["dedicated_mt"]["text"] == "Legacy cached translation"
