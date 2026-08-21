"""Local semantic review for generated character-reference candidates."""

from __future__ import annotations

import json
import re
import ast
from pathlib import Path

from .local_image_environment import REVIEW_MODEL_ID, REVIEW_MODEL_REVISION


class SmolVLMReviewer:
    """Review images against source evidence using cached SmolVLM2 weights."""

    name = f"smolvlm2-local:{REVIEW_MODEL_ID}@{REVIEW_MODEL_REVISION[:12]}"

    def __init__(self) -> None:
        self._pipeline = None

    def _load(self):
        if self._pipeline is None:
            import torch
            from huggingface_hub import snapshot_download
            from transformers import pipeline

            if not torch.cuda.is_available():
                raise RuntimeError("CUDA is required for local semantic image review")
            snapshot = snapshot_download(
                REVIEW_MODEL_ID, revision=REVIEW_MODEL_REVISION, local_files_only=True,
            )
            self._pipeline = pipeline(
                "image-text-to-text", model=snapshot,
                device=0, dtype=torch.bfloat16,
            )
        return self._pipeline

    def review(self, image: Path, brief: str) -> dict:
        return self._review(image, brief, scene=False)

    def review_scene(self, image: Path, contract: dict, reference_images: list[Path]) -> dict:
        brief = (
            f"SETTING: {contract.get('setting')}. VISIBLE EVENT: {contract.get('visible_event')}. "
            f"CHARACTERS: {', '.join(contract.get('characters', []))}. MOOD: {contract.get('mood')}. "
            f"A canonical reference image is supplied separately to generation; verify that the "
            f"pictured main character retains the same age, hair, face, and clothing design."
        )
        return self._review(image, brief, scene=True)

    def _review(self, image: Path, brief: str, *, scene: bool) -> dict:
        instruction = (
            ("Judge whether this image visibly depicts the required setting and event, and "
             "whether the main character design is internally consistent. " if scene else
             "Compare the pictured character only with this source evidence. Reject any age, "
             "gender, clothing, role, or physical contradiction. ")
            + f"Do not add franchise knowledge. Requirements: {brief[:3500]}\n"
            "Return JSON only with keys score, character_match, setting_match, "
            "action_match, and reasons. Each *_match value must be true or false. For a "
            "character reference, setting_match and action_match may be true. Accept only when "
            "every match is true and score is at least 0.75."
        )
        messages = [{"role": "user", "content": [
            {"type": "image", "path": str(image.resolve())},
            {"type": "text", "text": instruction},
        ]}]
        output = self._load()(text=messages, max_new_tokens=160, return_full_text=False)
        text = output[0].get("generated_text", "")
        if isinstance(text, list):
            text = text[-1].get("content", "")
        match = re.search(r"\{.*\}", str(text), re.DOTALL)
        if not match:
            diagnostic = " ".join(str(text).split())[:240]
            return {"accepted": False, "score": 0.0, "reasons": [f"invalid response: {diagnostic}"]}
        try:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                result = ast.literal_eval(match.group())
            score = max(0.0, min(1.0, float(result.get("score", 0))))
            reasons = [str(value)[:240] for value in result.get("reasons", [])][:6]
            criteria = {
                key: result.get(key) is True
                for key in ("character_match", "setting_match", "action_match")
            }
            return {
                "accepted": score >= 0.75 and all(criteria.values()),
                "score": score, **criteria,
                "reasons": reasons or ["no reviewer rationale"],
            }
        except (ValueError, TypeError, SyntaxError):
            diagnostic = " ".join(str(text).split())[:240]
            return {"accepted": False, "score": 0.0, "reasons": [f"malformed response: {diagnostic}"]}


def review_character_references(
    assets: dict, prompts: dict, root: Path, reviewer: SmolVLMReviewer | None = None,
) -> dict:
    """Fail closed unless each character has a semantically accepted candidate."""
    selected_reviewer = reviewer or SmolVLMReviewer()
    requirements = {
        item["reference_id"]: item for item in prompts.get("reference_requirements", [])
    }
    reviewed = []
    all_accepted = True
    for asset in assets.get("assets", []):
        requirement = requirements[asset["asset_id"]]
        candidates = []
        for candidate in asset["candidates"]:
            result = selected_reviewer.review(root / candidate["path"], requirement["reference_prompt"])
            candidates.append({"candidate_id": candidate["candidate_id"], **result})
        passing = [item for item in candidates if item["accepted"]]
        winner = max(passing, key=lambda item: (item["score"], item["candidate_id"])) if passing else None
        accepted = winner is not None
        all_accepted &= accepted
        reviewed.append({
            "asset_id": asset["asset_id"], "candidates": candidates,
            "selected_candidate_id": winner["candidate_id"] if winner else None,
            "status": "auto_accepted" if accepted else "retry_required",
        })
    return {
        "schema_version": 1, "reviewer": selected_reviewer.name,
        "status": "auto_accepted" if all_accepted else "retry_required",
        "acceptance_threshold": 0.75, "assets": reviewed,
    }


def review_shot_assets(assets: dict, prompts: dict, root: Path, reviewer=None) -> dict:
    """Apply the cached semantic reviewer to a bounded shot batch."""
    selected_reviewer = reviewer or SmolVLMReviewer()
    prompt_map = {item["shot_id"]: item for item in prompts.get("prompts", [])}
    reviewed = []
    all_accepted = True
    semantic_cores = []
    for asset in assets.get("assets", []):
        prompt_item = prompt_map[asset["asset_id"]]
        prompt = prompt_item["prompt"]
        semantic_cores.append(re.sub(
            r"Composition supports [^.]+ motion\.\s*", "", prompt,
            flags=re.IGNORECASE,
        ))
        candidates = []
        for candidate in asset["candidates"]:
            review_scene = getattr(selected_reviewer, "review_scene", None)
            result = (
                review_scene(root / candidate["path"], prompt_item.get("scene_contract", {}), [])
                if review_scene else selected_reviewer.review(root / candidate["path"], prompt)
            )
            candidates.append({"candidate_id": candidate["candidate_id"], **result})
        passing = [item for item in candidates if item["accepted"]]
        winner = max(passing, key=lambda item: (item["score"], item["candidate_id"])) if passing else None
        all_accepted &= winner is not None
        reviewed.append({
            "asset_id": asset["asset_id"], "candidates": candidates,
            "selected_candidate_id": winner["candidate_id"] if winner else None,
            "status": "auto_accepted" if winner else "retry_required",
        })
    issues = []
    if len(set(semantic_cores)) != len(semantic_cores):
        issues.append("pilot shots require distinct narrative visual beats, not camera-only variants")
        all_accepted = False
    if assets.get("canonical_reference_sha256") and not assets.get("reference_conditioning"):
        issues.append("canonical reference images must condition generation before expansion")
        all_accepted = False
    return {
        "schema_version": 1, "reviewer": selected_reviewer.name,
        "status": "auto_accepted" if all_accepted else "retry_required",
        "acceptance_threshold": 0.75, "issues": issues, "assets": reviewed,
    }
