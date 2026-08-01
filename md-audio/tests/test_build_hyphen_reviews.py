import json
from pathlib import Path

import build_hyphen_reviews as pipeline


def test_integrated_pipeline_writes_portable_split_records(tmp_path):
    library = tmp_path / "Library"
    library.mkdir()
    (library / "hyphen.md").write_text(
        "They saw animals-creatures gathering. Mana-rich ground.", encoding="utf-8"
    )
    (library / "punctuation.md").write_text(
        "They saw animals—creatures gathering. Mana-rich ground.", encoding="utf-8"
    )
    main = tmp_path / "main.json"
    ambiguous = tmp_path / "ambiguous.json"
    resolved = tmp_path / "resolved.json"

    cache = tmp_path / "cache.json"
    result = pipeline.run_pipeline(
        library, main, ambiguous, resolved, cache_path=cache
    )

    main_record = json.loads(main.read_text(encoding="utf-8"))
    ambiguous_record = json.loads(ambiguous.read_text(encoding="utf-8"))
    assert result["cross_evidence_resolved"] == 1
    assert any(item["token"] == "animals-creatures" for item in main_record["candidates"])
    assert any(
        item["token"].casefold() == "mana-rich"
        for item in ambiguous_record["candidates"]
    )
    assert not Path(main_record["source_root"]).is_absolute()
    assert not Path(main_record["ambiguous_output"]).is_absolute()


def test_incremental_cache_scans_only_changed_files(tmp_path, monkeypatch):
    library = tmp_path / "Library"
    library.mkdir()
    first = library / "first.md"
    second = library / "second.md"
    first.write_text("half-sigh", encoding="utf-8")
    second.write_text("mana-rich", encoding="utf-8")
    cache = tmp_path / "cache.json"

    _, initial = pipeline.update_cache(library, cache)
    assert initial == {"cache_hits": 0, "cache_misses": 2, "cache_removed": 0}

    original_scan = pipeline.scan_markdown_file
    scanned = []

    def tracked_scan(path, raw=None):
        scanned.append(path.name)
        return original_scan(path, raw)

    monkeypatch.setattr(pipeline, "scan_markdown_file", tracked_scan)
    _, unchanged = pipeline.update_cache(library, cache)
    assert unchanged == {"cache_hits": 2, "cache_misses": 0, "cache_removed": 0}
    assert scanned == []

    first.write_text("half-smile is longer", encoding="utf-8")
    _, modified = pipeline.update_cache(library, cache)
    assert modified == {"cache_hits": 1, "cache_misses": 1, "cache_removed": 0}
    assert scanned == ["first.md"]


def test_gzip_cache_is_deterministic_and_portable(tmp_path):
    cache_path = tmp_path / "portable.json.gz"
    value = {"schema_version": pipeline.CACHE_SCHEMA_VERSION, "files": {}}

    pipeline.write_cache(cache_path, value)
    first = cache_path.read_bytes()
    pipeline.write_cache(cache_path, value)

    assert cache_path.read_bytes() == first
    assert pipeline.load_cache(cache_path) == value
