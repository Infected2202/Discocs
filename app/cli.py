from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from pathlib import Path
import signal
import tempfile
import time
from time import perf_counter
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urljoin

import typer
import numpy as np

from app.audio_features import AUDIO_FEATURE_EXTRACTOR, AudioFeatureAnalyzer
from app.config import Settings
from app.embedder import DiscogsEffnetEmbedder
from app.head_pack import (
    DISCOGS_EFFNET_HEADS,
    DiscogsEffnetHeadPackAnalyzer,
    download_head_pack_models,
    head_pack_readiness,
)
from app.logging_config import configure_logging, get_analysis_logger
from app.recommender import Recommender, build_index
from app.scanner import AUDIO_EXTENSIONS, iter_audio_files, scan_music_folder
from app.store import Store, similar_track_dict


cli = typer.Typer(no_args_is_help=True)
logger = logging.getLogger(__name__)
analysis_logger = get_analysis_logger()


def get_store_and_settings() -> tuple[Store, Settings]:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    return store, settings


@cli.command()
def scan(music_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    """Scan a music folder and upsert track metadata."""
    store, _settings = get_store_and_settings()
    logger.info("Starting scan music_dir=%s", music_dir)
    total = 0
    changed = 0
    for scanned in scan_music_folder(music_dir):
        _track_id, did_change = store.upsert_track(scanned)
        total += 1
        changed += int(did_change)
        if total % 100 == 0:
            logger.info("Scan progress music_dir=%s scanned=%s changed=%s", music_dir, total, changed)
    logger.info(
        "Finished scan music_dir=%s scanned=%s changed=%s tracks=%s",
        music_dir,
        total,
        changed,
        store.count_tracks(),
    )
    typer.echo(f"scanned={total} changed={changed} tracks={store.count_tracks()}")


@cli.command("inspect-folder")
def inspect_folder(
    music_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    limit: Annotated[int, typer.Option("--limit")] = 20,
) -> None:
    """Show what the container can see in a music folder."""
    all_files = [path for path in music_dir.rglob("*") if path.is_file()]
    audio_files = list(iter_audio_files(music_dir))
    suffix_counts: dict[str, int] = {}
    for path in all_files:
        suffix = path.suffix.lower() or "<none>"
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1

    typer.echo(f"path={music_dir.resolve()}")
    typer.echo(f"exists={music_dir.exists()} is_dir={music_dir.is_dir()}")
    typer.echo(f"all_files={len(all_files)}")
    typer.echo(f"supported_audio_files={len(audio_files)}")
    typer.echo(f"supported_extensions={', '.join(sorted(AUDIO_EXTENSIONS))}")
    typer.echo("top_extensions:")
    for suffix, count in sorted(suffix_counts.items(), key=lambda item: item[1], reverse=True)[:20]:
        typer.echo(f"  {suffix}: {count}")
    typer.echo("examples:")
    for path in all_files[:limit]:
        typer.echo(f"  {path}")


@cli.command()
def extract_one(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    model: Annotated[str, typer.Option("--model")] = "discogs_multi",
) -> None:
    """Run the embedding spike for one audio file."""
    _store, settings = get_store_and_settings()
    embedder = DiscogsEffnetEmbedder(settings, model)
    analysis_logger.info("Extracting one track path=%s model=%s", path, model)
    vector = embedder.extract_track_vector(path)
    typer.echo(f"pooled vector shape: {tuple(vector.shape)}")
    typer.echo(f"norm: {float((vector * vector).sum() ** 0.5):.6f}")


@cli.command()
def analyze(
    model: Annotated[str, typer.Option("--model")] = "discogs_multi",
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Extract embeddings for tracks missing the selected model."""
    store, settings = get_store_and_settings()
    embedder = DiscogsEffnetEmbedder(settings, model)
    tracks = store.list_tracks_missing_embedding(model, limit=limit)
    total = len(tracks)
    failed = 0
    analysis_logger.info("Starting CLI analyze model=%s limit=%s total=%s", model, limit, total)
    if total == 0:
        typer.echo(f"nothing to analyze for model={model}")
        analysis_logger.info("Finished CLI analyze model=%s total=0", model)
        return
    typer.echo(f"analyzing={total} model={model}")
    done = 0
    started = perf_counter()
    for index, track in enumerate(tracks, start=1):
        label = f"{track.artist or ''} - {track.title or Path(track.path).stem}".strip(" -")
        typer.echo(f"[{index}/{total}] start track_id={track.id} {label}")
        track_started = perf_counter()
        try:
            vector = embedder.extract_track_vector(Path(track.path))
            store.save_embedding(track.id, model, vector)
            done += 1
            elapsed = perf_counter() - track_started
            avg = (perf_counter() - started) / max(done + failed, 1)
            remaining = max(total - index, 0) * avg
            typer.echo(
                f"[{index}/{total}] ok track_id={track.id} "
                f"seconds={elapsed:.1f} eta_seconds={remaining:.0f}"
            )
        except Exception as exc:
            failed += 1
            elapsed = perf_counter() - track_started
            analysis_logger.exception(
                "Track analyze failed track_id=%s path=%s model=%s seconds=%.1f",
                track.id,
                track.path,
                model,
                elapsed,
            )
            typer.echo(
                f"[{index}/{total}] failed track_id={track.id} seconds={elapsed:.1f} "
                f"path={track.path}: {exc}",
                err=True,
            )
    analysis_logger.info(
        "Finished CLI analyze model=%s done=%s failed=%s embeddings=%s",
        model,
        done,
        failed,
        store.count_embeddings(model),
    )
    typer.echo(f"analyzed={done} failed={failed} embeddings={store.count_embeddings(model)}")


def post_json(server: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    url = urljoin(server.rstrip("/") + "/", path.lstrip("/"))
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def download_task_audio(server: str, audio_url: str, target: Path) -> None:
    url = urljoin(server.rstrip("/") + "/", audio_url.lstrip("/"))
    with urlopen(url, timeout=300) as response:
        target.write_bytes(response.read())


def submit_worker_buffers(
    server: str,
    worker_id: str,
    results: list[dict[str, object]],
    feature_results: list[dict[str, object]],
    head_results: list[dict[str, object]],
    failures: list[dict[str, object]],
) -> None:
    if results or feature_results or head_results:
        post_json(
            server,
            "/workers/results",
            {
                "worker_id": worker_id,
                "results": results,
                "feature_results": feature_results,
                "head_results": head_results,
            },
        )
        results.clear()
        feature_results.clear()
        head_results.clear()
    if failures:
        post_json(server, "/workers/failures", {"worker_id": worker_id, "failures": failures})
        failures.clear()


@cli.command("worker")
def worker(
    server: Annotated[str, typer.Option("--server")] = "http://127.0.0.1:8711",
    worker_id: Annotated[str, typer.Option("--worker-id")] = "discocs-worker",
    models: Annotated[list[str], typer.Option("--models")] = [
        "discogs_multi",
        AUDIO_FEATURE_EXTRACTOR,
        "discogs-effnet-heads",
    ],
    claim_batch_size: Annotated[int, typer.Option("--claim-batch-size")] = 16,
    lease_seconds: Annotated[int, typer.Option("--lease-seconds")] = 900,
    poll_seconds: Annotated[float, typer.Option("--poll-seconds")] = 5.0,
    once: Annotated[bool, typer.Option("--once")] = False,
    max_inflight_tasks: Annotated[int, typer.Option("--max-inflight-tasks")] = 64,
    submit_batch_size: Annotated[int, typer.Option("--submit-batch-size")] = 16,
    download_concurrency: Annotated[int, typer.Option("--download-concurrency")] = 1,
    decode_workers: Annotated[int, typer.Option("--decode-workers")] = 1,
    gpu_batch_size: Annotated[int, typer.Option("--gpu-batch-size")] = 1,
    ready_batches: Annotated[int, typer.Option("--ready-batches")] = 1,
) -> None:
    """Run a trusted HTTP pull worker for analysis tasks."""
    _store, settings = get_store_and_settings()
    embedders = {
        model: DiscogsEffnetEmbedder(settings, model)
        for model in models
        if model not in {AUDIO_FEATURE_EXTRACTOR, "discogs-effnet-heads"}
    }
    audio_feature_analyzer = AudioFeatureAnalyzer() if AUDIO_FEATURE_EXTRACTOR in models else None
    head_pack_analyzer = DiscogsEffnetHeadPackAnalyzer(settings) if "discogs-effnet-heads" in models else None
    typer.echo(
        "worker starting "
        f"server={server} worker_id={worker_id} models={','.join(models)} "
        f"claim_batch_size={claim_batch_size} max_inflight_tasks={max_inflight_tasks} "
        f"submit_batch_size={submit_batch_size} download_concurrency={download_concurrency} "
        f"decode_workers={decode_workers} gpu_batch_size={gpu_batch_size} ready_batches={ready_batches}"
    )
    post_json(server, "/workers/register", {"worker_id": worker_id, "models": models})
    draining = False

    def request_drain(_signum, _frame) -> None:
        nonlocal draining
        draining = True
        typer.echo("drain requested; finishing in-flight tasks and releasing the rest", err=True)

    previous_sigint = signal.signal(signal.SIGINT, request_drain)
    leased_task_ids: set[str] = set()
    completed_task_ids: set[str] = set()
    results: list[dict[str, object]] = []
    feature_results: list[dict[str, object]] = []
    head_results: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    processed_any = False
    try:
        with tempfile.TemporaryDirectory(prefix="discocs-worker-") as temp_dir:
            temp_path = Path(temp_dir)
            with ThreadPoolExecutor(max_workers=max(download_concurrency, 1)) as downloader:
                future_to_task: dict[object, tuple[dict[str, object], Path]] = {}

                def claim_more() -> None:
                    nonlocal processed_any
                    if draining:
                        return
                    capacity = max(max_inflight_tasks, 1) - len(future_to_task)
                    if capacity <= 0:
                        return
                    limit = min(max(claim_batch_size, 1), capacity)
                    claimed = post_json(
                        server,
                        "/workers/claim",
                        {
                            "worker_id": worker_id,
                            "models": models,
                            "limit": limit,
                            "lease_seconds": lease_seconds,
                        },
                    )
                    tasks = claimed.get("tasks", [])
                    if not isinstance(tasks, list) or not tasks:
                        return
                    for task in tasks:
                        task_id = str(task["task_id"])
                        leased_task_ids.add(task_id)
                        audio_path = temp_path / f"{task_id}.audio"
                        future = downloader.submit(
                            download_task_audio,
                            server,
                            str(task["audio_url"]),
                            audio_path,
                        )
                        future_to_task[future] = (task, audio_path)
                    processed_any = True

                while not draining:
                    try:
                        claim_more()
                    except (HTTPError, URLError, TimeoutError) as exc:
                        typer.echo(f"claim failed: {exc}", err=True)
                        if once:
                            raise typer.Exit(1) from exc

                    if not future_to_task:
                        if once:
                            typer.echo("no tasks" if not processed_any else "done")
                            return
                        post_json(server, "/workers/heartbeat", {"worker_id": worker_id, "models": models})
                        time.sleep(poll_seconds)
                        continue

                    for future in as_completed(list(future_to_task), timeout=None):
                        task, audio_path = future_to_task.pop(future)
                        task_id = str(task["task_id"])
                        model_name = str(task["model_name"])
                        try:
                            future.result()
                            if model_name == AUDIO_FEATURE_EXTRACTOR:
                                if audio_feature_analyzer is None:
                                    raise KeyError(model_name)
                                features = audio_feature_analyzer.analyze_track(audio_path)
                                feature_results.append(
                                    {
                                        "task_id": task_id,
                                        "track_id": int(task["track_id"]),
                                        "model_name": model_name,
                                        "file_size": int(task["file_size"]),
                                        "mtime": int(task["mtime"]),
                                        "features": [
                                            {
                                                "name": feature.name,
                                                "value": feature.value,
                                                "text_value": feature.text_value,
                                                "unit": feature.unit,
                                                "confidence": feature.confidence,
                                                "extractor": feature.extractor,
                                            }
                                            for feature in features
                                        ],
                                    }
                                )
                            elif model_name == "discogs-effnet-heads":
                                if head_pack_analyzer is None:
                                    raise KeyError(model_name)
                                outputs = head_pack_analyzer.analyze_track(audio_path)
                                head_results.append(
                                    {
                                        "task_id": task_id,
                                        "track_id": int(task["track_id"]),
                                        "model_name": model_name,
                                        "file_size": int(task["file_size"]),
                                        "mtime": int(task["mtime"]),
                                        "outputs": [
                                            {
                                                "model_name": output.model_name,
                                                "dim": int(output.scores.shape[0]),
                                                "dtype": "float32",
                                                "aggregation": output.aggregation,
                                                "scores_b64": base64.b64encode(
                                                    np.asarray(output.scores, dtype=np.float32).tobytes()
                                                ).decode("ascii"),
                                                "predictions": [
                                                    {
                                                        "label": prediction.label,
                                                        "score": prediction.score,
                                                        "rank": prediction.rank,
                                                    }
                                                    for prediction in output.predictions
                                                ],
                                            }
                                            for output in outputs
                                        ],
                                    }
                                )
                            else:
                                vector = np.asarray(
                                    embedders[model_name].extract_track_vector(audio_path),
                                    dtype=np.float32,
                                )
                                results.append(
                                    {
                                        "task_id": task_id,
                                        "track_id": int(task["track_id"]),
                                        "model_name": model_name,
                                        "dim": int(vector.shape[0]),
                                        "dtype": "float32",
                                        "vector_b64": base64.b64encode(vector.tobytes()).decode("ascii"),
                                        "file_size": int(task["file_size"]),
                                        "mtime": int(task["mtime"]),
                                    }
                                )
                            completed_task_ids.add(task_id)
                            typer.echo(f"ok task_id={task_id} track_id={task['track_id']} model={model_name}")
                        except Exception as exc:
                            failures.append(
                                {
                                    "task_id": task_id,
                                    "error": str(exc),
                                    "error_type": type(exc).__name__,
                                    "stage": "worker",
                                    "retryable": not isinstance(exc, KeyError),
                                }
                            )
                            completed_task_ids.add(task_id)
                            typer.echo(f"failed task_id={task_id}: {exc}", err=True)

                        if (
                            len(results) + len(feature_results) + len(head_results) + len(failures)
                            >= max(submit_batch_size, 1)
                        ):
                            submit_worker_buffers(server, worker_id, results, feature_results, head_results, failures)
                        if not draining:
                            try:
                                claim_more()
                            except (HTTPError, URLError, TimeoutError) as exc:
                                typer.echo(f"claim failed: {exc}", err=True)
                        break

                for future in as_completed(list(future_to_task)):
                    task, audio_path = future_to_task.pop(future)
                    task_id = str(task["task_id"])
                    model_name = str(task["model_name"])
                    try:
                        future.result()
                        if model_name == AUDIO_FEATURE_EXTRACTOR:
                            if audio_feature_analyzer is None:
                                raise KeyError(model_name)
                            features = audio_feature_analyzer.analyze_track(audio_path)
                            feature_results.append(
                                {
                                    "task_id": task_id,
                                    "track_id": int(task["track_id"]),
                                    "model_name": model_name,
                                    "file_size": int(task["file_size"]),
                                    "mtime": int(task["mtime"]),
                                    "features": [
                                        {
                                            "name": feature.name,
                                            "value": feature.value,
                                            "text_value": feature.text_value,
                                            "unit": feature.unit,
                                            "confidence": feature.confidence,
                                            "extractor": feature.extractor,
                                        }
                                        for feature in features
                                    ],
                                }
                            )
                        elif model_name == "discogs-effnet-heads":
                            if head_pack_analyzer is None:
                                raise KeyError(model_name)
                            outputs = head_pack_analyzer.analyze_track(audio_path)
                            head_results.append(
                                {
                                    "task_id": task_id,
                                    "track_id": int(task["track_id"]),
                                    "model_name": model_name,
                                    "file_size": int(task["file_size"]),
                                    "mtime": int(task["mtime"]),
                                    "outputs": [
                                        {
                                            "model_name": output.model_name,
                                            "dim": int(output.scores.shape[0]),
                                            "dtype": "float32",
                                            "aggregation": output.aggregation,
                                            "scores_b64": base64.b64encode(
                                                np.asarray(output.scores, dtype=np.float32).tobytes()
                                            ).decode("ascii"),
                                            "predictions": [
                                                {
                                                    "label": prediction.label,
                                                    "score": prediction.score,
                                                    "rank": prediction.rank,
                                                }
                                                for prediction in output.predictions
                                            ],
                                        }
                                        for output in outputs
                                    ],
                                }
                            )
                        else:
                            vector = np.asarray(
                                embedders[model_name].extract_track_vector(audio_path),
                                dtype=np.float32,
                            )
                            results.append(
                                {
                                    "task_id": task_id,
                                    "track_id": int(task["track_id"]),
                                    "model_name": model_name,
                                    "dim": int(vector.shape[0]),
                                    "dtype": "float32",
                                    "vector_b64": base64.b64encode(vector.tobytes()).decode("ascii"),
                                    "file_size": int(task["file_size"]),
                                    "mtime": int(task["mtime"]),
                                }
                            )
                        completed_task_ids.add(task_id)
                    except Exception as exc:
                        failures.append(
                            {
                                "task_id": task_id,
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                                "stage": "worker",
                                "retryable": True,
                            }
                        )
                        completed_task_ids.add(task_id)
        submit_worker_buffers(server, worker_id, results, feature_results, head_results, failures)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        unreleased = sorted(leased_task_ids - completed_task_ids)
        if unreleased:
            try:
                post_json(
                    server,
                    "/workers/release",
                    {"worker_id": worker_id, "task_ids": unreleased},
                )
            except Exception as exc:
                typer.echo(f"release failed for {len(unreleased)} task(s): {exc}", err=True)


@cli.command("download-models")
def download_models(
    pack: Annotated[str, typer.Option("--pack")] = "discogs-effnet-heads",
) -> None:
    """Download model files required for a model pack."""
    if pack != "discogs-effnet-heads":
        raise typer.BadParameter("Only --pack discogs-effnet-heads is supported")
    _store, settings = get_store_and_settings()
    logger.info("Downloading model pack pack=%s", pack)
    results = download_head_pack_models(settings)
    downloaded = sum(1 for result in results if result.downloaded)
    typer.echo(f"downloaded={downloaded} already_present={len(results) - downloaded}")
    for result in results:
        state = "downloaded" if result.downloaded else "ready"
        typer.echo(f"{state} {result.path}")


@cli.command("analyze-heads")
def analyze_heads(
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Run all enabled Discogs-EffNet classification heads for missing tracks."""
    store, settings = get_store_and_settings()
    head_model_names = [head.id for head in DISCOGS_EFFNET_HEADS]
    analyzer = DiscogsEffnetHeadPackAnalyzer(settings)
    tracks = store.list_tracks_missing_head_pack(head_model_names, limit=limit)
    total = len(tracks)
    failed = 0
    analysis_logger.info("Starting CLI analyze-heads limit=%s total=%s heads=%s", limit, total, len(DISCOGS_EFFNET_HEADS))
    if total == 0:
        typer.echo("nothing to analyze for discogs-effnet-heads")
        analysis_logger.info("Finished CLI analyze-heads total=0")
        return
    typer.echo(f"analyzing_heads={total} heads={len(DISCOGS_EFFNET_HEADS)}")
    done = 0
    started = perf_counter()
    for index, track in enumerate(tracks, start=1):
        label = f"{track.artist or ''} - {track.title or Path(track.path).stem}".strip(" -")
        typer.echo(f"[{index}/{total}] start track_id={track.id} {label}")
        track_started = perf_counter()
        try:
            outputs = analyzer.analyze_track(Path(track.path))
            for output in outputs:
                store.save_model_output(track.id, output.model_name, output.scores, output.aggregation)
                store.save_predictions(track.id, output.model_name, output.predictions)
            done += 1
            elapsed = perf_counter() - track_started
            avg = (perf_counter() - started) / max(done + failed, 1)
            remaining = max(total - index, 0) * avg
            typer.echo(
                f"[{index}/{total}] ok track_id={track.id} "
                f"seconds={elapsed:.1f} eta_seconds={remaining:.0f} outputs={len(outputs)}"
            )
        except Exception as exc:
            failed += 1
            elapsed = perf_counter() - track_started
            analysis_logger.exception(
                "Track head analysis failed track_id=%s path=%s seconds=%.1f",
                track.id,
                track.path,
                elapsed,
            )
            typer.echo(
                f"[{index}/{total}] failed track_id={track.id} seconds={elapsed:.1f} "
                f"path={track.path}: {exc}",
                err=True,
            )
    analysis_logger.info(
        "Finished CLI analyze-heads done=%s failed=%s model_outputs=%s",
        done,
        failed,
        store.count_model_outputs(),
    )
    typer.echo(
        f"analyzed_heads={done} failed={failed} "
        f"model_outputs={store.count_model_outputs()}"
    )


@cli.command("analyze-genres")
def analyze_genres_compat(
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Compatibility alias for analyze-heads."""
    analyze_heads(limit=limit)


@cli.command("analyze-audio-features")
def analyze_audio_features(
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """Extract BPM, key, loudness, and dynamics for tracks missing audio features."""
    store, _settings = get_store_and_settings()
    analyzer = AudioFeatureAnalyzer()
    tracks = store.list_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR, limit=limit)
    total = len(tracks)
    failed = 0
    analysis_logger.info("Starting CLI analyze-audio-features limit=%s total=%s", limit, total)
    if total == 0:
        typer.echo("nothing to analyze for audio features")
        analysis_logger.info("Finished CLI analyze-audio-features total=0")
        return
    typer.echo(f"analyzing_audio_features={total}")
    done = 0
    started = perf_counter()
    for index, track in enumerate(tracks, start=1):
        label = f"{track.artist or ''} - {track.title or Path(track.path).stem}".strip(" -")
        typer.echo(f"[{index}/{total}] start track_id={track.id} {label}")
        track_started = perf_counter()
        try:
            features = analyzer.analyze_track(Path(track.path))
            store.save_features(track.id, features)
            done += 1
            elapsed = perf_counter() - track_started
            avg = (perf_counter() - started) / max(done + failed, 1)
            remaining = max(total - index, 0) * avg
            typer.echo(
                f"[{index}/{total}] ok track_id={track.id} "
                f"seconds={elapsed:.1f} eta_seconds={remaining:.0f} features={len(features)}"
            )
        except Exception as exc:
            failed += 1
            elapsed = perf_counter() - track_started
            analysis_logger.exception(
                "Track audio feature analysis failed track_id=%s path=%s seconds=%.1f",
                track.id,
                track.path,
                elapsed,
            )
            typer.echo(
                f"[{index}/{total}] failed track_id={track.id} seconds={elapsed:.1f} "
                f"path={track.path}: {exc}",
                err=True,
            )
    analysis_logger.info(
        "Finished CLI analyze-audio-features done=%s failed=%s feature_tracks=%s",
        done,
        failed,
        store.count_feature_tracks(AUDIO_FEATURE_EXTRACTOR),
    )
    typer.echo(
        f"analyzed_audio_features={done} failed={failed} "
        f"feature_tracks={store.count_feature_tracks(AUDIO_FEATURE_EXTRACTOR)}"
    )


@cli.command("build-index")
def build_index_command(model: Annotated[str, typer.Option("--model")] = "discogs_multi") -> None:
    """Build and save the HNSW cosine index for a model."""
    store, settings = get_store_and_settings()
    logger.info("Starting CLI build-index model=%s", model)
    path = build_index(store, settings, model)
    logger.info("Finished CLI build-index model=%s path=%s", model, path)
    typer.echo(f"index={path}")


@cli.command()
def similar(
    track_id: Annotated[int | None, typer.Option("--track-id")] = None,
    path: Annotated[Path | None, typer.Option("--path", exists=True, dir_okay=False)] = None,
    model: Annotated[str, typer.Option("--model")] = "discogs_multi",
    k: Annotated[int, typer.Option("--k")] = 30,
    max_per_artist: Annotated[int, typer.Option("--max-per-artist")] = 2,
    exclude_same_album: Annotated[bool, typer.Option("--exclude-same-album/--include-same-album")] = True,
) -> None:
    """Print similar tracks for a seed track."""
    store, settings = get_store_and_settings()
    logger.info(
        "Starting CLI similar track_id=%s path=%s model=%s k=%s max_per_artist=%s exclude_same_album=%s",
        track_id,
        path,
        model,
        k,
        max_per_artist,
        exclude_same_album,
    )
    seed = store.get_track(track_id) if track_id is not None else None
    if seed is None and path is not None:
        seed = store.find_track_by_path(path)
    if seed is None:
        logger.warning("CLI similar seed not found track_id=%s path=%s", track_id, path)
        raise typer.BadParameter("Provide a known --track-id or --path")

    results = Recommender(store, settings, model).similar(
        seed,
        k=k,
        max_per_artist=max_per_artist,
        exclude_same_album=exclude_same_album,
    )
    typer.echo(f"Seed: {seed.artist or ''} - {seed.title or seed.path}")
    logger.info("Finished CLI similar seed_id=%s model=%s results=%s", seed.id, model, len(results))
    for item in results:
        track = item.track
        typer.echo(
            f"{item.similarity:.3f}  {track.id}  "
            f"{track.artist or ''} - {track.title or Path(track.path).stem}"
        )


@cli.command()
def stats(model: Annotated[str, typer.Option("--model")] = "discogs_multi") -> None:
    """Print catalog stats."""
    store, settings = get_store_and_settings()
    typer.echo(f"db={settings.db_path}")
    typer.echo(f"tracks={store.count_tracks()}")
    typer.echo(f"embeddings[{model}]={store.count_embeddings(model)}")
    typer.echo(f"head_pack_outputs={store.count_model_outputs()}")
    typer.echo(
        "missing_head_pack_tracks="
        f"{store.count_tracks_missing_head_pack([head.id for head in DISCOGS_EFFNET_HEADS])}"
    )
    typer.echo(f"head_pack_ready={head_pack_readiness(settings)['ready']}")
    typer.echo(f"audio_features={store.count_feature_tracks(AUDIO_FEATURE_EXTRACTOR)}")
    typer.echo(
        f"missing_audio_features={store.count_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR)}"
    )
    typer.echo(f"index={settings.index_path(model)}")


def main() -> None:
    configure_logging()
    cli()


if __name__ == "__main__":
    main()
