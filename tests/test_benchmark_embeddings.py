from pathlib import Path

import numpy as np

from scripts.benchmark_embeddings import (
    analyze_track,
    build_parser,
    format_summary,
    metric_summary,
    read_audio_list,
    summarize_records,
)


def test_read_audio_list_skips_comments_and_blank_lines(tmp_path: Path):
    audio_list = tmp_path / "tracks.txt"
    audio_list.write_text(
        """
# comment
/music/a.flac

/music/b.flac
""".strip(),
        encoding="utf-8",
    )

    paths = read_audio_list(audio_list)

    assert len(paths) == 2
    assert paths[0].as_posix().endswith("/music/a.flac")
    assert paths[1].as_posix().endswith("/music/b.flac")


def test_summarize_records_computes_throughput_and_slowest():
    records = [
        {
            "path": "/music/a.flac",
            "status": "ok",
            "audio_seconds": 120.0,
            "load_seconds": 0.5,
            "predict_seconds": 3.0,
            "pool_seconds": 0.1,
            "total_seconds": 3.6,
            "warning_count": 0,
            "info_count": 1,
        },
        {
            "path": "/music/b.flac",
            "status": "ok",
            "audio_seconds": 240.0,
            "load_seconds": 1.0,
            "predict_seconds": 6.0,
            "pool_seconds": 0.2,
            "total_seconds": 7.2,
            "warning_count": 2,
            "info_count": 0,
        },
    ]

    summary = summarize_records(records, wall_seconds=12.0)

    assert summary["tracks"] == 2
    assert summary["ok"] == 2
    assert summary["tracks_per_min"] == 10.0
    assert summary["audio_hours_per_hour"] == 30.0
    assert summary["warnings"] == 2
    assert summary["slowest"][0]["path"] == "/music/b.flac"
    rendered = format_summary(summary)
    assert "10.00 tracks/min" in rendered
    assert "30.00 audio-hours/hour" in rendered


def test_metric_summary_handles_empty_values():
    assert metric_summary([]) == {
        "avg": None,
        "median": None,
        "p90": None,
        "p95": None,
        "max": None,
    }


def test_analyze_track_records_success_metrics(tmp_path: Path):
    class FakeEmbedder:
        def _load_audio(self, path: Path):
            return np.zeros(32000, dtype=np.float32)

        def _predict(self, audio):
            return np.ones((2, 3), dtype=np.float32)

    record = analyze_track(
        tmp_path / "track.flac",
        FakeEmbedder(),
        lambda embeddings: embeddings.mean(axis=0).astype(np.float32),
        capture=True,
    )

    assert record["status"] == "ok"
    assert record["audio_seconds"] == 2.0
    assert record["embedding_shape"] == [2, 3]
    assert record["vector_dim"] == 3
    assert record["total_seconds"] >= record["predict_seconds"]


def test_analyze_track_records_failure(tmp_path: Path):
    class FakeEmbedder:
        def _load_audio(self, path: Path):
            raise RuntimeError("boom")

    record = analyze_track(
        tmp_path / "track.flac",
        FakeEmbedder(),
        lambda embeddings: embeddings,
        capture=True,
    )

    assert record["status"] == "failed"
    assert record["error"] == "boom"


def test_parser_accepts_workers(tmp_path: Path):
    audio_list = tmp_path / "tracks.txt"
    args = build_parser().parse_args(["--audio-list", str(audio_list), "--workers", "2"])

    assert args.workers == 2


def test_parser_accepts_prefetch_pipeline(tmp_path: Path):
    audio_list = tmp_path / "tracks.txt"
    args = build_parser().parse_args(
        ["--audio-list", str(audio_list), "--pipeline", "prefetch"]
    )

    assert args.pipeline == "prefetch"
