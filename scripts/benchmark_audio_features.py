from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path


STAGES = (
    "decode_16k",
    "decode_44k",
    "rhythm",
    "key",
    "loudness",
    "dynamic",
    "timeline",
    "analysis_total",
)
_PROCESS_ANALYZER = None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def metric_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "median": None, "p95": None, "max": None}
    return {
        "avg": statistics.fmean(values),
        "median": statistics.median(values),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


def analysis_fingerprint(analysis: object) -> str:
    features = [
        {
            "name": feature.name,
            "value": feature.value,
            "text_value": feature.text_value,
            "unit": feature.unit,
            "confidence": feature.confidence,
            "extractor": feature.extractor,
        }
        for feature in analysis.features
    ]
    digest = hashlib.sha256()
    digest.update(json.dumps(features, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(analysis.timeline_payload)
    return digest.hexdigest()


def analyze_path(path: Path, track_id: int, analyzer: object | None = None) -> dict[str, object]:
    from app.audio_features import AudioFeatureAnalyzer

    started = time.perf_counter()
    try:
        active_analyzer = analyzer or AudioFeatureAnalyzer()
        analysis = active_analyzer.analyze_bundle(
            path,
            track_id=track_id,
            source={
                "path": str(path),
                "mtime": int(path.stat().st_mtime),
                "file_size": path.stat().st_size,
            },
        )
        return {
            "path": str(path),
            "status": "ok",
            "wall_seconds": time.perf_counter() - started,
            "timings": analysis.timings or {},
            "fingerprint": analysis_fingerprint(analysis),
        }
    except Exception as exc:
        return {
            "path": str(path),
            "status": "failed",
            "wall_seconds": time.perf_counter() - started,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _initialize_process(warmup_path: str | None) -> None:
    global _PROCESS_ANALYZER
    from app.audio_features import AudioFeatureAnalyzer

    _PROCESS_ANALYZER = AudioFeatureAnalyzer()
    if warmup_path is not None:
        result = analyze_path(Path(warmup_path), 1, _PROCESS_ANALYZER)
        if result["status"] != "ok":
            raise RuntimeError(f"worker warmup failed: {result.get('error')}")


def _analyze_process_partition(
    items: list[tuple[str, int]],
    concurrency: int,
) -> list[dict[str, object]]:
    if _PROCESS_ANALYZER is None:
        raise RuntimeError("benchmark process was not initialized")

    def run(item: tuple[str, int]) -> dict[str, object]:
        path, track_id = item
        return analyze_path(Path(path), track_id, _PROCESS_ANALYZER)

    if concurrency == 1:
        return [run(item) for item in items]
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        return list(executor.map(run, items))


def partition_paths(paths: list[Path], processes: int) -> list[list[tuple[str, int]]]:
    partitions: list[list[tuple[str, int]]] = [[] for _ in range(processes)]
    for track_id, path in enumerate(paths, start=1):
        partitions[(track_id - 1) % processes].append((str(path), track_id))
    return [partition for partition in partitions if partition]


def run_benchmark(
    paths: list[Path],
    concurrency: int,
    processes: int = 1,
    *,
    warmup_path: Path | None = None,
) -> tuple[list[dict[str, object]], float]:
    if processes > 1:
        partitions = partition_paths(paths, processes)
        with ProcessPoolExecutor(
            max_workers=processes,
            initializer=_initialize_process,
            initargs=(str(warmup_path) if warmup_path is not None else None,),
        ) as executor:
            started = time.perf_counter()
            futures = [
                executor.submit(_analyze_process_partition, partition, concurrency)
                for partition in partitions
            ]
            records = [record for future in as_completed(futures) for record in future.result()]
            return records, time.perf_counter() - started

    started = time.perf_counter()
    if concurrency == 1:
        records = [analyze_path(path, index) for index, path in enumerate(paths, start=1)]
    else:
        records = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(analyze_path, path, index): path
                for index, path in enumerate(paths, start=1)
            }
            for future in as_completed(futures):
                records.append(future.result())
    return records, time.perf_counter() - started


def summarize(
    records: list[dict[str, object]],
    wall_seconds: float,
    *,
    concurrency: int,
    processes: int,
    ffmpeg_threads: int,
    omp_threads: int,
) -> dict[str, object]:
    successes = [record for record in records if record["status"] == "ok"]
    stage_metrics = {
        stage: metric_summary(
            [float(record["timings"][stage]) for record in successes if stage in record["timings"]]
        )
        for stage in STAGES
    }
    return {
        "configuration": {
            "concurrency": concurrency,
            "processes": processes,
            "ffmpeg_threads": ffmpeg_threads,
            "omp_threads": omp_threads,
        },
        "tracks": len(records),
        "ok": len(successes),
        "failed": len(records) - len(successes),
        "wall_seconds": wall_seconds,
        "tracks_per_min": len(successes) * 60 / wall_seconds if wall_seconds else None,
        "stages": stage_metrics,
        "fingerprints": {
            str(record["path"]): record["fingerprint"] for record in successes
        },
        "failures": [record for record in records if record["status"] != "ok"],
    }


def read_paths(audio_dir: Path, limit: int | None, repeat: int) -> list[Path]:
    paths = sorted(path for path in audio_dir.iterdir() if path.is_file())
    if limit is not None:
        paths = paths[:limit]
    return paths * repeat


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark the production audio_features_v2 analyzer without database writes."
    )
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--processes", type=int, default=1)
    parser.add_argument("--ffmpeg-threads", type=int, default=1)
    parser.add_argument("--omp-threads", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--warmup", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.concurrency < 1 or args.processes < 1 or args.ffmpeg_threads < 1 or args.omp_threads < 1:
        raise SystemExit("concurrency and thread counts must be >= 1")
    if args.repeat < 1:
        raise SystemExit("repeat must be >= 1")

    os.environ["DISCOCS_AUDIO_LOADER"] = "ffmpeg"
    os.environ["DISCOCS_FFMPEG_THREADS"] = str(args.ffmpeg_threads)
    os.environ["OMP_NUM_THREADS"] = str(args.omp_threads)
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

    paths = read_paths(args.audio_dir, args.limit, args.repeat)
    if not paths:
        raise SystemExit("audio directory contains no files")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit(f"missing audio files: {', '.join(missing)}")

    if args.warmup and args.processes == 1:
        warmup = analyze_path(paths[0], 1)
        if warmup["status"] != "ok":
            raise SystemExit(f"warmup failed: {warmup.get('error')}")

    records, wall_seconds = run_benchmark(
        paths,
        args.concurrency,
        args.processes,
        warmup_path=paths[0] if args.warmup else None,
    )
    summary = summarize(
        records,
        wall_seconds,
        concurrency=args.concurrency,
        processes=args.processes,
        ffmpeg_threads=args.ffmpeg_threads,
        omp_threads=args.omp_threads,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"audio_features benchmark processes={args.processes} concurrency={args.concurrency} "
        f"ffmpeg_threads={args.ffmpeg_threads} omp_threads={args.omp_threads} "
        f"ok={summary['ok']} failed={summary['failed']} "
        f"wall_seconds={wall_seconds:.3f} tracks_per_min={summary['tracks_per_min']:.2f}",
        flush=True,
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
