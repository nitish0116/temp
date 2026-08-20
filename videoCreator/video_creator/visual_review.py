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
        instruction = (
            "Compare the pictured character only with this source evidence. Reject any "
            "age, gender, clothing, role, or physical contradiction. Do not add franchise "
            f"knowledge. Evidence: {brief[:3500]}\n"
            "Answer exactly one line: VERDICT: ACCEPT or REJECT; SCORE: 0.00-1.00; "
            "REASON: one short explanation."
        )
        messages = [{"role": "user", "content": [
            {"type": "image", "path": str(image.resolve())},
            {"type": "text", "text": instruction},
        ]}]
        output = self._load()(text=messages, max_new_tokens=80, return_full_text=False)
        text = output[0].get("generated_text", "")
        if isinstance(text, list):
            text = text[-1].get("content", "")
        line_match = re.search(
            r"VERDICT\s*:\s*(ACCEPT|REJECT).*?SCORE\s*:\s*(0(?:\.\d+)?|1(?:\.0+)?)"
            r".*?REASON\s*:\s*(.+)", str(text), re.IGNORECASE | re.DOTALL,
        )
        if line_match:
            score = float(line_match.group(2))
            return {
                "accepted": line_match.group(1).upper() == "ACCEPT" and score >= 0.75,
                "score": score, "reasons": [" ".join(line_match.group(3).split())[:240]],
            }
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
            return {
                "accepted": bool(result.get("accepted")) and score >= 0.75,
                "score": score, "reasons": reasons or ["no reviewer rationale"],
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
