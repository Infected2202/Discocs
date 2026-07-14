import inspect

import numpy as np
import pytest
from contextlib import nullcontext

from app.config import MUQ_MULAN_MODEL
from app.embedder import (
    MuqMulanEmbedder,
    _ensure_int_max_str_digits_compat,
    create_track_embedder,
    pool_and_normalize,
)


class DummySettings:
    model_dir = None


class FakeMuqMulanEmbedder(MuqMulanEmbedder):
    def __init__(self, settings, output):
        super().__init__(settings)
        self.output = np.asarray(output, dtype=np.float32)

    def _predict(self, audio):
        return self.output


def test_pool_and_normalize_means_patches():
    vector = pool_and_normalize(np.array([[3.0, 0.0], [0.0, 3.0]], dtype=np.float32))

    assert vector.dtype == np.float32
    assert np.allclose(np.linalg.norm(vector), 1.0)
    assert np.allclose(vector, np.array([0.70710677, 0.70710677], dtype=np.float32))


def test_pool_and_normalize_rejects_zero_vector():
    with pytest.raises(ValueError, match="zero norm"):
        pool_and_normalize(np.zeros((2, 3), dtype=np.float32))


def test_create_track_embedder_routes_muq_without_loading_dependencies(tmp_path):
    settings = DummySettings()
    settings.model_dir = tmp_path

    embedder = create_track_embedder(settings, MUQ_MULAN_MODEL)

    assert isinstance(embedder, MuqMulanEmbedder)
    assert embedder.model_name == MUQ_MULAN_MODEL
    assert embedder.cache_dir == tmp_path / "muq"
    assert embedder._model is None


def test_muq_mulan_embedder_loads_audio_at_24khz_and_normalizes(monkeypatch, tmp_path):
    settings = DummySettings()
    settings.model_dir = tmp_path
    calls = []

    def fake_load_audio(path, sample_rate=16000):
        calls.append((path, sample_rate))
        return np.array([0.1, -0.1, 0.2], dtype=np.float32)

    monkeypatch.setattr("app.embedder.load_audio_with_ffmpeg", fake_load_audio)
    embedder = FakeMuqMulanEmbedder(settings, [[3.0, 0.0], [0.0, 3.0]])

    vector = embedder.extract_track_vector(tmp_path / "track.wav")

    assert calls == [(tmp_path / "track.wav", 24000)]
    assert vector.dtype == np.float32
    assert np.allclose(np.linalg.norm(vector), 1.0)
    assert np.allclose(vector, np.array([0.70710677, 0.70710677], dtype=np.float32))


def test_muq_mulan_embedder_rejects_zero_vector(monkeypatch, tmp_path):
    settings = DummySettings()
    settings.model_dir = tmp_path
    monkeypatch.setattr(
        "app.embedder.load_audio_with_ffmpeg",
        lambda path, sample_rate=16000: np.ones(3, dtype=np.float32),
    )
    embedder = FakeMuqMulanEmbedder(settings, [0.0, 0.0, 0.0])

    with pytest.raises(ValueError, match="zero norm"):
        embedder.extract_track_vector(tmp_path / "track.wav")


def test_muq_mulan_text_vector_uses_text_embedding_api(tmp_path):
    settings = DummySettings()
    settings.model_dir = tmp_path
    calls = []

    class FakeModel:
        def get_text_embedding(self, texts):
            calls.append(texts)
            return np.array([[3.0, 4.0]], dtype=np.float32)

    class FakeTorch:
        inference_mode = staticmethod(nullcontext)

    embedder = MuqMulanEmbedder(settings)
    embedder._model = FakeModel()
    embedder._torch = FakeTorch()
    embedder._resolved_device = "cpu"

    vector = embedder.extract_text_vector("  warm deep house  ")

    assert calls == [["warm deep house"]]
    assert vector.dtype == np.float32
    assert np.allclose(vector, np.array([0.6, 0.8], dtype=np.float32))
    assert np.allclose(np.linalg.norm(vector), 1.0)


def test_ensure_int_max_str_digits_compat_adds_missing_shims() -> None:
    fake_sys = type("FakeSys", (), {})()

    _ensure_int_max_str_digits_compat(fake_sys)

    assert fake_sys.get_int_max_str_digits() == 0
    assert fake_sys.set_int_max_str_digits(1234) is None


def test_set_int_max_str_digits_shim_parameter_name_matches_torch_polyfill() -> None:
    """On Python builds missing sys.set_int_max_str_digits, torch._dynamo.polyfills.sys
    later registers its own set_int_max_str_digits(maxdigits) as a substitute for it via
    torch._dynamo.decorators.substitute_in_graph, which rejects the pairing if the parameter
    name differs from ours (not just type/count) -- breaking MuQ-MuLan loading with
    "Signature mismatch ... _maxdigits != maxdigits" the last time this got renamed.
    """
    fake_sys = type("FakeSys", (), {})()

    _ensure_int_max_str_digits_compat(fake_sys)

    params = list(inspect.signature(fake_sys.set_int_max_str_digits).parameters)
    assert params == ["maxdigits"]
