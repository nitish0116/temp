"""Image-provider contract, deterministic fixtures, ranking, and asset validation."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path
from typing import Protocol

from .artifacts import sha256_file


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, output: Path, *, seed: int) -> None:
        """Generate one image candidate at output."""


class DeterministicFixtureImageProvider:
    """Create tiny valid PNG fixtures without network or model dependencies."""

    name = "deterministic-fixture-image-v1"

    def generate(self, prompt: str, output: Path, *, seed: int) -> None:
        digest = hashlib.sha256(f"{seed}:{prompt}".encode("utf-8")).digest()
        width, height = 64, 36
        rows = []
        for y in range(height):
            row = bytearray([0])
            for x in range(width):
                row.extend((digest[0] ^ x, digest[1] ^ y, digest[2] ^ (x + y)))
            rows.append(bytes(row))
        raw = b"".join(rows)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(
                ">I", zlib.crc32(kind + data) & 0xFFFFFFFF,
            )

        png = b"\x89PNG\r\n\x1a\n" + chunk(
            b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        ) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(png)


class SanaImageProvider:
    """Offline Sana adapter loading only from a pre-populated local model cache."""

    def __init__(
        self, model_id: str = "Efficient-Large-Model/Sana_1600M_1024px_diffusers", *,
        model_revision: str = "ac0da2ff55fbe434795be0dce883042e4d49e2fc",
        cache_directory: Path | None = None, inference_steps: int = 20,
        guidance_scale: float = 4.5, device: str = "cuda",
    ) -> None:
        self.model_id = model_id
        self.model_revision = model_revision
        self.cache_directory = cache_directory
        self.inference_steps = inference_steps
        self.guidance_scale = guidance_scale
        self.device = device
        self._pipeline = None

    @property
    def name(self) -> str:
        return (
            f"sana-local:{self.model_id}@{self.model_revision[:12]}:steps={self.inference_steps}:"
            f"guidance={self.guidance_scale}"
        )

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        if self.cache_directory is not None and not self.cache_directory.is_dir():
            raise RuntimeError(
                f"local Sana model cache is missing or incomplete: {self.model_id}; "
                "run setup-local-images online first"
            )
        try:
            import torch
            from diffusers import SanaPipeline
        except ImportError as error:
            raise RuntimeError(
                "Sana dependencies are missing; run the command from imageEnv"
            ) from error
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for the configured local Sana provider")
        try:
            pipeline = SanaPipeline.from_pretrained(
                self.model_id, revision=self.model_revision, dtype=torch.float16,
                variant="fp16", local_files_only=True,
                cache_dir=str(self.cache_directory) if self.cache_directory else None,
            )
        except OSError as error:
            raise RuntimeError(
                f"local Sana model cache is missing or incomplete: {self.model_id}; "
                "run setup-local-images online first"
            ) from error
        pipeline.to(self.device)
        self._pipeline = pipeline
        return pipeline

    def generate(self, prompt: str, output: Path, *, seed: int) -> None:
        """Generate a real image without any network access."""
        pipeline = self._load()
        import torch
        generator = torch.Generator(device=self.device).manual_seed(seed)
        result = pipeline(
            prompt=prompt, negative_prompt="", width=1024, height=1024,
            num_inference_steps=self.inference_steps,
            guidance_scale=self.guidance_scale, generator=generator,
        )
        if not result.images:
            raise RuntimeError("local Sana provider returned no image")
        output.parent.mkdir(parents=True, exist_ok=True)
        result.images[0].save(output, format="PNG")


def _score(seed: int, prompt: str) -> dict:
    digest = hashlib.sha256(f"score:{seed}:{prompt}".encode("utf-8")).digest()
    values = [round(0.75 + byte / 1020, 4) for byte in digest[:3]]
    return {
        "technical": values[0], "prompt_fit": values[1], "continuity": values[2],
        "total": round(sum(values) / len(values), 4),
    }


def generate_assets(
    prompts: dict, root: Path, provider: ImageProvider | None = None,
    *, candidates_per_item: int = 2, maximum_attempts: int = 2,
    fallback_provider: ImageProvider | None = None, previous: dict | None = None,
    asset_kinds: frozenset[str] | None = None, asset_namespace: str | None = None,
) -> dict:
    """Generate, rank, and select image and character-reference candidates."""
    if prompts.get("status") != "auto_accepted":
        raise ValueError("image generation requires accepted prompts")
    if not 1 <= candidates_per_item <= 4:
        raise ValueError("candidates_per_item must be between one and four")
    if maximum_attempts < 1:
        raise ValueError("maximum_attempts must be positive")
    selected = provider or DeterministicFixtureImageProvider()
    fallback = fallback_provider or (
        DeterministicFixtureImageProvider() if provider is None else selected
    )
    previous_matches_namespace = (previous or {}).get("asset_namespace") == asset_namespace
    prior = {
        item.get("asset_id"): item for item in (previous or {}).get("assets", [])
    } if previous_matches_namespace else {}
    items = []
    reused = []
    regenerated = []
    all_work = [
        (item["shot_id"], "shot", item["prompt"], item["dependency_sha256"])
        for item in prompts["prompts"]
    ] + [
        (
            item["reference_id"], "character_reference",
            f"Canonical character reference portrait: {item['canonical_name']}",
            hashlib.sha256(json_key(item).encode("utf-8")).hexdigest(),
        ) for item in prompts["reference_requirements"]
    ]
    work = [item for item in all_work if asset_kinds is None or item[1] in asset_kinds]
    for identifier, kind, prompt, dependency in work:
        existing = prior.get(identifier)
        if (
            existing
            and existing.get("dependency_sha256") == dependency
            and existing.get("provider") == selected.name
            and len(existing.get("candidates", [])) == candidates_per_item
            and all(
                (root / candidate.get("path", "")).is_file()
                and sha256_file(root / candidate["path"]) == candidate.get("sha256")
                and bool(candidate.get("generation_attempts"))
                for candidate in existing["candidates"]
            )
        ):
            items.append(existing)
            reused.append(identifier)
            continue
        regenerated.append(identifier)
        candidates = []
        for index in range(1, candidates_per_item + 1):
            seed = int(hashlib.sha256(f"{identifier}:{index}".encode()).hexdigest()[:8], 16)
            relative = Path("images")
            if asset_namespace:
                relative /= asset_namespace
            relative = relative / kind / identifier / f"candidate-{index:02d}.png"
            output = root / relative
            attempts = []
            generated_by = selected
            generated_successfully = False
            for attempt in range(1, maximum_attempts + 2):
                generated_by = selected if attempt <= maximum_attempts else fallback
                try:
                    generated_by.generate(prompt, output, seed=seed)
                    attempts.append({
                        "attempt": attempt, "provider": generated_by.name,
                        "status": "generated", "error": None,
                    })
                    generated_successfully = True
                    break
                except Exception as exc:  # provider errors are recoverable stage data
                    attempts.append({
                        "attempt": attempt, "provider": generated_by.name,
                        "status": "failed", "error": str(exc),
                    })
            if not generated_successfully or not output.is_file():
                raise ValueError(f"all image providers failed for {identifier} candidate {index}")
            candidates.append({
                "candidate_id": f"{identifier}-candidate-{index:02d}",
                "path": relative.as_posix(), "seed": seed,
                "sha256": sha256_file(output), "score": _score(seed, prompt),
                "provider": generated_by.name, "generation_attempts": attempts,
            })
        winner = max(candidates, key=lambda value: (value["score"]["total"], value["candidate_id"]))
        items.append({
            "asset_id": identifier, "kind": kind, "dependency_sha256": dependency,
            "provider": selected.name, "candidates": candidates,
            "selected_candidate_id": winner["candidate_id"],
            "selection": "automatic_rank", "status": "auto_accepted",
        })
    return {
        "schema_version": 1, "asset_set_id": "asset-set-0001",
        "prompt_set_id": prompts["prompt_set_id"], "source_sha256": prompts["source_sha256"],
        "provider": selected.name, "status": "auto_accepted", "release_usable": False,
        "asset_kinds": sorted(asset_kinds or {"shot", "character_reference"}),
        "asset_namespace": asset_namespace,
        "assets": items,
        "regeneration": {
            "reused_asset_ids": reused, "regenerated_asset_ids": regenerated,
        },
    }


def json_key(item: dict) -> str:
    """Return a stable character-reference dependency key."""
    return "|".join(str(item.get(key) or "") for key in (
        "reference_id", "canonical_entity_id", "canonical_name", "default_action",
    ))


def validate_assets(assets: dict, prompts: dict, root: Path) -> list[str]:
    """Validate coverage, selections, paths, and immutable file hashes."""
    issues = []
    kinds = set(assets.get("asset_kinds", {"shot", "character_reference"}))
    expected = (
        [item["shot_id"] for item in prompts.get("prompts", [])] if "shot" in kinds else []
    ) + (
        [item["reference_id"] for item in prompts.get("reference_requirements", [])]
        if "character_reference" in kinds else []
    )
    actual = [item.get("asset_id") for item in assets.get("assets", [])]
    if actual != expected:
        issues.append("assets must cover every prompt and reference exactly once")
    for item in assets.get("assets", []):
        identifier = item.get("asset_id")
        candidates = item.get("candidates", [])
        candidate_ids = {candidate.get("candidate_id") for candidate in candidates}
        if item.get("selected_candidate_id") not in candidate_ids:
            issues.append(f"invalid selected candidate for {identifier}")
        if item.get("status") != "auto_accepted" or not candidates:
            issues.append(f"unaccepted or empty asset: {identifier}")
        for candidate in candidates:
            path = root / str(candidate.get("path") or "")
            if not path.is_file():
                issues.append(f"missing candidate file: {candidate.get('candidate_id')}")
            elif sha256_file(path) != candidate.get("sha256"):
                issues.append(f"candidate hash mismatch: {candidate.get('candidate_id')}")
            if not 0 <= float(candidate.get("score", {}).get("total", -1)) <= 1:
                issues.append(f"invalid candidate score: {candidate.get('candidate_id')}")
            attempts = candidate.get("generation_attempts", [])
            if not attempts or attempts[-1].get("status") != "generated":
                issues.append(f"missing successful generation attempt: {candidate.get('candidate_id')}")
    return issues
