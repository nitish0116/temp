"""Offline tests for the optional COMETKiwi quality-estimation adapter."""

from types import SimpleNamespace

import pytest

from videotranslator.commands.cometkiwi_quality import (
    CometKiwiQualityEstimator,
    default_comet_cache,
)


class FakeModel:
    """Record prediction arguments while returning fixture scores."""

    def __init__(self, scores=(0.91, 0.82), requires_references=False):
        """Configure deterministic scores and reference requirements."""
        self.scores = scores
        self.reference_required = requires_references
        self.calls = []
        self.evaluating = False

    def requires_references(self):
        """Expose the COMET reference-free capability contract."""
        return self.reference_required

    def eval(self):
        """Record that inference mode was selected after loading."""
        self.evaluating = True

    def predict(self, data, **kwargs):
        """Return a modern COMET-style prediction object."""
        self.calls.append((data, kwargs))
        return SimpleNamespace(scores=self.scores[:len(data)])


def adapter(tmp_path, model, **kwargs):
    """Build an adapter with injected offline download and load functions."""
    downloads = []
    loads = []

    def download_model(name, **options):
        """Record a model lookup without network access."""
        downloads.append((name, options))
        return str(tmp_path / "model.ckpt")

    def load_model(path, **options):
        """Return the injected fake model for a checkpoint path."""
        loads.append((path, options))
        return model

    estimator = CometKiwiQualityEstimator(
        cache_directory=tmp_path / "comet", device="cpu",
        download_model=download_model, load_model=load_model, **kwargs,
    )
    return estimator, downloads, loads


def test_default_cache_follows_workstation_profile(tmp_path):
    assert default_comet_cache({"PYTHON_CACHE_HOME": str(tmp_path)}) == tmp_path / "comet"


def test_adapter_is_lazy_batches_pairs_and_reuses_model(tmp_path):
    model = FakeModel()
    estimator, downloads, loads = adapter(tmp_path, model, local_files_only=True)
    assert downloads == [] and loads == []
    scores = estimator.score_batch([("source one", "target one"), ("source two", "target two")])
    assert scores == [0.91, 0.82]
    assert downloads[0][1]["local_files_only"] is True
    assert loads[0][1]["local_files_only"] is True
    assert model.calls[0][1]["gpus"] == 0
    assert model.calls[0][0] == [
        {"src": "source one", "mt": "target one"},
        {"src": "source two", "mt": "target two"},
    ]
    assert model.evaluating is True
    assert estimator("source three", "target three") == 0.91
    assert len(downloads) == len(loads) == 1


def test_adapter_rejects_reference_based_model(tmp_path):
    estimator, _downloads, _loads = adapter(
        tmp_path, FakeModel(requires_references=True),
    )
    with pytest.raises(RuntimeError, match="not reference-free"):
        estimator("source", "translation")


@pytest.mark.parametrize("scores, message", [
    ((1.2,), "outside"),
    ((), "0 scores"),
])
def test_adapter_rejects_invalid_prediction_contract(tmp_path, scores, message):
    estimator, _downloads, _loads = adapter(tmp_path, FakeModel(scores=scores))
    with pytest.raises(RuntimeError, match=message):
        estimator("source", "translation")


def test_adapter_rejects_empty_text_without_loading(tmp_path):
    estimator, downloads, _loads = adapter(tmp_path, FakeModel())
    with pytest.raises(ValueError, match="nonempty"):
        estimator("", "translation")
    assert downloads == []


def test_adapter_wraps_offline_cache_failure(tmp_path):
    def fail_download(*args, **kwargs):
        """Simulate a model missing from the offline cache."""
        raise KeyError("not cached")

    estimator = CometKiwiQualityEstimator(
        cache_directory=tmp_path, device="cpu", local_files_only=True,
        download_model=fail_download, load_model=lambda *args, **kwargs: None,
    )
    with pytest.raises(RuntimeError, match="offline cache lookup failed"):
        estimator("source", "translation")
