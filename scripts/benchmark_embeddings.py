from __future__ import annotations

import argparse
import json
import os
import contextlib
import statistics
import sys
import time
import tempfile
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np


SAMPLE_RATE = 16000
_WORKER_EMBEDDER = None
_WORKER_POOL_FN = None
_WORKER_CAPTURE = True


@contextlib.contextmanager
def captured_stderr(enabled: bool = True):
    if not enabled:
        class EmptyCapture:
            text = ""

        yield EmptyCapture()
        return

    class Capture:
        text = ""

    sys.stderr.flush()
    original_fd = os.dup(2)
    capture = Capture()
    with tempfile.TemporaryFile(mode="w+b") as captured:
        os.dup2(captured.fileno(), 2)
        try:
            yield capture
        finally:
            sys.stderr.flush()
            os.dup2(original_fd, 2)
            os.close(original_fd)
            captured.seek(0)
            capture.text = captured.read().decode(errors="replace").strip()


def timed_call(func) -> tuple[object, float]:
    started = time.perf_counter()
    result = func()
    return result, time.perf_counter() - started


def read_audio_list(path: Path) -> list[Path]:
    return [
        Path(line.strip()).resolve()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * pct))
    return ordered[index]


def metric_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "median": None, "p90": None, "p95": None, "max": None}
    return {
        "avg": statistics.fmean(values),
        "median": statistics.median(values),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


def summarize_records(records: list[dict[str, object]], wall_seconds: float) -> dict[str, object]:
    successes = [record for record in records if record["status"] == "ok"]
    failures = [record for record in records if record["status"] != "ok"]
    total_audio_seconds = sum(float(record.get("audio_seconds") or 0.0) for record in successes)
    total_tracks = len(records)
    ok_tracks = len(successes)
    warning_count = sum(int(record.get("warning_count", 0)) for record in records)
    info_count = sum(int(record.get("info_count", 0)) for record in records)
    return {
        "tracks": total_tracks,
        "ok": ok_tracks,
        "failed": len(failures),
        "wall_seconds": wall_seconds,
        "tracks_per_min": (ok_tracks / wall_seconds) * 60 if wall_seconds > 0 else None,
        "audio_hours_per_hour": (total_audio_seconds / wall_seconds) if wall_seconds > 0 else None,
        "audio_seconds": total_audio_seconds,
        "warnings": warning_count,
        "infos": info_count,
        "load": metric_summary([float(record["load_seconds"]) for record in successes]),
        "predict": metric_summary([float(record["predict_seconds"]) for record in successes]),
        "pool": metric_summary([float(record["pool_seconds"]) for record in successes]),
        "total": metric_summary([float(record["total_seconds"]) for record in successes]),
        "slowest": sorted(successes, key=lambda record: float(record["total_seconds"]), reverse=True)[:10],
        "failures": failures,
    }


def format_seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}s"


def format_summary(summary: dict[str, object]) -> str:
    lines = [
        "Embedding benchmark summary",
        f"- tracks: {summary['ok']} ok / {summary['failed']} failed / {summary['tracks']} total",
        f"- wall: {format_seconds(summary['wall_seconds'])}",
        f"- throughput: {summary['tracks_per_min']:.2f} tracks/min" if summary["tracks_per_min"] else "- throughput: n/a",
        (
            f"- audio speed: {summary['audio_hours_per_hour']:.2f} audio-hours/hour"
            if summary["audio_hours_per_hour"]
            else "- audio speed: n/a"
        ),
        f"- stderr warnings: {summary['warnings']}",
        f"- stderr infos: {summary['infos']}",
        "",
        "Timing:",
    ]
    for key in ["load", "predict", "pool", "total"]:
        metrics = summary[key]
        lines.append(
            f"- {key}: avg {format_seconds(metrics['avg'])}, median {format_seconds(metrics['median'])}, "
            f"p90 {format_seconds(metrics['p90'])}, p95 {format_seconds(metrics['p95'])}, max {format_seconds(metrics['max'])}"
        )
    if summary["slowest"]:
        lines.extend(["", "Slowest tracks:"])
        for record in summary["slowest"][:5]:
            lines.append(f"- {float(record['total_seconds']):.3f}s {record['path']}")
    if summary["failures"]:
        lines.extend(["", "Failures:"])
        for record in summary["failures"][:10]:
            lines.append(f"- {record['path']}: {record.get('error')}")
    return "\n".join(lines)


def stderr_counts(*chunks: str) -> tuple[int, int]:
    text = "\n".join(chunk for chunk in chunks if chunk)
    return text.count("[ WARNING"), text.count("[   INFO")


def analyze_track(path: Path, embedder, pool_fn, capture: bool) -> dict[str, object]:
    record: dict[str, object] = {"path": str(path), "status": "ok"}
    started = time.perf_counter()
    try:
        with captured_stderr(capture) as load_stderr:
            audio, load_seconds = timed_call(lambda: embedder._load_audio(path))
        record["audio_samples"] = int(getattr(audio, "shape", [0])[0])
        record["audio_seconds"] = float(record["audio_samples"]) / SAMPLE_RATE

        with captured_stderr(capture) as predict_stderr:
            embeddings, predict_seconds = timed_call(lambda: embedder._predict(audio))
        record["embedding_shape"] = list(getattr(embeddings, "shape", []))

        with captured_stderr(capture) as pool_stderr:
            vector, pool_seconds = timed_call(lambda: pool_fn(embeddings))
        record["vector_dim"] = int(getattr(vector, "shape", [0])[0])

        warning_count, info_count = stderr_counts(
            load_stderr.text,
            predict_stderr.text,
            pool_stderr.text,
        )
        record.update(
            {
                "load_seconds": load_seconds,
                "predict_seconds": predict_seconds,
                "pool_seconds": pool_seconds,
                "total_seconds": time.perf_counter() - started,
                "warning_count": warning_count,
                "info_count": info_count,
                "load_stderr": load_stderr.text,
                "predict_stderr": predict_stderr.text,
                "pool_stderr": pool_stderr.text,
            }
        )
    except Exception as exc:
        record.update(
            {
                "status": "failed",
                "error": str(exc),
                "load_seconds": 0.0,
                "predict_seconds": 0.0,
                "pool_seconds": 0.0,
                "total_seconds": time.perf_counter() - started,
                "warning_count": 0,
                "info_count": 0,
            }
        )
    return record


def predict_loaded_track(
    path: Path,
    audio,
    load_seconds: float,
    load_stderr_text: str,
    started: float,
    embedder,
    pool_fn,
    capture: bool,
) -> dict[str, object]:
    record: dict[str, object] = {"path": str(path), "status": "ok"}
    try:
        record["audio_samples"] = int(getattr(audio, "shape", [0])[0])
        record["audio_seconds"] = float(record["audio_samples"]) / SAMPLE_RATE

        with captured_stderr(capture) as predict_stderr:
            embeddings, predict_seconds = timed_call(lambda: embedder._predict(audio))
        record["embedding_shape"] = list(getattr(embeddings, "shape", []))

        with captured_stderr(capture) as pool_stderr:
            vector, pool_seconds = timed_call(lambda: pool_fn(embeddings))
        record["vector_dim"] = int(getattr(vector, "shape", [0])[0])

        warning_count, info_count = stderr_counts(
            load_stderr_text,
            predict_stderr.text,
            pool_stderr.text,
        )
        record.update(
            {
                "load_seconds": load_seconds,
                "predict_seconds": predict_seconds,
                "pool_seconds": pool_seconds,
                "total_seconds": time.perf_counter() - started,
                "warning_count": warning_count,
                "info_count": info_count,
                "load_stderr": load_stderr_text,
                "predict_stderr": predict_stderr.text,
                "pool_stderr": pool_stderr.text,
            }
        )
    except Exception as exc:
        record.update(
            {
                "status": "failed",
                "error": str(exc),
                "load_seconds": load_seconds,
                "predict_seconds": 0.0,
                "pool_seconds": 0.0,
                "total_seconds": time.perf_counter() - started,
                "warning_count": 0,
                "info_count": 0,
            }
        )
    return record


def load_track_for_prefetch(path: Path, embedder) -> dict[str, object]:
    started = time.perf_counter()
    try:
        audio, load_seconds = timed_call(lambda: embedder._load_audio(path))
        return {
            "path": path,
            "status": "loaded",
            "audio": audio,
            "load_seconds": load_seconds,
            "load_stderr": "",
            "started": started,
        }
    except Exception as exc:
        return {
            "path": path,
            "status": "failed",
            "error": str(exc),
            "load_seconds": 0.0,
            "predict_seconds": 0.0,
            "pool_seconds": 0.0,
            "total_seconds": time.perf_counter() - started,
            "warning_count": 0,
            "info_count": 0,
        }


def iter_prefetch_records(paths: list[Path], embedder, pool_fn, capture: bool):
    print(f"benchmark dispatch prefetch tracks={len(paths)}", flush=True)
    if not paths:
        return

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(load_track_for_prefetch, paths[0], embedder)
        for index, path in enumerate(paths):
            loaded = pending.result()
            if index + 1 < len(paths):
                pending = executor.submit(load_track_for_prefetch, paths[index + 1], embedder)

            if loaded["status"] != "loaded":
                failed = dict(loaded)
                failed["path"] = str(failed["path"])
                yield failed
                continue

            yield predict_loaded_track(
                loaded["path"],
                loaded["audio"],
                float(loaded["load_seconds"]),
                str(loaded["load_stderr"]),
                float(loaded["started"]),
                embedder,
                pool_fn,
                capture,
            )


def init_worker(settings, model: str, capture: bool, warmup_path: str | None) -> None:
    global _WORKER_EMBEDDER, _WORKER_POOL_FN, _WORKER_CAPTURE
    from app.embedder import DiscogsEffnetEmbedder, pool_and_normalize

    print(f"worker pid={os.getpid()} initializing model={model}", flush=True)
    _WORKER_EMBEDDER = DiscogsEffnetEmbedder(settings, model)
    _WORKER_POOL_FN = pool_and_normalize
    _WORKER_CAPTURE = capture
    if warmup_path is not None:
        print(f"worker pid={os.getpid()} warmup {warmup_path}", flush=True)
        analyze_track(Path(warmup_path), _WORKER_EMBEDDER, _WORKER_POOL_FN, capture)
    print(f"worker pid={os.getpid()} ready", flush=True)


def analyze_track_in_worker(path: Path) -> dict[str, object]:
    if _WORKER_EMBEDDER is None or _WORKER_POOL_FN is None:
        raise RuntimeError("benchmark worker was not initialized")
    record = analyze_track(path, _WORKER_EMBEDDER, _WORKER_POOL_FN, _WORKER_CAPTURE)
    record["worker_pid"] = os.getpid()
    return record


def worker_ready() -> int:
    if _WORKER_EMBEDDER is None or _WORKER_POOL_FN is None:
        raise RuntimeError("benchmark worker was not initialized")
    return os.getpid()


def iter_benchmark_records(
    paths: list[Path],
    settings,
    model: str,
    embedder,
    pool_fn,
    capture: bool,
    workers: int,
    warmup_path: Path | None,
    start_method: str,
    pipeline: str,
    on_start,
):
    if pipeline == "prefetch":
        if workers != 1:
            raise ValueError("--pipeline prefetch requires --workers 1")
        on_start()
        yield from iter_prefetch_records(paths, embedder, pool_fn, capture)
        return

    if workers <= 1:
        print(f"benchmark dispatch sequential tracks={len(paths)}", flush=True)
        on_start()
        for path in paths:
            yield analyze_track(path, embedder, pool_fn, capture)
        return

    print(
        f"benchmark dispatch parallel tracks={len(paths)} workers={workers} start_method={start_method}",
        flush=True,
    )
    context = multiprocessing.get_context(start_method)
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=init_worker,
        initargs=(
            settings,
            model,
            capture,
            str(warmup_path) if warmup_path is not None else None,
        ),
        mp_context=context,
    ) as executor:
        print(f"benchmark waiting for {workers} worker(s)", flush=True)
        ready_pids = sorted({future.result() for future in as_completed(
            [executor.submit(worker_ready) for _ in range(workers)]
        )})
        print(f"benchmark workers ready pids={','.join(str(pid) for pid in ready_pids)}", flush=True)
        print(f"benchmark submitting {len(paths)} tracks", flush=True)
        on_start()
        future_to_path = {executor.submit(analyze_track_in_worker, path): path for path in paths}
        for future in as_completed(future_to_path):
            path = future_to_path[future]
            try:
                yield future.result()
            except Exception as exc:
                yield {
                    "path": str(path),
                    "status": "failed",
                    "error": str(exc),
                    "load_seconds": 0.0,
                    "predict_seconds": 0.0,
                    "pool_seconds": 0.0,
                    "total_seconds": 0.0,
                    "warning_count": 0,
                    "info_count": 0,
                }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark Discogs-EffNet embedding extraction.")
    parser.add_argument("--audio-list", required=True, type=Path, help="Text file with one audio path per line")
    parser.add_argument("--model", default="discogs_multi", help="Model key from app.config")
    parser.add_argument("--loader", choices=["ffmpeg", "essentia"], default="ffmpeg")
    parser.add_argument("--limit", type=int, help="Analyze only the first N paths")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat the selected path list N times")
    parser.add_argument("--warmup", type=int, default=0, help="Run and discard the first N selected paths")
    parser.add_argument("--workers", type=int, default=1, help="Number of analyzer processes")
    parser.add_argument(
        "--pipeline",
        choices=["sequential", "prefetch"],
        default="sequential",
        help="Single-process execution pipeline. Use --workers 1 with prefetch.",
    )
    parser.add_argument(
        "--start-method",
        choices=["spawn", "fork", "forkserver"],
        default="spawn",
        help="Multiprocessing start method for --workers > 1",
    )
    parser.add_argument("--jsonl", type=Path, default=Path("benchmark-embeddings.jsonl"))
    parser.add_argument("--summary-json", type=Path, default=Path("benchmark-embeddings-summary.json"))
    parser.add_argument("--no-capture", action="store_true", help="Do not capture C/C++ stderr per track")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.pipeline == "prefetch" and args.workers != 1:
        raise SystemExit("--pipeline prefetch requires --workers 1")
    os.environ["DISCOCS_AUDIO_LOADER"] = args.loader
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

    from app.config import Settings

    paths = read_audio_list(args.audio_list)
    if args.limit is not None:
        paths = paths[: args.limit]
    paths = paths * args.repeat
    if not paths:
        raise SystemExit("audio list is empty")

    settings = Settings.from_env()
    warmup_paths = paths[: args.warmup]
    benchmark_paths = paths[args.warmup :]
    embedder = None
    pool_fn = None
    if args.workers <= 1:
        from app.embedder import DiscogsEffnetEmbedder, pool_and_normalize

        embedder = DiscogsEffnetEmbedder(settings, args.model)
        pool_fn = pool_and_normalize
        for index, path in enumerate(warmup_paths, start=1):
            print(f"warmup {index}/{len(warmup_paths)} {path}", flush=True)
            analyze_track(path, embedder, pool_fn, not args.no_capture)
    elif warmup_paths:
        print(
            f"warmup moved into each worker count={len(warmup_paths)} using={warmup_paths[0]}",
            flush=True,
        )

    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    records = []
    started = None

    def mark_started() -> None:
        nonlocal started
        started = time.perf_counter()

    with args.jsonl.open("w", encoding="utf-8") as handle:
        record_iter = iter_benchmark_records(
            benchmark_paths,
            settings,
            args.model,
            embedder,
            pool_fn,
            not args.no_capture,
            args.workers,
            warmup_paths[0] if warmup_paths else None,
            args.start_method,
            args.pipeline,
            mark_started,
        )
        for index, record in enumerate(record_iter, start=1):
            worker = f" pid={record['worker_pid']}" if record.get("worker_pid") else ""
            print(
                f"benchmark {index}/{len(benchmark_paths)} status={record['status']}{worker} "
                f"seconds={float(record['total_seconds']):.3f} {record['path']}",
                flush=True,
            )
            records.append(record)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
    wall_seconds = time.perf_counter() - (started if started is not None else time.perf_counter())
    summary = summarize_records(records, wall_seconds)
    args.summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(format_summary(summary))
    print(f"\nWrote JSONL: {args.jsonl}")
    print(f"Wrote summary JSON: {args.summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
