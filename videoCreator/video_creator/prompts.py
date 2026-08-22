"""Deterministic image prompts and optional character-reference requirements."""

from __future__ import annotations

import json
import re

from .artifacts import sha256_text


COMPILER = "deterministic-prompt-compiler-v4-hybrid-context"
REFERENCE_BRIEF_COMPILER = "source-character-profile-v2"
DEFAULT_VISUAL_STYLE = "anime-style illustration, polished cinematic anime key art"
NEGATIVE_PROMPT = (
    "text, watermark, logo, duplicate character, extra limbs, identity drift, "
    "anachronistic objects, inconsistent costume, photorealism, live action, 3D render"
)


def _fingerprint(shot: dict, prompt: str, reference_ids: list[str], style: str) -> str:
    return sha256_text(json.dumps({
        "compiler": COMPILER,
        "shot_dependency_sha256": shot["dependency_sha256"],
        "prompt": prompt,
        "negative_prompt": NEGATIVE_PROMPT,
        "reference_ids": reference_ids,
        "style": style,
    }, sort_keys=True, ensure_ascii=False))


def _visualize_narrative_beat(beat: str) -> str:
    """Rewrite contrastive prose so excluded concepts do not become image subjects."""
    prefix, _separator, sentence = beat.partition(": ")
    match = re.match(r"Instead of (.+?), there (?:is|are) (.+)", sentence, re.IGNORECASE)
    if not match:
        return beat
    _excluded, depicted = match.groups()
    return f"{prefix}: Show {depicted}"


def _compact_words(value: str, maximum: int) -> str:
    """Keep SDXL scene prompts inside CLIP's useful token window."""
    words = value.replace(";", ",").split()
    return " ".join(words[:maximum]).rstrip(".,:;")


def _compact_constraint(value: str) -> str:
    lowered = value.casefold()
    if "infant or toddler" in lowered:
        return "infant toddler"
    if "child smock" in lowered and "bareheaded" in lowered:
        return "1914 child smock, bareheaded, hair visible"
    return _compact_words(value, 8)


def _compact_event(value: str) -> str:
    lowered = value.casefold()
    visualizations = (
        (
            "newborn body struggles for its first breath",
            "newborn Tanya lies in a crib, gasping and crying, nurse reaching toward her",
        ),
        (
            "awareness comes only in fragments",
            "infant Tanya cries in a crib, blurred gaslit nursery and nuns around her",
        ),
        (
            "this body is an infant's, vulnerable and easily exhausted",
            "exhausted infant Tanya lies under a crib blanket, frightened adult awareness in her eyes",
        ),
    )
    for phrase, visible_event in visualizations:
        if phrase in lowered:
            return visible_event
    cleaned = re.sub(r"(?:\.\.\.\s*)?flip(?:,?\s*flip)*\s*(?:\.\.\.)?", " ", value, flags=re.I)
    words = cleaned.replace(";", ",").split()
    return " ".join(words[:90]).rstrip(".,:;")


def _estimated_tokens(value: str) -> int:
    """Conservatively catch prompt overflow before model-specific tokenization."""
    return len(re.findall(r"\w+|[^\w\s]", value, re.UNICODE))


def compile_prompts(
    storyboard: dict, analysis: dict, *, style: str = DEFAULT_VISUAL_STYLE,
    previous: dict | None = None, source_text: str | None = None,
) -> dict:
    """Compile every accepted shot without requiring a visual decision prompt."""
    if storyboard.get("status") != "auto_accepted":
        raise ValueError("prompt compilation requires an accepted storyboard")
    if not style.strip():
        raise ValueError("style must be nonempty")
    entities = {
        item["canonical_id"]: item for item in analysis.get("entities", [])
        if item.get("review_status") == "approved"
    }
    character_ids = {
        identifier for identifier, item in entities.items() if item.get("kind") == "character"
    }
    locations = [item for item in entities.values() if item.get("kind") == "location"]
    requirements = []
    for identifier in sorted(character_ids):
        entity = entities[identifier]
        name = entity.get("canonical_name") or entity["name"]
        aliases = [name, *entity.get("aliases", [])]
        evidence = _character_evidence(source_text, aliases) if source_text else []
        brief, visual_constraints, profile = _visual_reference_prompt(
            name, source_text, aliases, style,
        )
        requirements.append({
            "reference_id": f"character-{identifier}",
            "canonical_entity_id": identifier, "canonical_name": name,
            "aliases": sorted(set(aliases), key=str.casefold),
            "source_evidence": evidence, "reference_prompt": brief,
            "character_profile": profile,
            "text_encoder": {"family": "clip", "maximum_tokens": 77},
            "brief_compiler": REFERENCE_BRIEF_COMPILER,
            "visual_constraints": visual_constraints,
            "selection_mode": "optional_user_override",
            "default_action": "generate_and_auto_rank", "status": "default_ready",
        })
    prior = {item.get("shot_id"): item for item in (previous or {}).get("prompts", [])}
    requirement_map = {
        item["canonical_entity_id"]: item for item in requirements
    }
    prompts = []
    reused = []
    regenerated = []
    for shot in storyboard["shots"]:
        names = [
            entities[identifier].get("canonical_name") or entities[identifier]["name"]
            for identifier in shot["canonical_entity_ids"] if identifier in entities
        ]
        references = [
            f"character-{identifier}" for identifier in shot["canonical_entity_ids"]
            if identifier in character_ids
        ][:1]
        subjects = ", ".join(names) if names else "environmental storytelling"
        action = _visualize_narrative_beat(shot["narrative_beat"])
        location_names = [
            item.get("canonical_name") or item["name"] for item in locations
            if any(re.search(rf"\b{re.escape(term)}\b", action, re.I) for term in {
                str(item.get("name") or ""), str(item.get("canonical_name") or ""),
                *(str(value) for value in item.get("aliases", [])),
            } - {""})
        ]
        setting = ", ".join(location_names) or shot["setting_id"].replace("-", " ")
        compact_action = _compact_event(action.partition(": ")[2] or action)
        compact_constraints = [
            _compact_constraint(value)
            for identifier in shot["canonical_entity_ids"]
            if identifier in requirement_map
            for value in requirement_map[identifier]["visual_constraints"]
        ]
        prompt = (
            f"Anime cinematic story scene. Characters: {subjects}. "
            + (f"Appearance: {', '.join(compact_constraints)}. " if compact_constraints else "")
            + f"Event: {compact_action}. Setting: {setting}. {shot['mood']} mood. No posed portrait."
        )
        fingerprint = _fingerprint(shot, prompt, references, style)
        existing = prior.get(shot["shot_id"])
        if existing and existing.get("dependency_sha256") == fingerprint:
            prompts.append(existing)
            reused.append(shot["shot_id"])
            continue
        regenerated.append(shot["shot_id"])
        prompts.append({
            "shot_id": shot["shot_id"],
            "prompt": prompt,
            "negative_prompt": NEGATIVE_PROMPT,
            "reference_ids": references,
            "scene_contract": {
                "setting": setting, "visible_event": action,
                "characters": names, "mood": shot["mood"],
            },
            "style": style,
            "text_encoder": {"family": "sana-gemma", "maximum_tokens": 300},
            "dependency_sha256": fingerprint,
            "status": "auto_accepted",
        })
    return {
        "schema_version": 1,
        "prompt_set_id": "prompt-set-0001",
        "storyboard_id": storyboard["storyboard_id"],
        "source_sha256": storyboard["source_sha256"],
        "compiler": COMPILER,
        "status": "auto_accepted",
        "release_usable": False,
        "reference_requirements": requirements,
        "prompts": prompts,
        "regeneration": {
            "reused_shot_ids": reused,
            "regenerated_shot_ids": regenerated,
        },
    }


def _character_evidence(source_text: str, aliases: list[str]) -> list[str]:
    """Collect bounded source passages around canonical names and aliases."""
    matches = []
    for alias in sorted(set(aliases), key=lambda value: (-len(value), value.casefold())):
        if len(alias.strip()) < 3:
            continue
        for match in re.finditer(rf"\b{re.escape(alias)}\b", source_text, re.IGNORECASE):
            start = max(0, match.start() - 500)
            end = min(len(source_text), match.end() + 500)
            excerpt = " ".join(source_text[start:end].split())
            matches.append((match.start(), excerpt))
    unique = []
    for _offset, excerpt in sorted(matches):
        if excerpt not in unique:
            unique.append(excerpt)
        if len(unique) == 3:
            break
    return unique


def _visual_reference_prompt(
    name: str, source_text: str | None, aliases: list[str], style: str,
) -> tuple[str, list[str], dict]:
    """Build one source-grounded human portrait contract."""
    searchable_aliases = list(aliases)
    source = source_text or ""
    canonical = re.escape(name)
    inferred_father = bool(re.search(
        rf"\b(?:my\s+)?father\s*[,—-]?\s*{canonical}\b", source, re.I,
    ))
    inferred_mother = bool(re.search(
        rf"\b(?:my\s+)?mother\s*[,—-]?\s*{canonical}\b", source, re.I,
    ))
    if inferred_father:
        searchable_aliases.extend(["father", "my father"])
    if inferred_mother:
        searchable_aliases.extend(["mother", "my mother"])
    evidence = _character_evidence(source, searchable_aliases) if source_text else []
    context = " ".join(evidence)
    folded = context.casefold()
    alias_words = {value.strip().casefold() for value in searchable_aliases}
    subject_offsets = [
        match.start() for alias in searchable_aliases if len(alias.strip()) >= 3
        for match in re.finditer(rf"\b{re.escape(alias)}\b", source, re.I)
    ]

    def close_to_subject(start: int, maximum: int = 320) -> bool:
        return bool(subject_offsets) and min(abs(start - offset) for offset in subject_offsets) <= maximum

    def trait_belongs(match: re.Match) -> bool:
        sentence_start = max(source.rfind(".", 0, match.start()), source.rfind("\n", 0, match.start())) + 1
        stops = [value for value in (source.find(".", match.end()), source.find("\n", match.end())) if value >= 0]
        sentence_end = min(stops) if stops else len(source)
        sentence = source[sentence_start:sentence_end]
        first_person_owner = bool(re.search(
            r"\b(?:I had|my (?:hair|eyes|face|features))\b", sentence, re.I,
        ))
        if first_person_owner:
            return presentation in {"boy", "girl"}
        if presentation == "woman":
            pronoun_match = bool(re.search(r"\b(?:she|her|hers)\b", sentence, re.I))
            if pronoun_match:
                return True
        if presentation == "man":
            pronoun_match = bool(re.search(r"\b(?:he|him|his)\b", sentence, re.I))
            if pronoun_match:
                return True
        if presentation in {"boy", "girl"}:
            return False
        return any(
            abs(match.start() - alias_match.start()) <= 100
            for alias in aliases
            for alias_match in re.finditer(rf"\b{re.escape(alias)}\b", source, re.I)
        )

    age = "adult"
    juvenile_nearby = any(
        close_to_subject(match.start(), 260) for match in re.finditer(
            r"\b(?:newborn|infant|baby|barely crawl(?:ing)?)\b", source, re.I,
        )
    )
    if not ({"mother", "my mother", "father", "my father"} & alias_words) and juvenile_nearby:
        age = "infant or crawling baby"
    presentation = "person"
    if inferred_mother or "mother" in {value.strip().casefold() for value in aliases}:
        presentation = "woman"
    elif inferred_father or "father" in {value.strip().casefold() for value in aliases}:
        presentation = "man"
    else:
        direct_contexts = []
        for alias in aliases:
            for match in re.finditer(rf"\b{re.escape(alias)}\b", source_text or "", re.I):
                direct_contexts.append((source_text or "")[match.start():match.end() + 180].casefold())
        direct = " ".join(direct_contexts[:5])
        if re.search(r"\b(?:she|her|hers)\b", direct):
            presentation = "woman"
        elif re.search(r"\b(?:he|him|his)\b", direct):
            presentation = "man"
    if age != "adult" and presentation == "man":
        presentation = "boy"
    elif age != "adult" and presentation == "woman":
        presentation = "girl"
    traits = []
    patterns = (
        r"(?:ashy\s+|dark\s+|light\s+|striking\s+)?"
        r"(?:auburn|brown|black|blond|blonde|white|silver|grey|gray|red)\s+hair",
        r"(?:bright\s+|deep\s+|almost\s+)?(?:azure|blue|brown|green|grey|gray|violet|red)"
        r"(?:,\s*almost\s+[a-z]+)?(?:\s+color)?\s+(?:eyes|hue of his irises)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, source, re.I):
            if not close_to_subject(match.start()) or not trait_belongs(match):
                continue
            value = " ".join(match.group().split()).strip(" ,.")
            if value and value.casefold() not in {item.casefold() for item in traits}:
                traits.append(value)
    for match in re.finditer(
        r"eyes\s+(?:were|are)\s+(?:a\s+)?((?:bright\s+|deep\s+)?"
        r"(?:azure|blue|brown|green|grey|gray|violet|red))(?:\s+color)?", source, re.I,
    ):
        if close_to_subject(match.start()) and trait_belongs(match):
            value = f"{match.group(1).strip()} eyes"
            if value.casefold() not in {item.casefold() for item in traits}:
                traits.append(value)
    for match in re.finditer(r"hair,\s*([a-z -]{1,24})\s+in color", source, re.I):
        if not close_to_subject(match.start()) or not trait_belongs(match):
            continue
        value = f"{match.group(1).strip()} hair"
        if value.casefold() not in {item.casefold() for item in traits}:
            traits.append(value)
    for phrase in ("square jawline", "long eye lashes", "perky nose"):
        for match in re.finditer(re.escape(phrase), source, re.I):
            if close_to_subject(match.start()) and trait_belongs(match):
                traits.append(phrase)
                break
    profile = {
        "name": name, "species": "human", "age_stage": age,
        "presentation": presentation, "visible_traits": traits[:8],
        "source_evidence": evidence,
    }
    constraints = [f"human {age} {presentation}", *profile["visible_traits"]]
    if "nun" in folded and "infant" in folded:
        constraints.append(
            "plain early-twentieth-century child smock, bareheaded with hair visible"
        )
    base = (
        f"{style}. Full-body portrait of {name}. Exactly one human character: "
        f"{'; '.join(constraints)}. "
        "Single front view, natural standing pose, plain background, clean linework, cel shading."
    )
    return base, constraints, profile


def validate_prompts(compiled: dict, storyboard: dict, analysis: dict) -> list[str]:
    """Validate prompt coverage and constrain references to canonical characters."""
    issues = []
    if compiled.get("status") != "auto_accepted" or compiled.get("release_usable"):
        issues.append("prompts must be automatically accepted non-release artifacts")
    expected = [shot["shot_id"] for shot in storyboard.get("shots", [])]
    actual = [item.get("shot_id") for item in compiled.get("prompts", [])]
    if actual != expected:
        issues.append("prompts must cover every shot exactly once and in order")
    characters = {
        item["canonical_id"] for item in analysis.get("entities", [])
        if item.get("review_status") == "approved" and item.get("kind") == "character"
    }
    valid_references = {f"character-{identifier}" for identifier in characters}
    requirement_ids = {
        item.get("reference_id") for item in compiled.get("reference_requirements", [])
    }
    if requirement_ids != valid_references:
        issues.append("reference requirements must cover canonical characters exactly")
    for item in compiled.get("reference_requirements", []):
        if not str(item.get("reference_prompt") or "").strip():
            issues.append(f"empty reference prompt for {item.get('reference_id')}")
        if _estimated_tokens(str(item.get("reference_prompt") or "")) > 70:
            issues.append(f"reference prompt may exceed CLIP limit: {item.get('reference_id')}")
    for item in compiled.get("prompts", []):
        identifier = item.get("shot_id")
        if not str(item.get("prompt") or "").strip() or not str(item.get("negative_prompt") or "").strip():
            issues.append(f"empty image prompt for {identifier}")
        if _estimated_tokens(str(item.get("prompt") or "")) > 260:
            issues.append(f"scene prompt may exceed Sana limit: {identifier}")
        unknown = set(item.get("reference_ids", [])) - valid_references
        if unknown:
            issues.append(f"unknown character references for {identifier}: {sorted(unknown)}")
        fingerprint = str(item.get("dependency_sha256") or "")
        if len(fingerprint) != 64 or any(value not in "0123456789abcdef" for value in fingerprint):
            issues.append(f"invalid prompt dependency fingerprint for {identifier}")
        if item.get("status") != "auto_accepted":
            issues.append(f"unaccepted prompt: {identifier}")
    return issues
