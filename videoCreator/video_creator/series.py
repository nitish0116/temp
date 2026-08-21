"""Series-level canonical character-reference reuse across manuscript workspaces."""

from __future__ import annotations

import shutil
import re
from pathlib import Path

from .artifacts import read_json, sha256_file, write_json_atomic


CATALOG_NAME = "characters.json"


def seed_analysis_with_shared_characters(analysis: dict, text: str, library: Path) -> dict:
    """Add known series characters that occur even once in a short manuscript."""
    catalog_path = library / CATALOG_NAME
    if not catalog_path.is_file():
        return analysis
    catalog = read_json(catalog_path)
    existing = {str(item.get("name") or "").casefold() for item in analysis.get("entities", [])}
    for shared in catalog.get("references", []):
        name = str(shared.get("canonical_name") or "").strip()
        if not name or name.casefold() in existing:
            continue
        matches = list(re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE))
        if not matches:
            continue
        analysis["entities"].append({
            "entity_id": f"series-character-{len(analysis['entities']) + 1:04d}",
            "name": name, "kind": "unknown", "mention_count": len(matches),
            "evidence": [
                {"source_start": match.start(), "source_end": match.end()}
                for match in matches[:5]
            ],
            "review_status": "needs_review",
            "series_canonical_id": shared["canonical_entity_id"],
        })
        existing.add(name.casefold())
    return analysis


def load_shared_references(library: Path, prompts: dict, root: Path) -> dict[str, dict]:
    """Copy hash-valid matching series references into a chapter workspace."""
    catalog_path = library / CATALOG_NAME
    if not catalog_path.is_file():
        return {}
    catalog = read_json(catalog_path)
    if catalog.get("schema_version") != 1:
        raise ValueError("unsupported shared character-reference catalog")
    by_id = {item.get("canonical_entity_id"): item for item in catalog.get("references", [])}
    by_name = {
        str(item.get("canonical_name") or "").casefold(): item
        for item in catalog.get("references", [])
    }
    current_style = next(
        (item.get("style") for item in prompts.get("prompts", []) if item.get("style")), None,
    )
    reused = {}
    for requirement in prompts.get("reference_requirements", []):
        item = by_id.get(requirement["canonical_entity_id"]) or by_name.get(
            requirement["canonical_name"].casefold()
        )
        if not item:
            continue
        if item.get("visual_style") and current_style and item["visual_style"] != current_style:
            continue
        source = library / str(item.get("path") or "")
        if not source.is_file() or sha256_file(source) != item.get("sha256"):
            raise ValueError(f"shared character reference is missing or modified: {requirement['canonical_name']}")
        relative = Path("images/reference-stage/character_reference") / requirement["reference_id"] / "shared.png"
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        reused[requirement["reference_id"]] = {
            "canonical_entity_id": requirement["canonical_entity_id"],
            "canonical_name": requirement["canonical_name"],
            "path": relative.as_posix(), "sha256": sha256_file(destination),
            "source_library_sha256": item["sha256"],
        }
    return reused


def publish_shared_references(library: Path, canonical: dict, prompts: dict, root: Path) -> dict:
    """Merge accepted chapter references into the durable series catalog."""
    library.mkdir(parents=True, exist_ok=True)
    catalog_path = library / CATALOG_NAME
    previous = read_json(catalog_path) if catalog_path.is_file() else {
        "schema_version": 1, "references": [],
    }
    requirements = {
        item["reference_id"]: item for item in prompts.get("reference_requirements", [])
    }
    current_style = next(
        (item.get("style") for item in prompts.get("prompts", []) if item.get("style")), None,
    )
    merged = {
        item["canonical_entity_id"]: item for item in previous.get("references", [])
        if item.get("canonical_entity_id")
    }
    for item in canonical.get("references", []):
        requirement = requirements[item["reference_id"]]
        canonical_id = requirement["canonical_entity_id"]
        relative = Path("characters") / f"{canonical_id}.png"
        destination = library / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / item["path"], destination)
        merged[canonical_id] = {
            "canonical_entity_id": canonical_id,
            "canonical_name": requirement["canonical_name"],
            "path": relative.as_posix(), "sha256": sha256_file(destination),
            "visual_style": current_style,
        }
    catalog = {
        "schema_version": 1, "status": "auto_accepted",
        "references": sorted(merged.values(), key=lambda value: value["canonical_entity_id"]),
    }
    write_json_atomic(catalog_path, catalog)
    return catalog
