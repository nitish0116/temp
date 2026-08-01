import classify_ambiguous_hyphens as classifier


def candidate(token):
    return {
        "token": token,
        "status": "review",
        "replacement": None,
        "contexts": [{"text": f"It was a common {token} adventurer left."}],
    }


def test_cross_library_punctuation_resolves_only_supported_pairs(tmp_path):
    (tmp_path / "source.md").write_text(
        "It was a common story—an adventurer left. Mana-rich soil.",
        encoding="utf-8",
    )
    index = classifier.build_punctuation_index(tmp_path)

    resolved = classifier.classify_with_evidence(candidate("story-an"), index)

    assert resolved["status"] == "replace"
    assert resolved["replacement"] == "story, an"
    assert resolved["confidence"] == 0.99
    assert classifier.classify_with_evidence(candidate("mana-rich"), index) is None


def test_classify_and_merge_preserve_unresolved_entries():
    nothing = candidate("nothing-I")
    signature = classifier.context_signature(
        nothing["contexts"][0]["text"], 16, 25, "nothing", "I"
    )
    index = {signature: classifier.Counter({"—": 2})}
    ambiguous = {"candidates": [nothing, candidate("mana-rich")]}
    resolved, unresolved = classifier.classify_record(ambiguous, index)
    main = {"candidates": [], "ambiguous_candidates": 2}

    classifier.merge_decisions(main, resolved)

    assert [item["token"] for item in resolved] == ["nothing-I"]
    assert [item["token"] for item in unresolved] == ["mana-rich"]
    assert main["ambiguous_candidates"] == 1
