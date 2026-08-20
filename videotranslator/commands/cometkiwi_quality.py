"""Lazy reference-free COMETKiwi quality-estimation adapter."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .runtime_device import resolve_device


DEFAULT_COMETKIWI_MODEL = "Unbabel/wmt22-cometkiwi-da"


def default_comet_cache(source: dict[str, str] | None = None) -> Path:
    """Resolve COMET storage from the workstation's shared cache profile."""
    env = os.environ if source is None else source
    if env.get("PYTHON_CACHE_HOME"):
        root = Path(env["PYTHON_CACHE_HOME"]).expanduser()
    elif env.get("LOCALAPPDATA"):
        root = Path(env["LOCALAPPDATA"]) / "videotranslator" / "models"
    else:
        root = Path.home() / ".cache" / "videotranslator" / "models"
    return root / "comet"


class CometKiwiQualityEstimator:
    """Score source/translation pairs without requiring reference translations."""

    def __init__(
        self,
        model_name: str = DEFAULT_COMETKIWI_MODEL,
        *,
        device: str = "auto",
        cache_directory: Path | None = None,
        local_files_only: bool = False,
        batch_size: int = 8,
        download_model: Callable[..., str] | None = None,
        load_model: Callable[..., Any] | None = None,
    ) -> None:
        """Configure lazy loading so constructing the adapter never downloads weights."""
        if batch_size < 1:
            raise ValueError("COMETKiwi batch_size must be positive")
        self.model_name = model_name
        self.device = resolve_device(device)
        self.cache_directory = cache_directory or default_comet_cache()
        self.local_files_only = local_files_only
        self.batch_size = batch_size
        self._download_model = download_model
        self._load_model = load_model
        self._model: Any = None

    def _resolve_library(self) -> tuple[Callable[..., str], Callable[..., Any]]:
        """Import the optional COMET dependency only when scoring is requested."""
        if self._download_model is not None and self._load_model is not None:
            return self._download_model, self._load_model
        try:
            from comet import download_model, load_from_checkpoint
        except ImportError as error:
            raise RuntimeError(
                "COMETKiwi requires the optional machine-review dependencies; "
                "install videotranslator/requirements/machine-review.txt"
            ) from error
        return download_model, load_from_checkpoint

    def _ensure_loaded(self) -> Any:
        """Download from or resolve within the shared cache, then load the model."""
        if self._model is not None:
            return self._model
        download_model, load_model = self._resolve_library()
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        try:
            checkpoint = download_model(
                self.model_name,
                saving_directory=self.cache_directory,
                local_files_only=self.local_files_only,
            )
            model = load_model(checkpoint, local_files_only=self.local_files_only)
        except Exception as error:
            mode = "offline cache lookup" if self.local_files_only else "model acquisition"
            raise RuntimeError(
                f"COMETKiwi {mode} failed for {self.model_name}: {error}"
            ) from error
        requires_references = getattr(model, "requires_references", None)
        if callable(requires_references) and requires_references():
            raise RuntimeError(f"COMET model {self.model_name} is not reference-free")
        if hasattr(model, "eval"):
            model.eval()
        self._model = model
        return model

    @staticmethod
    def _scores(prediction: Any, expected: int) -> list[float]:
        """Normalize the current COMET Prediction contract into bounded floats."""
        values = prediction.get("scores") if isinstance(prediction, dict) else getattr(prediction, "scores", None)
        if values is None:
            raise RuntimeError("COMETKiwi prediction did not contain sentence scores")
        if hasattr(values, "tolist"):
            values = values.tolist()
        try:
            scores = [float(value) for value in values]
        except (TypeError, ValueError) as error:
            raise RuntimeError("COMETKiwi returned invalid sentence scores") from error
        if len(scores) != expected:
            raise RuntimeError(
                f"COMETKiwi returned {len(scores)} scores for {expected} inputs"
            )
        if any(not 0.0 <= score <= 1.0 for score in scores):
            raise RuntimeError("COMETKiwi returned a score outside the calibrated 0..1 range")
        return scores

    def score_batch(self, pairs: Iterable[tuple[str, str]]) -> list[float]:
        """Score a batch of nonempty source and machine-translation pairs."""
        batch = [(str(source).strip(), str(translation).strip()) for source, translation in pairs]
        if not batch:
            return []
        if any(not source or not translation for source, translation in batch):
            raise ValueError("COMETKiwi source and translation text must be nonempty")
        prediction = self._ensure_loaded().predict(
            [{"src": source, "mt": translation} for source, translation in batch],
            batch_size=self.batch_size,
            gpus=1 if self.device == "cuda" else 0,
            progress_bar=False,
            num_workers=0,
        )
        return self._scores(prediction, len(batch))

    def __call__(self, source_text: str, translation: str) -> float:
        """Score one source/translation pair for the machine-review interface."""
        return self.score_batch([(source_text, translation)])[0]
