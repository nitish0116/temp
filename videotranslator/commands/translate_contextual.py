"""Translate clean semantic groups with bounded surrounding dialogue context."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

try:
    from .canonical_timed_text import append_provenance, validate_canonical_timed_text
    from .runtime_device import resolve_device
except ImportError:
    from canonical_timed_text import append_provenance, validate_canonical_timed_text
    from runtime_device import resolve_device


CONTEXT_PROTOCOL_VERSION = 1
WRAPPER_PREFIX = re.compile(
    r"^\s*(?:translation|translated\s+(?:text|dialogue)|answer)\s*:\s*",
    re.IGNORECASE,
)


def normalize_translation_response(text: str) -> str:
    """Remove harmless presentation wrappers without rewriting translated content."""
    cleaned = text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = re.sub(r"^```(?:\w+)?\s*|\s*```$", "", cleaned).strip()
    cleaned = WRAPPER_PREFIX.sub("", cleaned).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'", "“", "”"}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


@dataclass(frozen=True)
class TranslationRequest:
    """One semantic group plus read-only neighboring dialogue context."""

    group_id: str
    source_language: str
    target_language: str
    current_text: str
    previous: tuple[str, ...]
    following: tuple[str, ...]


def translation_request(
    segments: list[dict], index: int, source_language: str,
    target_language: str, context_size: int = 3,
) -> TranslationRequest:
    """Build a bounded context window without leaking another group's output."""
    if context_size < 0:
        raise ValueError("context_size must be nonnegative")
    start = max(0, index - context_size)
    end = min(len(segments), index + context_size + 1)
    return TranslationRequest(
        group_id=str(segments[index]["semantic_group_id"]),
        source_language=source_language,
        target_language=target_language,
        current_text=str(segments[index]["source_text"]),
        previous=tuple(str(item["source_text"]) for item in segments[start:index]),
        following=tuple(str(item["source_text"]) for item in segments[index + 1:end]),
    )


def translation_prompt(request: TranslationRequest) -> str:
    """Create an explicit prompt whose response must contain only current text."""
    previous = "\n".join(f"- {text}" for text in request.previous) or "(none)"
    following = "\n".join(f"- {text}" for text in request.following) or "(none)"
    return (
        f"Translate the CURRENT dialogue directly from {request.source_language} "
        f"to {request.target_language}. Use the surrounding dialogue only to resolve "
        "meaning, names, pronouns, tone, and omitted subjects. Return only the "
        "translation of CURRENT, without labels or commentary.\n\n"
        f"PREVIOUS CONTEXT:\n{previous}\n\n"
        f"CURRENT:\n{request.current_text}\n\n"
        f"FOLLOWING CONTEXT:\n{following}"
    )


def valid_translation_response(text: str, request: TranslationRequest) -> bool:
    """Reject instruction leakage, context copying, and verbose model commentary."""
    cleaned = text.strip()
    if not cleaned or len(cleaned) > max(120, len(request.current_text) * 6):
        return False
    lowered = cleaned.casefold()
    forbidden = (
        "current:", "previous context", "following context", "translation:",
        "here's the", "here is the", "this translation", "source text",
    )
    if any(marker in lowered for marker in forbidden):
        return False
    if any(context.strip() and context.strip() in cleaned for context in (*request.previous, *request.following)):
        return False
    return len([line for line in cleaned.splitlines() if line.strip()]) <= 2


def cache_key(request: TranslationRequest, model: str) -> str:
    """Key translations by the complete versioned linguistic input."""
    payload = {
        "protocol_version": CONTEXT_PROTOCOL_VERSION,
        "model": model,
        **asdict(request),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_cached(cache_directory: Path | None, key: str) -> str | None:
    if cache_directory is None:
        return None
    path = cache_directory / f"{key}.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    text = value.get("translated_text")
    return text if isinstance(text, str) and text.strip() else None


def _write_cached(cache_directory: Path | None, key: str, text: str) -> None:
    if cache_directory is None:
        return
    cache_directory.mkdir(parents=True, exist_ok=True)
    destination = cache_directory / f"{key}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"translated_text": text}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def translate_contextual(
    document: dict,
    target_language: str,
    model: str,
    translate_one: Callable[[TranslationRequest], str],
    *,
    context_size: int = 3,
    cache_directory: Path | None = None,
    refresh_group_ids: set[str] | None = None,
) -> dict:
    """Translate every semantic group while retaining canonical source evidence."""
    validate_canonical_timed_text(document)
    if document["stage"] != "clean_transcript":
        raise ValueError("Contextual translation requires a clean_transcript artifact")
    if not target_language.strip():
        raise ValueError("target_language must be nonempty")
    segments = document["segments"]
    translated_segments = []
    cache_hits = 0
    for index, segment in enumerate(segments):
        request = translation_request(
            segments, index, document["source_language"], target_language, context_size
        )
        key = cache_key(request, model)
        refresh = request.group_id in (refresh_group_ids or set())
        translated_text = None if refresh else _read_cached(cache_directory, key)
        cache_hit = translated_text is not None
        if not cache_hit:
            translated_text = normalize_translation_response(translate_one(request))
            if not translated_text:
                raise RuntimeError(f"Translation returned empty text for {request.group_id}")
            _write_cached(cache_directory, key, translated_text)
        else:
            cache_hits += 1
        translated_segments.append({
            **segment,
            "translated_text": translated_text,
            "provenance": append_provenance(
                segment, "contextual-translation", "bounded-dialogue-window",
                model=model, context_size=context_size,
                source_language=document["source_language"],
                target_language=target_language, cache_key=key, cache_hit=cache_hit,
                protocol_version=CONTEXT_PROTOCOL_VERSION,
            ),
        })
    result = {
        **document,
        "stage": "translated",
        "output_language": target_language,
        "metadata": {
            **document.get("metadata", {}),
            "contextual_translation": {
                "protocol_version": CONTEXT_PROTOCOL_VERSION,
                "model": model,
                "context_size": context_size,
                "direct_language_pair": [document["source_language"], target_language],
                "cache_hits": cache_hits,
                "translated_group_count": len(translated_segments),
                "refreshed_group_count": len(refresh_group_ids or set()),
            },
        },
        "segments": translated_segments,
    }
    validate_canonical_timed_text(result)
    return result


class TransformersContextTranslator:
    """Local instruction-model backend for the contextual request protocol."""

    def __init__(self, model_name: str, device: str = "auto") -> None:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.model_name = model_name
        self.device = resolve_device(device)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_name).to(self.device)

    def __call__(self, request: TranslationRequest) -> str:
        inputs = self.tokenizer(
            translation_prompt(request), return_tensors="pt", truncation=True,
            max_length=1024,
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        generated = self.model.generate(
            **inputs, max_new_tokens=256, no_repeat_ngram_size=3,
            repetition_penalty=1.1,
        )
        return self.tokenizer.decode(generated[0], skip_special_tokens=True).strip()


class CausalContextTranslator:
    """Chat-template backend for small multilingual causal instruction models."""

    def __init__(self, model_name: str, device: str = "auto") -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_name = model_name
        self.device = resolve_device(device)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name, dtype=dtype,
        ).to(self.device)

    def __call__(self, request: TranslationRequest) -> str:
        messages = [
            {
                "role": "system",
                "content": "You are a professional subtitle translator. Output only the translated current dialogue. Never explain, label, quote, or translate context.",
            },
            {"role": "user", "content": translation_prompt(request)},
        ]
        prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self.tokenizer(
            prompt, return_tensors="pt", truncation=True, max_length=2048
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        generated = self.model.generate(
            **inputs, max_new_tokens=256, do_sample=False,
            no_repeat_ngram_size=3, repetition_penalty=1.1,
            pad_token_id=self.tokenizer.eos_token_id,
        )
        new_tokens = generated[0][inputs["input_ids"].shape[-1]:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


class NLLBFallbackTranslator:
    """GPU-first direct translation with automatic CUDA-memory recovery."""

    def __init__(self, model_name: str, device: str = "cpu") -> None:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self.model_name = model_name
        self.device = resolve_device(device)
        self.AutoTokenizer = AutoTokenizer
        self.model_class = AutoModelForSeq2SeqLM
        self.tokenizer = None
        self.model = None
        self.source_code = None
        self.runtime_events: list[dict] = []

    def _load(self, source_code: str) -> None:
        import torch

        self.tokenizer = self.AutoTokenizer.from_pretrained(
            self.model_name, src_lang=source_code
        )
        kwargs = {"dtype": torch.float16} if self.device == "cuda" else {}
        self.model = self.model_class.from_pretrained(self.model_name, **kwargs).to(self.device)
        self.source_code = source_code

    def _generate(self, request: TranslationRequest, target_code: str) -> str:
        inputs = self.tokenizer(
            request.current_text, return_tensors="pt", truncation=True, max_length=512
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        target_token = self.tokenizer.convert_tokens_to_ids(target_code)
        generated = self.model.generate(
            **inputs, forced_bos_token_id=target_token, max_new_tokens=256,
            no_repeat_ngram_size=3, repetition_penalty=1.1,
        )
        return self.tokenizer.decode(generated[0], skip_special_tokens=True).strip()

    def __call__(self, request: TranslationRequest) -> str:
        try:
            from .auto_prepare_script import nllb_code
        except ImportError:
            from auto_prepare_script import nllb_code

        source_code = nllb_code(request.source_language, None)
        target_code = nllb_code(request.target_language, None)
        try:
            if self.model is None or self.source_code != source_code:
                self._load(source_code)
            return self._generate(request, target_code)
        except RuntimeError as error:
            if self.device != "cuda" or "out of memory" not in str(error).casefold():
                raise
            import torch

            self.model = self.model.to("cpu") if self.model is not None else None
            self.device = "cpu"
            torch.cuda.empty_cache()
            self.runtime_events.append({
                "group_id": request.group_id,
                "reason": "cuda-out-of-memory",
                "resolution": "continue-nllb-on-cpu",
            })
            if self.model is None:
                self._load(source_code)
            return self._generate(request, target_code)


class FallbackContextTranslator:
    """Use contextual translation first and record deterministic direct fallbacks."""

    def __init__(self, primary: Callable[[TranslationRequest], str], fallback: Callable[[TranslationRequest], str]) -> None:
        self.primary = primary
        self.fallback = fallback
        self.events: list[dict] = []

    def __call__(self, request: TranslationRequest) -> str:
        try:
            result = normalize_translation_response(self.primary(request))
        except Exception as error:
            self.events.append({
                "group_id": request.group_id, "reason": type(error).__name__,
                "resolution": "direct-translation-fallback",
            })
            return normalize_translation_response(self.fallback(request))
        if result and valid_translation_response(result, request):
            return result
        self.events.append({
            "group_id": request.group_id,
            "reason": "empty-primary-output" if not result else "invalid-primary-output-contract",
            "resolution": "direct-translation-fallback",
        })
        return normalize_translation_response(self.fallback(request))


def main() -> None:
    """Translate a clean transcript with a local instruction-following model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--target-language", required=True)
    parser.add_argument(
        "--model", required=True,
        help="Local/Hugging Face instruction-following seq2seq model",
    )
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--context-size", type=int, default=3)
    parser.add_argument("--cache-directory", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    backend = TransformersContextTranslator(args.model, args.device)
    translated = translate_contextual(
        source, args.target_language, args.model, backend,
        context_size=args.context_size, cache_directory=args.cache_directory,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(translated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Translated {len(translated['segments'])} semantic groups to {args.target_language}")


if __name__ == "__main__":
    main()
