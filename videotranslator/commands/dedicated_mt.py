"""Dedicated multilingual text-translation backends for release qualification."""

from __future__ import annotations

import torch

from .auto_prepare_script import nllb_code
from .runtime_device import resolve_device


MADLAD_MODEL = "google/madlad400-3b-mt"
NLLB_MODEL = "facebook/nllb-200-3.3B"


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
