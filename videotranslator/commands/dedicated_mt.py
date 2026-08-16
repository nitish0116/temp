"""Dedicated multilingual text-translation backends for release qualification."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Callable

import torch

from .canonical_timed_text import validate_canonical_timed_text

from .auto_prepare_script import nllb_code
from .runtime_device import resolve_device


MADLAD_MODEL = "google/madlad400-3b-mt"
NLLB_MODEL = "facebook/nllb-200-3.3B"


def dedicated_cache_key(model: str, source_language: str, target_language: str, text: str) -> str:
    """Hash every input that can change a dedicated translation candidate."""
    payload = [model, source_language, target_language, text]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def collect_dedicated_mt_evidence(
    document: dict, translate: Callable[[str, str, str], str], *, model: str,
    cache_directory: Path | None = None,
) -> tuple[dict, dict[str, str], dict]:
    """Attach cached dedicated-MT evidence without replacing canonical text."""
    validate_canonical_timed_text(document)
    output = deepcopy(document)
    candidates: dict[str, str] = {}
    checks = []
    for segment in output["segments"]:
        group_id = str(segment["semantic_group_id"])
        key = dedicated_cache_key(model, output["source_language"], output["output_language"], segment["source_text"])
        path = None if cache_directory is None else cache_directory / f"{key}.json"
        cache_hit = bool(path and path.is_file())
        try:
            if cache_hit:
                cached = json.loads(path.read_text(encoding="utf-8"))
                text = str(cached.get("translated_text") or cached.get("text") or "").strip()
            else:
                text = str(translate(segment["source_text"], output["source_language"], output["output_language"])).strip()
            if not text:
                raise ValueError("dedicated MT returned empty text")
            if path and not cache_hit:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"translated_text": text}, ensure_ascii=False) + "\n", encoding="utf-8")
            candidates[group_id] = text
            status, error = "ok", None
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            text, status, error = None, "failed", f"{type(exc).__name__}: {exc}"
        segment["metadata"] = {**segment.get("metadata", {}), "dedicated_mt": {"status": status, "text": text, "model": model}}
        checks.append({"semantic_group_id": group_id, "status": status, "translated_text": text, "cache_hit": cache_hit, "error": error})
    report = {"schema_version": 1, "model": model, "passed": len(candidates) == len(output["segments"]), "group_count": len(output["segments"]), "failed_count": len(output["segments"]) - len(candidates), "checks": checks}
    return output, candidates, report


class DedicatedMTTranslator:
    """Translate source text with the native MADLAD or NLLB protocol."""

    def __init__(self, model_name: str, device: str = "auto", local_files_only: bool = False):
        """Configure a lazy native-protocol translator without loading weights."""
        self.model_name = model_name
        self.device = resolve_device(device)
        self.local_files_only = local_files_only
        self.tokenizer = None
        self.model = None
        self.protocol = "madlad" if "madlad" in model_name.casefold() else "nllb"
        self.source_code = None

    def _load(self, source_language: str) -> None:
        """Load float16 CUDA or float32 CPU weights for one source language."""
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        kwargs = {"local_files_only": self.local_files_only}
        if self.protocol == "nllb":
            self.source_code = nllb_code(source_language, None)
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_name, src_lang=self.source_code, **kwargs,
            )
        else:
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, **kwargs)
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.model = AutoModelForSeq2SeqLM.from_pretrained(
            self.model_name, dtype=dtype, **kwargs,
        ).to(self.device).eval()

    def translate(self, text: str, source_language: str, target_language: str = "en") -> str:
        """Translate one source group using deterministic beam generation."""
        source_code = nllb_code(source_language, None) if self.protocol == "nllb" else None
        if self.model is None or (self.protocol == "nllb" and source_code != self.source_code):
            self.unload()
            self._load(source_language)
        prompt = f"<2{target_language}> {text}" if self.protocol == "madlad" else text
        inputs = self.tokenizer(prompt, return_tensors="pt", truncation=True, max_length=512)
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        generation = {"max_new_tokens": 128, "num_beams": 4, "do_sample": False}
        if self.protocol == "nllb":
            target_code = nllb_code(target_language, None)
            generation["forced_bos_token_id"] = self.tokenizer.convert_tokens_to_ids(target_code)
        with torch.inference_mode():
            output = self.model.generate(**inputs, **generation)
        return self.tokenizer.decode(output[0], skip_special_tokens=True).strip()

    def unload(self) -> None:
        """Release model references and reusable CUDA allocations."""
        self.model = None
        self.tokenizer = None
        self.source_code = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
