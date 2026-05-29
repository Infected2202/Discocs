from pathlib import Path
import sys
import types

import numpy as np

from app.config import Settings
from app.head_pack import (
    DISCOGS_EFFNET_HEADS,
    DiscogsEffnetHeadPackAnalyzer,
    HeadModel,
    aggregate_scores,
    known_model_files,
    load_model_classes,
    required_model_files,
    tensor_candidates,
    top_predictions,
)


def test_head_registry_entries_have_required_files_and_urls():
    assert DISCOGS_EFFNET_HEADS
    for head in DISCOGS_EFFNET_HEADS:
        assert head.id
        assert head.filename.endswith(".pb")
        assert head.source_url.endswith(head.filename)
        assert head.input_tensor
        assert head.output_tensor
        assert head.output_kind in {"binary", "multiclass", "multilabel", "regression"}

    filenames = [filename for filename, _url in required_model_files()]
    assert "discogs-effnet-bs64-1.pb" in filenames
    assert "genre_discogs400-discogs-effnet-1.pb" in filenames


def test_known_model_files_include_embedding_and_head_pack_files(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )
    settings.model_dir.mkdir()
    (settings.model_dir / "discogs_multi_embeddings-effnet-bs64-1.pb").write_bytes(b"fake")

    files = known_model_files(settings)

    by_filename = {file["filename"]: file for file in files}
    assert by_filename["discogs_multi_embeddings-effnet-bs64-1.pb"]["ready"] is True
    assert by_filename["discogs_multi_embeddings-effnet-bs64-1.pb"]["kind"] == "embedding"
    assert by_filename["discogs-effnet-bs64-1.pb"]["ready"] is False
    assert by_filename["genre_discogs400-discogs-effnet-1.pb"]["kind"] == "head-pack"


def test_tensor_candidates_include_legacy_essentia_graph_names():
    head = HeadModel(
        id="first",
        folder="fake",
        filename="first.pb",
        metadata_filename=None,
        output_kind="binary",
    )

    candidates = tensor_candidates(head)

    assert candidates[0] == ("serving_default_model_Placeholder", "PartitionedCall:0")
    assert ("model/Placeholder", "model/Sigmoid") in candidates
    assert ("model/Placeholder", "model/Identity") in candidates


def test_load_model_classes_from_metadata(tmp_path: Path):
    path = tmp_path / "model.json"
    path.write_text('{"classes": ["a", "b"]}', encoding="utf-8")

    assert load_model_classes(path) == ["a", "b"]


def test_top_predictions_sorts_scores_and_limits_to_top_20():
    labels = [f"label_{index}" for index in range(25)]
    scores = np.array([0.01 * index for index in range(25)], dtype=np.float32)

    predictions = top_predictions(scores, labels, 20)

    assert len(predictions) == 20
    assert predictions[0].label == "label_24"
    assert predictions[0].rank == 1
    assert predictions[-1].label == "label_5"


def test_aggregate_scores_accepts_1d_and_2d_outputs():
    one_dimensional = aggregate_scores(np.array([0.2, 0.8], dtype=np.float32))
    two_dimensional = aggregate_scores(
        np.array([[0.0, 1.0], [0.4, 0.6]], dtype=np.float32)
    )

    assert np.allclose(one_dimensional, np.array([0.2, 0.8], dtype=np.float32))
    assert np.allclose(two_dimensional, np.array([0.2, 0.8], dtype=np.float32))


def test_head_pack_analyzer_reuses_one_patch_embedding_for_multiple_heads(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )
    settings.model_dir.mkdir()
    heads = (
        HeadModel(
            id="first",
            folder="fake",
            filename="first.pb",
            metadata_filename=None,
            output_kind="multilabel",
            top_n=2,
        ),
        HeadModel(
            id="second",
            folder="fake",
            filename="second.pb",
            metadata_filename=None,
            output_kind="binary",
            top_n=2,
        ),
    )

    class FakeEmbedder:
        calls = 0

        def __init__(self, _settings, _model):
            pass

        def extract_patch_embeddings(self, _path):
            FakeEmbedder.calls += 1
            return np.ones((2, 1280), dtype=np.float32)

    class FakeAnalyzer(DiscogsEffnetHeadPackAnalyzer):
        def __init__(self):
            super().__init__(settings, heads=heads)
            self.base_embedder = FakeEmbedder(settings, "discogs_effnet")

        def _predictor(self, head):
            def predict(patches):
                assert patches.shape == (2, 1280)
                if head.id == "first":
                    return np.array([[0.1, 0.9], [0.3, 0.7]], dtype=np.float32)
                return np.array([0.8, 0.2], dtype=np.float32)

            return predict

    outputs = FakeAnalyzer().analyze_track(tmp_path / "track.flac")

    assert FakeEmbedder.calls == 1
    assert [output.model_name for output in outputs] == ["first", "second"]
    assert np.allclose(outputs[0].scores, np.array([0.2, 0.8], dtype=np.float32))
    assert outputs[0].predictions[0].label == "first_1"
    assert outputs[1].predictions[0].label == "second_0"


def test_head_predictor_falls_back_to_legacy_tensor_names(tmp_path: Path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )
    settings.model_dir.mkdir()
    (settings.model_dir / "first.pb").write_bytes(b"fake")
    head = HeadModel(
        id="first",
        folder="fake",
        filename="first.pb",
        metadata_filename=None,
        output_kind="binary",
    )
    attempts = []

    class FakeTensorflowPredict2D:
        def __init__(self, graphFilename, input, output):
            attempts.append((input, output))
            if input == "serving_default_model_Placeholder":
                raise RuntimeError("not a valid node name")

        def __call__(self, _patches):
            return np.array([0.3, 0.7], dtype=np.float32)

    essentia_module = types.ModuleType("essentia")
    standard_module = types.ModuleType("essentia.standard")
    standard_module.TensorflowPredict2D = FakeTensorflowPredict2D
    monkeypatch.setitem(sys.modules, "essentia", essentia_module)
    monkeypatch.setitem(sys.modules, "essentia.standard", standard_module)

    analyzer = DiscogsEffnetHeadPackAnalyzer(settings, heads=(head,))
    predictor = analyzer._predictor(head)

    assert np.allclose(
        predictor(np.ones((2, 1280), dtype=np.float32)),
        np.array([0.3, 0.7], dtype=np.float32),
    )
    assert attempts[:2] == [
        ("serving_default_model_Placeholder", "PartitionedCall:0"),
        ("model/Placeholder", "model/Sigmoid"),
    ]
