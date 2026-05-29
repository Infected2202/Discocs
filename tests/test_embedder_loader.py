from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.config import Settings
from app.embedder import DiscogsEffnetEmbedder


def test_opus_uses_ffmpeg_before_essentia_loader(tmp_path: Path):
    embedder = DiscogsEffnetEmbedder(
        Settings(
            data_dir=tmp_path,
            db_path=tmp_path / "app.db",
            model_dir=tmp_path / "models",
            index_dir=tmp_path,
        ),
        "discogs_multi",
    )

    with patch("app.embedder.load_audio_with_ffmpeg") as load:
        load.return_value = np.array([0.0, 0.1], dtype=np.float32)

        audio = embedder._load_audio(tmp_path / "track.opus")

    assert np.allclose(audio, np.array([0.0, 0.1], dtype=np.float32))
    load.assert_called_once()


def test_default_loader_uses_ffmpeg_for_all_formats(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DISCOCS_AUDIO_LOADER", raising=False)
    embedder = DiscogsEffnetEmbedder(
        Settings(
            data_dir=tmp_path,
            db_path=tmp_path / "app.db",
            model_dir=tmp_path / "models",
            index_dir=tmp_path,
        ),
        "discogs_multi",
    )

    with patch("app.embedder.load_audio_with_ffmpeg") as load_ffmpeg:
        load_ffmpeg.return_value = np.array([0.2, 0.3], dtype=np.float32)

        audio = embedder._load_audio(tmp_path / "track.flac")

    assert np.allclose(audio, np.array([0.2, 0.3], dtype=np.float32))
    load_ffmpeg.assert_called_once()


def test_essentia_loader_can_be_enabled_explicitly(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DISCOCS_AUDIO_LOADER", "essentia")
    embedder = DiscogsEffnetEmbedder(
        Settings(
            data_dir=tmp_path,
            db_path=tmp_path / "app.db",
            model_dir=tmp_path / "models",
            index_dir=tmp_path,
        ),
        "discogs_multi",
    )

    with patch("app.embedder.load_audio_with_essentia") as load_essentia:
        load_essentia.return_value = np.array([0.4, 0.5], dtype=np.float32)

        audio = embedder._load_audio(tmp_path / "track.flac")

    assert np.allclose(audio, np.array([0.4, 0.5], dtype=np.float32))
    load_essentia.assert_called_once()


def test_invalid_audio_loader_fails_fast(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DISCOCS_AUDIO_LOADER", "nope")
    embedder = DiscogsEffnetEmbedder(
        Settings(
            data_dir=tmp_path,
            db_path=tmp_path / "app.db",
            model_dir=tmp_path / "models",
            index_dir=tmp_path,
        ),
        "discogs_multi",
    )

    try:
        embedder._load_audio(tmp_path / "track.flac")
    except ValueError as exc:
        assert "DISCOCS_AUDIO_LOADER" in str(exc)
    else:
        raise AssertionError("expected invalid DISCOCS_AUDIO_LOADER to fail")
