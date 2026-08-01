import json

import audit_hyphens as audit


def test_canonical_files_prefer_full_volume(tmp_path):
    volume = tmp_path / "Volume 01"
    volume.mkdir()
    full = volume / "Volume 01.md"
    full.write_text("full", encoding="utf-8")
    (volume / "Volume 01-1 - Chapter.md").write_text("chapter", encoding="utf-8")

    assert audit.canonical_markdown_files(tmp_path) == [full]


def test_build_audit_excludes_genuine_and_stutters(tmp_path):
    volume = tmp_path / "Volume 01"
    volume.mkdir()
    source = volume / "Volume 01.md"
    source.write_text(
        "A half-sigh escaped. W-What was that? It should be-a warning. "
        "The abyss-a dangerous place-was dark.",
        encoding="utf-8",
    )

    result = audit.build_audit(tmp_path, {"half-sigh"})

    assert result["files_scanned"] == 1
    assert [item["token"] for item in result["candidates"]] == [
        "abyss-a",
        "be-a",
        "place-was",
    ]
    assert {item["reason"] for item in result["excluded"]} == {
        "reviewed genuine",
        "speech stutter",
    }
    assert result["candidates"][0]["contexts"][0]["line"] == 1


def test_all_files_scans_distinct_files_and_skips_identical_copies(tmp_path):
    first = tmp_path / "one.md"
    first.write_text("story-an example", encoding="utf-8")
    (tmp_path / "copy.md").write_text("story-an example", encoding="utf-8")
    (tmp_path / "other.md").write_text("staple-or something", encoding="utf-8")

    result = audit.build_audit(tmp_path, set(), all_files=True)

    assert result["markdown_files_discovered"] == 3
    assert result["files_scanned"] == 2
    assert result["identical_duplicates_skipped"] == 1
    assert {item["token"] for item in result["candidates"]} == {
        "story-an",
        "staple-or",
    }


def test_load_genuine_hyphens_supports_record_shape(tmp_path):
    path = tmp_path / "genuine.json"
    path.write_text(json.dumps({"genuine": ["mana-rich"]}), encoding="utf-8")

    result = audit.load_genuine_hyphens(path)

    assert "mana-rich" in result
    assert "half-sigh" in result


def test_classify_token_uses_explainable_conservative_rules():
    assert audit.classify_token("hush-hush")["decision"] == "acceptable"
    assert audit.classify_token("self-control")["decision"] == "acceptable"
    assert audit.classify_token("Bronze-class")["decision"] == "acceptable"

    rejected = audit.classify_token("story-an")
    assert rejected["decision"] == "not_acceptable"
    assert rejected["status"] == "replace"
    assert rejected["replacement"] == "story, an"
    assert rejected["confidence"] == 0.97

    assert audit.classify_token("mana-rich")["decision"] == "undecided"
    assert audit.classify_token("run-of-the-mill")["decision"] == "undecided"


def test_auto_classify_adds_totals_and_decision_metadata():
    record = {
        "candidates": [
            {"token": "story-an", "status": "review", "replacement": None},
            {"token": "self-aware", "status": "review", "replacement": None},
            {"token": "mana-rich", "status": "review", "replacement": None},
        ]
    }

    totals = audit.auto_classify(record)

    assert totals == {"acceptable": 1, "not_acceptable": 1, "undecided": 1}
    assert record["candidates"][0]["decision_reason"]

    record.update(
        schema_version=1,
        generated_at="now",
        source_root="library",
        review_candidates=3,
    )
    ambiguous = audit.move_ambiguous_entries(record)
    assert [item["token"] for item in record["candidates"]] == [
        "story-an",
        "self-aware",
    ]
    assert [item["token"] for item in ambiguous["candidates"]] == ["mana-rich"]
    assert record["ambiguous_candidates"] == 1
