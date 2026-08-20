"""Image-provider contract, deterministic fixtures, ranking, and asset validation."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path
from typing import Protocol

from .artifacts import sha256_file


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, output: Path, *, seed: int) -> None:
        """Generate one image candidate at output."""

    def generate_conditioned(
        self, prompt: str, output: Path, *, seed: int, reference_images: list[Path],
    ) -> None:
        """Generate using canonical images as model inputs."""


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

    def generate_conditioned(
        self, prompt: str, output: Path, *, seed: int, reference_images: list[Path],
    ) -> None:
        if not reference_images or not all(path.is_file() for path in reference_images):
            raise ValueError("conditioned generation requires existing reference images")
        reference_key = ":".join(sha256_file(path) for path in reference_images)
        self.generate(f"{prompt}:references={reference_key}", output, seed=seed)


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


class SanaControlNetImageProvider:
    """Offline Sana ControlNet adapter using canonical images as edge conditions."""

    def __init__(
        self, model_id: str = "ishan24/Sana_600M_1024px_ControlNetPlus_diffusers", *,
        model_revision: str = "c2c790efb0285f3d42dc6d7e73e58c80577cf447",
        cache_directory: Path | None = None, inference_steps: int = 20,
        guidance_scale: float = 4.5, conditioning_scale: float = 0.65,
        device: str = "cuda",
    ) -> None:
        self.model_id = model_id
        self.model_revision = model_revision
        self.cache_directory = cache_directory
        self.inference_steps = inference_steps
        self.guidance_scale = guidance_scale
        self.conditioning_scale = conditioning_scale
        self.device = device
        self._pipeline = None

    @property
    def name(self) -> str:
        return (
            f"sana-controlnet-local:{self.model_id}@{self.model_revision[:12]}:"
            f"steps={self.inference_steps}:guidance={self.guidance_scale}:"
            f"conditioning={self.conditioning_scale}"
        )

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        if self.cache_directory is not None and not self.cache_directory.is_dir():
            raise RuntimeError("local Sana ControlNet cache is missing; run setup-local-images")
        try:
            import torch
            from diffusers import SanaControlNetPipeline
        except ImportError as error:
            raise RuntimeError("Sana ControlNet dependencies are missing from imageEnv") from error
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for Sana ControlNet")
        try:
            pipeline = SanaControlNetPipeline.from_pretrained(
                self.model_id, revision=self.model_revision, variant="fp16",
                torch_dtype={"default": torch.float16}, local_files_only=True,
                cache_dir=str(self.cache_directory) if self.cache_directory else None,
            )
        except OSError as error:
            raise RuntimeError("local Sana ControlNet cache is missing; run setup-local-images") from error
        pipeline.to(self.device)
        self._pipeline = pipeline
        return pipeline

    @staticmethod
    def _control_image(reference_images: list[Path]):
        from PIL import Image, ImageFilter, ImageOps
        if not reference_images:
            raise ValueError("Sana ControlNet requires at least one canonical reference")
        images = [Image.open(path).convert("RGB").resize((1024, 1024)) for path in reference_images]
        if len(images) == 1:
            source = images[0]
        else:
            tile_width = 1024 // len(images)
            source = Image.new("RGB", (1024, 1024), "white")
            for index, image in enumerate(images):
                source.paste(ImageOps.fit(image, (tile_width, 1024)), (index * tile_width, 0))
        return ImageOps.autocontrast(source.convert("L").filter(ImageFilter.FIND_EDGES)).convert("RGB")

    def generate_conditioned(
        self, prompt: str, output: Path, *, seed: int, reference_images: list[Path],
    ) -> None:
        self._generate_with_control(
            prompt, output, seed=seed, control_image=self._control_image(reference_images),
        )

    def _generate_with_control(self, prompt: str, output: Path, *, seed: int, control_image) -> None:
        pipeline = self._load()
        import torch
        result = pipeline(
            prompt=prompt, negative_prompt="photorealism, live action, 3D render",
            control_image=control_image, width=1024, height=1024,
            num_inference_steps=self.inference_steps, guidance_scale=self.guidance_scale,
            controlnet_conditioning_scale=self.conditioning_scale,
            generator=torch.Generator(device=self.device).manual_seed(seed),
        )
        if not result.images:
            raise RuntimeError("local Sana ControlNet returned no image")
        output.parent.mkdir(parents=True, exist_ok=True)
        result.images[0].save(output, format="PNG")

    def generate(self, prompt: str, output: Path, *, seed: int) -> None:
        from PIL import Image
        self._generate_with_control(
            prompt, output, seed=seed, control_image=Image.new("RGB", (1024, 1024), "black"),
        )


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
    canonical_references: dict[str, dict[str, str] | str] | None = None,
    asset_ids: frozenset[str] | None = None,
    reference_conditioning: bool = False,
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
    reference_records = canonical_references or {}
    reference_hashes = {
        identifier: value if isinstance(value, str) else value["sha256"]
        for identifier, value in reference_records.items()
    }
    all_work = [
        (
            item["shot_id"], "shot", item["prompt"],
            hashlib.sha256(json.dumps({
                "prompt_dependency_sha256": item["dependency_sha256"],
                "reference_sha256": {
                    identifier: reference_hashes.get(identifier)
                    for identifier in item.get("reference_ids", [])
                },
            }, sort_keys=True).encode()).hexdigest(),
        )
        for item in prompts["prompts"]
    ] + [
        (
            item["reference_id"], "character_reference",
            item.get("reference_prompt")
            or f"Canonical character reference portrait: {item['canonical_name']}",
            hashlib.sha256(json_key(item).encode("utf-8")).hexdigest(),
        ) for item in prompts["reference_requirements"]
    ]
    work = [
        item for item in all_work
        if (asset_kinds is None or item[1] in asset_kinds)
        and (asset_ids is None or item[0] in asset_ids)
    ]
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
                    referenced = [
                        root / reference_records[reference_id]["path"]
                        for reference_id in next(
                            (candidate.get("reference_ids", []) for candidate in prompts["prompts"]
                             if candidate["shot_id"] == identifier), []
                        )
                        if isinstance(reference_records.get(reference_id), dict)
                    ]
                    if referenced:
                        generate_conditioned = getattr(generated_by, "generate_conditioned", None)
                        if generate_conditioned is None:
                            raise ValueError("selected provider does not support reference conditioning")
                        generate_conditioned(prompt, output, seed=seed, reference_images=referenced)
                    else:
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
        "canonical_reference_sha256": reference_hashes,
        "asset_ids": sorted(asset_ids) if asset_ids is not None else None,
        "reference_conditioning": bool(reference_records) and reference_conditioning,
        "assets": items,
        "regeneration": {
            "reused_asset_ids": reused, "regenerated_asset_ids": regenerated,
        },
    }


def json_key(item: dict) -> str:
    """Return a stable character-reference dependency key."""
    return "|".join(str(item.get(key) or "") for key in (
        "reference_id", "canonical_entity_id", "canonical_name", "default_action",
        "reference_prompt",
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
    configured_ids = assets.get("asset_ids")
    if configured_ids is not None:
        expected = [identifier for identifier in expected if identifier in set(configured_ids)]
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
