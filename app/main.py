from __future__ import annotations

import base64
import binascii
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
import json
import logging
import multiprocessing
import os
from pathlib import Path
import socket
import sqlite3
from threading import Thread
import time
from time import perf_counter
from datetime import UTC, datetime, timedelta
import traceback
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
import numpy as np
from starlette.background import BackgroundTask

from app.audio_features import AUDIO_FEATURE_EXTRACTOR, AudioFeatureAnalyzer
from app.audio_source import has_navidrome_audio_source, navidrome_item_id_for_track, track_audio_path
from app.autoplay import (
    autoplay_default_settings,
    refill_autoplay_queue,
)
from app.config import (
    DISCOGS_EFFNET_MODEL,
    MODEL_FILES,
    MUQ_MULAN_MODEL,
    Settings,
    load_runtime_settings,
    save_runtime_settings,
)
from app.embedder import DiscogsEffnetEmbedder, MuqMulanEmbedder, create_track_embedder
from app.head_pack import (
    DISCOGS_EFFNET_HEADS,
    DiscogsEffnetHeadPackAnalyzer,
    HeadOutput,
    download_model_file,
    download_head_pack_models,
    head_pack_readiness,
    required_model_files,
)
from app.logging_config import configure_logging, get_analysis_logger, get_navidrome_plugin_logger
from app.mixes import (
    dashboard_mix_generation_plan,
    ensure_dashboard_mixes,
    generate_mixes,
    generated_mix_default_settings,
)
from app.navidrome import NavidromeClient, artist_info_bio, artist_info_image_url
from app.navidrome_starred import (
    build_starred_catalog,
    build_starred_track_ids,
    ready_tracks_from_starred_catalog,
)
from app.navidrome_sync import sync_navidrome_catalog
from app.recommender import Recommender, build_index, index_metadata_path
from app.models import (
    AnalysisTask,
    Artist,
    ArtistSummaryRow,
    COMPLETION_FRACTION,
    EARLY_SKIP_FRACTION,
    EARLY_SKIP_SECONDS,
    FeatureFilter,
    FeatureTrack,
    InstantMixRequest,
    LATE_SKIP_FRACTION,
    MEANINGFUL_LISTEN_FRACTION,
    MEANINGFUL_LISTEN_SECONDS,
    ReleaseSummaryRow,
    ReleaseTrackRow,
    Track,
    TrackFeature,
    TrackPrediction,
    utc_now,
)
from app.store import (
    Store,
    row_to_track,
    playback_event_is_completion,
    similar_track_dict,
    track_dict,
    track_listing_dict,
)


configure_logging()
logger = logging.getLogger(__name__)
analysis_logger = get_analysis_logger()
navidrome_logger = logging.getLogger("discocs.navidrome")
navidrome_plugin_logger = get_navidrome_plugin_logger()
app = FastAPI(title="discocs", version="0.1.0")

from app.state import (  # noqa: E402  (import after app init to avoid circular issues)
    ACTIVE_JOB_STATUSES,
    ANALYZE_EXECUTORS,
    ANALYZE_EXECUTORS_LOCK,
    AUTO_INDEX_ANALYSIS_JOBS,
    AUTO_INDEX_LOCK,
    COVER_CACHE,
    COVER_CACHE_LOCK,
    COVER_CACHE_MAX_ITEMS,
    COVER_CACHE_TTL_SECONDS,
    COVER_ERROR_CACHE,
    COVER_ERROR_CACHE_TTL_SECONDS,
    COVER_TIMEOUT_SECONDS,
    DEFAULT_ANALYZE_TF_THREADS,
    DEFAULT_ANALYZE_WORKERS,
    DEFAULT_AUDIO_FEATURE_WORKERS,
    DEFERRED_JOB_ORDER,
    DEFERRED_JOB_STARTERS,
    DEFERRED_JOBS_LOCK,
    JOBS,
    JOBS_LOCK,
    MAINTENANCE_STOP,
    MAX_ANALYZE_TF_THREADS,
    MAX_ANALYZE_WORKERS,
    MAX_AUDIO_FEATURE_WORKERS,
    MAX_MIX_SEEDS,
    MIX_GENERATION_LOCK,
    SHUTDOWN_REQUESTED,
    STATS_CACHE,
    STATS_CACHE_LOCK,
    STATS_CACHE_TTL_SECONDS,
    TEXT_SEARCH_EMBEDDER,
    TEXT_SEARCH_EMBEDDER_LOCK,
    UI_BUILD_ID,
    WORKER_CONNECTED_TTL_SECONDS,
    WORKER_HEARTBEAT_WRITE_INTERVAL_SECONDS,
)


from app.schemas.responses import (  # noqa: E402
    ApiErrorDetail,
    ApiErrorResponse,
    ArtistAvailabilityStubResponse,
    ArtistDiscographyResponse,
    ArtistLinkResponse,
    ArtistResponse,
    ArtistSummaryResponse,
    AutoplayRefillResponse,
    AvailabilityStubResponse,
    DiscographyGroupResponse,
    EntityActionResponse,
    ImageInfoResponse,
    ImageRefResponse,
    LibraryStatsResponse,
    NavidromeSimilarItem,
    NavidromeSimilarResponse,
    PlaybackEventIngestResponse,
    PlaybackEventSummaryResponse,
    PlaybackQueueItemResponse,
    PlaybackQueueResponse,
    PlaybackSessionEnvelopeResponse,
    PlaybackSessionSummaryResponse,
    PlaybackSettingsResponse,
    RelatedDiscographyResponse,
    ReleaseAvailabilityStubResponse,
    ReleaseResponse,
    ReleaseSummaryResponse,
    ReleaseTrackItemResponse,
    ReleaseTracksResponse,
    SearchGroupResponse,
    SearchResponse,
    SearchTopResultResponse,
    TrackReleaseLinkResponse,
    TrackSummaryResponse,
)
from app.schemas.requests import (  # noqa: E402
    AnalyzeAudioFeaturesRequest,
    AnalyzeHeadsRequest,
    AnalyzeRequest,
    AutoplayRefillRequest,
    CancelJobRequest,
    DeleteAnalysisErrorsRequest,
    DeleteTracksRequest,
    FeedbackRequest,
    FeatureFilterRequest,
    FeatureSearchRequest,
    GeneratedMixSettingsRequest,
    IndexRequest,
    InstantMixSettingsRequest,
    MixGenerateRequest,
    NavidromePluginEventRequest,
    NavidromeSettingsRequest,
    NavidromeStarRequest,
    NavidromeSyncRequest,
    PlaybackEventRequest,
    PlaybackQueueItemRequest,
    PlaybackQueuePatchRequest,
    PlaybackSessionCreateRequest,
    PlaybackSessionPatchRequest,
    TextSearchRequest,
    WorkerClaimRequest,
    WorkerFailureItem,
    WorkerFailuresRequest,
    WorkerFeatureItem,
    WorkerFeatureResultItem,
    WorkerHeadOutputItem,
    WorkerHeadResultItem,
    WorkerPredictionItem,
    WorkerRegisterRequest,
    WorkerReleaseRequest,
    WorkerResultItem,
    WorkerSubmitRequest,
)


def should_log_http_request(path: str) -> bool:
    if path in {"/stats", "/jobs"}:
        return True
    if path.startswith(("/metrics", "/navidrome", "/instant-mix", "/text-search")):
        return True
    return path.startswith("/tracks/") and (
        path.endswith("/cover")
        or path.endswith("/similar")
        or path.endswith("/navidrome-star")
    )


@app.middleware("http")
async def log_http_request(request: Request, call_next):
    path = request.url.path
    should_log = should_log_http_request(path)
    started = perf_counter()
    if should_log:
        logger.info(
            "HTTP request started method=%s path=%s query=%s",
            request.method,
            path,
            request.url.query,
        )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "HTTP request failed method=%s path=%s query=%s seconds=%.3f",
            request.method,
            path,
            request.url.query,
            perf_counter() - started,
        )
        raise
    seconds = perf_counter() - started
    if should_log or seconds >= 1.0:
        logger.info(
            "HTTP request completed method=%s path=%s status=%s seconds=%.3f",
            request.method,
            path,
            response.status_code,
            seconds,
        )
    return response


@dataclass
class JobStatus:
    id: str
    kind: str
    status: str
    message: str
    total: int | None = None
    done: int = 0
    failed: int = 0
    current: str | None = None
    elapsed_seconds: float = 0.0
    tracks_per_min: float | None = None
    eta_seconds: float | None = None
    error_detail: str | None = None
    started_at: float = 0.0
    created_at_epoch: float = 0.0
    finished_at: float | None = None




def context() -> tuple[Store, Settings]:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    store.init()
    return store, settings


def instant_mix_settings(settings: Settings) -> dict[str, object]:
    saved = load_runtime_settings(settings.data_dir).get("instant_mix", {})
    saved = saved if isinstance(saved, dict) else {}
    return {
        "model": str(saved.get("model") or "discogs_multi"),
        "count": _bounded_int(saved.get("count"), default=50, minimum=1, maximum=500),
        "min_similarity": _optional_bounded_float(
            saved.get("min_similarity"),
            default=None,
            minimum=0.0,
            maximum=1.0,
        ),
        "max_per_artist": _bounded_int(
            saved.get("max_per_artist"),
            default=2,
            minimum=1,
            maximum=100,
        ),
        "exclude_same_album": bool(saved.get("exclude_same_album", True)),
        "count_collaboration_artists": bool(saved.get("count_collaboration_artists", True)),
    }


def generated_mix_settings(settings: Settings) -> dict[str, object]:
    runtime = load_runtime_settings(settings.data_dir)
    saved = runtime.get("generated_mixes", runtime.get("mixes", {}))
    saved = saved if isinstance(saved, dict) else {}
    return {**generated_mix_default_settings(), **saved}


def ensure_dashboard_mixes_fast(store: Store, settings: Settings) -> dict[str, object]:
    mix_settings = generated_mix_settings(settings)
    model = str(mix_settings.get("mix_model") or "discogs_multi")
    embedding_count = store.count_embeddings(model)
    plan = dashboard_mix_generation_plan(store, mix_settings)
    if not plan.should_generate:
        diagnostics = dict(plan.diagnostics)
        diagnostics.update({"background": False, "embedding_count": embedding_count})
        return diagnostics
    if embedding_count <= 5000:
        return ensure_dashboard_mixes(store, settings, mix_settings).diagnostics
    started = _start_dashboard_mix_generation(settings.db_path, settings)
    diagnostics = dict(plan.diagnostics)
    diagnostics.update({
        "reason": "scheduled" if started else "already_running",
        "generated_count": 0,
        "background": True,
        "embedding_count": embedding_count,
    })
    return diagnostics


def _start_dashboard_mix_generation(db_path: Path, settings: Settings) -> bool:
    if not MIX_GENERATION_LOCK.acquire(blocking=False):
        return False

    def run() -> None:
        try:
            logger.info("Background generated mix refresh started db_path=%s", db_path)
            background_store = Store(db_path)
            background_store.init()
            result = ensure_dashboard_mixes(background_store, settings, generated_mix_settings(settings))
            logger.info(
                "Background generated mix refresh finished generated=%s reason=%s",
                result.diagnostics.get("generated_count"),
                result.diagnostics.get("reason"),
            )
        except Exception:
            logger.exception("Background generated mix refresh failed")
        finally:
            MIX_GENERATION_LOCK.release()

    Thread(target=run, name="generated-mix-refresh", daemon=True).start()
    return True


def playback_settings_defaults() -> dict[str, object]:
    settings = {
        "meaningful_listen_seconds": MEANINGFUL_LISTEN_SECONDS,
        "meaningful_listen_fraction": MEANINGFUL_LISTEN_FRACTION,
        "early_skip_seconds": EARLY_SKIP_SECONDS,
        "early_skip_fraction": EARLY_SKIP_FRACTION,
        "late_skip_fraction": LATE_SKIP_FRACTION,
        "completion_fraction": COMPLETION_FRACTION,
        "progress_event_frequency_seconds": 10,
        "visible_queue_size": 25,
    }
    settings.update(autoplay_default_settings())
    settings.update(generated_mix_default_settings())
    return settings


def playback_session_settings(request_settings: dict[str, object]) -> dict[str, object]:
    settings = playback_settings_defaults()
    settings.update(request_settings)
    return settings


def request_field_names(model: BaseModel) -> set[str]:
    if hasattr(model, "model_fields_set"):
        return set(model.model_fields_set)
    return set(getattr(model, "__fields_set__", set()))


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _optional_bounded_float(
    value: object,
    *,
    default: float | None,
    minimum: float,
    maximum: float,
) -> float | None:
    if value is None:
        return default
    if value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def instant_mix_result_dict(
    store: Store,
    raw: dict[str, object],
    ratings: dict[int, int],
) -> dict[str, object]:
    track_id = raw.get("track_id")
    try:
        parsed_track_id = int(track_id) if track_id is not None else None
    except (TypeError, ValueError):
        parsed_track_id = None
    if parsed_track_id is None:
        return raw
    track = store.get_track(parsed_track_id)
    if track is None:
        return raw
    data = enriched_track_dict(store, track)
    data["item_id"] = raw.get("item_id")
    data["distance"] = raw.get("distance")
    data["similarity"] = raw.get("similarity")
    data["rating"] = ratings.get(parsed_track_id)
    data["has_embedding"] = True
    return data


def instant_mix_request_dict(
    request: InstantMixRequest,
    *,
    include_results: bool,
    store: Store | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {
        "id": request.id,
        "provider": request.provider,
        "seed_item_id": request.seed_item_id,
        "seed_track_id": request.seed_track_id,
        "model": request.model_name,
        "requested_count": request.requested_count,
        "effective_count": request.effective_count,
        "max_per_artist": request.max_per_artist,
        "exclude_same_album": request.exclude_same_album,
        "min_similarity": request.min_similarity,
        "status": request.status,
        "result_count": request.result_count,
        "skipped_without_external_id": request.skipped_without_external_id,
        "duration_ms": request.duration_ms,
        "error": request.error,
        "created_at": request.created_at,
        "params": json.loads(request.params_json or "{}"),
    }
    if store is not None and request.seed_track_id is not None:
        seed_track = store.get_track(request.seed_track_id)
        if seed_track is not None:
            data["seed_track"] = enriched_track_dict(store, seed_track)
    if include_results:
        results = json.loads(request.results_json or "[]")
        if store is not None and request.seed_track_id is not None:
            ratings = store.feedback_for_seed(request.seed_track_id, request.model_name)
            results = [
                instant_mix_result_dict(store, result, ratings)
                if isinstance(result, dict)
                else result
                for result in results
            ]
        data["results"] = results
    return data


def record_instant_mix_request(
    store: Store,
    *,
    request_id: str,
    item_id: str,
    seed_track_id: int | None,
    model: str,
    requested_model: str | None,
    requested_count: int | None,
    effective_count: int,
    max_per_artist: int,
    exclude_same_album: bool,
    count_collaboration_artists: bool,
    min_similarity: float | None,
    status: str,
    results: list[NavidromeSimilarItem],
    skipped_without_external_id: int,
    duration_ms: float | None,
    requested_max_per_artist: int | None = None,
    requested_exclude_same_album: bool | None = None,
    provider: str = "navidrome",
    error: str | None = None,
) -> None:
    params = {
        "requested_model": requested_model,
        "effective_model": model,
        "requested_count": requested_count,
        "requested_max_per_artist": requested_max_per_artist,
        "requested_exclude_same_album": requested_exclude_same_album,
        "effective_count": effective_count,
        "max_per_artist": max_per_artist,
        "exclude_same_album": exclude_same_album,
        "count_collaboration_artists": count_collaboration_artists,
        "min_similarity": min_similarity,
    }
    store.record_instant_mix_request(
        request_id=request_id,
        provider=provider,
        seed_item_id=item_id,
        seed_track_id=seed_track_id,
        model_name=model,
        requested_count=requested_count,
        effective_count=effective_count,
        max_per_artist=max_per_artist,
        exclude_same_album=exclude_same_album,
        min_similarity=min_similarity,
        status=status,
        result_count=len(results),
        skipped_without_external_id=skipped_without_external_id,
        duration_ms=duration_ms,
        error=error,
        params_json=json.dumps(params, ensure_ascii=True, sort_keys=True),
        results_json=json.dumps([model_to_dict(item) for item in results], ensure_ascii=True),
    )


def model_to_dict(model: BaseModel) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def api_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@app.exception_handler(RequestValidationError)
async def api_v1_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    if not request.url.path.startswith("/api/v1"):
        return await request_validation_exception_handler(request, exc)
    errors = exc.errors()
    message = "Invalid request"
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.get("loc", []) if part != "query")
        detail = str(first.get("msg") or message)
        message = f"{location}: {detail}" if location else detail
    return api_error(422, "invalid_request", message)


def image_ref(url: str | None, source: str = "none") -> dict[str, object]:
    return {"url": url, "source": source if url else "none", "placeholder": url is None}


def entity_action(action_type: str, enabled: bool = True, endpoint: str | None = None) -> dict[str, object]:
    return {"type": action_type, "enabled": enabled, "endpoint": endpoint}


def release_type_label(release_type: str) -> str:
    return {
        "album": "Album",
        "ep": "EP",
        "single": "Single",
        "compilation": "Compilation",
        "soundtrack": "Soundtrack",
        "mix": "Mix",
    }.get(release_type, "Release")


def artist_summary_dict(row: ArtistSummaryRow | Artist) -> dict[str, object]:
    artist = row.artist if isinstance(row, ArtistSummaryRow) else row
    track_count = row.track_count if isinstance(row, ArtistSummaryRow) else 0
    release_count = row.release_count if isinstance(row, ArtistSummaryRow) else 0
    return {
        "id": artist.id,
        "name": artist.name,
        "image": image_ref(artist.image_url, "external" if artist.image_url else "none"),
        "library_stats": {
            "tracks": track_count,
            "releases": release_count,
            "liked_tracks": 0,
            "plays": 0,
        },
    }


def artist_summary_with_external_image(
    store: Store,
    settings: Settings,
    row: ArtistSummaryRow | Artist,
) -> dict[str, object]:
    artist = row.artist if isinstance(row, ArtistSummaryRow) else row
    if not artist.image_url:
        external_id = store.external_id_for_entity("navidrome", "artist", artist.id)
        if external_id:
            try:
                info = NavidromeClient(settings.navidrome).get_artist_info2(external_id, count=0)
                image_url = artist_info_image_url(info)
                bio = artist_info_bio(info)
                store.update_artist_external_info(artist.id, image_url=image_url, bio=bio)
                if image_url or bio:
                    refreshed = store.get_artist(artist.id)
                    if refreshed is not None:
                        row = refreshed
            except Exception as exc:
                logger.warning("Navidrome artist info lookup failed artist_id=%s external_id=%s: %s", artist.id, external_id, exc)
    return artist_summary_dict(row)


def artist_link_dict(artist: Artist) -> dict[str, object]:
    return {"id": artist.id, "name": artist.name}


def release_summary_dict(row: ReleaseSummaryRow) -> dict[str, object]:
    release = row.release
    cover_url = f"/api/v1/releases/{release.id}/cover" if release.cover_art_id else None
    return {
        "id": release.id,
        "title": release.title,
        "release_type": release.release_type,
        "release_type_label": release_type_label(release.release_type),
        "artists": [artist_link_dict(artist) for artist in row.artists],
        "release_date": release.release_date,
        "release_year": release.release_year,
        "track_count": release.track_count,
        "duration": release.duration,
        "artwork": image_ref(cover_url, "navidrome" if cover_url else "none"),
    }


def track_summary_dict(store: Store, track: Track, artists: list[Artist] | None = None) -> dict[str, object]:
    release = _track_release_summary(store, track.id)
    navidrome_item_id = store.external_id_for_track("navidrome", track.id)
    return {
        "id": track.id,
        "title": track.title or Path(track.path).stem,
        "artist": track.artist,
        "album": track.album,
        "artists": [artist_link_dict(artist) for artist in (artists or [])],
        "duration": track.duration,
        "release": release,
        "artwork": image_ref(f"/tracks/{track.id}/cover", "local"),
        "navidrome_item_id": navidrome_item_id,
        "explicit": False,
        "liked": False,
        "actions": [],
    }


def release_track_dict(store: Store, item: ReleaseTrackRow) -> dict[str, object]:
    data = track_summary_dict(store, item.track, item.artists)
    data.update(
        {
            "disc_number": item.disc_number,
            "track_number": item.track_number,
            "position": item.position,
        }
    )
    return data


def _track_release_summary(store: Store, track_id: int) -> dict[str, object] | None:
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT r.id, r.title
            FROM release_tracks rt
            JOIN releases r ON r.id = rt.release_id
            WHERE rt.track_id = ?
            ORDER BY rt.position
            LIMIT 1
            """,
            (track_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": int(row["id"]), "title": str(row["title"])}


def _json_object(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def playback_session_dict(store: Store, session) -> dict[str, object]:
    current_track = store.get_track(session.current_track_id) if session.current_track_id else None
    return {
        "id": session.id,
        "source_type": session.source_type,
        "source_id": session.source_id,
        "source_label": session.source_label,
        "mode": session.mode,
        "status": session.status,
        "current_track_id": session.current_track_id,
        "current_queue_item_id": session.current_queue_item_id,
        "current_track": track_summary_dict(store, current_track) if current_track else None,
        "autoplay_enabled": session.autoplay_enabled,
        "shuffle_enabled": session.shuffle_enabled,
        "repeat_mode": session.repeat_mode,
        "started_at": session.started_at,
        "updated_at": session.updated_at,
        "ended_at": session.ended_at,
        "settings": _json_object(session.settings_json),
        "state": _json_object(session.state_json),
    }


def queue_item_dict(
    store: Store,
    item,
    include_debug: bool = False,
    artists_by_track: dict[int, list[Artist]] | None = None,
) -> dict[str, object]:
    track = store.get_track(item.track_id)
    artists = artists_by_track.get(item.track_id, []) if artists_by_track is not None else []
    data: dict[str, object] = {
        "id": item.id,
        "session_id": item.session_id,
        "track_id": item.track_id,
        "track": track_summary_dict(store, track, artists) if track else None,
        "position": item.position,
        "origin": item.origin,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "status": item.status,
        "locked": item.locked,
        "reason": item.reason,
        "score": item.score,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_debug:
        data["debug"] = _json_object(item.debug_json)
    return data


def playback_queue_dict(store: Store, session, items, include_debug: bool = False) -> dict[str, object]:
    artists_by_track = store.artists_for_tracks([item.track_id for item in items])
    current_index = 0
    if session.current_queue_item_id:
        for index, item in enumerate(items):
            if item.id == session.current_queue_item_id:
                current_index = index
                break
    current_item = items[current_index] if items else None
    return {
        "items": [
            queue_item_dict(store, item, include_debug=include_debug, artists_by_track=artists_by_track)
            for item in items
        ],
        "current_index": current_index,
        "current_item": (
            queue_item_dict(store, current_item, include_debug=include_debug, artists_by_track=artists_by_track)
            if current_item
            else None
        ),
        "upcoming": [
            queue_item_dict(store, item, include_debug=include_debug, artists_by_track=artists_by_track)
            for item in items[current_index + 1 :]
        ],
        "played": [
            queue_item_dict(store, item, include_debug=include_debug, artists_by_track=artists_by_track)
            for item in items
            if item.status in {"played", "skipped"}
        ],
        "source_items": [
            queue_item_dict(store, item, include_debug=include_debug, artists_by_track=artists_by_track)
            for item in items
            if item.origin == "source"
        ],
        "generated_items": [
            queue_item_dict(store, item, include_debug=include_debug, artists_by_track=artists_by_track)
            for item in items
            if item.origin in {"autoplay", "flow", "generated_mix"}
        ],
        "autoplay_pool": autoplay_pool_dict(store, session, include_debug=include_debug),
    }


def autoplay_pool_dict(store: Store, session, include_debug: bool = False) -> list[dict[str, object]]:
    state = _json_object(session.state_json)
    raw_pool = state.get("autoplay_pool")
    if not isinstance(raw_pool, list):
        return []
    track_ids: list[int] = []
    for raw_item in raw_pool:
        if not isinstance(raw_item, dict):
            continue
        try:
            track_ids.append(int(raw_item["track_id"]))
        except (KeyError, TypeError, ValueError):
            continue
    artists_by_track = store.artists_for_tracks(track_ids)
    result: list[dict[str, object]] = []
    for index, raw_item in enumerate(raw_pool):
        if not isinstance(raw_item, dict):
            continue
        try:
            track_id = int(raw_item["track_id"])
        except (KeyError, TypeError, ValueError):
            continue
        track = store.get_track(track_id)
        if track is None:
            continue
        item: dict[str, object] = {
            "id": f"autoplay-pool-{track_id}",
            "session_id": session.id,
            "track_id": track_id,
            "track": track_summary_dict(store, track, artists_by_track.get(track_id, [])),
            "position": index,
            "origin": "autoplay_pool",
            "source_type": raw_item.get("source_type"),
            "source_id": raw_item.get("source_id"),
            "status": "prepared",
            "locked": False,
            "reason": raw_item.get("reason"),
            "score": raw_item.get("score"),
        }
        if include_debug and isinstance(raw_item.get("debug"), dict):
            item["debug"] = raw_item["debug"]
        result.append(item)
    return result


def playback_event_dict(event) -> dict[str, object]:
    return {
        "id": event.id,
        "session_id": event.session_id,
        "queue_item_id": event.queue_item_id,
        "track_id": event.track_id,
        "release_id": event.release_id,
        "artist_id": event.artist_id,
        "event_type": event.event_type,
        "position_seconds": event.position_seconds,
        "duration_seconds": event.duration_seconds,
        "play_fraction": event.play_fraction,
        "created_at": event.created_at,
        "client_event_id": event.client_event_id,
        "source": event.source,
        "payload": _json_object(event.payload_json),
    }


def playback_event_time_ms(created_at: str) -> int | None:
    try:
        value = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def should_scrobble_navidrome_play(store: Store, result) -> bool:
    event = result.event
    if result.duplicate or event.track_id is None:
        return False
    if event.event_type == "play_threshold_reached":
        return True
    if event.event_type != "completed" or not playback_event_is_completion(
        event.position_seconds,
        event.duration_seconds,
        event.play_fraction,
    ):
        return False
    if not event.session_id:
        return True
    for prior in store.list_playback_events(event.session_id):
        if prior.id == event.id:
            continue
        if prior.event_type not in {"play_threshold_reached", "completed"}:
            continue
        same_queue_item = event.queue_item_id and prior.queue_item_id == event.queue_item_id
        same_track_without_queue = not event.queue_item_id and prior.track_id == event.track_id
        if same_queue_item or same_track_without_queue:
            return False
    return True


def navidrome_scrobble_submission(store: Store, result) -> tuple[bool, str] | None:
    event = result.event
    if result.duplicate:
        return None
    if event.track_id is None:
        return None
    if event.event_type == "track_started":
        return (False, "now_playing")
    if should_scrobble_navidrome_play(store, result):
        return (True, "submission")
    return None


def maybe_scrobble_navidrome_play(store: Store, settings: Settings, result) -> dict[str, object]:
    decision = navidrome_scrobble_submission(store, result)
    if decision is None:
        return {"status": "skipped", "reason": "event_not_scrobbleable"}
    submission, mode = decision
    track_id = result.event.track_id
    if track_id is None:
        return {"status": "skipped", "reason": "missing_track_id"}
    item_id = store.external_id_for_track("navidrome", track_id)
    if not item_id:
        return {"status": "skipped", "reason": "no_navidrome_mapping", "track_id": track_id}
    try:
        NavidromeClient(settings.navidrome).scrobble_song(
            item_id,
            played_at_ms=playback_event_time_ms(result.event.created_at),
            submission=submission,
        )
    except Exception as exc:
        navidrome_logger.warning(
            "Navidrome scrobble failed track_id=%s item_id=%s event_id=%s error=%s",
            track_id,
            item_id,
            result.event.id,
            exc,
        )
        return {"status": "failed", "mode": mode, "track_id": track_id, "item_id": item_id, "error": str(exc)}
    return {"status": "ok", "mode": mode, "track_id": track_id, "item_id": item_id, "submission": submission}


def playback_session_response(store: Store, session, include_debug: bool = False) -> dict[str, object]:
    queue_items = store.list_queue_items(session.id)
    return {
        "session": playback_session_dict(store, session),
        "queue": playback_queue_dict(store, session, queue_items, include_debug=include_debug),
    }


def build_initial_playback_queue(store: Store, request: PlaybackSessionCreateRequest) -> list[int]:
    if request.track_ids:
        return request.track_ids
    if request.track_id is not None:
        return [request.track_id]
    if request.source_type == "track":
        if request.source_id is None:
            raise ValueError("source_id is required for track playback sessions")
        return [request.source_id]
    if request.source_type == "release":
        if request.source_id is None:
            raise ValueError("source_id is required for release playback sessions")
        tracks = store.list_release_tracks(request.source_id)
        return [item.track.id for item in tracks]
    if request.source_type == "artist":
        if request.source_id is None:
            raise ValueError("source_id is required for artist playback sessions")
        with store.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT t.id
                FROM track_artists ta
                JOIN tracks t ON t.id = ta.track_id
                LEFT JOIN release_tracks rt ON rt.track_id = t.id
                WHERE ta.artist_id = ? AND ta.role = 'primary'
                ORDER BY COALESCE(rt.position, t.id), t.id
                LIMIT 100
                """,
                (request.source_id,),
            ).fetchall()
        return [int(row["id"]) for row in rows]
    return []


def queue_patch_items(request: PlaybackQueuePatchRequest) -> list[dict[str, object]]:
    if request.items:
        return [item.model_dump(exclude_none=True) for item in request.items]
    if request.track_ids:
        return [{"track_id": track_id, "origin": "manual"} for track_id in request.track_ids]
    if request.track_id is not None:
        return [{"track_id": request.track_id, "origin": "manual"}]
    return []


def search_group(group_type: str, title: str, items: list[dict[str, object]], total: int, limit: int, offset: int) -> dict[str, object]:
    next_offset = offset + limit if offset + limit < total else None
    return {
        "type": group_type,
        "title": title,
        "items": items,
        "total": total,
        "next_offset": next_offset,
    }


def _field_search_score(query: str, value: object) -> int:
    if not isinstance(value, str) or not query:
        return 0
    query_text = query.casefold()
    value_text = value.casefold()
    if value_text == query_text:
        return 100
    if value_text.startswith(query_text):
        return 80
    if query_text in value_text:
        return 50
    return 0


def _entity_search_score(query: str, entity_type: str, entity: dict[str, object]) -> int:
    score = _field_search_score(query, entity.get("name") or entity.get("title"))
    if entity_type in {"release", "track"}:
        for artist in entity.get("artists") or []:
            if isinstance(artist, dict):
                score = max(score, _field_search_score(query, artist.get("name")))
    if entity_type == "track":
        release = entity.get("release")
        if isinstance(release, dict):
            score = max(score, _field_search_score(query, release.get("title")))
    tie_breaker = {"artist": 3, "release": 2, "track": 1}.get(entity_type, 0)
    return score * 10 + tie_breaker if score else tie_breaker


def search_top_result(
    query: str,
    artists: list[dict[str, object]],
    releases: list[dict[str, object]],
    tracks: list[dict[str, object]],
) -> dict[str, object] | None:
    candidates: list[tuple[int, str, dict[str, object]]] = []
    for entity_type, items in (("artist", artists), ("release", releases), ("track", tracks)):
        for item in items:
            candidates.append((_entity_search_score(query, entity_type, item), entity_type, item))
    if not candidates:
        return None
    _score, entity_type, entity = max(candidates, key=lambda item: item[0])
    return {"entity_type": entity_type, "entity": entity}


def _compact_artist_names(artists: list[dict[str, object]]) -> str:
    names = [str(artist.get("name") or "") for artist in artists if artist.get("name")]
    return ", ".join(names) if names else "Unknown artist"


def dashboard_shelf_item(
    entity_type: str,
    entity_id: int,
    title: str,
    subtitle: str,
    target: str,
    *,
    artwork_url: str | None = None,
    play_source_type: str | None = None,
    play_source_id: int | None = None,
    reason: str | None = None,
    badges: list[str] | None = None,
    debug: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": f"{entity_type}:{entity_id}",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "title": title,
        "subtitle": subtitle,
        "artwork": image_ref(artwork_url, "local" if artwork_url else "none"),
        "action": {"type": "open", "target": target},
        "play_action": {
            "type": "play",
            "source_type": play_source_type or entity_type,
            "source_id": play_source_id or entity_id,
        },
        "badges": badges or [],
        "reason": reason,
        "debug": debug,
    }


def _release_shelf_item(row: ReleaseSummaryRow, reason: str | None = None) -> dict[str, object]:
    release = release_summary_dict(row)
    artwork = release.get("artwork") if isinstance(release.get("artwork"), dict) else {}
    return dashboard_shelf_item(
        "release",
        int(release["id"]),
        str(release["title"]),
        _compact_artist_names(list(release.get("artists") or [])),
        f"/releases/{release['id']}",
        artwork_url=str(artwork.get("url") or "") or None,
        play_source_type="release",
        play_source_id=int(release["id"]),
        reason=reason,
        badges=[str(release.get("release_type_label") or "Release")],
    )


def _track_shelf_item(store: Store, track: Track, artists: list[Artist], reason: str | None = None) -> dict[str, object]:
    summary = track_summary_dict(store, track, artists)
    release = summary.get("release") if isinstance(summary.get("release"), dict) else None
    target = f"/releases/{release['id']}" if release and release.get("id") else f"?view=recommendations&seed={track.id}"
    return dashboard_shelf_item(
        "track",
        track.id,
        str(summary["title"]),
        _compact_artist_names(list(summary.get("artists") or [])),
        target,
        artwork_url=f"/tracks/{track.id}/cover?size=512",
        play_source_type="track",
        play_source_id=track.id,
        reason=reason,
        badges=[str(release["title"])] if release and release.get("title") else [],
    )


def generated_mix_summary_dict(store: Store, mix) -> dict[str, object]:
    items = store.list_generated_mix_items(mix.id)
    anchor = _json_object(mix.anchor_json)
    settings = _json_object(mix.settings_json)
    score_summary = _json_object(mix.score_summary_json)
    subtitle_parts = [
        str(anchor.get("representative_artist") or ""),
        str(anchor.get("representative_album") or ""),
    ]
    subtitle = ", ".join(value for value in subtitle_parts if value) or f"{len(items)} tracks"
    artwork = (
        image_ref(f"/api/v1/mixes/{mix.id}/cover", "generated_mix")
        if mix.cover_path
        else (image_ref(f"/tracks/{items[0].track_id}/cover?size=512", "track") if items else image_ref(None, "none"))
    )
    return {
        "id": mix.id,
        "title": mix.title,
        "mix_type": mix.mix_type,
        "status": mix.status,
        "subtitle": subtitle,
        "track_count": len(items),
        "artwork": artwork,
        "anchor": anchor,
        "settings": settings,
        "score_summary": score_summary,
        "created_at": mix.created_at,
        "updated_at": mix.updated_at,
        "expires_at": mix.expires_at,
        "saved_playlist_id": mix.saved_playlist_id,
        "action": {"type": "open", "target": f"/mixes/{mix.id}"},
        "play_action": {"type": "post", "endpoint": f"/api/v1/mixes/{mix.id}/play"},
    }


def generated_mix_detail_dict(store: Store, mix) -> dict[str, object]:
    item_rows = store.list_generated_mix_items(mix.id)
    tracks = store.get_tracks([item.track_id for item in item_rows])
    artists_by_track = store.artists_for_tracks([item.track_id for item in item_rows])
    items: list[dict[str, object]] = []
    for item in item_rows:
        track = tracks.get(item.track_id)
        items.append(
            {
                "mix_id": item.mix_id,
                "position": item.position,
                "track_id": item.track_id,
                "track": track_summary_dict(store, track, artists_by_track.get(item.track_id, [])) if track else None,
                "score": item.score,
                "score_breakdown": _json_object(item.score_breakdown_json),
                "reason": _json_object(item.reason_json),
                "created_at": item.created_at,
            }
        )
    summary = generated_mix_summary_dict(store, mix)
    summary["items"] = items
    summary["actions"] = {
        "save": {"method": "POST", "endpoint": f"/api/v1/mixes/{mix.id}/save"},
        "play": {"method": "POST", "endpoint": f"/api/v1/mixes/{mix.id}/play"},
    }
    return summary


def _dashboard_generated_mixes(store: Store, limit: int, offset: int, include_debug: bool = False) -> tuple[list[dict[str, object]], int]:
    mixes = store.list_generated_mixes(statuses=["active", "saved"], limit=limit, offset=offset)
    items: list[dict[str, object]] = []
    for mix in mixes:
        summary = generated_mix_summary_dict(store, mix)
        item = {
            "id": f"generated_mix:{mix.id}",
            "entity_type": "generated_mix",
            "entity_id": mix.id,
            "title": summary["title"],
            "subtitle": summary["subtitle"],
            "artwork": summary["artwork"],
            "action": summary["action"],
            "play_action": summary["play_action"],
            "badges": [str(summary["track_count"]) + " tracks", str(mix.status)],
            "reason": "Generated from your taste regions",
        }
        if include_debug:
            item["debug"] = {
                "anchor": summary["anchor"],
                "score_summary": summary["score_summary"],
                "settings": summary["settings"],
            }
        items.append(item)
    return items, store.count_generated_mixes(statuses=["active", "saved"])


def _dashboard_recently_added(store: Store, limit: int, offset: int, include_debug: bool = False) -> tuple[list[dict[str, object]], int]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT r.id, COALESCE(r.added_at, MAX(COALESCE(t.added_at, t.created_at, rt.created_at, r.created_at))) AS added_at
            FROM releases r
            JOIN release_tracks rt ON rt.release_id = r.id
            JOIN tracks t ON t.id = rt.track_id
            WHERE t.missing_at IS NULL
            GROUP BY r.id
            ORDER BY added_at DESC, r.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM (
                SELECT r.id
                FROM releases r
                JOIN release_tracks rt ON rt.release_id = r.id
                JOIN tracks t ON t.id = rt.track_id
                WHERE t.missing_at IS NULL
                GROUP BY r.id
            )
            """
        ).fetchone()
    items: list[dict[str, object]] = []
    for row in rows:
        release = store.get_release(int(row["id"]))
        if release is None:
            continue
        item = _release_shelf_item(release, "New in collection")
        if include_debug:
            item["debug"] = {"added_at": row["added_at"]}
        items.append(item)
    return items, int(total_row["total"] if total_row else 0)


def _dashboard_listen_again(store: Store, limit: int, offset: int, include_debug: bool = False) -> tuple[list[dict[str, object]], int]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT t.*, p.liked, p.play_count, p.completion_count, p.replay_count, p.score,
                   p.last_played_at, p.last_completed_at, p.last_skipped_at
            FROM user_track_preferences p
            JOIN tracks t ON t.id = p.track_id
            WHERE t.missing_at IS NULL
              AND p.disliked = 0
              AND (p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0)
              AND (
                    p.last_skipped_at IS NULL
                    OR p.liked = 1
                    OR COALESCE(p.last_completed_at, p.last_played_at, p.updated_at) >= p.last_skipped_at
                  )
            ORDER BY p.liked DESC,
                     COALESCE(p.last_completed_at, p.last_played_at, p.updated_at) DESC,
                     p.score DESC,
                     t.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM user_track_preferences p
            JOIN tracks t ON t.id = p.track_id
            WHERE t.missing_at IS NULL
              AND p.disliked = 0
              AND (p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0)
              AND (
                    p.last_skipped_at IS NULL
                    OR p.liked = 1
                    OR COALESCE(p.last_completed_at, p.last_played_at, p.updated_at) >= p.last_skipped_at
                  )
            """
        ).fetchone()
    tracks = [row_to_track(row) for row in rows]
    artists_by_track = store.artists_for_tracks([track.id for track in tracks])
    items: list[dict[str, object]] = []
    for row, track in zip(rows, tracks, strict=True):
        if int(row["liked"] or 0):
            reason = "You liked this"
        elif int(row["replay_count"] or 0):
            reason = f"Replayed {int(row['replay_count'])} times"
        elif int(row["completion_count"] or 0):
            reason = f"Completed {int(row['completion_count'])} times"
        elif int(row["play_count"] or 0):
            reason = f"Played {int(row['play_count'])} times"
        else:
            reason = "Played before"
        item = _track_shelf_item(store, track, artists_by_track.get(track.id, []), reason)
        if include_debug:
            item["debug"] = {
                "score": row["score"],
                "last_played_at": row["last_played_at"],
                "last_completed_at": row["last_completed_at"],
                "last_skipped_at": row["last_skipped_at"],
            }
        items.append(item)
    return items, int(total_row["total"] if total_row else 0)


def _dashboard_long_time_no_listen(store: Store, limit: int, offset: int, include_debug: bool = False) -> tuple[list[dict[str, object]], int]:
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT t.*, p.liked, p.completion_count, p.replay_count, p.score,
                   p.last_played_at, p.last_completed_at, p.last_skipped_at
            FROM user_track_preferences p
            JOIN tracks t ON t.id = p.track_id
            WHERE t.missing_at IS NULL
              AND p.disliked = 0
              AND (p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0)
              AND p.last_played_at IS NOT NULL
              AND p.last_played_at < ?
              AND (p.last_skipped_at IS NULL OR p.last_skipped_at <= p.last_played_at OR p.liked = 1)
            ORDER BY p.liked DESC, p.last_played_at ASC, p.score DESC, t.id DESC
            LIMIT ? OFFSET ?
            """,
            (cutoff, limit, offset),
        ).fetchall()
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM user_track_preferences p
            JOIN tracks t ON t.id = p.track_id
            WHERE t.missing_at IS NULL
              AND p.disliked = 0
              AND (p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0)
              AND p.last_played_at IS NOT NULL
              AND p.last_played_at < ?
              AND (p.last_skipped_at IS NULL OR p.last_skipped_at <= p.last_played_at OR p.liked = 1)
            """,
            (cutoff,),
        ).fetchone()
    tracks = [row_to_track(row) for row in rows]
    artists_by_track = store.artists_for_tracks([track.id for track in tracks])
    items: list[dict[str, object]] = []
    for row, track in zip(rows, tracks, strict=True):
        item = _track_shelf_item(store, track, artists_by_track.get(track.id, []), "Long time since last listen")
        if include_debug:
            item["debug"] = {"cutoff": cutoff, "last_played_at": row["last_played_at"], "score": row["score"]}
        items.append(item)
    return items, int(total_row["total"] if total_row else 0)


def _discover_track_shelf_item(store: Store, track: Track, artists: list[Artist], reason: str | None = None) -> dict[str, object]:
    summary = track_summary_dict(store, track, artists)
    release = summary.get("release") if isinstance(summary.get("release"), dict) else None
    target = f"/releases/{release['id']}" if release and release.get("id") else f"?view=recommendations&seed={track.id}"
    item = dashboard_shelf_item(
        "track",
        track.id,
        str(summary["title"]),
        _compact_artist_names(list(summary.get("artists") or [])),
        target,
        artwork_url=f"/tracks/{track.id}/cover?size=512",
        play_source_type="track",
        play_source_id=track.id,
        reason=reason,
        badges=[str(release["title"])] if release and release.get("title") else [],
    )
    # Automatically trigger instant-mix for discover-random tracks on play click
    item["play_action"] = {
        "type": "post",
        "endpoint": f"/tracks/{track.id}/instant-mix"
    }
    return item


def _dashboard_discover_random(store: Store, limit: int, offset: int, include_debug: bool = False) -> tuple[list[dict[str, object]], int]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT t.*
            FROM tracks t
            LEFT JOIN user_track_preferences p ON p.track_id = t.id
            WHERE t.missing_at IS NULL
              AND (p.track_id IS NULL OR (p.disliked = 0 AND p.play_count = 0 AND p.last_played_at IS NULL))
            ORDER BY RANDOM()
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM tracks t
            LEFT JOIN user_track_preferences p ON p.track_id = t.id
            WHERE t.missing_at IS NULL
              AND (p.track_id IS NULL OR (p.disliked = 0 AND p.play_count = 0 AND p.last_played_at IS NULL))
            """
        ).fetchone()
    tracks = [row_to_track(row) for row in rows]
    artists_by_track = store.artists_for_tracks([track.id for track in tracks])
    items: list[dict[str, object]] = []
    for track in tracks:
        item = _discover_track_shelf_item(store, track, artists_by_track.get(track.id, []), "Never played before")
        if include_debug:
            item["debug"] = {"random": True}
        items.append(item)
    return items, int(total_row["total"] if total_row else 0)


def dashboard_shelf_response(
    store: Store,
    key: str,
    *,
    limit: int,
    offset: int,
    include_debug: bool = False,
) -> dict[str, object] | None:
    titles = {
        "recently_added": ("Recently Added", "New in your collection"),
        "listen_again": ("Listen Again", "Tracks with positive listening history"),
        "long_time_no_listen": ("Long Time No Listen", "Good tracks that fell out of rotation"),
        "mixes_for_you": ("Mixes For You", "Generated finite mixes from taste regions"),
        "discover_random": ("Discover Random", "Tracks you haven't played yet"),
    }
    if key not in titles:
        return None
    if key == "recently_added":
        items, total = _dashboard_recently_added(store, limit, offset, include_debug)
    elif key == "listen_again":
        items, total = _dashboard_listen_again(store, limit, offset, include_debug)
    elif key == "long_time_no_listen":
        items, total = _dashboard_long_time_no_listen(store, limit, offset, include_debug)
    elif key == "discover_random":
        items, total = _dashboard_discover_random(store, limit, offset, include_debug)
    else:
        items, total = _dashboard_generated_mixes(store, limit, offset, include_debug)
    title, subtitle = titles[key]
    next_offset = offset + limit if offset + limit < total else None
    return {
        "key": key,
        "title": title,
        "subtitle": subtitle,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "available": True,
    }


def create_job(kind: str, message: str) -> str:
    job_id = str(uuid4())
    now_epoch = time.time()
    with JOBS_LOCK:
        JOBS[job_id] = JobStatus(
            id=job_id,
            kind=kind,
            status="queued",
            message=message,
            started_at=perf_counter(),
            created_at_epoch=now_epoch,
        )
    logger.info("Created job job_id=%s kind=%s message=%s", job_id, kind, message)
    return job_id


def update_job(job_id: str, **changes: object) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        for key, value in changes.items():
            setattr(job, key, value)


def finish_job(job_id: str, status: str, message: str, error_detail: str | None = None) -> None:
    finished_at = perf_counter()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        elapsed_seconds = max(0.0, finished_at - job.started_at) if job else 0.0
    update_job(
        job_id,
        status=status,
        message=message,
        current=None,
        elapsed_seconds=elapsed_seconds,
        eta_seconds=None,
        error_detail=error_detail,
        finished_at=finished_at,
    )
    log = logger.error if status == "failed" else logger.info
    log("Finished job job_id=%s status=%s message=%s", job_id, status, message)
    maybe_start_next_deferred_job()


def has_active_job(store: Store | None = None) -> bool:
    with JOBS_LOCK:
        if any(job.status in {"queued", "running"} for job in JOBS.values()):
            return True
    if store is None:
        try:
            store, _settings = context()
        except Exception:
            logger.exception("Failed to inspect durable jobs while checking active queue state")
            return True
    return store.has_active_analysis_job()


def create_deferred_job_if_busy(
    kind: str,
    message: str,
    starter_factory: Callable[[str], Callable[[], None]],
    *,
    store: Store | None = None,
) -> tuple[str | None, bool]:
    with DEFERRED_JOBS_LOCK:
        if not has_active_job(store):
            return None, False
        job_id = create_job(kind, message)
        DEFERRED_JOB_STARTERS[job_id] = starter_factory(job_id)
        DEFERRED_JOB_ORDER.append(job_id)
    update_job(
        job_id,
        status="deferred",
        message=f"Waiting for previous job: {message}",
        current=None,
    )
    logger.info("Deferred job job_id=%s kind=%s message=%s", job_id, kind, message)
    return job_id, True


def _run_deferred_job(job_id: str) -> None:
    with DEFERRED_JOBS_LOCK:
        starter = DEFERRED_JOB_STARTERS.get(job_id)
    if starter is None:
        return
    try:
        starter()
    finally:
        with DEFERRED_JOBS_LOCK:
            DEFERRED_JOB_STARTERS.pop(job_id, None)
            if job_id in DEFERRED_JOB_ORDER:
                DEFERRED_JOB_ORDER.remove(job_id)
        maybe_start_next_deferred_job()


def maybe_start_next_deferred_job() -> str | None:
    with DEFERRED_JOBS_LOCK:
        if has_active_job():
            return None
        next_job_id = None
        for candidate in list(DEFERRED_JOB_ORDER):
            with JOBS_LOCK:
                job = JOBS.get(candidate)
                is_waiting = job is not None and job.status == "deferred"
            if candidate in DEFERRED_JOB_STARTERS and is_waiting:
                next_job_id = candidate
                DEFERRED_JOB_ORDER.remove(candidate)
                break
            DEFERRED_JOB_STARTERS.pop(candidate, None)
            DEFERRED_JOB_ORDER.remove(candidate)
    if next_job_id is None:
        return None
    update_job(
        next_job_id,
        status="queued",
        message="Starting deferred job",
        current=None,
        started_at=perf_counter(),
    )
    Thread(target=_run_deferred_job, args=(next_job_id,), daemon=True).start()
    logger.info("Started deferred job job_id=%s", next_job_id)
    return next_job_id


def sync_memory_jobs_from_durable_jobs(jobs: list[object]) -> None:
    with JOBS_LOCK:
        for durable_job in jobs:
            memory_job = JOBS.get(durable_job.id)
            if memory_job is None:
                continue
            memory_job.status = durable_job.status
            memory_job.message = durable_job.message
            memory_job.total = durable_job.total
            memory_job.done = durable_job.done
            memory_job.failed = durable_job.failed
            if durable_job.status not in {"queued", "running"}:
                memory_job.current = None
                memory_job.eta_seconds = None
                if memory_job.finished_at is None:
                    memory_job.finished_at = perf_counter()


def schedule_auto_index_for_analysis(
    store: Store,
    analysis_job_id: str,
    background_tasks: BackgroundTasks | None = None,
) -> str | None:
    job = store.get_analysis_job(analysis_job_id)
    if job is None or job.kind != "analyze" or job.status != "completed" or job.done <= 0:
        return None
    with AUTO_INDEX_LOCK:
        if analysis_job_id in AUTO_INDEX_ANALYSIS_JOBS:
            return None
        AUTO_INDEX_ANALYSIS_JOBS.add(analysis_job_id)

    deferred_job_id, deferred = create_deferred_job_if_busy(
        "index",
        f"Waiting to rebuild index for {job.model_name} after analyze",
        lambda job_id: lambda: _index_job(job_id, job.model_name),
        store=store,
    )
    if deferred:
        logger.info(
            "Deferred auto index job analysis_job_id=%s index_job_id=%s model=%s",
            analysis_job_id,
            deferred_job_id,
            job.model_name,
        )
        return deferred_job_id

    index_job_id = create_job(
        "index",
        f"Waiting to rebuild index for {job.model_name} after analyze",
    )
    logger.info(
        "Scheduled auto index job analysis_job_id=%s index_job_id=%s model=%s",
        analysis_job_id,
        index_job_id,
        job.model_name,
    )
    if background_tasks is not None:
        background_tasks.add_task(_index_job, index_job_id, job.model_name)
    else:
        _index_job(index_job_id, job.model_name)
    return index_job_id


def exception_detail(exc: Exception) -> str:
    parts = [
        "".join(traceback.format_exception_only(type(exc), exc)).strip(),
        f"repr: {exc!r}",
        f"args: {exc.args!r}",
    ]
    reason = getattr(exc, "reason", None)
    if reason is not None:
        parts.append(f"reason: {reason!r}")
    return "\n".join(part for part in parts if part)


def exception_traceback(exc: Exception) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()


def is_sqlite_locked(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower()


def is_sqlite_disk_io_error(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "disk i/o error" in str(exc).lower()


def sqlite_retry(operation, *, attempts: int = 8):
    delay = 0.05
    for attempt in range(attempts):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not is_sqlite_locked(exc) or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
    raise RuntimeError("unreachable sqlite retry state")


def raise_worker_sqlite_http_exception(exc: sqlite3.OperationalError, action: str) -> None:
    if is_sqlite_locked(exc):
        raise HTTPException(status_code=503, detail=f"SQLite is busy; retry worker {action}") from exc
    if is_sqlite_disk_io_error(exc):
        logger.error("SQLite disk I/O error during worker %s", action, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="SQLite disk I/O error; check database storage and retry",
        ) from exc
    raise exc


def download_failure_hint(exc: Exception) -> str | None:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, socket.gaierror):
        return (
            "DNS lookup failed in the server runtime. Check network/DNS access from the "
            "machine or container running discocs, or place the model files in models/ manually."
        )
    return None


def analyze_progress(started_at: float, total: int, done: int, failed: int) -> dict[str, object]:
    elapsed = max(0.0, perf_counter() - started_at)
    completed = done + failed
    tracks_per_min = (completed / elapsed) * 60 if elapsed > 0 and completed > 0 else None
    eta_seconds = None
    if tracks_per_min and total > completed:
        eta_seconds = ((total - completed) / tracks_per_min) * 60
    return {
        "elapsed_seconds": elapsed,
        "tracks_per_min": tracks_per_min,
        "eta_seconds": eta_seconds,
    }


def head_pack_status(store: Store, settings: Settings) -> dict[str, object]:
    readiness = head_pack_readiness(settings)
    head_names = [head.id for head in DISCOGS_EFFNET_HEADS]
    tracks = store.count_tracks()
    saved_outputs = store.count_model_outputs()
    expected_outputs = tracks * len(head_names)
    missing_tracks = store.count_tracks_missing_head_pack(head_names)
    return {
        **readiness,
        "track_count": tracks,
        "expected_outputs": expected_outputs,
        "saved_outputs": saved_outputs,
        "complete_tracks": max(tracks - missing_tracks, 0),
        "missing_tracks": missing_tracks,
        "per_head_output_counts": store.count_model_outputs_by_model(head_names),
    }


def audio_feature_status(store: Store) -> dict[str, object]:
    tracks = store.count_tracks()
    complete = store.count_feature_tracks(AUDIO_FEATURE_EXTRACTOR)
    missing = store.count_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR)
    return {
        "extractor": AUDIO_FEATURE_EXTRACTOR,
        "track_count": tracks,
        "complete_tracks": complete,
        "missing_tracks": missing,
    }


def recommender_index_status(
    settings: Settings,
    model: str,
    embedding_count: int,
) -> dict[str, object]:
    path = settings.index_path(model)
    metadata_path = index_metadata_path(path)
    metadata: dict[str, object] = {}
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            metadata = {}

    raw_index_count = metadata.get("count")
    try:
        index_count = int(raw_index_count) if raw_index_count is not None else None
    except (TypeError, ValueError):
        index_count = None

    exists = path.exists()
    metadata_matches_model = metadata.get("model_name") == model
    if not exists:
        status = "missing"
    elif index_count is None or not metadata_matches_model:
        status = "unknown"
    elif index_count == embedding_count:
        status = "ready"
    else:
        status = "stale"

    return {
        "status": status,
        "exists": exists,
        "path": str(path),
        "metadata_path": str(metadata_path),
        "metadata_exists": metadata_path.exists(),
        "count": index_count,
        "embedding_count": embedding_count,
        "stale": status in {"stale", "unknown"},
    }


def analysis_error_count(store: Store) -> int:
    where = """
        t.error IS NOT NULL
        AND COALESCE(t.error_type, '') != 'Cancelled'
        AND COALESCE(t.stage, '') != 'cancelled'
        AND t.error NOT LIKE 'Model file not found:%'
    """
    with store.connect() as conn:
        return conn.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT t.track_id, t.model_name
                FROM analysis_tasks t
                JOIN tracks tr ON tr.id = t.track_id
                WHERE {where}
                GROUP BY t.track_id, t.model_name
            )
            """
        ).fetchone()[0]


def prediction_dict(prediction) -> dict[str, object]:
    return {
        "label": prediction.label,
        "score": prediction.score,
        "rank": prediction.rank,
    }


def feature_dict(feature) -> dict[str, object]:
    return {
        "name": feature.name,
        "value": feature.value,
        "text_value": feature.text_value,
        "unit": feature.unit,
        "confidence": feature.confidence,
        "extractor": feature.extractor,
    }


def feature_track_dict(item: FeatureTrack) -> dict[str, object]:
    data = track_dict(item.track)
    data["features"] = [feature_dict(feature) for feature in item.features]
    return data


def enriched_feature_track_dict(store: Store, item: FeatureTrack) -> dict[str, object]:
    data = feature_track_dict(item)
    data.update(track_card_metadata(store, item.track))
    return data


def track_card_metadata(store: Store, track: Track) -> dict[str, object]:
    features = store.load_features(track.id, AUDIO_FEATURE_EXTRACTOR)
    feature_by_name = {feature.name: feature for feature in features}
    navidrome_item_id = store.external_id_for_track("navidrome", track.id)
    raw = navidrome_raw_metadata(store, track)
    return {
        "navidrome_item_id": navidrome_item_id,
        "card_features": {
            name: feature_dict(feature)
            for name, feature in feature_by_name.items()
            if name in {"bpm", "key", "scale"}
        },
        "genre_discogs400": [
            prediction_dict(prediction)
            for prediction in store.load_predictions(track.id, "genre_discogs400", limit=3)
        ],
        "approachability_3c": first_prediction_dict(
            store.load_predictions(track.id, "approachability_3c", limit=3),
            "approachable",
        ),
        "engagement_3c": first_prediction_dict(
            store.load_predictions(track.id, "engagement_3c", limit=3),
            "engaging",
        ),
        "audio_format": audio_format(track, raw),
        "bitrate": audio_bitrate(track, raw),
    }


def first_prediction_dict(
    predictions: list[TrackPrediction],
    preferred_label: str,
) -> dict[str, object] | None:
    preferred = preferred_label.strip().lower()
    for prediction in predictions:
        if prediction.label.strip().lower() == preferred:
            return prediction_dict(prediction)
    return None


def navidrome_raw_metadata(store: Store, track: Track) -> dict[str, object]:
    external_id = store.external_id_for_track("navidrome", track.id)
    if external_id is None:
        return {}
    mapping = store.get_external_track("navidrome", external_id)
    if mapping is None or not mapping.raw_json:
        return {}
    try:
        parsed = json.loads(mapping.raw_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def audio_format(track: Track, raw: dict[str, object]) -> str | None:
    suffix = raw.get("suffix") or raw.get("format")
    if suffix:
        return str(suffix).strip(". ").upper() or None
    content_type = raw.get("contentType") or raw.get("content_type")
    if content_type:
        clean = str(content_type).split("/")[-1].strip()
        return clean.upper() if clean else None
    path_suffix = Path(track.path).suffix
    return path_suffix.strip(".").upper() if path_suffix else None


def audio_bitrate(track: Track, raw: dict[str, object]) -> int | None:
    for key in ("bitRate", "bitrate", "bit_rate"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            bitrate = int(float(value))
        except (TypeError, ValueError):
            continue
        if bitrate > 0:
            return bitrate
    if track.duration and track.file_size > 0:
        return max(1, round((track.file_size * 8) / (float(track.duration) * 1000)))
    return None


def enriched_track_dict(store: Store, track: Track) -> dict[str, object]:
    data = track_dict(track)
    data.update(track_card_metadata(store, track))
    return data


def enriched_track_listing_dict(store: Store, listing) -> dict[str, object]:
    data = track_listing_dict(listing)
    data["navidrome_item_id"] = store.external_id_for_track("navidrome", listing.track.id)
    return data


def enriched_similar_track_dict(store: Store, result) -> dict[str, object]:
    data = similar_track_dict(result)
    data.update(track_card_metadata(store, result.track))
    return data


def text_search_embedder(settings: Settings) -> MuqMulanEmbedder:
    global TEXT_SEARCH_EMBEDDER
    with TEXT_SEARCH_EMBEDDER_LOCK:
        if TEXT_SEARCH_EMBEDDER is None:
            TEXT_SEARCH_EMBEDDER = MuqMulanEmbedder(settings)
        return TEXT_SEARCH_EMBEDDER


def analysis_task_dict(task: AnalysisTask) -> dict[str, object]:
    return {
        "task_id": task.id,
        "job_id": task.job_id,
        "track_id": task.track_id,
        "model_name": task.model_name,
        "status": task.status,
        "attempts": task.attempts,
        "max_attempts": task.max_attempts,
        "lease_owner": task.lease_owner,
        "lease_expires_at": task.lease_expires_at,
        "path": task.path,
        "file_size": task.file_size,
        "mtime": task.mtime,
        "error": task.error,
        "error_type": task.error_type,
        "stage": task.stage,
        "audio_url": f"/workers/tasks/{task.id}/audio",
    }


def timestamp_from_iso(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def analysis_worker_age_seconds(worker, now: datetime | None = None) -> float | None:
    try:
        last_seen = datetime.fromisoformat(worker.last_seen_at)
    except (TypeError, ValueError):
        return None
    current = now or datetime.now(last_seen.tzinfo)
    return max(0.0, (current - last_seen).total_seconds())


def analysis_worker_connected(worker, now: datetime | None = None) -> bool:
    age = analysis_worker_age_seconds(worker, now)
    return worker.status == "online" and age is not None and age <= WORKER_CONNECTED_TTL_SECONDS


def analysis_job_status_dict(
    job,
    store: Store | None = None,
    *,
    detail: bool = True,
    workers: list[object] | None = None,
) -> dict[str, object]:
    completed = job.done + job.failed
    started_at = timestamp_from_iso(job.created_at)
    now_ts = datetime.now().timestamp()
    finished_at = timestamp_from_iso(job.finished_at) if job.finished_at else None
    terminal = job.status not in {"queued", "running"}
    elapsed_until = finished_at or (timestamp_from_iso(job.updated_at) if terminal else now_ts)
    elapsed_seconds = max(0.0, elapsed_until - started_at) if started_at else 0.0
    tracks_per_min = (
        (completed / elapsed_seconds) * 60
        if job.status == "running" and elapsed_seconds > 0 and completed > 0
        else None
    )
    recent_tracks_per_min = None
    recent_window_seconds = None
    recent_completed = None
    if detail and store is not None and job.status == "running" and started_at:
        recent_window_seconds = min(300.0, max(1.0, now_ts - started_at))
        recent_since = datetime.fromtimestamp(now_ts - recent_window_seconds, UTC).isoformat()
        recent_completed = store.count_recent_finished_analysis_tasks(job.id, recent_since)
        if recent_completed > 0:
            recent_tracks_per_min = (recent_completed / recent_window_seconds) * 60
    eta_seconds = None
    eta_rate = recent_tracks_per_min or tracks_per_min
    if eta_rate and job.total > completed and job.status == "running":
        eta_seconds = ((job.total - completed) / eta_rate) * 60
    task_summary = store.analysis_job_task_summary(job.id) if detail and store is not None else {}
    if detail and workers is None and store is not None:
        workers = store.list_analysis_workers()
    workers = workers or []
    connected_workers = [worker for worker in workers if analysis_worker_connected(worker)]
    supporting_workers = [
        worker.worker_id
        for worker in connected_workers
        if job.model_name in {model for model in worker.models.split(",") if model}
    ]
    online_workers = [
        {
            "worker_id": worker.worker_id,
            "models": [model for model in worker.models.split(",") if model],
            "stage": worker.stage,
            "message": worker.message,
            "last_seen_at": worker.last_seen_at,
        }
        for worker in connected_workers
    ]
    status_hint = ""
    if detail and job.status == "running":
        leased_workers = task_summary.get("leased_workers", [])
        if job.leased and leased_workers:
            labels = ", ".join(
                f"{item['worker_id']}({item['count']})" for item in leased_workers
            )
            status_hint = f"Processing on {labels}; queued {job.queued}, leased {job.leased}"
        elif job.queued:
            if supporting_workers:
                status_hint = (
                    f"Queued {job.queued}; waiting for claim by "
                    f"{', '.join(supporting_workers)}"
                )
            else:
                worker_labels = [
                    f"{worker['worker_id']}[{', '.join(worker['models'])}]"
                    for worker in online_workers
                ]
                suffix = f" Online workers: {', '.join(worker_labels)}" if worker_labels else " No workers online."
                status_hint = f"Waiting for worker supporting {job.model_name}.{suffix}"
    recent_errors = task_summary.get("recent_errors", [])
    oldest_lease = task_summary.get("oldest_lease")
    oldest_lease_age = None
    if isinstance(oldest_lease, dict) and oldest_lease.get("updated_at"):
        oldest_lease_age = max(0.0, datetime.now().timestamp() - timestamp_from_iso(str(oldest_lease["updated_at"])))
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "message": job.message,
        "total": job.total,
        "done": job.done,
        "failed": job.failed,
        "current": None,
        "elapsed_seconds": elapsed_seconds,
        "tracks_per_min": tracks_per_min,
        "recent_tracks_per_min": recent_tracks_per_min,
        "recent_rate_window_seconds": recent_window_seconds,
        "recent_rate_completed": recent_completed,
        "eta_seconds": eta_seconds,
        "error_detail": None,
        "started_at": started_at,
        "finished_at": finished_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "finished_at_iso": job.finished_at,
        "model": job.model_name,
        "queued": job.queued,
        "leased": job.leased,
        "final_failed": job.final_failed,
        "status_breakdown": task_summary.get("status_breakdown", []),
        "oldest_lease": oldest_lease,
        "oldest_lease_age": oldest_lease_age,
        "completed": completed,
        "status_hint": status_hint,
        "supporting_workers": supporting_workers,
        "online_workers": online_workers,
        "leased_workers": task_summary.get("leased_workers", []),
        "recent_errors": recent_errors,
        "last_error": recent_errors[0] if recent_errors else None,
    }


def analysis_worker_dict(worker) -> dict[str, object]:
    age_seconds = analysis_worker_age_seconds(worker)
    connected = analysis_worker_connected(worker)
    return {
        "worker_id": worker.worker_id,
        "models": [model for model in worker.models.split(",") if model],
        "status": worker.status,
        "display_status": worker.status if connected else "stale",
        "connected": connected,
        "stale_seconds": age_seconds,
        "connected_ttl_seconds": WORKER_CONNECTED_TTL_SECONDS,
        "stage": worker.stage,
        "message": worker.message,
        "current_task_id": worker.current_task_id,
        "last_seen_at": worker.last_seen_at,
        "created_at": worker.created_at,
        "claimed_count": worker.claimed_count,
        "completed_count": worker.completed_count,
        "failed_count": worker.failed_count,
        "released_count": worker.released_count,
    }


def decode_worker_vector(item: WorkerResultItem) -> np.ndarray:
    if item.dtype != "float32":
        raise ValueError("Only float32 worker vectors are supported")
    try:
        raw = base64.b64decode(item.vector_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 vector") from exc
    expected = item.dim * np.dtype(np.float32).itemsize
    if len(raw) != expected:
        raise ValueError(f"Vector byte length mismatch: expected {expected}, got {len(raw)}")
    vector = np.frombuffer(raw, dtype=np.float32).copy()
    if not np.all(np.isfinite(vector)):
        raise ValueError("Vector contains NaN or Inf")
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        raise ValueError("Vector has zero norm")
    return vector


def decode_worker_scores(item: WorkerHeadOutputItem) -> np.ndarray:
    if item.dtype != "float32":
        raise ValueError("Only float32 worker scores are supported")
    try:
        raw = base64.b64decode(item.scores_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 scores") from exc
    expected = item.dim * np.dtype(np.float32).itemsize
    if len(raw) != expected:
        raise ValueError(f"Scores byte length mismatch: expected {expected}, got {len(raw)}")
    scores = np.frombuffer(raw, dtype=np.float32).copy()
    if not np.all(np.isfinite(scores)):
        raise ValueError("Scores contain NaN or Inf")
    return scores


def validate_worker_task_identity(
    store: Store,
    task_id: str,
    worker_id: str,
    track_id: int,
    model_name: str,
    file_size: int,
    mtime: int,
    *,
    expire_leases: bool = True,
) -> AnalysisTask:
    if expire_leases:
        store.expire_analysis_leases()
    task = store.get_analysis_task(task_id)
    if task is None:
        raise ValueError("Task not found")
    if task.status != "leased":
        raise ValueError(f"Task is not active: {task.status}")
    if task.lease_owner != worker_id:
        raise ValueError("Task is not leased by this worker")
    if task.track_id != track_id or task.model_name != model_name:
        raise ValueError("Task result identity mismatch")
    if task.file_size != file_size or task.mtime != mtime:
        raise ValueError("Task result is stale")
    track = store.get_track(task.track_id)
    if track is None:
        raise ValueError("Track not found")
    if track.file_size != task.file_size or track.mtime != task.mtime:
        raise ValueError("Track changed after task was created")
    return task


@app.get("/workers/tasks/{task_id}/state")
def get_worker_task_state(task_id: str, worker_id: str) -> dict[str, object]:
    store, _settings = context()
    task = store.get_analysis_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    job_status = store.get_analysis_job_status(task.job_id)
    active = (
        task.status == "leased"
        and task.lease_owner == worker_id
        and job_status == "running"
    )
    return {
        "task_id": task.id,
        "job_id": task.job_id,
        "status": task.status,
        "stage": task.stage,
        "lease_owner": task.lease_owner,
        "job_status": job_status,
        "active": active,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/playback/settings", response_model=PlaybackSettingsResponse)
def api_v1_playback_settings() -> dict[str, object]:
    return {"settings": playback_settings_defaults()}


@app.post("/api/v1/autoplay/refill", response_model=AutoplayRefillResponse)
def api_v1_autoplay_refill(
    request: AutoplayRefillRequest,
    include_debug: bool = Query(False),
) -> dict[str, object] | JSONResponse:
    store, settings = context()
    session = store.get_playback_session(request.session_id)
    if session is None:
        return api_error(404, "not_found", "Playback session not found")
    if not session.autoplay_enabled:
        return {
            "session_id": session.id,
            "added_items": [],
            "candidate_count": request.candidate_count or 0,
            "debug": {"autoplay_enabled": False} if include_debug else None,
        }
    try:
        result = refill_autoplay_queue(
            store,
            settings,
            session,
            request.settings,
            visible_buffer=request.visible_buffer,
            candidate_count=request.candidate_count,
            include_debug=include_debug,
        )
    except FileNotFoundError as exc:
        return api_error(409, "index_not_ready", str(exc))
    except LookupError as exc:
        return api_error(404, "not_found", str(exc))
    return {
        "session_id": result.session_id,
        "added_items": [
            queue_item_dict(store, item, include_debug=include_debug)
            for item in result.added_items
        ],
        "candidate_count": result.candidate_count,
        "debug": result.debug,
    }


@app.post("/api/v1/playback/sessions", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_create_playback_session(request: PlaybackSessionCreateRequest) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    try:
        track_ids = build_initial_playback_queue(store, request)
        if request.source_type in {"track", "release", "artist"} and not track_ids:
            return api_error(404, "not_found", "Playback source has no local tracks")
        session, _queue = store.create_playback_session(
            source_type=request.source_type,
            source_id=request.source_id,
            source_label=request.source_label,
            mode=request.mode,
            track_ids=track_ids,
            autoplay_enabled=request.autoplay_enabled,
            shuffle_enabled=request.shuffle_enabled,
            repeat_mode=request.repeat_mode,
            settings=playback_session_settings(request.settings),
            state=request.state,
        )
    except ValueError as exc:
        return api_error(400, "invalid_request", str(exc))
    return playback_session_response(store, session)


@app.get("/api/v1/playback/sessions/{session_id}", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_get_playback_session(session_id: str) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    session = store.get_playback_session(session_id)
    if session is None:
        return api_error(404, "not_found", "Playback session not found")
    return playback_session_response(store, session)


@app.patch("/api/v1/playback/sessions/{session_id}", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_update_playback_session(
    session_id: str,
    request: PlaybackSessionPatchRequest,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    fields = request_field_names(request)
    try:
        session = store.update_playback_session(
            session_id,
            status=request.status,
            current_track_id=request.current_track_id,
            current_queue_item_id=request.current_queue_item_id,
            clear_current_track="current_track_id" in fields and request.current_track_id is None,
            clear_current_queue_item="current_queue_item_id" in fields and request.current_queue_item_id is None,
            autoplay_enabled=request.autoplay_enabled,
            shuffle_enabled=request.shuffle_enabled,
            repeat_mode=request.repeat_mode,
            settings=request.settings,
            state=request.state,
        )
    except ValueError as exc:
        return api_error(400, "invalid_request", str(exc))
    if session is None:
        return api_error(404, "not_found", "Playback session not found")
    return playback_session_response(store, session)


@app.get("/api/v1/playback/sessions/{session_id}/queue", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_get_playback_queue(
    session_id: str,
    include_debug: bool = False,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    session = store.get_playback_session(session_id)
    if session is None:
        return api_error(404, "not_found", "Playback session not found")
    items = store.list_queue_items(session_id)
    return {"session": playback_session_dict(store, session), "queue": playback_queue_dict(store, session, items, include_debug)}


@app.patch("/api/v1/playback/sessions/{session_id}/queue", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_patch_playback_queue(
    session_id: str,
    request: PlaybackQueuePatchRequest,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    session = store.get_playback_session(session_id)
    if session is None:
        return api_error(404, "not_found", "Playback session not found")
    try:
        if request.operation == "replace":
            store.replace_queue_items(session_id, queue_patch_items(request))
        elif request.operation == "add":
            items = queue_patch_items(request)
            if not items:
                return api_error(400, "invalid_request", "add requires track_id, track_ids, or items")
            store.append_queue_items(session_id, items)
        elif request.operation == "remove":
            if not request.queue_item_id:
                return api_error(400, "invalid_request", "remove requires queue_item_id")
            item = store.remove_queue_item(session_id, request.queue_item_id)
            if item is None:
                return api_error(404, "not_found", "Queue item not found")
            store.record_playback_event(
                session_id=session_id,
                queue_item_id=request.queue_item_id,
                track_id=item.track_id,
                event_type="removed_from_queue",
                source="api",
            )
        elif request.operation == "move":
            if not request.queue_item_id or request.position is None:
                return api_error(400, "invalid_request", "move requires queue_item_id and position")
            store.move_queue_item(session_id, request.queue_item_id, request.position)
        elif request.operation in {"jump", "mark_current"}:
            if not request.queue_item_id:
                return api_error(400, "invalid_request", f"{request.operation} requires queue_item_id")
            item = store.jump_to_queue_item(session_id, request.queue_item_id)
            if item is None:
                return api_error(404, "not_found", "Queue item not found")
            event_type = "queue_click" if request.operation == "jump" else "track_started"
            store.record_playback_event(
                session_id=session_id,
                queue_item_id=request.queue_item_id,
                track_id=item.track_id,
                event_type=event_type,
                source="api",
            )
        else:
            return api_error(400, "invalid_request", "Unsupported queue operation")
    except ValueError as exc:
        return api_error(400, "invalid_request", str(exc))
    session = store.get_playback_session(session_id)
    items = store.list_queue_items(session_id)
    return {"session": playback_session_dict(store, session), "queue": playback_queue_dict(store, session, items)}


@app.post("/api/v1/playback/events", response_model=PlaybackEventIngestResponse)
def api_v1_record_playback_event(request: PlaybackEventRequest) -> dict[str, object] | JSONResponse:
    store, settings = context()
    try:
        result = store.record_playback_event(
            session_id=request.session_id,
            queue_item_id=request.queue_item_id,
            track_id=request.track_id,
            release_id=request.release_id,
            artist_id=request.artist_id,
            event_type=request.event_type,
            position_seconds=request.position_seconds,
            duration_seconds=request.duration_seconds,
            play_fraction=request.play_fraction,
            client_event_id=request.client_event_id,
            source=request.source,
            payload=request.payload,
        )
    except ValueError as exc:
        return api_error(400, "invalid_request", str(exc))
    navidrome_scrobble = maybe_scrobble_navidrome_play(store, settings, result)
    return {
        "accepted": True,
        "duplicate": result.duplicate,
        "event_id": result.event.id,
        "event": playback_event_dict(result.event),
        "preference_delta": result.preference_delta,
        "navidrome_scrobble": navidrome_scrobble,
    }


@app.get("/api/v1/mixes", response_model=None)
def api_v1_mixes(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    include_debug: bool = False,
) -> dict[str, object]:
    store, settings = context()
    ensure_diagnostics = ensure_dashboard_mixes_fast(store, settings)
    mixes = store.list_generated_mixes(statuses=["active", "saved"], limit=limit, offset=offset)
    items = [generated_mix_summary_dict(store, mix) for mix in mixes]
    if not include_debug:
        for item in items:
            item.pop("score_summary", None)
            item.pop("anchor", None)
            item.pop("settings", None)
    total = store.count_generated_mixes(statuses=["active", "saved"])
    response: dict[str, object] = {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
        "generated_at": utc_now(),
    }
    if include_debug:
        response["generation"] = ensure_diagnostics
    return response


@app.get("/api/v1/mixes/settings", response_model=None)
def api_v1_mix_settings() -> dict[str, object]:
    _store, settings = context()
    return {"settings": generated_mix_settings(settings)}


@app.get("/api/v1/mixes/status", response_model=None)
def api_v1_mix_status() -> dict[str, object]:
    store, settings = context()
    mix_settings = generated_mix_settings(settings)
    plan = dashboard_mix_generation_plan(store, mix_settings)
    diagnostics = dict(plan.diagnostics)
    diagnostics["embedding_count"] = store.count_embeddings(str(mix_settings.get("mix_model") or "discogs_multi"))
    diagnostics["mixes"] = [
        generated_mix_summary_dict(store, mix)
        for mix in store.list_generated_mixes(statuses=["active", "saved"], limit=int(mix_settings.get("mix_dashboard_count", 8)))
    ]
    return {"generation": diagnostics}


@app.put("/api/v1/mixes/settings", response_model=None)
def api_v1_update_mix_settings(request: GeneratedMixSettingsRequest) -> dict[str, object]:
    _store, settings = context()
    runtime = load_runtime_settings(settings.data_dir)
    saved = runtime.get("generated_mixes", {})
    saved = saved if isinstance(saved, dict) else {}
    patch = request.model_dump(exclude_unset=True) if hasattr(request, "model_dump") else request.dict(exclude_unset=True)
    saved.update({key: value for key, value in patch.items() if value is not None})
    runtime["generated_mixes"] = saved
    save_runtime_settings(settings.data_dir, runtime)
    return {"settings": generated_mix_settings(settings)}


@app.get("/api/v1/mixes/{mix_id}", response_model=None)
def api_v1_mix_detail(mix_id: str, include_debug: bool = False) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    mix = store.get_generated_mix(mix_id)
    if mix is None:
        return api_error(404, "not_found", "Generated mix not found")
    detail = generated_mix_detail_dict(store, mix)
    if not include_debug:
        detail.pop("score_summary", None)
        detail.pop("settings", None)
        for item in detail["items"]:
            item.pop("score_breakdown", None)
    return detail


@app.get("/api/v1/mixes/{mix_id}/cover", response_model=None)
def api_v1_mix_cover(mix_id: str) -> FileResponse | JSONResponse:
    store, _settings = context()
    mix = store.get_generated_mix(mix_id)
    if mix is None:
        return api_error(404, "not_found", "Generated mix not found")
    if not mix.cover_path:
        return api_error(404, "not_found", "Generated mix has no cover")
    path = Path(mix.cover_path)
    if not path.exists() or not path.is_file():
        return api_error(404, "not_found", "Generated mix cover not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/v1/mixes/generate", response_model=None)
def api_v1_generate_mixes(request: MixGenerateRequest) -> dict[str, object] | JSONResponse:
    store, settings = context()
    request_settings = {**generated_mix_settings(settings), **request.settings}
    result = generate_mixes(
        store,
        settings,
        request_settings,
        count=request.count,
        tracks_per_mix=request.tracks_per_mix,
        force=request.force,
    )
    if not result.mixes:
        return api_error(409, "not_enough_data", "No embedded tracks or positive listening signals available for generated mixes")
    return {
        "items": [generated_mix_summary_dict(store, mix) for mix in result.mixes],
        "generated_at": utc_now(),
        "diagnostics": result.diagnostics,
    }


@app.post("/api/v1/mixes/{mix_id}/save", response_model=None)
def api_v1_save_mix(mix_id: str) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    mix = store.save_generated_mix_as_playlist(mix_id)
    if mix is None:
        return api_error(404, "not_found", "Generated mix not found")
    return generated_mix_summary_dict(store, mix)


@app.post("/api/v1/mixes/{mix_id}/play", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_play_mix(mix_id: str) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    mix = store.get_generated_mix(mix_id)
    if mix is None:
        return api_error(404, "not_found", "Generated mix not found")
    track_ids = [item.track_id for item in store.list_generated_mix_items(mix_id)]
    if not track_ids:
        return api_error(409, "empty_mix", "Generated mix has no playable tracks")
    session, _queue = store.create_playback_session(
        source_type="generated_mix",
        source_label=mix.title,
        mode="linear",
        track_ids=track_ids,
        autoplay_enabled=True,
        settings=playback_session_settings({"source_mix_id": mix.id}),
    )
    items = [
        {
            "track_id": item.track_id,
            "origin": "generated_mix",
            "source_type": "generated_mix",
            "reason": "Generated mix item",
            "score": item.score,
            "debug": {
                "mix_id": mix.id,
                "position": item.position,
                "score_breakdown": _json_object(item.score_breakdown_json),
                "reason": _json_object(item.reason_json),
            },
        }
        for item in store.list_generated_mix_items(mix_id)
    ]
    store.replace_queue_items(session.id, items)
    refreshed = store.get_playback_session(session.id)
    if refreshed is None:
        return api_error(500, "internal_error", "Playback session disappeared after creation")
    return playback_session_response(store, refreshed)


@app.get("/api/v1/dashboard", response_model=None)
def api_v1_dashboard(
    limit: int = Query(default=12, ge=1, le=50),
    include_debug: bool = False,
) -> dict[str, object]:
    store, settings = context()
    ensure_diagnostics = ensure_dashboard_mixes_fast(store, settings)
    shelf_keys = ["mixes_for_you", "recently_added", "discover_random", "listen_again", "long_time_no_listen"]
    shelves = [
        shelf
        for key in shelf_keys
        if (shelf := dashboard_shelf_response(store, key, limit=limit, offset=0, include_debug=include_debug)) is not None
    ]
    return {
        "hero": {
            "type": "flow",
            "title": "Flow",
            "subtitle": "Start your personal stream",
            "available": False,
            "action": {"type": "start_flow", "enabled": False, "endpoint": None},
        },
        "shelves": shelves,
        "settings": {
            "visible_shelves": shelf_keys,
            "items_per_shelf": limit,
        },
        **({"generation": {"mixes_for_you": ensure_diagnostics}} if include_debug else {}),
    }


@app.get("/api/v1/dashboard/shelves/{key}", response_model=None)
def api_v1_dashboard_shelf(
    key: str,
    limit: int = Query(default=12, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    include_debug: bool = False,
) -> dict[str, object] | JSONResponse:
    store, settings = context()
    ensure_result = None
    if key == "mixes_for_you":
        ensure_result = ensure_dashboard_mixes_fast(store, settings)
    shelf = dashboard_shelf_response(store, key, limit=limit, offset=offset, include_debug=include_debug)
    if shelf is None:
        return api_error(404, "not_found", "Dashboard shelf not found")
    if include_debug and ensure_result is not None:
        shelf["generation"] = ensure_result
    return shelf


@app.get("/api/v1/search", response_model=SearchResponse)
def api_v1_search(
    q: str = "",
    type: str = Query(default="all", pattern="^(all|artist|release|track)$"),
    limit: int = Query(default=8, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    include_debug: bool = False,
) -> dict[str, object]:
    store, settings = context()
    query = " ".join(q.strip().split())
    results = store.search_entities(query, entity_type=type, limit=limit, offset=offset)
    artist_rows = results["artists"]["items"]
    release_rows = results["releases"]["items"]
    track_rows = results["tracks"]["items"]
    artists = [artist_summary_with_external_image(store, settings, row) for row in artist_rows]
    releases = [release_summary_dict(row) for row in release_rows]
    artists_by_track = store.artists_for_tracks([track.id for track in track_rows])
    tracks = [
        track_summary_dict(store, track, artists_by_track.get(track.id, []))
        for track in track_rows
    ]
    groups = [
        search_group("artists", "Artists", artists, int(results["artists"]["total"]), limit, offset),
        search_group("tracks", "Tracks", tracks, int(results["tracks"]["total"]), limit, offset),
        search_group("releases", "Releases", releases, int(results["releases"]["total"]), limit, offset),
    ]
    top_result = search_top_result(query, artists, releases, tracks)
    if include_debug:
        for group in groups:
            for item in group["items"]:
                if isinstance(item, dict):
                    item["debug"] = {
                        "search_score": _entity_search_score(
                            query,
                            str(group["type"]).rstrip("s"),
                            item,
                        )
                    }
    return {"query": query, "top_result": top_result, "groups": groups}


@app.get("/api/v1/artists/{artist_id}", response_model=ArtistResponse)
def api_v1_artist(artist_id: int) -> dict[str, object] | JSONResponse:
    store, settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    return {
        "artist": {**artist_summary_with_external_image(store, settings, artist), "sort_name": artist.artist.sort_name},
        "actions": [entity_action("mix", True, None)],
        "links": {
            "image": f"/api/v1/artists/{artist_id}/image",
            "discography": f"/api/v1/artists/{artist_id}/discography",
            "top_tracks": f"/api/v1/artists/{artist_id}/top-tracks",
            "similar": f"/api/v1/artists/{artist_id}/similar",
        },
    }


@app.get("/api/v1/artists/{artist_id}/discography", response_model=ArtistDiscographyResponse)
def api_v1_artist_discography(
    artist_id: int,
    sort: str = Query(default="release_date_desc", pattern="^(release_date_desc|release_date_asc|title)$"),
    limit: int | None = Query(default=None, ge=1, le=100),
    include_tracks: bool = False,
) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    titles = {
        "albums": "Albums",
        "eps": "EPs",
        "singles": "Singles",
        "compilations": "Compilations",
        "featured_in": "Featured In",
        "releases": "Releases",
    }
    discography = store.artist_discography(artist_id)
    groups = []
    for key, title in titles.items():
        items = discography[key]
        if sort == "title":
            items = sorted(items, key=lambda item: item.release.title.casefold())
        elif sort == "release_date_asc":
            items = sorted(
                items,
                key=lambda item: (
                    item.release.release_year is None,
                    item.release.release_year or 0,
                    item.release.title.casefold(),
                ),
            )
        if limit is not None:
            items = items[:limit]
        release_items = []
        for item in items:
            release_item = release_summary_dict(item)
            if include_tracks:
                release_item["tracks"] = [
                    release_track_dict(store, track)
                    for track in store.list_release_tracks(item.release.id)
                ]
            release_items.append(release_item)
        groups.append({"key": key, "title": title, "items": release_items})
    return {"artist": artist_link_dict(artist.artist), "groups": groups}


@app.get("/api/v1/artists/{artist_id}/image", response_model=ImageInfoResponse)
def api_v1_artist_image(artist_id: int) -> dict[str, object] | JSONResponse:
    store, settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    summary = artist_summary_with_external_image(store, settings, artist)
    image = summary["image"] if isinstance(summary.get("image"), dict) else image_ref(None)
    if not image.get("url"):
        return api_error(404, "not_found", "Artist image not available")
    return {"image": image}


@app.get("/api/v1/artists/{artist_id}/top-tracks", response_model=ArtistAvailabilityStubResponse)
def api_v1_artist_top_tracks(artist_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    return {"artist": artist_link_dict(artist.artist), "items": [], "basis": "local_playback", "available": False}


@app.get("/api/v1/artists/{artist_id}/similar", response_model=ArtistAvailabilityStubResponse)
def api_v1_artist_similar(artist_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    artist = store.get_artist(artist_id)
    if artist is None:
        return api_error(404, "not_found", "Artist not found")
    return {"artist": artist_link_dict(artist.artist), "items": [], "available": False, "basis": "not_available"}


@app.get("/api/v1/releases/{release_id}", response_model=ReleaseResponse)
def api_v1_release(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    return {
        "release": release_summary_dict(release),
        "actions": [entity_action("play", True, None), entity_action("shuffle", True, None)],
        "links": {
            "tracks": f"/api/v1/releases/{release_id}/tracks",
            "discography": f"/api/v1/releases/{release_id}/related-discography",
            "recommendations": f"/api/v1/releases/{release_id}/recommendations",
        },
    }


@app.get("/api/v1/releases/{release_id}/tracks", response_model=ReleaseTracksResponse)
def api_v1_release_tracks(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "items": [release_track_dict(store, item) for item in store.list_release_tracks(release_id)],
    }


@app.get("/api/v1/releases/{release_id}/related-discography", response_model=RelatedDiscographyResponse)
def api_v1_release_related_discography(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    items = store.related_discography_for_release(release_id)
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "context_artists": [
            artist_link_dict(artist)
            for artist in store.participating_artists_for_release(release_id)
        ],
        "items": [release_summary_dict(item) for item in items],
    }


@app.get("/api/v1/releases/{release_id}/recommendations", response_model=ReleaseAvailabilityStubResponse)
def api_v1_release_recommendations(release_id: int) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    return {
        "release": {"id": release.release.id, "title": release.release.title},
        "available": False,
        "basis": "not_available",
        "items": [],
    }


@app.get("/api/v1/releases/{release_id}/cover", response_model=None)
def api_v1_release_cover(
    release_id: int,
    size: int = Query(default=300, ge=32, le=1000),
) -> Response | JSONResponse:
    store, settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    if not release.release.cover_art_id:
        return api_error(404, "not_found", "Release has no cover art")
    try:
        cover = NavidromeClient(settings.navidrome).get_cover_art(release.release.cover_art_id, size=size)
    except Exception as exc:
        logger.warning("Release cover lookup failed release_id=%s: %s", release_id, exc)
        return api_error(404, "not_found", "Release cover not available")
    return Response(content=cover.payload, media_type=cover.content_type)


def run_maintenance_tick(store: Store | None = None) -> None:
    if store is None:
        store, _settings = context()
    store.expire_analysis_leases()
    store.refresh_active_analysis_jobs()
    sync_memory_jobs_from_durable_jobs(store.recent_analysis_jobs(limit=100))
    maybe_start_next_deferred_job()


def maintenance_loop() -> None:
    while not MAINTENANCE_STOP.wait(15):
        if SHUTDOWN_REQUESTED:
            return
        try:
            run_maintenance_tick()
        except Exception:
            logger.exception("Background maintenance tick failed")


@app.on_event("startup")
def start_maintenance_loop() -> None:
    MAINTENANCE_STOP.clear()
    Thread(target=maintenance_loop, name="discocs-maintenance", daemon=True).start()


@app.on_event("shutdown")
def shutdown_analyze_workers() -> None:
    global SHUTDOWN_REQUESTED
    logger.info("Application shutdown requested; terminating analyze workers")
    SHUTDOWN_REQUESTED = True
    MAINTENANCE_STOP.set()
    with ANALYZE_EXECUTORS_LOCK:
        executors = list(ANALYZE_EXECUTORS)
    for executor in executors:
        terminate_process_pool(executor)


@app.get("/metrics/features")
def metrics_features(
    source: str = Query(default="audio_features", pattern="^(audio_features|heads)$"),
    extractor: str = AUDIO_FEATURE_EXTRACTOR,
):
    store, _settings = context()
    if source == "heads":
        summaries = store.list_head_summaries()
        return {
            "source": source,
            "features": [
                {
                    "name": item.model_name,
                    "extractor": "discogs_effnet_heads",
                    "value_count": item.prediction_track_count,
                    "text_count": item.label_count,
                    "track_count": item.output_count,
                    "min_value": None,
                    "max_value": item.max_score,
                    "avg_value": item.avg_score,
                    "unit": "score",
                }
                for item in summaries
            ],
        }
    summaries = store.list_feature_summaries(extractor or None)
    return {
        "source": source,
        "extractor": extractor,
        "features": [
            {
                "name": item.name,
                "extractor": item.extractor,
                "value_count": item.value_count,
                "text_count": item.text_count,
                "track_count": item.track_count,
                "min_value": item.min_value,
                "max_value": item.max_value,
                "avg_value": item.avg_value,
                "unit": item.unit,
            }
            for item in summaries
        ],
    }


@app.get("/metrics/features/{feature_name}/values")
def metrics_feature_values(
    feature_name: str,
    source: str = Query(default="audio_features", pattern="^(audio_features|heads)$"),
    extractor: str = AUDIO_FEATURE_EXTRACTOR,
    limit: int = Query(default=100, ge=1, le=500),
):
    store, _settings = context()
    if source == "heads":
        values = store.list_head_prediction_labels(feature_name, limit)
        return {
            "feature": feature_name,
            "source": source,
            "values": [
                {
                    "value": label,
                    "track_count": track_count,
                    "avg_score": avg_score,
                    "max_score": max_score,
                }
                for label, track_count, avg_score, max_score in values
            ],
        }
    values = store.list_feature_text_values(feature_name, extractor or None, limit)
    return {
        "feature": feature_name,
        "source": source,
        "extractor": extractor,
        "values": [
            {"value": value, "track_count": track_count}
            for value, track_count in values
        ],
    }


@app.post("/metrics/search")
def metrics_search(request: FeatureSearchRequest):
    store, _settings = context()
    filters = [
        FeatureFilter(
            name=item.name,
            min_value=item.min_value,
            max_value=item.max_value,
            text_values=tuple(value for value in item.text_values if value),
        )
        for item in request.filters
        if item.name.strip()
    ]
    if request.source == "heads":
        results = store.search_tracks_by_head_predictions(
            filters,
            query=request.query,
            sort_by=request.sort_by,
            sort_direction=request.sort_direction,
            limit=request.limit,
        )
    else:
        results = store.search_tracks_by_features(
            filters,
            query=request.query,
            extractor=request.extractor or None,
            sort_by=request.sort_by,
            sort_direction=request.sort_direction,
            limit=request.limit,
        )
    return {
        "source": request.source,
        "extractor": request.extractor,
        "count": len(results),
        "results": [enriched_feature_track_dict(store, item) for item in results],
    }


@app.get("/", response_class=HTMLResponse)
@app.get("/search", response_class=HTMLResponse)
@app.get("/artists/{artist_id}", response_class=HTMLResponse)
@app.get("/releases/{release_id}", response_class=HTMLResponse)
@app.get("/mixes/{mix_id}", response_class=HTMLResponse)
@app.get("/settings", response_class=HTMLResponse)
def test_ui() -> HTMLResponse:
    return HTMLResponse(
        UI_HTML,
        headers={
            "Cache-Control": "no-store",
            "X-Discocs-UI-Build": UI_BUILD_ID,
        },
    )


@app.get("/debug/ui")
def debug_ui() -> dict[str, object]:
    return {
        "build": UI_BUILD_ID,
        "likes_source": "navidrome",
        "frontend_likes_cache": False,
    }


@app.get("/stats")
def stats(model: str = "discogs_multi") -> dict[str, object]:
    cached = cached_stats(model)
    if cached is not None:
        return cached
    started = perf_counter()
    store, settings = context()
    logger.info("Stats build started model=%s", model)
    head_started = perf_counter()
    head_status = head_pack_status(store, settings)
    logger.info("Stats head_pack_status seconds=%.3f", perf_counter() - head_started)
    audio_started = perf_counter()
    audio_status = audio_feature_status(store)
    logger.info("Stats audio_feature_status seconds=%.3f", perf_counter() - audio_started)
    counts_started = perf_counter()
    tracks = store.count_tracks()
    missing_files = store.count_missing_files()
    navidrome_external_tracks = store.count_external_tracks("navidrome")
    errored_files = analysis_error_count(store)
    embeddings = store.count_embeddings(model)
    missing_embeddings = store.count_missing_embeddings(model)
    index_status = recommender_index_status(settings, model, embeddings)
    known_models = sorted(model for model in [*MODEL_FILES, MUQ_MULAN_MODEL] if model != DISCOGS_EFFNET_MODEL)
    model_stats = []
    for known_model in known_models:
        model_embeddings = store.count_embeddings(known_model)
        model_missing_embeddings = store.count_missing_embeddings(known_model)
        model_index_status = recommender_index_status(settings, known_model, model_embeddings)
        if known_model == MUQ_MULAN_MODEL:
            known_model_path = settings.model_dir / "muq"
            known_model_exists = known_model_path.exists()
        else:
            known_model_path = settings.model_path(known_model)
            known_model_exists = known_model_path.exists()
        model_stats.append(
            {
                "model": known_model,
                "embeddings": model_embeddings,
                "missing_embeddings": model_missing_embeddings,
                "model_path": str(known_model_path),
                "model_exists": known_model_exists,
                "index": str(settings.index_path(known_model)),
                "index_exists": settings.index_path(known_model).exists(),
                "index_status": model_index_status["status"],
                "index_stale": model_index_status["stale"],
                "index_count": model_index_status["count"],
                "index_embedding_count": model_index_status["embedding_count"],
                "index_metadata_exists": model_index_status["metadata_exists"],
                "index_metadata": model_index_status["metadata_path"],
            }
        )
    if model == MUQ_MULAN_MODEL:
        model_path = settings.model_dir / "muq"
        model_exists = model_path.exists()
    else:
        model_path = settings.model_path(model)
        model_exists = model_path.exists()
    logger.info("Stats base_counts seconds=%.3f", perf_counter() - counts_started)
    data: dict[str, object] = {
        "db": str(settings.db_path),
        "tracks": tracks,
        "missing_files": missing_files,
        "analysis_error_count": errored_files,
        "navidrome_external_tracks": navidrome_external_tracks,
        "embeddings": embeddings,
        "missing_embeddings": missing_embeddings,
        "head_pack_expected_outputs": head_status["expected_outputs"],
        "head_pack_outputs": head_status["saved_outputs"],
        "head_pack_complete_tracks": head_status["complete_tracks"],
        "head_pack_missing_tracks": head_status["missing_tracks"],
        "missing_head_pack_tracks": head_status["missing_tracks"],
        "head_pack": head_status,
        "audio_features_complete_tracks": audio_status["complete_tracks"],
        "audio_features_missing_tracks": audio_status["missing_tracks"],
        "audio_features": audio_status,
        "model": model,
        "models": known_models,
        "model_stats": model_stats,
        "model_path": str(model_path),
        "model_exists": model_exists,
        "index": str(settings.index_path(model)),
        "index_exists": settings.index_path(model).exists(),
        "index_status": index_status["status"],
        "index_stale": index_status["stale"],
        "index_count": index_status["count"],
        "index_embedding_count": index_status["embedding_count"],
        "index_metadata_exists": index_status["metadata_exists"],
        "index_metadata": index_status["metadata_path"],
    }
    remember_stats(model, data)
    logger.info("Stats build completed model=%s seconds=%.3f", model, perf_counter() - started)
    return data


def cached_stats(model: str) -> dict[str, object] | None:
    now = time.time()
    with STATS_CACHE_LOCK:
        cached = STATS_CACHE.get(model)
        if cached is None:
            return None
        cached_at, data = cached
        if now - cached_at > STATS_CACHE_TTL_SECONDS:
            STATS_CACHE.pop(model, None)
            return None
        result = dict(data)
        result["cached"] = True
        return result


def remember_stats(model: str, data: dict[str, object]) -> None:
    with STATS_CACHE_LOCK:
        STATS_CACHE[model] = (time.time(), dict(data))


def clear_stats_cache() -> None:
    with STATS_CACHE_LOCK:
        STATS_CACHE.clear()


@app.get("/settings/navidrome")
def get_navidrome_settings() -> dict[str, object]:
    _store, settings = context()
    nav = settings.navidrome
    return {
        "url": nav.url,
        "user": nav.user,
        "password_set": bool(nav.password),
        "auth_mode": nav.auth_mode,
        "timeout_seconds": nav.timeout_seconds,
        "download_mode": nav.download_mode,
        "temp_dir": str(nav.temp_dir),
    }


@app.put("/settings/navidrome")
def update_navidrome_settings(request: NavidromeSettingsRequest) -> dict[str, object]:
    _store, settings = context()
    saved = load_runtime_settings(settings.data_dir)
    existing = saved.get("navidrome", {})
    existing_password = existing.get("password", "") if isinstance(existing, dict) else ""
    password = request.password if request.password is not None else str(existing_password)
    saved["navidrome"] = {
        "url": request.url.strip(),
        "user": request.user.strip(),
        "password": password,
        "auth_mode": request.auth_mode,
        "timeout_seconds": request.timeout_seconds,
        "download_mode": request.download_mode,
        "temp_dir": request.temp_dir.strip()
        if request.temp_dir
        else str(settings.data_dir / "tmp" / "navidrome"),
    }
    save_runtime_settings(settings.data_dir, saved)
    navidrome_logger.info(
        "Saved Navidrome settings url=%s user=%s auth_mode=%s timeout_seconds=%s download_mode=%s password_set=%s",
        request.url,
        request.user,
        request.auth_mode,
        request.timeout_seconds,
        request.download_mode,
        bool(password),
    )
    return get_navidrome_settings()


@app.post("/navidrome/ping")
def ping_navidrome() -> dict[str, object]:
    _store, settings = context()
    try:
        payload = NavidromeClient(settings.navidrome).ping()
    except Exception as exc:
        navidrome_logger.warning("Navidrome ping failed", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Navidrome ping failed: {exc}") from exc
    return {
        "status": "ok",
        "version": payload.get("version", ""),
        "server_version": payload.get("serverVersion", ""),
    }


@app.post("/navidrome/plugin-event")
def record_navidrome_plugin_event(request: NavidromePluginEventRequest) -> dict[str, str]:
    navidrome_plugin_logger.info(
        "plugin_event event=%s item_id=%s model=%s count=%s status=%s discocs_url=%s message=%s",
        request.event,
        request.item_id,
        request.model,
        request.count,
        request.status,
        request.discocs_url,
        request.message,
    )
    return {"status": "ok"}


@app.get("/instant-mix/settings")
def get_instant_mix_settings() -> dict[str, object]:
    _store, settings = context()
    return instant_mix_settings(settings)


@app.put("/instant-mix/settings")
def update_instant_mix_settings(request: InstantMixSettingsRequest) -> dict[str, object]:
    _store, settings = context()
    saved = load_runtime_settings(settings.data_dir)
    saved["instant_mix"] = {
        "model": request.model,
        "count": request.count,
        "min_similarity": request.min_similarity,
        "max_per_artist": request.max_per_artist,
        "exclude_same_album": request.exclude_same_album,
        "count_collaboration_artists": request.count_collaboration_artists,
    }
    save_runtime_settings(settings.data_dir, saved)
    return instant_mix_settings(settings)


@app.get("/instant-mix/requests")
def list_instant_mix_requests(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    store, _settings = context()
    try:
        requests = store.list_instant_mix_requests(limit=limit, offset=offset)
    except sqlite3.DatabaseError as exc:
        logger.exception("Failed to list instant mix requests")
        raise HTTPException(
            status_code=503,
            detail=f"Instant mix history could not be read from SQLite: {exc}",
        ) from exc
    return {
        "count": len(requests),
        "limit": limit,
        "offset": offset,
        "results": [
            instant_mix_request_dict(request, include_results=False, store=store)
            for request in requests
        ],
    }


@app.get("/instant-mix/requests/{request_id}")
def get_instant_mix_request(request_id: str) -> dict[str, object]:
    store, _settings = context()
    try:
        request = store.get_instant_mix_request(request_id)
    except sqlite3.DatabaseError as exc:
        logger.exception("Failed to read instant mix request request_id=%s", request_id)
        raise HTTPException(
            status_code=503,
            detail=f"Instant mix request could not be read from SQLite: {exc}",
        ) from exc
    if request is None:
        raise HTTPException(status_code=404, detail="Instant mix request not found")
    return instant_mix_request_dict(request, include_results=True, store=store)


@app.post("/tracks/{track_id}/instant-mix")
def create_track_instant_mix(track_id: int) -> dict[str, object]:
    started = perf_counter()
    request_id = str(uuid4())
    store, settings = context()
    mix_settings = instant_mix_settings(settings)
    model = str(mix_settings["model"])
    effective_count = int(mix_settings["count"])
    min_similarity = mix_settings["min_similarity"]
    effective_max_per_artist = int(mix_settings["max_per_artist"])
    effective_exclude_same_album = bool(mix_settings["exclude_same_album"])
    effective_count_collaboration_artists = bool(mix_settings["count_collaboration_artists"])
    seed = store.get_track(track_id)
    seed_item_id = store.external_id_for_track("navidrome", track_id) or f"track:{track_id}"
    if seed is None:
        logger.warning("Instant mix requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    try:
        candidates = Recommender(store, settings, model).similar(
            seed,
            k=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
        )
    except FileNotFoundError as exc:
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=seed_item_id,
            seed_track_id=seed.id,
            model=model,
            requested_model=None,
            requested_count=None,
            effective_count=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
            min_similarity=min_similarity,
            status="failed",
            results=[],
            skipped_without_external_id=0,
            duration_ms=(perf_counter() - started) * 1000,
            provider="local",
            error=str(exc),
        )
        logger.warning("Track instant mix index missing track_id=%s model=%s error=%s", track_id, model, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=seed_item_id,
            seed_track_id=seed.id,
            model=model,
            requested_model=None,
            requested_count=None,
            effective_count=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
            min_similarity=min_similarity,
            status="failed",
            results=[],
            skipped_without_external_id=0,
            duration_ms=(perf_counter() - started) * 1000,
            provider="local",
            error=str(exc),
        )
        logger.warning("Track instant mix lookup failed track_id=%s model=%s error=%s", track_id, model, exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    results: list[NavidromeSimilarItem] = []
    for candidate in sorted(candidates, key=lambda item: item.similarity, reverse=True):
        if min_similarity is not None and candidate.similarity < float(min_similarity):
            continue
        external_id = store.external_id_for_track("navidrome", candidate.track.id)
        results.append(
            NavidromeSimilarItem(
                item_id=external_id or f"track:{candidate.track.id}",
                track_id=candidate.track.id,
                artist=candidate.track.artist,
                title=candidate.track.title,
                album=candidate.track.album,
                distance=candidate.distance,
                similarity=candidate.similarity,
            )
        )
        if len(results) >= effective_count:
            break
    record_instant_mix_request(
        store,
        request_id=request_id,
        item_id=seed_item_id,
        seed_track_id=seed.id,
        model=model,
        requested_model=None,
        requested_count=None,
        effective_count=effective_count,
        max_per_artist=effective_max_per_artist,
        exclude_same_album=effective_exclude_same_album,
        count_collaboration_artists=effective_count_collaboration_artists,
        min_similarity=min_similarity,
        status="completed",
        results=results,
        skipped_without_external_id=0,
        duration_ms=(perf_counter() - started) * 1000,
        provider="local",
    )
    result_track_ids = [item.track_id for item in results if item.track_id is not None]
    queue_track_ids = [seed.id] + [track_id for track_id in result_track_ids if track_id != seed.id]
    session, _queue = store.create_playback_session(
        source_type="track",
        source_id=seed.id,
        source_label=f"Instant Mix: {seed.artist or ''} - {seed.title or Path(seed.path).stem}".strip(" -"),
        mode="radio",
        track_ids=queue_track_ids,
        autoplay_enabled=True,
        settings=playback_session_settings(
            {
                "instant_mix_request_id": request_id,
                "instant_mix_seed_track_id": seed.id,
                "instant_mix_model": model,
            }
        ),
        state={"instant_mix": True},
    )
    request = store.get_instant_mix_request(request_id)
    return {
        "request_id": request_id,
        "request": instant_mix_request_dict(request, include_results=True, store=store) if request else None,
        **playback_session_response(store, session),
    }


@app.get("/models/head-pack")
def get_head_pack() -> dict[str, object]:
    store, settings = context()
    return head_pack_status(store, settings)


@app.get("/tracks")
def list_tracks(
    query: str = "",
    limit: int = Query(50, ge=1, le=500),
    embedding_status: str = Query("all", pattern="^(all|ready|missing)$"),
    model: str = "discogs_multi",
    folder: str | None = None,
    genre: str | None = None,
    year: int | None = None,
    artist: str | None = None,
    album: str | None = None,
) -> dict[str, object]:
    store, _settings = context()
    try:
        tracks = store.list_tracks(
            query=query,
            limit=limit,
            model_name=model,
            embedding_status=embedding_status,
            folder=folder,
            genre=genre,
            year=year,
            artist=artist,
            album=album,
        )
    except ValueError as exc:
        logger.warning("Invalid track list filters query=%s embedding_status=%s model=%s error=%s", query, embedding_status, model, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"results": [enriched_track_listing_dict(store, track) for track in tracks]}


@app.get("/tracks/search")
def search_tracks(
    q: str = "",
    limit: int = Query(50, ge=1, le=500),
    embedding_status: str = Query("all", pattern="^(all|ready|missing)$"),
    model: str = "discogs_multi",
) -> dict[str, object]:
    return list_tracks(
        query=q,
        limit=limit,
        embedding_status=embedding_status,
        model=model,
    )


@app.get("/lost-files")
def list_lost_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict[str, object]:
    store, _settings = context()
    count = store.count_missing_files()
    offset = (page - 1) * page_size
    tracks = store.list_missing_tracks(limit=page_size, offset=offset)
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "pages": max((count + page_size - 1) // page_size, 1),
        "results": [track_dict(track) for track in tracks],
    }


@app.get("/analysis/errors")
def list_analysis_errors(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict[str, object]:
    store, _settings = context()
    offset = (page - 1) * page_size
    where = """
        t.error IS NOT NULL
        AND COALESCE(t.error_type, '') != 'Cancelled'
        AND COALESCE(t.stage, '') != 'cancelled'
        AND t.error NOT LIKE 'Model file not found:%'
    """
    with store.connect() as conn:
        count = conn.execute(
            f"""
            SELECT COUNT(*) FROM (
                SELECT t.track_id, t.model_name
                FROM analysis_tasks t
                JOIN tracks tr ON tr.id = t.track_id
                WHERE {where}
                    GROUP BY t.track_id, t.model_name
            )
            """
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            WITH latest AS (
                SELECT t.track_id, t.model_name, MAX(t.updated_at) AS updated_at
                FROM analysis_tasks t
                JOIN tracks tr ON tr.id = t.track_id
                WHERE {where}
                GROUP BY t.track_id, t.model_name
            )
            SELECT
                t.id AS task_id,
                t.job_id,
                j.kind AS job_kind,
                t.track_id,
                t.model_name,
                t.status,
                t.attempts,
                t.max_attempts,
                t.path,
                t.file_size,
                t.mtime,
                t.error,
                t.error_type,
                t.stage,
                t.updated_at,
                t.completed_at,
                tr.artist,
                tr.title,
                tr.album
            FROM analysis_tasks t
            JOIN latest l
                ON l.track_id = t.track_id
                AND l.model_name = t.model_name
                AND l.updated_at = t.updated_at
            JOIN analysis_jobs j ON j.id = t.job_id
            JOIN tracks tr ON tr.id = t.track_id
            WHERE {where}
            ORDER BY t.updated_at DESC, t.id DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "pages": max((count + page_size - 1) // page_size, 1),
        "results": [dict(row) for row in rows],
    }


@app.delete("/analysis/errors")
def delete_analysis_errors(request: DeleteAnalysisErrorsRequest) -> dict[str, object]:
    store, _settings = context()
    where = """
        error IS NOT NULL
        AND COALESCE(error_type, '') != 'Cancelled'
        AND COALESCE(stage, '') != 'cancelled'
        AND error NOT LIKE 'Model file not found:%'
    """
    with store.connect() as conn:
        if request.all_errors:
            cursor = conn.execute(
                f"""
                UPDATE analysis_tasks
                SET error = NULL,
                    error_type = NULL,
                    stage = NULL,
                    updated_at = ?
                WHERE id IN (
                    SELECT t.id
                    FROM analysis_tasks t
                    JOIN tracks tr ON tr.id = t.track_id
                    WHERE {where}
                )
                """,
                (datetime.now().isoformat(),),
            )
            return {"status": "ok", "cleared": cursor.rowcount}
        task_ids = [task_id for task_id in request.task_ids if task_id]
        if not task_ids:
            return {"status": "ok", "cleared": 0}
        placeholders = ",".join("?" for _ in task_ids)
        cursor = conn.execute(
            f"""
            UPDATE analysis_tasks
            SET error = NULL,
                error_type = NULL,
                stage = NULL,
                updated_at = ?
            WHERE id IN ({placeholders})
            """,
            (datetime.now().isoformat(), *task_ids),
        )
    return {"status": "ok", "cleared": cursor.rowcount}


@app.delete("/lost-files")
def delete_lost_files(request: DeleteTracksRequest) -> dict[str, object]:
    store, _settings = context()
    if request.all_missing:
        deleted = store.delete_missing_tracks()
        logger.info("Deleted all lost file records deleted=%s", deleted)
        return {"status": "ok", "deleted": deleted}
    missing_ids = {track.id for track in store.list_missing_tracks(limit=100000)}
    track_ids = [track_id for track_id in request.track_ids if track_id in missing_ids]
    deleted = store.delete_tracks(track_ids)
    logger.info("Deleted lost file records requested=%s deleted=%s", len(request.track_ids), deleted)
    return {"status": "ok", "deleted": deleted}


@app.get("/browse/facets")
def browse_facets(
    model: str = "discogs_multi",
    embedding_status: str = Query("all", pattern="^(all|ready|missing)$"),
) -> dict[str, object]:
    store, _settings = context()
    try:
        return store.browser_facets(model_name=model, embedding_status=embedding_status)
    except ValueError as exc:
        logger.warning("Invalid browse facets filters model=%s embedding_status=%s error=%s", model, embedding_status, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/tracks/{track_id}")
def get_track(track_id: int) -> dict[str, object]:
    store, _settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Track not found track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    return enriched_track_dict(store, track)


@app.get("/tracks/{track_id}/analysis")
def get_track_analysis(track_id: int) -> dict[str, object]:
    store, _settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Track analysis requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    predictions_by_model = store.list_predictions(track_id)
    outputs = [
        {
            "model_name": output.model_name,
            "dim": int(output.scores.shape[0]),
            "dtype": output.dtype,
            "aggregation": output.aggregation,
            "scores": [float(score) for score in output.scores],
            "top_predictions": [
                prediction_dict(prediction)
                for prediction in predictions_by_model.get(output.model_name, [])
            ],
        }
        for output in store.list_model_outputs(track_id)
    ]
    output_names = {output["model_name"] for output in outputs}
    for model_name, predictions in predictions_by_model.items():
        if model_name not in output_names:
            outputs.append(
                {
                    "model_name": model_name,
                    "dim": 0,
                    "dtype": None,
                    "aggregation": None,
                    "scores": [],
                    "top_predictions": [prediction_dict(prediction) for prediction in predictions],
                }
            )
    outputs.sort(key=lambda output: str(output["model_name"]))
    return {
        "track": enriched_track_dict(store, track),
        "outputs": outputs,
        "features": [feature_dict(feature) for feature in store.load_features(track_id)],
    }


@app.head("/tracks/{track_id}/audio")
@app.get("/tracks/{track_id}/audio")
def get_track_audio(track_id: int, request: Request) -> Response:
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Audio requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    item_id = navidrome_item_id_for_track(store, track)
    if item_id is not None:
        try:
            response = navidrome_audio_stream_response(
                settings,
                item_id,
                range_header=request.headers.get("range"),
                method=request.method,
            )
        except Exception as exc:
            logger.warning(
                "Navidrome audio stream unavailable track_id=%s item_id=%s path=%s",
                track_id,
                item_id,
                track.path,
                exc_info=True,
            )
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        store.mark_track_available(track_id)
        return response
    path = Path(track.path)
    if not path.exists() or not path.is_file():
        logger.warning("Audio file missing track_id=%s path=%s", track_id, path)
        store.mark_track_missing(track_id)
        raise HTTPException(status_code=410, detail="Audio file not mounted or no longer exists")
    store.mark_track_available(track_id)
    return FileResponse(path, media_type=audio_response_media_type(path))


def navidrome_audio_stream_response(
    settings: Settings,
    item_id: str,
    *,
    range_header: str | None = None,
    method: str = "GET",
) -> StreamingResponse:
    client = NavidromeClient(settings.navidrome)
    headers = {"Accept": "*/*"}
    if range_header:
        headers["Range"] = range_header
    method = "HEAD" if method.upper() == "HEAD" else "GET"
    request = UrlRequest(client.url("stream", {"id": item_id}), headers=headers, method=method)
    try:
        upstream = urlopen(request, timeout=float(settings.navidrome.timeout_seconds))
    except HTTPError as exc:
        raise HTTPException(
            status_code=exc.code,
            detail=f"Navidrome stream unavailable: {exc.reason}",
        ) from exc
    except (OSError, URLError) as exc:
        raise RuntimeError(f"Navidrome stream unavailable: {exc}") from exc

    response_headers = navidrome_stream_headers(upstream.headers)
    content_type = normalize_audio_media_type(upstream.headers.get("Content-Type", ""))
    status_code = int(getattr(upstream, "status", None) or upstream.getcode() or 200)

    if method == "HEAD":
        upstream.close()
        return Response(
            status_code=status_code,
            media_type=content_type,
            headers=response_headers,
        )

    def body():
        try:
            while True:
                chunk = upstream.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        body(),
        status_code=status_code,
        media_type=content_type,
        headers=response_headers,
    )


def navidrome_stream_headers(headers) -> dict[str, str]:
    passthrough = {
        "Accept-Ranges",
        "Content-Length",
        "Content-Range",
        "ETag",
        "Last-Modified",
        "X-Content-Duration",
        "X-Content-Type-Options",
    }
    return {
        name: value
        for name in passthrough
        if (value := headers.get(name)) is not None
    }


def audio_response_media_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".flac":
        return "audio/flac"
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix in {".m4a", ".mp4", ".aac"}:
        return "audio/mp4"
    if suffix == ".ogg":
        return "audio/ogg"
    if suffix == ".opus":
        return "audio/opus"
    if suffix == ".wav":
        return "audio/wav"
    return None


def normalize_audio_media_type(content_type: str) -> str:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type == "audio/x-flac":
        return "audio/flac"
    return media_type or "application/octet-stream"


def cached_cover_response(cache_key: tuple[str, int]) -> tuple[bytes, str] | None:
    now = time.time()
    with COVER_CACHE_LOCK:
        cached = COVER_CACHE.get(cache_key)
        if cached is None:
            return None
        cached_at, payload, content_type = cached
        if now - cached_at > COVER_CACHE_TTL_SECONDS:
            COVER_CACHE.pop(cache_key, None)
            return None
        return payload, content_type


def cached_cover_error(cache_key: tuple[str, int]) -> str | None:
    now = time.time()
    with COVER_CACHE_LOCK:
        cached = COVER_ERROR_CACHE.get(cache_key)
        if cached is None:
            return None
        cached_at, message = cached
        if now - cached_at > COVER_ERROR_CACHE_TTL_SECONDS:
            COVER_ERROR_CACHE.pop(cache_key, None)
            return None
        return message


def remember_cover(cache_key: tuple[str, int], payload: bytes, content_type: str) -> None:
    with COVER_CACHE_LOCK:
        COVER_CACHE[cache_key] = (time.time(), payload, content_type)
        COVER_ERROR_CACHE.pop(cache_key, None)
        while len(COVER_CACHE) > COVER_CACHE_MAX_ITEMS:
            oldest_key = next(iter(COVER_CACHE))
            COVER_CACHE.pop(oldest_key, None)


def remember_cover_error(cache_key: tuple[str, int], message: str) -> None:
    with COVER_CACHE_LOCK:
        COVER_ERROR_CACHE[cache_key] = (time.time(), message)
        while len(COVER_ERROR_CACHE) > COVER_CACHE_MAX_ITEMS:
            oldest_key = next(iter(COVER_ERROR_CACHE))
            COVER_ERROR_CACHE.pop(oldest_key, None)


def cover_response(payload: bytes, content_type: str) -> Response:
    return Response(
        content=payload,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/tracks/{track_id}/cover")
def get_track_cover(track_id: int, size: int = Query(default=96, ge=32, le=600)) -> Response:
    started = perf_counter()
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Cover requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    external_id = store.external_id_for_track("navidrome", track_id)
    if external_id is None:
        raise HTTPException(status_code=404, detail="Track has no Navidrome mapping")
    mapping = store.get_external_track("navidrome", external_id)
    raw: dict[str, object] = {}
    if mapping is not None and mapping.raw_json:
        try:
            parsed = json.loads(mapping.raw_json)
            raw = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Invalid Navidrome raw_json for track_id=%s item_id=%s", track_id, external_id)
    cover_art_id = raw.get("coverArt")
    if not cover_art_id:
        raise HTTPException(status_code=404, detail="Track has no Navidrome cover art id")
    cache_key = (str(cover_art_id), size)
    cached = cached_cover_response(cache_key)
    if cached is not None:
        payload, content_type = cached
        logger.info(
            "Cover cache hit track_id=%s item_id=%s cover_art_id=%s size=%s seconds=%.3f",
            track_id,
            external_id,
            cover_art_id,
            size,
            perf_counter() - started,
        )
        return cover_response(payload, content_type)
    cached_error = cached_cover_error(cache_key)
    if cached_error is not None:
        logger.info(
            "Cover negative cache hit track_id=%s item_id=%s cover_art_id=%s size=%s error=%s seconds=%.3f",
            track_id,
            external_id,
            cover_art_id,
            size,
            cached_error,
            perf_counter() - started,
        )
        raise HTTPException(status_code=502, detail=cached_error)
    logger.info(
        "Cover fetch started track_id=%s item_id=%s cover_art_id=%s size=%s timeout_seconds=%s",
        track_id,
        external_id,
        cover_art_id,
        size,
        min(settings.navidrome.timeout_seconds, COVER_TIMEOUT_SECONDS),
    )
    try:
        cover_settings = replace(
            settings.navidrome,
            timeout_seconds=min(settings.navidrome.timeout_seconds, COVER_TIMEOUT_SECONDS),
        )
        cover = NavidromeClient(cover_settings).get_cover_art(str(cover_art_id), size=size)
    except Exception as exc:
        remember_cover_error(cache_key, str(exc))
        navidrome_logger.warning(
            "Navidrome cover art unavailable track_id=%s item_id=%s cover_art_id=%s",
            track_id,
            external_id,
            cover_art_id,
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    remember_cover(cache_key, cover.payload, cover.content_type)
    logger.info(
        "Cover fetch completed track_id=%s item_id=%s cover_art_id=%s size=%s bytes=%s seconds=%.3f",
        track_id,
        external_id,
        cover_art_id,
        size,
        len(cover.payload),
        perf_counter() - started,
    )
    return cover_response(cover.payload, cover.content_type)


@app.get("/tracks/{track_id}/similar")
def get_similar_tracks(
    track_id: int,
    model: str = "discogs_multi",
    k: int = 30,
    max_per_artist: int = 2,
    exclude_same_album: bool = True,
) -> dict[str, object]:
    store, settings = context()
    seed = store.get_track(track_id)
    if seed is None:
        logger.warning("Similar requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail="Track not found")
    try:
        results = Recommender(store, settings, model).similar(
            seed,
            k=k,
            max_per_artist=max_per_artist,
            exclude_same_album=exclude_same_album,
        )
        ratings = store.feedback_for_seed(seed.id, model)
        results = [
            replace(result, rating=ratings.get(result.track.id))
            for result in results
        ]
    except FileNotFoundError as exc:
        logger.warning("Similar index missing track_id=%s model=%s error=%s", track_id, model, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        logger.warning("Similar lookup failed track_id=%s model=%s error=%s", track_id, model, exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "seed": enriched_track_dict(store, seed),
        "model": model,
        "results": [enriched_similar_track_dict(store, result) for result in results],
    }


@app.post("/text-search")
def text_search(request: TextSearchRequest) -> dict[str, object]:
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Text query is empty")
    store, settings = context()
    started = perf_counter()
    try:
        vector = text_search_embedder(settings).extract_text_vector(query)
        results = Recommender(store, settings, MUQ_MULAN_MODEL).similar_vector(
            vector,
            exclude_track_ids=set(),
            album_seeds=[],
            k=request.count,
            max_per_artist=request.max_per_artist,
            exclude_same_album=request.exclude_same_album,
            count_collaboration_artists=request.count_collaboration_artists,
        )
    except FileNotFoundError as exc:
        logger.warning("Text search index missing model=%s error=%s", MUQ_MULAN_MODEL, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.warning("Text search embedding failed error=%s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    filtered = [
        result
        for result in results
        if request.min_similarity is None or result.similarity >= request.min_similarity
    ]
    similarities = [float(result.similarity) for result in filtered]
    logger.info(
        "Text search completed query=%r results=%s returned=%s seconds=%.3f",
        query,
        len(results),
        len(filtered),
        perf_counter() - started,
    )
    return {
        "query": query,
        "model": MUQ_MULAN_MODEL,
        "count": request.count,
        "min_similarity": request.min_similarity,
        "vector_norm": float(np.linalg.norm(vector)),
        "similarity_min": min(similarities) if similarities else None,
        "similarity_max": max(similarities) if similarities else None,
        "similarity_avg": (sum(similarities) / len(similarities)) if similarities else None,
        "results": [enriched_similar_track_dict(store, result) for result in filtered],
    }


def _parse_seed_ids_param(seed_ids: str) -> list[int]:
    parts = [part.strip() for part in seed_ids.split(",") if part.strip()]
    if not parts:
        raise HTTPException(status_code=400, detail="seed_ids is required")
    if len(parts) > MAX_MIX_SEEDS:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_MIX_SEEDS} seed_ids are allowed",
        )
    parsed: list[int] = []
    for part in parts:
        try:
            parsed.append(int(part))
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid seed id: {part}",
            ) from exc
    return parsed


@app.get("/tracks/similar/mix")
def get_similar_mix_tracks(
    seed_ids: str = Query(..., description="Comma-separated track ids"),
    model: str = "discogs_multi",
    k: int = 30,
    max_per_artist: int = 2,
    exclude_same_album: bool = True,
) -> dict[str, object]:
    store, settings = context()
    track_ids = _parse_seed_ids_param(seed_ids)
    seeds: list[Track] = []
    missing_ids: list[int] = []
    for track_id in track_ids:
        track = store.get_track(track_id)
        if track is None:
            missing_ids.append(track_id)
            continue
        seeds.append(track)
    if missing_ids:
        logger.warning("Similar mix requested missing tracks track_ids=%s", missing_ids)
        raise HTTPException(
            status_code=404,
            detail=f"Tracks not found: {', '.join(str(track_id) for track_id in missing_ids)}",
        )
    try:
        results, skipped_seed_ids = Recommender(store, settings, model).similar_mix(
            seeds,
            k=k,
            max_per_artist=max_per_artist,
            exclude_same_album=exclude_same_album,
        )
    except FileNotFoundError as exc:
        logger.warning("Similar mix index missing model=%s error=%s", model, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        logger.warning("Similar mix lookup failed model=%s error=%s", model, exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "blend": "average",
        "seeds": [enriched_track_dict(store, track) for track in seeds],
        "skipped_seed_ids": skipped_seed_ids,
        "model": model,
        "results": [enriched_similar_track_dict(store, result) for result in results],
    }


def _navidrome_client(settings: Settings) -> NavidromeClient:
    try:
        return NavidromeClient(settings.navidrome)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/navidrome/starred")
def get_navidrome_starred(model: str = "discogs_multi") -> dict[str, object]:
    store, settings = context()
    client = _navidrome_client(settings)
    try:
        return build_starred_catalog(
            store,
            client,
            model=model,
            user=settings.navidrome.user,
        )
    except Exception as exc:
        navidrome_logger.warning("Navidrome starred failed model=%s error=%s", model, exc)
        raise HTTPException(status_code=502, detail=f"Navidrome starred failed: {exc}") from exc


@app.get("/navidrome/starred/ids")
def get_navidrome_starred_ids() -> dict[str, object]:
    store, settings = context()
    client = _navidrome_client(settings)
    try:
        data = build_starred_track_ids(store, client, user=settings.navidrome.user)
        navidrome_logger.info(
            "Navidrome starred ids user=%s count=%s mapped_count=%s track_ids=%s",
            data.get("user"),
            data.get("count"),
            data.get("mapped_count"),
            data.get("track_ids"),
        )
        return data
    except Exception as exc:
        navidrome_logger.warning("Navidrome starred ids failed error=%s", exc)
        raise HTTPException(status_code=502, detail=f"Navidrome starred failed: {exc}") from exc


@app.put("/tracks/{track_id}/navidrome-star")
def set_track_navidrome_star(track_id: int, request: NavidromeStarRequest) -> dict[str, object]:
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    item_id = store.external_id_for_track("navidrome", track_id)
    if item_id is None:
        raise HTTPException(status_code=404, detail="Track has no Navidrome mapping")
    client = _navidrome_client(settings)
    try:
        if request.starred:
            client.star_song(item_id)
        else:
            client.unstar_song(item_id)
    except Exception as exc:
        navidrome_logger.warning(
            "Navidrome star update failed track_id=%s item_id=%s starred=%s error=%s",
            track_id,
            item_id,
            request.starred,
            exc,
        )
        raise HTTPException(status_code=502, detail=f"Navidrome star update failed: {exc}") from exc
    navidrome_logger.info(
        "Navidrome star update ok user=%s track_id=%s item_id=%s starred=%s",
        settings.navidrome.user,
        track_id,
        item_id,
        request.starred,
    )
    return {
        "track_id": track_id,
        "item_id": item_id,
        "starred": request.starred,
        "user": settings.navidrome.user,
    }


@app.get("/navidrome/starred/similar")
def get_navidrome_starred_similar(
    model: str = "discogs_multi",
    count: int = Query(default=50, ge=1, le=500),
    max_per_artist: int = Query(default=2, ge=1, le=100),
    exclude_same_album: bool = True,
) -> dict[str, object]:
    store, settings = context()
    client = _navidrome_client(settings)
    try:
        catalog = build_starred_catalog(
            store,
            client,
            model=model,
            user=settings.navidrome.user,
        )
    except Exception as exc:
        navidrome_logger.warning("Navidrome starred similar catalog failed model=%s error=%s", model, exc)
        raise HTTPException(status_code=502, detail=f"Navidrome starred failed: {exc}") from exc

    ready_tracks = ready_tracks_from_starred_catalog(catalog, store, model)
    if not ready_tracks:
        raise HTTPException(
            status_code=404,
            detail=(
                "No ready liked tracks with embeddings. "
                "Sync Navidrome catalog and analyze missing tracks first."
            ),
        )
    try:
        results, skipped_seed_ids = Recommender(store, settings, model).similar_mix(
            ready_tracks,
            k=count,
            max_per_artist=max_per_artist,
            exclude_same_album=exclude_same_album,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "source": "navidrome_starred",
        "blend": "average",
        "user": catalog.get("user", ""),
        "count": catalog.get("count", 0),
        "mapped_count": catalog.get("mapped_count", 0),
        "ready_count": catalog.get("ready_count", 0),
        "missing_embedding_count": catalog.get("missing_embedding_count", 0),
        "not_synced_count": catalog.get("not_synced_count", 0),
        "skipped_seed_ids": skipped_seed_ids,
        "model": model,
        "results": [enriched_similar_track_dict(store, result) for result in results],
    }


@app.get("/navidrome/similar", response_model=NavidromeSimilarResponse)
def get_navidrome_similar(
    item_id: str,
    count: int = Query(default=50, ge=1, le=500),
    model: str | None = None,
    max_per_artist: int | None = Query(default=None, ge=1, le=100),
    exclude_same_album: bool | None = None,
) -> NavidromeSimilarResponse:
    started = perf_counter()
    request_id = str(uuid4())
    store, settings = context()
    mix_settings = instant_mix_settings(settings)
    requested_model = model
    model = str(mix_settings["model"])
    effective_count = int(mix_settings["count"])
    min_similarity = mix_settings["min_similarity"]
    effective_max_per_artist = int(mix_settings["max_per_artist"])
    effective_exclude_same_album = bool(mix_settings["exclude_same_album"])
    effective_count_collaboration_artists = bool(mix_settings["count_collaboration_artists"])
    navidrome_logger.info(
        "Navidrome similar request request_id=%s item_id=%s requested_model=%s model=%s requested_count=%s effective_count=%s max_per_artist=%s exclude_same_album=%s min_similarity=%s",
        request_id,
        item_id,
        requested_model,
        model,
        count,
        effective_count,
        effective_max_per_artist,
        effective_exclude_same_album,
        min_similarity,
    )
    navidrome_plugin_logger.info(
        "api_request request_id=%s item_id=%s requested_model=%s model=%s requested_count=%s effective_count=%s max_per_artist=%s exclude_same_album=%s min_similarity=%s",
        request_id,
        item_id,
        requested_model,
        model,
        count,
        effective_count,
        effective_max_per_artist,
        effective_exclude_same_album,
        min_similarity,
    )
    seed = store.get_track_by_external_id("navidrome", item_id)
    if seed is None:
        duration_ms = (perf_counter() - started) * 1000
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=item_id,
            seed_track_id=None,
            model=model,
            requested_model=requested_model,
            requested_count=count,
            requested_max_per_artist=max_per_artist,
            requested_exclude_same_album=exclude_same_album,
            effective_count=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
            min_similarity=min_similarity,
            status="failed",
            results=[],
            skipped_without_external_id=0,
            duration_ms=duration_ms,
            error="Navidrome item_id is not synced",
        )
        navidrome_logger.warning(
            "Navidrome similar failed request_id=%s item_id=%s reason=no_external_mapping",
            request_id,
            item_id,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s model=%s reason=no_external_mapping",
            request_id,
            item_id,
            model,
        )
        raise HTTPException(status_code=404, detail="Navidrome item_id is not synced")

    try:
        candidates = Recommender(store, settings, model).similar(
            seed,
            k=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
        )
    except FileNotFoundError as exc:
        duration_ms = (perf_counter() - started) * 1000
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=item_id,
            seed_track_id=seed.id,
            model=model,
            requested_model=requested_model,
            requested_count=count,
            requested_max_per_artist=max_per_artist,
            requested_exclude_same_album=exclude_same_album,
            effective_count=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
            min_similarity=min_similarity,
            status="failed",
            results=[],
            skipped_without_external_id=0,
            duration_ms=duration_ms,
            error=str(exc),
        )
        navidrome_logger.warning(
            "Navidrome similar failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_index error=%s",
            request_id,
            item_id,
            seed.id,
            model,
            exc,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_index error=%s",
            request_id,
            item_id,
            seed.id,
            model,
            exc,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        duration_ms = (perf_counter() - started) * 1000
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=item_id,
            seed_track_id=seed.id,
            model=model,
            requested_model=requested_model,
            requested_count=count,
            requested_max_per_artist=max_per_artist,
            requested_exclude_same_album=exclude_same_album,
            effective_count=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
            min_similarity=min_similarity,
            status="failed",
            results=[],
            skipped_without_external_id=0,
            duration_ms=duration_ms,
            error=str(exc),
        )
        navidrome_logger.warning(
            "Navidrome similar failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_embedding error=%s",
            request_id,
            item_id,
            seed.id,
            model,
            exc,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_embedding error=%s",
            request_id,
            item_id,
            seed.id,
            model,
            exc,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        duration_ms = (perf_counter() - started) * 1000
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=item_id,
            seed_track_id=seed.id,
            model=model,
            requested_model=requested_model,
            requested_count=count,
            requested_max_per_artist=max_per_artist,
            requested_exclude_same_album=exclude_same_album,
            effective_count=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
            min_similarity=min_similarity,
            status="failed",
            results=[],
            skipped_without_external_id=0,
            duration_ms=duration_ms,
            error=str(exc),
        )
        navidrome_logger.exception(
            "Navidrome similar failed request_id=%s item_id=%s track_id=%s model=%s reason=unexpected",
            request_id,
            item_id,
            seed.id,
            model,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s track_id=%s model=%s reason=unexpected error=%s",
            request_id,
            item_id,
            seed.id,
            model,
            exc,
        )
        raise HTTPException(status_code=503, detail=f"Navidrome similar failed: {exc}") from exc

    results: list[NavidromeSimilarItem] = []
    skipped_without_external_id = 0
    sorted_candidates = sorted(candidates, key=lambda item: item.similarity, reverse=True)
    for candidate in sorted_candidates:
        if min_similarity is not None and candidate.similarity < float(min_similarity):
            continue
        external_id = store.external_id_for_track("navidrome", candidate.track.id)
        if external_id is None:
            skipped_without_external_id += 1
            continue
        results.append(
            NavidromeSimilarItem(
                item_id=external_id,
                track_id=candidate.track.id,
                artist=candidate.track.artist,
                title=candidate.track.title,
                album=candidate.track.album,
                distance=candidate.distance,
                similarity=candidate.similarity,
            )
        )
        if len(results) >= effective_count:
            break
    if skipped_without_external_id:
        navidrome_logger.warning(
            "Navidrome similar skipped results without external ids request_id=%s item_id=%s skipped=%s",
            request_id,
            item_id,
            skipped_without_external_id,
        )
    duration_ms = (perf_counter() - started) * 1000
    record_instant_mix_request(
        store,
        request_id=request_id,
        item_id=item_id,
        seed_track_id=seed.id,
        model=model,
        requested_model=requested_model,
        requested_count=count,
        requested_max_per_artist=max_per_artist,
        requested_exclude_same_album=exclude_same_album,
        effective_count=effective_count,
        max_per_artist=effective_max_per_artist,
        exclude_same_album=effective_exclude_same_album,
        count_collaboration_artists=effective_count_collaboration_artists,
        min_similarity=min_similarity,
        status="completed",
        results=results,
        skipped_without_external_id=skipped_without_external_id,
        duration_ms=duration_ms,
    )
    navidrome_logger.info(
        "Navidrome similar completed request_id=%s item_id=%s track_id=%s model=%s results=%s skipped_without_external_id=%s duration_ms=%.1f",
        request_id,
        item_id,
        seed.id,
        model,
        len(results),
        skipped_without_external_id,
        duration_ms,
    )
    navidrome_plugin_logger.info(
        "api_completed request_id=%s item_id=%s track_id=%s model=%s results=%s skipped_without_external_id=%s duration_ms=%.1f",
        request_id,
        item_id,
        seed.id,
        model,
        len(results),
        skipped_without_external_id,
        duration_ms,
    )
    return NavidromeSimilarResponse(
        request_id=request_id,
        seed_item_id=item_id,
        seed_track_id=seed.id,
        model=model,
        requested_count=count,
        effective_count=effective_count,
        min_similarity=min_similarity,
        skipped_without_external_id=skipped_without_external_id,
        results=results,
    )


@app.post("/jobs/analyze")
def start_analyze(request: AnalyzeRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        local_executor_enabled = request.execution_mode in {"both", "local"} and request.local_executor_enabled
        if request.model == MUQ_MULAN_MODEL:
            local_executor_enabled = False
        store, _settings = context()
        durable_job = store.create_analysis_job(
            request.model,
            request.limit,
            kind="analyze",
            local_executor_enabled=local_executor_enabled,
            workers=request.workers,
            tf_threads=request.tf_threads,
            max_attempts=request.max_attempts,
            job_id=job_id,
        )
        update_job(
            job_id,
            status="running" if durable_job.total else "completed",
            total=durable_job.total,
            message=(
                f"Queued {durable_job.total} tracks for {request.model}"
                if durable_job.total
                else "Analyzed 0 tracks, failed 0"
            ),
        )
        if local_executor_enabled and durable_job.total:
            args = (
                job_id,
                request.model,
                request.limit,
                request.workers,
                request.tf_threads,
                True,
                request.max_attempts,
                False,
            )
            if tasks is None:
                _analyze_job(*args)
            else:
                tasks.add_task(_analyze_job, *args)
        elif not durable_job.total:
            maybe_start_next_deferred_job()
        return {
            "status": "accepted",
            "job_id": job_id,
            "model": request.model,
            "limit": request.limit,
            "workers": request.workers,
            "tf_threads": request.tf_threads,
            "local_executor_enabled": local_executor_enabled,
            "execution_mode": request.execution_mode,
        }

    store, _settings = context()
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "analyze",
        f"Waiting to analyze {request.model}",
        lambda job_id: lambda: start_now(job_id, None),
        store=store,
    )
    if deferred:
        return {
            "status": "deferred",
            "job_id": deferred_job_id,
            "model": request.model,
            "limit": request.limit,
            "workers": request.workers,
            "tf_threads": request.tf_threads,
            "local_executor_enabled": request.execution_mode != "remote" and request.local_executor_enabled,
            "execution_mode": request.execution_mode,
        }
    job_id = create_job("analyze", f"Waiting to analyze {request.model}")
    return start_now(job_id, background_tasks)


@app.post("/workers/register")
def register_worker(request: WorkerRegisterRequest) -> dict[str, object]:
    store, _settings = context()
    try:
        sqlite_retry(store.expire_analysis_leases)
        sqlite_retry(lambda: store.register_analysis_worker(request.worker_id, request.models))
    except sqlite3.OperationalError as exc:
        raise_worker_sqlite_http_exception(exc, "register")
    return {"status": "ok", "worker_id": request.worker_id, "models": request.models}


@app.post("/workers/heartbeat")
def heartbeat_worker(request: WorkerRegisterRequest) -> dict[str, object]:
    store, _settings = context()
    try:
        wrote = sqlite_retry(
            lambda: store.heartbeat_analysis_worker(
                request.worker_id,
                request.models,
                min_interval_seconds=WORKER_HEARTBEAT_WRITE_INTERVAL_SECONDS,
            )
        )
    except sqlite3.OperationalError as exc:
        raise_worker_sqlite_http_exception(exc, "heartbeat")
    return {
        "status": "ok",
        "worker_id": request.worker_id,
        "models": request.models,
        "heartbeat_written": bool(wrote),
    }


@app.get("/workers")
def list_workers() -> dict[str, object]:
    store, _settings = context()
    return {"workers": [analysis_worker_dict(worker) for worker in store.list_analysis_workers()]}


@app.post("/workers/claim")
def claim_worker_tasks(request: WorkerClaimRequest) -> dict[str, object]:
    store, _settings = context()
    try:
        sqlite_retry(store.expire_analysis_leases)
        sqlite_retry(lambda: store.register_analysis_worker(request.worker_id, request.models))
        tasks = sqlite_retry(
            lambda: store.claim_analysis_tasks(
                request.worker_id,
                request.models,
                limit=request.limit,
                lease_seconds=request.lease_seconds,
            )
        )
    except sqlite3.OperationalError as exc:
        raise_worker_sqlite_http_exception(exc, "claim")
    return {"tasks": [analysis_task_dict(task) for task in tasks]}


@app.get("/workers/tasks/{task_id}/audio")
def get_worker_task_audio(task_id: str) -> FileResponse:
    store, settings = context()
    task = store.get_analysis_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "leased":
        raise HTTPException(status_code=409, detail=f"Task is not active: {task.status}")
    job_status = store.get_analysis_job_status(task.job_id)
    if job_status != "running":
        raise HTTPException(status_code=409, detail="Task job is not running")
    track = store.get_track(task.track_id)
    if track is None:
        store.fail_analysis_task(
            task_id,
            error="Track not found",
            error_type="LookupError",
            stage="audio",
            retryable=False,
        )
        raise HTTPException(status_code=404, detail="Track not found")
    if track.file_size != task.file_size or track.mtime != task.mtime:
        store.fail_analysis_task(
            task_id,
            error="Track changed after task was created",
            error_type="StaleTaskError",
            stage="audio",
            retryable=False,
        )
        raise HTTPException(status_code=409, detail="Task file identity is stale")
    if has_navidrome_audio_source(store, track):
        try:
            manager = track_audio_path(store, settings, track)
            path = manager.__enter__()
        except Exception as exc:
            store.fail_analysis_task(
                task_id,
                error=str(exc),
                error_type=type(exc).__name__,
                stage="navidrome-download",
                worker_id=task.lease_owner,
                retryable=True,
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type=audio_response_media_type(path),
            background=BackgroundTask(manager.__exit__, None, None, None),
        )
    path = Path(track.path)
    if not path.exists() or not path.is_file():
        store.mark_track_missing(track.id)
        store.fail_analysis_task(
            task_id,
            error=f"Audio file not found: {track.path}",
            error_type="FileNotFoundError",
            stage="audio",
            retryable=False,
        )
        raise HTTPException(status_code=410, detail="Audio file not mounted or no longer exists")
    return FileResponse(path, media_type=audio_response_media_type(path))


@app.post("/workers/results")
def submit_worker_results(
    request: WorkerSubmitRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    started = perf_counter()
    store, _settings = context()
    try:
        sqlite_retry(store.expire_analysis_leases)
        sqlite_retry(
            lambda: store.update_analysis_worker(
                request.worker_id,
                stage="submitting",
                message=(
                    f"submitting {len(request.results)} embedding, "
                    f"{len(request.feature_results)} feature, {len(request.head_results)} head result(s)"
                ),
            )
        )
    except sqlite3.OperationalError as exc:
        if is_sqlite_locked(exc):
            raise HTTPException(status_code=503, detail="SQLite is busy; retry submit") from exc
        raise

    def reject_task(task_id: str, exc: Exception, log_label: str) -> None:
        if is_sqlite_locked(exc):
            raise HTTPException(status_code=503, detail="SQLite is busy; retry submit") from exc
        rejected.append({"task_id": task_id, "error": str(exc)})
        try:
            sqlite_retry(
                lambda: store.fail_analysis_task(
                    task_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    stage="submit",
                    worker_id=request.worker_id,
                    retryable=False,
                )
            )
        except sqlite3.OperationalError as lock_exc:
            if is_sqlite_locked(lock_exc):
                raise HTTPException(status_code=503, detail="SQLite is busy; retry submit") from lock_exc
            raise
        except Exception:
            logger.exception("Failed to mark worker %s rejected task_id=%s", log_label, task_id)

    completed_embedding_job_ids: set[str] = set()
    completed_job_ids: set[str] = set()

    def accept_embedding(item: WorkerResultItem) -> str:
        vector = decode_worker_vector(item)
        task = validate_worker_task_identity(
            store,
            item.task_id,
            request.worker_id,
            item.track_id,
            item.model_name,
            item.file_size,
            item.mtime,
            expire_leases=False,
        )
        store.save_embedding(task.track_id, task.model_name, vector)
        store.mark_track_available(task.track_id)
        store.complete_analysis_task(
            task.id,
            request.worker_id,
            refresh_job=False,
            update_worker=False,
        )
        completed_embedding_job_ids.add(task.job_id)
        completed_job_ids.add(task.job_id)
        return task.id

    def accept_features(item: WorkerFeatureResultItem) -> str:
        task = validate_worker_task_identity(
            store,
            item.task_id,
            request.worker_id,
            item.track_id,
            item.model_name,
            item.file_size,
            item.mtime,
            expire_leases=False,
        )
        features = [
            TrackFeature(
                name=feature.name,
                value=feature.value,
                text_value=feature.text_value,
                unit=feature.unit,
                confidence=feature.confidence,
                extractor=feature.extractor,
            )
            for feature in item.features
        ]
        store.save_features(task.track_id, features)
        store.mark_track_available(task.track_id)
        store.complete_analysis_task(
            task.id,
            request.worker_id,
            refresh_job=False,
            update_worker=False,
        )
        completed_job_ids.add(task.job_id)
        return task.id

    def accept_heads(item: WorkerHeadResultItem) -> str:
        task = validate_worker_task_identity(
            store,
            item.task_id,
            request.worker_id,
            item.track_id,
            item.model_name,
            item.file_size,
            item.mtime,
            expire_leases=False,
        )
        for output in item.outputs:
            scores = decode_worker_scores(output)
            store.save_model_output(
                task.track_id,
                output.model_name,
                scores,
                output.aggregation,
            )
            store.save_predictions(
                task.track_id,
                output.model_name,
                [
                    TrackPrediction(
                        label=prediction.label,
                        score=prediction.score,
                        rank=prediction.rank,
                    )
                    for prediction in output.predictions
                ],
            )
        store.mark_track_available(task.track_id)
        store.complete_analysis_task(
            task.id,
            request.worker_id,
            refresh_job=False,
            update_worker=False,
        )
        completed_job_ids.add(task.job_id)
        return task.id

    def accept_feature_batch(items: list[WorkerFeatureResultItem]) -> list[str]:
        if not items:
            return []
        now = utc_now()
        accepted_ids: list[str] = []
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for item in items:
                row = conn.execute(
                    """
                    SELECT
                        task.*,
                        track.file_size AS current_file_size,
                        track.mtime AS current_mtime
                    FROM analysis_tasks task
                    JOIN tracks track ON track.id = task.track_id
                    WHERE task.id = ?
                    """,
                    (item.task_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("Task not found")
                if str(row["status"]) != "leased":
                    raise ValueError(f"Task is not active: {row['status']}")
                if row["lease_owner"] != request.worker_id:
                    raise ValueError("Task is not leased by this worker")
                if int(row["track_id"]) != item.track_id or row["model_name"] != item.model_name:
                    raise ValueError("Task result identity mismatch")
                if int(row["file_size"]) != item.file_size or int(row["mtime"]) != item.mtime:
                    raise ValueError("Task result is stale")
                if int(row["current_file_size"]) != item.file_size or int(row["current_mtime"]) != item.mtime:
                    raise ValueError("Track changed after task was created")

                extractors = sorted({feature.extractor for feature in item.features})
                for extractor in extractors:
                    conn.execute(
                        "DELETE FROM track_features WHERE track_id = ? AND extractor = ?",
                        (item.track_id, extractor),
                    )
                conn.executemany(
                    """
                    INSERT INTO track_features (
                        track_id, feature_name, value, text_value, unit, confidence,
                        extractor, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            item.track_id,
                            feature.name,
                            feature.value,
                            feature.text_value,
                            feature.unit,
                            feature.confidence,
                            feature.extractor,
                            now,
                        )
                        for feature in item.features
                    ],
                )
                conn.execute(
                    "UPDATE tracks SET missing_at = NULL, last_seen_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, item.track_id),
                )
                conn.execute(
                    """
                    UPDATE analysis_tasks
                    SET status = 'completed',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        error = NULL,
                        error_type = NULL,
                        stage = 'completed',
                        updated_at = ?,
                        completed_at = ?
                    WHERE id = ?
                      AND lease_owner = ?
                    """,
                    (now, now, item.task_id, request.worker_id),
                )
                completed_job_ids.add(str(row["job_id"]))
                accepted_ids.append(item.task_id)
        return accepted_ids

    def accept_with_retry(operation):
        try:
            return sqlite_retry(operation)
        except sqlite3.OperationalError as exc:
            if is_sqlite_locked(exc):
                raise HTTPException(status_code=503, detail="SQLite is busy; retry submit") from exc
            raise

    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    accept_started = perf_counter()
    for item in request.results:
        try:
            accepted.append(accept_with_retry(lambda item=item: accept_embedding(item)))
        except HTTPException:
            raise
        except Exception as exc:
            reject_task(item.task_id, exc, "result")
    if request.feature_results:
        try:
            accepted.extend(accept_with_retry(lambda: accept_feature_batch(request.feature_results)))
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Batch feature submit failed; falling back to per-item submit worker_id=%s count=%s",
                request.worker_id,
                len(request.feature_results),
            )
            for item in request.feature_results:
                try:
                    accepted.append(accept_with_retry(lambda item=item: accept_features(item)))
                except HTTPException:
                    raise
                except Exception as exc:
                    reject_task(item.task_id, exc, "feature result")
    for item in request.head_results:
        try:
            accepted.append(accept_with_retry(lambda item=item: accept_heads(item)))
        except HTTPException:
            raise
        except Exception as exc:
            reject_task(item.task_id, exc, "head result")
    accept_seconds = perf_counter() - accept_started
    sqlite_retry(
        lambda: store.update_analysis_worker(
            request.worker_id,
            stage="submitted",
            message=f"accepted {len(accepted)}, rejected {len(rejected)} result(s)",
            current_task_id=None,
            completed_delta=len(accepted),
        )
    )
    refresh_started = perf_counter()
    for completed_job_id in sorted(completed_job_ids):
        sqlite_retry(lambda completed_job_id=completed_job_id: store.refresh_analysis_job(completed_job_id))
    refresh_seconds = perf_counter() - refresh_started
    for completed_job_id in sorted(completed_embedding_job_ids):
        schedule_auto_index_for_analysis(store, completed_job_id, background_tasks)
    total_seconds = perf_counter() - started
    if total_seconds >= 1.0 or len(accepted) + len(rejected) >= 16:
        logger.info(
            "Worker submit completed worker_id=%s embeddings=%s features=%s heads=%s "
            "accepted=%s rejected=%s accept_seconds=%.3f refresh_seconds=%.3f total_seconds=%.3f",
            request.worker_id,
            len(request.results),
            len(request.feature_results),
            len(request.head_results),
            len(accepted),
            len(rejected),
            accept_seconds,
            refresh_seconds,
            total_seconds,
        )
    return {"status": "ok", "accepted": accepted, "rejected": rejected}


@app.post("/workers/failures")
def submit_worker_failures(
    request: WorkerFailuresRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
    store.update_analysis_worker(
        request.worker_id,
        stage="reporting_failures",
        message=f"reporting {len(request.failures)} failure(s)",
    )
    failed: list[str] = []
    candidate_job_ids: set[str] = set()
    for item in request.failures:
        task = store.get_analysis_task(item.task_id)
        if task is not None:
            candidate_job_ids.add(task.job_id)
        store.fail_analysis_task(
            item.task_id,
            error=item.error,
            error_type=item.error_type,
            stage=item.stage,
            worker_id=request.worker_id,
            retryable=item.retryable,
        )
        failed.append(item.task_id)
    store.update_analysis_worker(
        request.worker_id,
        stage="failures_submitted",
        message=f"accepted {len(failed)} failure(s)",
        current_task_id=None,
    )
    for completed_job_id in sorted(candidate_job_ids):
        schedule_auto_index_for_analysis(store, completed_job_id, background_tasks)
    return {"status": "ok", "failed": failed}


@app.post("/workers/release")
def release_worker_tasks(request: WorkerReleaseRequest) -> dict[str, object]:
    store, _settings = context()
    store.expire_analysis_leases()
    released = store.release_analysis_tasks(request.worker_id, request.task_ids)
    return {"status": "ok", "released": released}


@app.post("/models/download-head-pack")
def download_head_pack() -> dict[str, object]:
    _store, settings = context()
    results = download_head_pack_models(settings)
    return {
        "status": "ok",
        "downloaded": [str(result.path) for result in results if result.downloaded],
        "already_present": [str(result.path) for result in results if not result.downloaded],
        "head_pack": head_pack_readiness(settings),
    }


@app.post("/jobs/download-head-models")
def start_download_head_models(background_tasks: BackgroundTasks) -> dict[str, object]:
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "download-head-models",
        "Waiting to download head models",
        lambda job_id: lambda: _download_head_models_job(job_id),
    )
    if deferred:
        return {"status": "deferred", "job_id": deferred_job_id}
    job_id = create_job("download-head-models", "Waiting to download head models")
    background_tasks.add_task(_download_head_models_job, job_id)
    return {"status": "accepted", "job_id": job_id}


@app.post("/jobs/analyze-heads")
def start_analyze_heads(
    request: AnalyzeHeadsRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        local_enabled = request.execution_mode in {"both", "local"} and request.local_executor_enabled
        store, _settings = context()
        head_model_names = [head.id for head in DISCOGS_EFFNET_HEADS]
        tracks = store.list_tracks_missing_head_pack(head_model_names, limit=request.limit)
        durable_job = store.create_analysis_job(
            "discogs-effnet-heads",
            request.limit,
            kind="analyze-heads",
            tracks=tracks,
            local_executor_enabled=local_enabled,
            max_attempts=request.max_attempts,
            job_id=job_id,
        )
        update_job(
            job_id,
            status="running" if durable_job.total else "completed",
            total=durable_job.total,
            message=(
                f"Queued {durable_job.total} head analysis tasks"
                if durable_job.total
                else "Analyzed heads for 0 tracks, failed 0"
            ),
        )
        if local_enabled and durable_job.total:
            args = (job_id, request.limit, True, request.max_attempts, False)
            if tasks is None:
                _analyze_heads_job(*args)
            else:
                tasks.add_task(_analyze_heads_job, *args)
        elif not durable_job.total:
            maybe_start_next_deferred_job()
        return {
            "status": "accepted",
            "job_id": job_id,
            "limit": request.limit,
            "execution_mode": request.execution_mode,
            "local_executor_enabled": local_enabled,
        }

    store, _settings = context()
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "analyze-heads",
        "Waiting to analyze Discogs-EffNet heads",
        lambda job_id: lambda: start_now(job_id, None),
        store=store,
    )
    if deferred:
        return {
            "status": "deferred",
            "job_id": deferred_job_id,
            "limit": request.limit,
            "execution_mode": request.execution_mode,
            "local_executor_enabled": request.execution_mode != "remote" and request.local_executor_enabled,
        }
    job_id = create_job("analyze-heads", "Waiting to analyze Discogs-EffNet heads")
    return start_now(job_id, background_tasks)


@app.post("/jobs/analyze-audio-features")
def start_analyze_audio_features(
    request: AnalyzeAudioFeaturesRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    extractor = request.extractor.strip() or AUDIO_FEATURE_EXTRACTOR
    if extractor != AUDIO_FEATURE_EXTRACTOR:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio feature extractor: {extractor}",
        )

    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        local_enabled = request.execution_mode in {"both", "local"} and request.local_executor_enabled
        store, _settings = context()
        if request.reset_existing:
            tracks = store.list_active_tracks(limit=request.limit)
            deleted_features = store.delete_features_for_tracks(
                [track.id for track in tracks],
                extractor,
            )
            logger.info(
                "Reset audio features before analysis tracks=%s deleted_features=%s extractor=%s",
                len(tracks),
                deleted_features,
                extractor,
            )
        else:
            tracks = store.list_tracks_missing_features(extractor, limit=request.limit)
            deleted_features = 0
        clear_stats_cache()
        durable_job = store.create_analysis_job(
            extractor,
            request.limit,
            kind="analyze-audio-features",
            tracks=tracks,
            local_executor_enabled=local_enabled,
            max_attempts=request.max_attempts,
            job_id=job_id,
        )
        update_job(
            job_id,
            status="running" if durable_job.total else "completed",
            total=durable_job.total,
            message=(
                f"Queued {durable_job.total} audio feature tasks"
                if durable_job.total
                else "Analyzed audio features for 0 tracks, failed 0"
            ),
        )
        if local_enabled and durable_job.total:
            args = (job_id, request.limit, request.workers, True, request.max_attempts, False)
            if tasks is None:
                _analyze_audio_features_job(*args)
            else:
                tasks.add_task(_analyze_audio_features_job, *args)
        elif not durable_job.total:
            maybe_start_next_deferred_job()
        return {
            "status": "accepted",
            "job_id": job_id,
            "limit": request.limit,
            "workers": request.workers,
            "execution_mode": request.execution_mode,
            "local_executor_enabled": local_enabled,
            "reset_existing": request.reset_existing,
            "deleted_features": deleted_features,
            "extractor": extractor,
        }

    store, _settings = context()
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "analyze-audio-features",
        "Waiting to analyze audio features",
        lambda job_id: lambda: start_now(job_id, None),
        store=store,
    )
    if deferred:
        return {
            "status": "deferred",
            "job_id": deferred_job_id,
            "limit": request.limit,
            "workers": request.workers,
            "execution_mode": request.execution_mode,
            "local_executor_enabled": request.execution_mode != "remote" and request.local_executor_enabled,
            "reset_existing": request.reset_existing,
            "deleted_features": 0,
            "extractor": extractor,
        }
    job_id = create_job("analyze-audio-features", "Waiting to analyze audio features")
    return start_now(job_id, background_tasks)


@app.post("/jobs/analyze-genres")
def start_analyze_genres_compat(
    request: AnalyzeHeadsRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    return start_analyze_heads(request, background_tasks)


@app.post("/jobs/index")
def start_index(request: IndexRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "index",
        f"Waiting to build index for {request.model}",
        lambda job_id: lambda: _index_job(job_id, request.model),
    )
    if deferred:
        return {"status": "deferred", "job_id": deferred_job_id, "model": request.model}
    job_id = create_job("index", f"Waiting to build index for {request.model}")
    background_tasks.add_task(_index_job, job_id, request.model)
    return {"status": "accepted", "job_id": job_id, "model": request.model}


@app.post("/jobs/check-missing-files")
def start_check_missing_files(background_tasks: BackgroundTasks) -> dict[str, object]:
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "check-missing-files",
        "Waiting to check file availability",
        lambda job_id: lambda: _check_missing_files_job(job_id),
    )
    if deferred:
        return {"status": "deferred", "job_id": deferred_job_id}
    job_id = create_job("check-missing-files", "Waiting to check file availability")
    background_tasks.add_task(_check_missing_files_job, job_id)
    return {"status": "accepted", "job_id": job_id}


@app.post("/jobs/navidrome-sync")
def start_navidrome_sync(
    request: NavidromeSyncRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    def start_now(job_id: str, tasks: BackgroundTasks | None) -> dict[str, object]:
        store, _settings = context()
        known_total = request.limit or store.count_external_tracks("navidrome") or 0
        job = store.create_progress_job(
            "navidrome-sync",
            "navidrome",
            total=known_total,
            message="Waiting to sync Navidrome catalog",
            job_id=job_id,
        )
        args = (job.id, request.page_size, request.limit, request.mark_stale)
        if tasks is None:
            _navidrome_sync_job(*args)
        else:
            tasks.add_task(_navidrome_sync_job, *args)
        return {
            "status": "accepted",
            "job_id": job.id,
            "page_size": request.page_size,
            "limit": request.limit,
            "mark_stale": request.mark_stale,
        }

    store, _settings = context()
    deferred_job_id, deferred = create_deferred_job_if_busy(
        "navidrome-sync",
        "Waiting to sync Navidrome catalog",
        lambda job_id: lambda: start_now(job_id, None),
        store=store,
    )
    if deferred:
        return {
            "status": "deferred",
            "job_id": deferred_job_id,
            "page_size": request.page_size,
            "limit": request.limit,
            "mark_stale": request.mark_stale,
        }
    job_id = create_job("navidrome-sync", "Waiting to sync Navidrome catalog")
    return start_now(job_id, background_tasks)


@app.get("/jobs")
def list_jobs(
    include_completed: bool = False,
    detail: bool = False,
    include_workers: bool = False,
) -> dict[str, object]:
    store, _settings = context()
    statuses = None if include_completed else sorted(ACTIVE_JOB_STATUSES)
    recent_jobs = store.recent_analysis_jobs(limit=100 if include_completed else 20, statuses=statuses)
    sync_memory_jobs_from_durable_jobs(recent_jobs)
    workers = store.list_analysis_workers() if detail else []
    durable_jobs = {
        job.id: analysis_job_status_dict(job, store, detail=detail, workers=workers)
        for job in recent_jobs[:20]
    }
    with JOBS_LOCK:
        now = perf_counter()
        queue_positions = {job_id: index + 1 for index, job_id in enumerate(DEFERRED_JOB_ORDER)}
        jobs = []
        for job in JOBS.values():
            if job.id in durable_jobs:
                continue
            if not include_completed and job.status not in {"queued", "running", "deferred"}:
                continue
            data = asdict(job)
            if job.status in {"queued", "running"}:
                data["elapsed_seconds"] = max(0.0, now - job.started_at)
            elif job.finished_at is not None:
                data["elapsed_seconds"] = max(0.0, job.finished_at - job.started_at)
                data["eta_seconds"] = None
            if job.status == "deferred":
                data["queue_position"] = queue_positions.get(job.id)
                data["status_hint"] = "Waiting for previous job to finish"
            jobs.append(data)
    jobs.extend(durable_jobs.values())
    jobs.sort(
        key=lambda job: float(job.get("created_at_epoch") or job.get("started_at") or 0.0),
        reverse=True,
    )
    response: dict[str, object] = {"jobs": jobs[:20]}
    if include_workers:
        response["workers"] = [analysis_worker_dict(worker) for worker in store.list_analysis_workers()]
    return response


@app.get("/jobs/{job_id}")
def get_job_detail(job_id: str) -> dict[str, object]:
    store, _settings = context()
    job = store.get_analysis_job(job_id)
    if job is None:
        with JOBS_LOCK:
            memory_job = JOBS.get(job_id)
        if memory_job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"job": asdict(memory_job), "tasks": []}
    statuses = ["leased", "queued", "failed_retryable", "final_failed"]
    tasks = store.list_analysis_job_tasks(job_id, statuses=statuses, limit=200)
    return {
        "job": analysis_job_status_dict(job, store, detail=True),
        "tasks": [analysis_task_dict(task) for task in tasks],
    }


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: CancelJobRequest | None = None) -> dict[str, object]:
    reason = request.reason if request is not None else "Cancelled by user"
    store, _settings = context()
    durable_job = store.cancel_analysis_job(job_id, reason)
    was_deferred = False
    with JOBS_LOCK:
        memory_job = JOBS.get(job_id)
        if memory_job is not None:
            was_deferred = memory_job.status == "deferred"
            memory_job.status = "cancelled"
            memory_job.message = reason
            memory_job.current = None
            memory_job.finished_at = perf_counter()
    if was_deferred:
        with DEFERRED_JOBS_LOCK:
            DEFERRED_JOB_STARTERS.pop(job_id, None)
            if job_id in DEFERRED_JOB_ORDER:
                DEFERRED_JOB_ORDER.remove(job_id)
        maybe_start_next_deferred_job()
    if durable_job is None and memory_job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "status": "cancelled",
        "job_id": job_id,
        "job": analysis_job_status_dict(durable_job, store) if durable_job is not None else asdict(memory_job),
    }


@app.post("/index/rebuild")
def rebuild_index(request: IndexRequest) -> dict[str, object]:
    store, settings = context()
    try:
        logger.info("Rebuilding index via API model=%s", request.model)
        path = build_index(store, settings, request.model)
    except ValueError as exc:
        logger.warning("Index rebuild failed model=%s error=%s", request.model, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "model": request.model, "index": str(path)}


@app.post("/feedback")
def save_feedback(request: FeedbackRequest) -> dict[str, str]:
    store, _settings = context()
    if store.get_track(request.seed_track_id) is None:
        logger.warning("Feedback seed track not found seed_track_id=%s", request.seed_track_id)
        raise HTTPException(status_code=404, detail="Seed track not found")
    if store.get_track(request.result_track_id) is None:
        logger.warning("Feedback result track not found result_track_id=%s", request.result_track_id)
        raise HTTPException(status_code=404, detail="Result track not found")
    try:
        store.save_feedback(
            request.seed_track_id,
            request.result_track_id,
            request.model,
            request.rating,
            request.note,
        )
    except ValueError as exc:
        logger.warning("Feedback validation failed seed_track_id=%s result_track_id=%s model=%s error=%s", request.seed_track_id, request.result_track_id, request.model, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info(
        "Saved feedback seed_track_id=%s result_track_id=%s model=%s rating=%s",
        request.seed_track_id,
        request.result_track_id,
        request.model,
        request.rating,
    )
    return {"status": "ok"}


def _check_missing_files_job(job_id: str) -> None:
    try:
        store, _settings = context()
        total = store.count_tracks()
        logger.info("Starting missing-file check job_id=%s total=%s", job_id, total)
        update_job(
            job_id,
            status="running",
            total=total,
            message=f"Checking availability for {total} tracks",
        )
        checked, missing = store.check_file_availability()
        update_job(job_id, done=checked, failed=0)
        finish_job(job_id, "completed", f"Checked {checked} tracks, lost files {missing}")
        logger.info("Finished missing-file check job_id=%s checked=%s missing=%s", job_id, checked, missing)
    except Exception as exc:
        logger.exception("Missing-file check failed job_id=%s", job_id)
        finish_job(job_id, "failed", str(exc))


def _navidrome_sync_job(
    job_id: str,
    page_size: int,
    limit: int | None,
    mark_stale: bool,
) -> None:
    store: Store | None = None
    try:
        store, settings = context()
        client = NavidromeClient(settings.navidrome)
        logger.info(
            "Starting Navidrome sync job job_id=%s page_size=%s limit=%s mark_stale=%s",
            job_id,
            page_size,
            limit,
            mark_stale,
        )
        known_total = limit or store.count_external_tracks("navidrome") or 0
        store.update_progress_job(
            job_id,
            status="running",
            total=known_total,
            message="Syncing Navidrome catalog",
        )

        def progress(count, song) -> None:
            nonlocal known_total
            if count > known_total:
                known_total = count
            if count == 1 or count % 25 == 0:
                current = f"{song.id} {song.artist or ''} - {song.title or ''}".strip()
                store.update_progress_job(
                    job_id,
                    done=count,
                    total=known_total,
                    message=f"Synced {count} Navidrome songs; current {current}",
                )

        result = sync_navidrome_catalog(
            store,
            client,
            page_size=page_size,
            limit=limit,
            mark_stale=mark_stale,
            progress=progress,
        )
        status = "failed" if result.failed_count else "completed"
        store.update_progress_job(
            job_id,
            done=result.seen_count,
            failed=result.failed_count,
            total=max(known_total, result.seen_count),
            status=status,
            message=f"Navidrome sync {result.summary()}",
            finished=True,
        )
        logger.info("Finished Navidrome sync job job_id=%s %s", job_id, result.summary())
        maybe_start_next_deferred_job()
    except Exception as exc:
        logger.exception("Navidrome sync job failed job_id=%s", job_id)
        if store is not None:
            store.update_progress_job(job_id, status="failed", message=str(exc), finished=True)
            maybe_start_next_deferred_job()
        else:
            finish_job(job_id, "failed", str(exc))


@dataclass(frozen=True)
class AnalyzeResult:
    task_id: str | None
    track_id: int
    path: str
    status: str
    vector: np.ndarray | None = None
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    stage: str | None = None


@dataclass(frozen=True)
class HeadAnalyzeResult:
    track_id: int
    path: str
    status: str
    outputs: list[HeadOutput] | None = None
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    stage: str | None = None


@dataclass(frozen=True)
class AudioFeaturesResult:
    track_id: int
    path: str
    status: str
    features: list | None = None
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None
    stage: str | None = None


def analyze_failure_fields(exc: Exception, stage: str) -> dict[str, str]:
    return {
        "error": str(exc),
        "error_type": type(exc).__name__,
        "traceback": traceback.format_exc(),
        "stage": stage,
    }


def embedding_failure_stage(exc: Exception) -> str:
    message = str(exc).lower()
    if any(token in message for token in ["ffmpeg", "audio", "codec", "sample", "decode"]):
        return "load_audio"
    return "predict"


def mark_missing_after_failure(store: Store, result: AnalyzeResult | HeadAnalyzeResult | AudioFeaturesResult) -> None:
    if result.error_type == "FileNotFoundError":
        store.mark_track_missing(result.track_id)


def analyze_failure_retryable(result: AnalyzeResult | HeadAnalyzeResult | AudioFeaturesResult) -> bool:
    if result.error_type == "FileNotFoundError":
        return False
    text = (result.error or "").lower()
    terminal_fragments = (
        "torch is required for muq-mulan",
        "muq is required for muq-mulan",
        "muq-mulan model could not be loaded",
        "model file not found",
        "embedding vector has zero norm",
    )
    return not any(fragment in text for fragment in terminal_fragments)


def create_analyze_embedder(settings: Settings, model: str) -> object:
    if model == MUQ_MULAN_MODEL:
        return create_track_embedder(settings, model)
    return DiscogsEffnetEmbedder(settings, model)


_WORKER_EMBEDDER: object | None = None
_WORKER_AUDIO_FEATURE_ANALYZER: AudioFeatureAnalyzer | None = None


def configure_analyze_runtime(tf_threads: int) -> None:
    os.environ["TF_NUM_INTRAOP_THREADS"] = str(tf_threads)
    os.environ["TF_NUM_INTEROP_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = str(tf_threads)


def _init_embedding_worker(settings: Settings, model: str, tf_threads: int) -> None:
    global _WORKER_EMBEDDER
    configure_analyze_runtime(tf_threads)
    _WORKER_EMBEDDER = create_analyze_embedder(settings, model)
    analysis_logger.info("Initialized embedding worker model=%s tf_threads=%s", model, tf_threads)


def _extract_embedding_worker(task_id: str | None, track_id: int, path: str) -> AnalyzeResult:
    if _WORKER_EMBEDDER is None:
        raise RuntimeError("Embedding worker was not initialized")
    try:
        vector = _WORKER_EMBEDDER.extract_track_vector(Path(path))
        return AnalyzeResult(task_id=task_id, track_id=track_id, path=path, status="ok", vector=vector)
    except Exception as exc:
        return AnalyzeResult(
            task_id=task_id,
            track_id=track_id,
            path=path,
            status="failed",
            **analyze_failure_fields(exc, embedding_failure_stage(exc)),
        )


def _init_audio_feature_worker() -> None:
    global _WORKER_AUDIO_FEATURE_ANALYZER
    os.environ.setdefault("DISCOCS_FFMPEG_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    _WORKER_AUDIO_FEATURE_ANALYZER = AudioFeatureAnalyzer()
    analysis_logger.info("Initialized audio feature worker extractor=%s", AUDIO_FEATURE_EXTRACTOR)


def _extract_audio_features_worker(track_id: int, path: str) -> AudioFeaturesResult:
    if _WORKER_AUDIO_FEATURE_ANALYZER is None:
        raise RuntimeError("Audio feature worker was not initialized")
    try:
        features = _WORKER_AUDIO_FEATURE_ANALYZER.analyze_track(Path(path))
        return AudioFeaturesResult(
            track_id=track_id,
            path=path,
            status="ok",
            features=features,
        )
    except Exception as exc:
        return AudioFeaturesResult(
            track_id=track_id,
            path=path,
            status="failed",
            **analyze_failure_fields(exc, "audio_features"),
        )


def _extract_embedding_local(
    embedder: object,
    store: Store,
    settings: Settings,
    track: Track,
) -> AnalyzeResult:
    audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
    if failure is not None:
        return failure
    try:
        vector = embedder.extract_track_vector(audio_path)
        return AnalyzeResult(task_id=None, track_id=track.id, path=track.path, status="ok", vector=vector)
    except Exception as exc:
        return AnalyzeResult(
            task_id=None,
            track_id=track.id,
            path=track.path,
            status="failed",
            **analyze_failure_fields(exc, embedding_failure_stage(exc)),
        )
    finally:
        _cleanup_audio_manager(manager)


def _prepare_analyze_audio_path(
    store: Store,
    settings: Settings,
    track: Track,
) -> tuple[Path | None, object | None, AnalyzeResult | None]:
    manager = track_audio_path(store, settings, track)
    try:
        return manager.__enter__(), manager, None
    except Exception as exc:
        return (
            None,
            None,
            AnalyzeResult(
                task_id=None,
                track_id=track.id,
                path=track.path,
                status="failed",
                **analyze_failure_fields(exc, "navidrome-download" if has_navidrome_audio_source(store, track) else embedding_failure_stage(exc)),
            ),
        )


def _cleanup_audio_manager(manager: object | None) -> None:
    if manager is None:
        return
    try:
        manager.__exit__(None, None, None)
    except Exception:
        logger.debug("Audio source cleanup failed", exc_info=True)


def register_analyze_executor(executor: ProcessPoolExecutor) -> None:
    with ANALYZE_EXECUTORS_LOCK:
        ANALYZE_EXECUTORS.add(executor)


def unregister_analyze_executor(executor: ProcessPoolExecutor) -> None:
    with ANALYZE_EXECUTORS_LOCK:
        ANALYZE_EXECUTORS.discard(executor)


def terminate_process_pool(executor: ProcessPoolExecutor) -> None:
    processes = list((getattr(executor, "_processes", {}) or {}).values())
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    for process in processes:
        try:
            if process.is_alive():
                process.terminate()
        except Exception:
            pass
    for process in processes:
        try:
            process.join(timeout=1)
        except Exception:
            pass
    for process in processes:
        try:
            if process.is_alive():
                process.kill()
        except Exception:
            pass


def _iter_analyze_results(
    tracks: list[Track],
    store: Store,
    settings: Settings,
    model: str,
    workers: int,
    tf_threads: int,
):
    if SHUTDOWN_REQUESTED:
        return
    configure_analyze_runtime(tf_threads)
    if workers <= 1 or model == MUQ_MULAN_MODEL:
        embedder = create_analyze_embedder(settings, model)
        for track in tracks:
            if SHUTDOWN_REQUESTED:
                return
            yield _extract_embedding_local(embedder, store, settings, track)
        return

    executor = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_embedding_worker,
        initargs=(settings, model, tf_threads),
        mp_context=multiprocessing.get_context("spawn"),
    )
    register_analyze_executor(executor)
    audio_managers: list[object] = []
    try:
        future_to_track = {}
        for track in tracks:
            audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
            if failure is not None:
                yield failure
                continue
            audio_managers.append(manager)
            future = executor.submit(_extract_embedding_worker, None, track.id, str(audio_path))
            future_to_track[future] = track
        for future in as_completed(future_to_track):
            if SHUTDOWN_REQUESTED:
                break
            track = future_to_track[future]
            try:
                yield future.result()
            except Exception as exc:
                yield AnalyzeResult(
                    task_id=None,
                    track_id=track.id,
                    path=track.path,
                    status="failed",
                    **analyze_failure_fields(exc, "predict"),
                )
    finally:
        for manager in audio_managers:
            _cleanup_audio_manager(manager)
        unregister_analyze_executor(executor)
        if SHUTDOWN_REQUESTED:
            terminate_process_pool(executor)
        else:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            except Exception:
                pass


def task_to_track(task: AnalysisTask) -> Track:
    return Track(
        id=task.track_id,
        path=task.path,
        artist=None,
        title=None,
        album=None,
        duration=None,
        file_size=task.file_size,
        mtime=task.mtime,
    )


def _iter_analyze_task_results(
    tasks: list[AnalysisTask],
    store: Store,
    settings: Settings,
    model: str,
    workers: int,
    tf_threads: int,
):
    if SHUTDOWN_REQUESTED:
        return
    configure_analyze_runtime(tf_threads)
    if workers <= 1 or model == MUQ_MULAN_MODEL:
        embedder = create_analyze_embedder(settings, model)
        for task in tasks:
            if SHUTDOWN_REQUESTED:
                return
            result = _extract_embedding_local(embedder, store, settings, task_to_track(task))
            yield replace(result, task_id=task.id)
        return

    executor = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_embedding_worker,
        initargs=(settings, model, tf_threads),
        mp_context=multiprocessing.get_context("spawn"),
    )
    register_analyze_executor(executor)
    audio_managers: list[object] = []
    try:
        future_to_task = {}
        for task in tasks:
            track = task_to_track(task)
            audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
            if failure is not None:
                yield replace(failure, task_id=task.id)
                continue
            audio_managers.append(manager)
            future = executor.submit(_extract_embedding_worker, task.id, task.track_id, str(audio_path))
            future_to_task[future] = task
        for future in as_completed(future_to_task):
            if SHUTDOWN_REQUESTED:
                break
            task = future_to_task[future]
            try:
                yield future.result()
            except Exception as exc:
                yield AnalyzeResult(
                    task_id=task.id,
                    track_id=task.track_id,
                    path=task.path,
                    status="failed",
                    **analyze_failure_fields(exc, "predict"),
                )
    finally:
        for manager in audio_managers:
            _cleanup_audio_manager(manager)
        unregister_analyze_executor(executor)
        if SHUTDOWN_REQUESTED:
            terminate_process_pool(executor)
        else:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            except Exception:
                pass


def _analyze_job(
    job_id: str,
    model: str,
    limit: int | None,
    workers: int = DEFAULT_ANALYZE_WORKERS,
    tf_threads: int = DEFAULT_ANALYZE_TF_THREADS,
    local_executor_enabled: bool = True,
    max_attempts: int = 3,
    enqueue: bool = True,
) -> None:
    try:
        if SHUTDOWN_REQUESTED:
            finish_job(job_id, "failed", "Analyze cancelled during application shutdown")
            return
        store, settings = context()
        if enqueue:
            durable_job = store.create_analysis_job(
                model,
                limit,
                kind="analyze",
                local_executor_enabled=local_executor_enabled,
                workers=workers,
                tf_threads=tf_threads,
                max_attempts=max_attempts,
                job_id=job_id,
            )
        else:
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                raise ValueError(f"Analysis job not found: {job_id}")
        total = durable_job.total
        started_at = perf_counter()
        analysis_logger.info(
            "Starting analyze job job_id=%s model=%s limit=%s workers=%s tf_threads=%s local_executor=%s total=%s",
            job_id,
            model,
            limit,
            workers,
            tf_threads,
            local_executor_enabled,
            total,
        )
        update_job(
            job_id,
            status="running",
            total=total,
            message=(
                f"Analyzing {total} tracks with {model} on {workers} worker(s), "
                f"tf_threads={tf_threads}"
            ),
            **analyze_progress(started_at, total, 0, 0),
        )
        if not local_executor_enabled or total == 0:
            if total == 0:
                finish_job(job_id, "completed", "Analyzed 0 tracks, failed 0")
            else:
                update_job(job_id, status="running", message=f"Queued {total} tracks for remote workers")
            return

        local_worker_id = f"local-{job_id}"
        done = 0
        failed = 0
        while True:
            if SHUTDOWN_REQUESTED:
                finish_job(job_id, "failed", "Analyze cancelled during application shutdown")
                return
            tasks = store.claim_analysis_tasks(
                local_worker_id,
                [model],
                limit=max(workers, 1),
                lease_seconds=3600,
            )
            if not tasks:
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None or durable_job.status not in ACTIVE_JOB_STATUSES:
                    break
                update_job(
                    job_id,
                    done=durable_job.done,
                    failed=durable_job.failed,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, durable_job.done, durable_job.failed),
                )
                time.sleep(2)
                continue
            for result in _iter_analyze_task_results(tasks, store, settings, model, workers, tf_threads):
                if SHUTDOWN_REQUESTED:
                    finish_job(job_id, "failed", "Analyze cancelled during application shutdown")
                    return
                update_job(job_id, current=result.path, message=f"Analyzing {result.path}")
                if result.task_id is None:
                    continue
                if result.status == "ok" and result.vector is not None:
                    try:
                        store.save_embedding(result.track_id, model, result.vector)
                        store.mark_track_available(result.track_id)
                        store.complete_analysis_task(result.task_id, local_worker_id)
                    except Exception as exc:
                        store.fail_analysis_task(
                            result.task_id,
                            error=str(exc),
                            error_type=type(exc).__name__,
                            stage="save",
                            worker_id=local_worker_id,
                            retryable=False,
                        )
                        analysis_logger.exception(
                            "Analyze save failed job_id=%s track_id=%s path=%s model=%s stage=save",
                            job_id,
                            result.track_id,
                            result.path,
                            model,
                        )
                else:
                    mark_missing_after_failure(store, result)
                    store.fail_analysis_task(
                        result.task_id,
                        error=result.error or "Analyze failed",
                        error_type=result.error_type or "AnalyzeError",
                        stage=result.stage or "predict",
                        worker_id=local_worker_id,
                        retryable=analyze_failure_retryable(result),
                    )
                    analysis_logger.error(
                        "Analyze track failed job_id=%s track_id=%s path=%s model=%s stage=%s error_type=%s error=%s\n%s",
                        job_id,
                        result.track_id,
                        result.path,
                        model,
                        result.stage,
                        result.error_type,
                        result.error,
                        result.traceback or "",
                    )
                durable_job = store.get_analysis_job(job_id)
                if durable_job is not None:
                    done = durable_job.done
                    failed = durable_job.failed
                    update_job(
                        job_id,
                        done=done,
                        failed=failed,
                        current=result.path,
                        message=durable_job.message,
                        **analyze_progress(started_at, total, done, failed),
                    )
                    if (done + failed) % 25 == 0 or done + failed == total:
                        progress = analyze_progress(started_at, total, done, failed)
                        analysis_logger.info(
                            "Analyze progress job_id=%s model=%s done=%s failed=%s total=%s elapsed=%.1f tracks_per_min=%s eta_seconds=%s",
                            job_id,
                            model,
                            done,
                            failed,
                            total,
                            progress["elapsed_seconds"],
                            progress["tracks_per_min"],
                            progress["eta_seconds"],
                        )
        durable_job = store.get_analysis_job(job_id)
        if durable_job is not None:
            done = durable_job.done
            failed = durable_job.failed
        analysis_logger.info(
            "Finished analyze job job_id=%s model=%s done=%s failed=%s total=%s",
            job_id,
            model,
            done,
            failed,
            total,
        )
        finish_job(job_id, "completed", f"Analyzed {done} tracks, failed {failed}")
        schedule_auto_index_for_analysis(store, job_id)
    except Exception as exc:
        analysis_logger.exception("Analyze job failed job_id=%s model=%s", job_id, model)
        finish_job(job_id, "failed", str(exc))


def _extract_heads_local(
    analyzer: DiscogsEffnetHeadPackAnalyzer,
    store: Store,
    settings: Settings,
    track: Track,
) -> HeadAnalyzeResult:
    audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
    if failure is not None:
        return HeadAnalyzeResult(
            track_id=track.id,
            path=track.path,
            status="failed",
            error=failure.error,
            error_type=failure.error_type,
            traceback=failure.traceback,
            stage=failure.stage,
        )
    try:
        outputs = analyzer.analyze_track(audio_path)
        return HeadAnalyzeResult(
            track_id=track.id,
            path=track.path,
            status="ok",
            outputs=outputs,
        )
    except Exception as exc:
        return HeadAnalyzeResult(
            track_id=track.id,
            path=track.path,
            status="failed",
            **analyze_failure_fields(exc, "analyze_heads"),
        )
    finally:
        _cleanup_audio_manager(manager)


def _analyze_heads_job(
    job_id: str,
    limit: int | None,
    local_executor_enabled: bool = True,
    max_attempts: int = 3,
    enqueue: bool = True,
) -> None:
    try:
        if SHUTDOWN_REQUESTED:
            finish_job(job_id, "failed", "Head analysis cancelled during application shutdown")
            return
        store, settings = context()
        head_model_names = [head.id for head in DISCOGS_EFFNET_HEADS]
        if enqueue:
            tracks = store.list_tracks_missing_head_pack(head_model_names, limit=limit)
            durable_job = store.create_analysis_job(
                "discogs-effnet-heads",
                limit,
                kind="analyze-heads",
                tracks=tracks,
                local_executor_enabled=local_executor_enabled,
                max_attempts=max_attempts,
                job_id=job_id,
            )
        else:
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                raise ValueError(f"Head analysis job not found: {job_id}")
        total = durable_job.total
        started_at = perf_counter()
        analysis_logger.info(
            "Starting analyze-heads job job_id=%s limit=%s total=%s heads=%s",
            job_id,
            limit,
            total,
            len(head_model_names),
        )
        update_job(
            job_id,
            status="running",
            total=total,
            message=f"Analyzing {total} tracks with Discogs-EffNet heads",
            **analyze_progress(started_at, total, 0, 0),
        )
        done = 0
        failed = 0
        if not local_executor_enabled or total == 0:
            if total == 0:
                finish_job(job_id, "completed", "Analyzed heads for 0 tracks, failed 0")
            else:
                update_job(job_id, status="running", message=f"Queued {total} head analysis tasks for remote workers")
            return
        analyzer = DiscogsEffnetHeadPackAnalyzer(settings)
        local_worker_id = f"local-{job_id}"
        while True:
            if SHUTDOWN_REQUESTED:
                finish_job(job_id, "failed", "Head analysis cancelled during application shutdown")
                return
            tasks = store.claim_analysis_tasks(
                local_worker_id,
                ["discogs-effnet-heads"],
                limit=1,
                lease_seconds=3600,
            )
            if not tasks:
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None or durable_job.status not in ACTIVE_JOB_STATUSES:
                    break
                update_job(
                    job_id,
                    done=durable_job.done,
                    failed=durable_job.failed,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, durable_job.done, durable_job.failed),
                )
                time.sleep(2)
                continue
            task = tasks[0]
            track = store.get_track(task.track_id)
            if track is None:
                store.fail_analysis_task(
                    task.id,
                    error="Track not found",
                    error_type="ValueError",
                    stage="load_track",
                    worker_id=local_worker_id,
                    retryable=False,
                )
                continue
            update_job(job_id, current=track.path, message=f"Analyzing heads for {track.path}")
            result = _extract_heads_local(analyzer, store, settings, track)
            if result.status == "ok" and result.outputs is not None:
                try:
                    for output in result.outputs:
                        store.save_model_output(
                            result.track_id,
                            output.model_name,
                            output.scores,
                            output.aggregation,
                        )
                        store.save_predictions(
                            result.track_id,
                            output.model_name,
                            output.predictions,
                        )
                    store.mark_track_available(result.track_id)
                    store.complete_analysis_task(task.id, local_worker_id)
                except Exception as exc:
                    store.fail_analysis_task(
                        task.id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        stage="save",
                        worker_id=local_worker_id,
                        retryable=False,
                    )
                    analysis_logger.exception(
                        "Head analysis save failed job_id=%s track_id=%s path=%s stage=save",
                        job_id,
                        result.track_id,
                        result.path,
                    )
            else:
                mark_missing_after_failure(store, result)
                store.fail_analysis_task(
                    task.id,
                    error=result.error or "Head analysis failed",
                    error_type=result.error_type or "HeadAnalyzeError",
                    stage=result.stage or "predict",
                    worker_id=local_worker_id,
                    retryable=result.error_type != "FileNotFoundError",
                )
                analysis_logger.error(
                    "Head analysis track failed job_id=%s track_id=%s path=%s stage=%s error_type=%s error=%s\n%s",
                    job_id,
                    result.track_id,
                    result.path,
                    result.stage,
                    result.error_type,
                    result.error,
                    result.traceback or "",
                )
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                continue
            done = durable_job.done
            failed = durable_job.failed
            update_job(
                job_id,
                done=done,
                failed=failed,
                current=result.path,
                message=durable_job.message,
                **analyze_progress(started_at, total, done, failed),
            )
            if (done + failed) % 25 == 0 or done + failed == total:
                progress = analyze_progress(started_at, total, done, failed)
                analysis_logger.info(
                    "Analyze-heads progress job_id=%s done=%s failed=%s total=%s elapsed=%.1f tracks_per_min=%s eta_seconds=%s",
                    job_id,
                    done,
                    failed,
                    total,
                    progress["elapsed_seconds"],
                    progress["tracks_per_min"],
                    progress["eta_seconds"],
                )
        analysis_logger.info(
            "Finished analyze-heads job job_id=%s done=%s failed=%s total=%s",
            job_id,
            done,
            failed,
            total,
        )
        finish_job(job_id, "completed", f"Analyzed heads for {done} tracks, failed {failed}")
    except Exception as exc:
        analysis_logger.exception("Analyze-heads job failed job_id=%s", job_id)
        finish_job(job_id, "failed", str(exc))


def _analyze_genres_job(job_id: str, _model: str, limit: int | None) -> None:
    _analyze_heads_job(job_id, limit)


def _download_head_models_job(job_id: str) -> None:
    try:
        _store, settings = context()
        files = required_model_files()
        total = len(files)
        logger.info("Starting download head models job job_id=%s files=%s", job_id, total)
        update_job(job_id, status="running", total=total, message="Downloading head models")
        downloaded = 0
        ready = 0
        for filename, source_url in files:
            update_job(
                job_id,
                current=filename,
                message=f"Checking {filename}",
            )
            try:
                result = download_model_file(settings, filename, source_url)
            except Exception as exc:
                logger.exception("Download head model failed job_id=%s filename=%s url=%s", job_id, filename, source_url)
                detail = exception_detail(exc)
                hint = download_failure_hint(exc)
                lines = [f"File: {filename}", f"URL: {source_url}", detail]
                if hint:
                    lines.append(f"Hint: {hint}")
                lines.append(exception_traceback(exc))
                finish_job(
                    job_id,
                    "failed",
                    f"Failed to download {filename}: {hint or detail.splitlines()[0]}",
                    error_detail="\n".join(lines),
                )
                return
            if result.downloaded:
                downloaded += 1
            ready += 1
            update_job(
                job_id,
                done=ready,
                current=str(result.path),
                message=f"Ready {ready}/{total} head model files, downloaded {downloaded}",
            )
        finish_job(
            job_id,
            "completed",
            f"Head model files ready: {ready}/{total}, downloaded {downloaded}",
        )
    except Exception as exc:
        logger.exception("Download head models job failed job_id=%s", job_id)
        detail = exception_detail(exc)
        finish_job(job_id, "failed", detail.splitlines()[0], error_detail=exception_traceback(exc))


def _extract_audio_features_local(
    analyzer: AudioFeatureAnalyzer,
    store: Store,
    settings: Settings,
    track: Track,
) -> AudioFeaturesResult:
    audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
    if failure is not None:
        return AudioFeaturesResult(
            track_id=track.id,
            path=track.path,
            status="failed",
            error=failure.error,
            error_type=failure.error_type,
            traceback=failure.traceback,
            stage=failure.stage,
        )
    try:
        features = analyzer.analyze_track(audio_path)
        return AudioFeaturesResult(
            track_id=track.id,
            path=track.path,
            status="ok",
            features=features,
        )
    except Exception as exc:
        return AudioFeaturesResult(
            track_id=track.id,
            path=track.path,
            status="failed",
            **analyze_failure_fields(exc, "audio_features"),
        )
    finally:
        _cleanup_audio_manager(manager)


def _iter_audio_feature_task_results(
    tasks: list[AnalysisTask],
    store: Store,
    settings: Settings,
    workers: int,
):
    if SHUTDOWN_REQUESTED:
        return
    if workers <= 1:
        analyzer = AudioFeatureAnalyzer()
        for task in tasks:
            if SHUTDOWN_REQUESTED:
                return
            result = _extract_audio_features_local(analyzer, store, settings, task_to_track(task))
            yield task, result
        return

    executor = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_audio_feature_worker,
        mp_context=multiprocessing.get_context("spawn"),
    )
    register_analyze_executor(executor)
    audio_managers: list[object] = []
    try:
        future_to_task = {}
        for task in tasks:
            track = task_to_track(task)
            audio_path, manager, failure = _prepare_analyze_audio_path(store, settings, track)
            if failure is not None:
                yield task, AudioFeaturesResult(
                    track_id=track.id,
                    path=track.path,
                    status="failed",
                    error=failure.error,
                    error_type=failure.error_type,
                    traceback=failure.traceback,
                    stage=failure.stage,
                )
                continue
            audio_managers.append(manager)
            future = executor.submit(_extract_audio_features_worker, task.track_id, str(audio_path))
            future_to_task[future] = task
        for future in as_completed(future_to_task):
            if SHUTDOWN_REQUESTED:
                break
            task = future_to_task[future]
            try:
                yield task, future.result()
            except Exception as exc:
                yield task, AudioFeaturesResult(
                    track_id=task.track_id,
                    path=task.path,
                    status="failed",
                    **analyze_failure_fields(exc, "audio_features"),
                )
    finally:
        for manager in audio_managers:
            _cleanup_audio_manager(manager)
        unregister_analyze_executor(executor)
        if SHUTDOWN_REQUESTED:
            terminate_process_pool(executor)
        else:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            except Exception:
                pass


def _analyze_audio_features_job(
    job_id: str,
    limit: int | None,
    workers: int = 1,
    local_executor_enabled: bool = True,
    max_attempts: int = 3,
    enqueue: bool = True,
) -> None:
    try:
        if SHUTDOWN_REQUESTED:
            finish_job(job_id, "failed", "Audio feature analysis cancelled during application shutdown")
            return
        store, settings = context()
        if enqueue:
            tracks = store.list_tracks_missing_features(AUDIO_FEATURE_EXTRACTOR, limit=limit)
            durable_job = store.create_analysis_job(
                AUDIO_FEATURE_EXTRACTOR,
                limit,
                kind="analyze-audio-features",
                tracks=tracks,
                local_executor_enabled=local_executor_enabled,
                max_attempts=max_attempts,
                job_id=job_id,
            )
        else:
            durable_job = store.get_analysis_job(job_id)
            if durable_job is None:
                raise ValueError(f"Audio feature analysis job not found: {job_id}")
        total = durable_job.total
        workers = max(1, min(int(workers), MAX_AUDIO_FEATURE_WORKERS))
        started_at = perf_counter()
        analysis_logger.info(
            "Starting analyze-audio-features job job_id=%s limit=%s total=%s extractor=%s workers=%s",
            job_id,
            limit,
            total,
            AUDIO_FEATURE_EXTRACTOR,
            workers,
        )
        update_job(
            job_id,
            status="running",
            total=total,
            message=f"Analyzing audio features for {total} tracks",
            **analyze_progress(started_at, total, 0, 0),
        )
        done = 0
        failed = 0
        if not local_executor_enabled or total == 0:
            if total == 0:
                finish_job(job_id, "completed", "Analyzed audio features for 0 tracks, failed 0")
            else:
                update_job(job_id, status="running", message=f"Queued {total} audio feature tasks for remote workers")
            return
        local_worker_id = f"local-{job_id}"
        local_claim_batch_size = max(workers * 4, 16)
        while True:
            if SHUTDOWN_REQUESTED:
                finish_job(job_id, "failed", "Audio feature analysis cancelled during application shutdown")
                return
            tasks = store.claim_analysis_tasks(
                local_worker_id,
                [AUDIO_FEATURE_EXTRACTOR],
                limit=local_claim_batch_size,
                lease_seconds=3600,
            )
            if not tasks:
                durable_job = store.get_analysis_job(job_id)
                if durable_job is None or durable_job.status not in ACTIVE_JOB_STATUSES:
                    break
                update_job(
                    job_id,
                    done=durable_job.done,
                    failed=durable_job.failed,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, durable_job.done, durable_job.failed),
                )
                time.sleep(2)
                continue
            for task, result in _iter_audio_feature_task_results(tasks, store, settings, workers):
                update_job(job_id, current=result.path, message=f"Analyzing audio features for {result.path}")
                if result.status == "ok" and result.features is not None:
                    try:
                        store.save_features(result.track_id, result.features)
                        store.mark_track_available(result.track_id)
                        store.complete_analysis_task(task.id, local_worker_id)
                    except Exception as exc:
                        store.fail_analysis_task(
                            task.id,
                            error=str(exc),
                            error_type=type(exc).__name__,
                            stage="save",
                            worker_id=local_worker_id,
                            retryable=False,
                        )
                        analysis_logger.exception(
                            "Audio feature save failed job_id=%s track_id=%s path=%s extractor=%s stage=save",
                            job_id,
                            result.track_id,
                            result.path,
                            AUDIO_FEATURE_EXTRACTOR,
                        )
                else:
                    mark_missing_after_failure(store, result)
                    store.fail_analysis_task(
                        task.id,
                        error=result.error or "Audio feature analysis failed",
                        error_type=result.error_type or "AudioFeatureError",
                        stage=result.stage or "extract",
                        worker_id=local_worker_id,
                        retryable=result.error_type != "FileNotFoundError",
                    )
                    analysis_logger.error(
                        "Audio feature track failed job_id=%s track_id=%s path=%s extractor=%s stage=%s error_type=%s error=%s\n%s",
                        job_id,
                        result.track_id,
                        result.path,
                        AUDIO_FEATURE_EXTRACTOR,
                        result.stage,
                        result.error_type,
                        result.error,
                        result.traceback or "",
                    )

                durable_job = store.get_analysis_job(job_id)
                if durable_job is None:
                    continue
                done = durable_job.done
                failed = durable_job.failed
                update_job(
                    job_id,
                    done=done,
                    failed=failed,
                    current=result.path,
                    message=durable_job.message,
                    **analyze_progress(started_at, total, done, failed),
                )
                if (done + failed) % 25 == 0 or done + failed == total:
                    progress = analyze_progress(started_at, total, done, failed)
                    analysis_logger.info(
                        "Analyze-audio-features progress job_id=%s done=%s failed=%s total=%s elapsed=%.1f tracks_per_min=%s eta_seconds=%s",
                        job_id,
                        done,
                        failed,
                        total,
                        progress["elapsed_seconds"],
                        progress["tracks_per_min"],
                        progress["eta_seconds"],
                    )
        analysis_logger.info(
            "Finished analyze-audio-features job job_id=%s done=%s failed=%s total=%s",
            job_id,
            done,
            failed,
            total,
        )
        finish_job(job_id, "completed", f"Analyzed audio features for {done} tracks, failed {failed}")
    except Exception as exc:
        analysis_logger.exception("Analyze-audio-features job failed job_id=%s", job_id)
        finish_job(job_id, "failed", str(exc))


def _index_job(job_id: str, model: str) -> None:
    try:
        store, settings = context()
        logger.info("Starting index job job_id=%s model=%s", job_id, model)
        update_job(job_id, status="running", message=f"Building index for {model}")
        path = build_index(store, settings, model)
        update_job(job_id, done=1, total=1)
        finish_job(job_id, "completed", f"Built {path}")
    except Exception as exc:
        logger.exception("Index job failed job_id=%s model=%s", job_id, model)
        finish_job(job_id, "failed", str(exc))


UI_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>discocs</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css">
  <style>
    :root {
      color-scheme: light dark;
      --surface-0: #0b0d0f;
      --surface-1: #111315;
      --surface-2: #171a1d;
      --surface-3: #242629;
      --surface-4: #34373b;
      --surface-hover: #2b2d30;
      --surface-current: #3a3a3a;
      --stroke: #2d3033;
      --stroke-soft: #222426;
      --menu-bg: #242629;
      --menu-player-bg: #111;
      --menu-hover: #33363a;
      --player-bg: #111;
      --bg: var(--surface-0);
      --panel: var(--surface-2);
      --panel-2: var(--surface-3);
      --text: #eef2f3;
      --muted: #aeb8bc;
      --line: var(--stroke);
      --accent: #ff2a6d;
      --accent-2: #c8c8c8;
      --bad: #e27373;
      --blue: #9aa0a6;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; height: 100vh; min-height: 100vh; display: flex; flex-direction: column;
      overflow: hidden; background: var(--bg); color: var(--text);
    }
    button, input, select { font: inherit; }
    button {
      min-height: 36px; border: 1px solid var(--line); border-radius: 6px; padding: 0 12px;
      background: var(--panel-2); color: var(--text); cursor: pointer;
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: #07110e; font-weight: 700; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    input, select {
      min-height: 36px; border: 1px solid var(--line); border-radius: 6px; padding: 0 10px;
      background: var(--surface-0); color: var(--text);
    }
    .app {
      display: grid; grid-template-columns: 220px minmax(0, 1fr);
      flex: 1; min-height: 0; height: 100vh; padding-bottom: 92px;
    }
    aside { border-right: 1px solid var(--line); background: var(--surface-1); padding: 18px; overflow-y: auto; min-height: 0; }
    main {
      padding: 18px; display: flex; flex-direction: column; gap: 16px;
      min-height: 0; overflow-y: auto;
    }
    h1 { font-size: 22px; margin: 0 0 16px; letter-spacing: 0; }
    h2 { font-size: 16px; margin: 0 0 10px; letter-spacing: 0; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 13px; margin: 10px 0; }
    .label-title { display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
    .info {
      position: relative; display: inline-flex; align-items: center; justify-content: center;
      width: 18px; height: 18px; border: 1px solid var(--line); border-radius: 999px;
      color: var(--muted); background: var(--panel-2); font-size: 12px; font-weight: 700;
      cursor: help; flex: 0 0 auto;
    }
    .info::after {
      content: attr(data-tooltip); position: absolute; left: 50%; bottom: calc(100% + 8px);
      transform: translateX(-50%); width: min(280px, 78vw); padding: 8px 10px;
      border: 1px solid var(--line); border-radius: 6px; background: #0b0d0f;
      color: var(--text); font-size: 12px; font-weight: 400; line-height: 1.35;
      opacity: 0; pointer-events: none; transition: opacity .12s ease; z-index: 10;
      box-shadow: 0 8px 24px rgba(0,0,0,.35);
    }
    .info:focus { outline: 2px solid var(--accent); outline-offset: 2px; }
    .info:hover::after, .info:focus::after { opacity: 1; }
    nav { display: grid; gap: 8px; }
    nav button { justify-content: flex-start; text-align: left; }
    nav button.active { border-color: var(--accent); color: var(--accent); }
    .section { display: none; }
    .section.active { display: grid; gap: 16px; }
    .section.section-fill.active {
      display: flex; flex-direction: column; flex: 1; min-height: 0; overflow-y: auto; gap: 16px;
    }
    .section-fill.active > .layout,
    .section-fill.active > .browse-layout,
    .section-fill.active > .instant-mix-layout,
    .section-fill.active > .metrics-layout { flex: 1; min-height: 0; align-items: stretch; }
    .section-fill.active > .panel.panel-fill { flex: 1; min-height: 0; }
    .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; }
    .stat {
      border: 1px solid var(--line); border-radius: 6px; padding: 12px; background: var(--panel);
      display: grid; grid-template-rows: auto auto 1fr auto; gap: 8px; min-width: 0;
    }
    .stat strong { display: block; font-size: 24px; line-height: 1.05; }
    .stat span { color: var(--muted); font-size: 12px; }
    .stat h3 { margin: 0; font-size: 13px; overflow-wrap: anywhere; min-height: 18px; }
    .stat-count { display: grid; gap: 2px; }
    .stat-lines { display: grid; gap: 4px; align-content: start; }
    .stat-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 36px; }
    .stat-actions button { width: fit-content; }
    .stat-index-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .stat-icon-button {
      min-height: 26px; width: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center;
      border-radius: 6px; line-height: 1; font-size: 16px;
    }
    .model-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 10px; }
    .model-card.selected { border-color: var(--accent); }
    .status-ready { border: 1px solid var(--accent); color: var(--accent); background: transparent; }
    .status-stale, .status-unknown { border: 1px solid var(--accent-2); color: var(--accent-2); background: transparent; }
    .status-missing, .status-failed { border: 1px solid var(--bad); color: var(--bad); background: transparent; }
    .index-status-ready { color: var(--accent); }
    .index-status-stale, .index-status-unknown { color: var(--accent-2); }
    .index-status-missing, .index-status-failed { color: var(--bad); }
    .layout {
      display: grid; grid-template-columns: minmax(320px, .9fr) minmax(360px, 1.1fr);
      gap: 16px; min-height: 0;
    }
    .instant-mix-layout {
      display: grid; grid-template-columns: minmax(280px, 360px) minmax(460px, 1fr);
      gap: 16px; min-height: 0;
    }
    .instant-mix-sidebar { display: flex; flex-direction: column; gap: 16px; min-height: 0; }
    .instant-mix-controls { flex: 0 0 auto; }
    .instant-mix-history { flex: 1; min-height: 0; }
    .browse-layout {
      display: grid; grid-template-columns: minmax(240px, .55fr) minmax(420px, 1.45fr);
      gap: 16px; min-height: 0;
    }
    .panel-fill { display: flex; flex-direction: column; min-height: 0; }
    .list-region, .table-region, .facet-scroll {
      flex: 1; min-height: 0; overflow-y: auto; padding-right: 4px; padding-bottom: 8px;
    }
    .blend-status { flex-shrink: 0; }
    .facet-group { display: grid; gap: 6px; margin-bottom: 12px; }
    .facet-list { display: grid; gap: 6px; max-height: 164px; overflow: auto; padding-right: 4px; }
    .facet-button { justify-content: space-between; text-align: left; width: 100%; min-height: 32px; overflow: hidden; }
    .facet-button.active { border-color: var(--accent); color: var(--accent); }
    .facet-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .basket { display: grid; gap: 8px; margin-top: 10px; }
    .rating-active { border-color: var(--accent); color: var(--accent); }
    .like-active { border-color: var(--accent); color: var(--accent); }
    .navidrome-like-button { display: inline-flex; align-items: center; justify-content: center; min-width: 42px; }
    .bi { width: 1em; height: 1em; fill: currentColor; flex: 0 0 auto; }
    .panel { border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 14px; min-width: 0; }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .search { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; margin-bottom: 10px; }
    .list { display: grid; gap: 8px; align-content: start; }
    .track {
      display: grid; gap: 4px; border: 1px solid var(--line); border-radius: 6px; padding: 10px;
      background: var(--surface-2);
    }
    .track:has(.track-body) {
      grid-template-columns: 112px minmax(0, 1fr); grid-template-rows: auto auto;
      column-gap: 12px; row-gap: 6px;
    }
    .track-body { display: contents; }
    .track-main { grid-column: 2; grid-row: 1; min-width: 0; display: grid; gap: 4px; }
    .track-head { min-width: 0; }
    .track-title-row { display: flex; align-items: baseline; gap: 6px; min-width: 0; flex-wrap: wrap; }
    .track-title-main { min-width: 0; overflow-wrap: anywhere; }
    .track-id { color: var(--muted); }
    .track-score-inline { color: var(--accent-2); font-weight: 700; }
    .track-card-line { display: flex; gap: 12px; justify-content: space-between; align-items: baseline; min-width: 0; }
    .track-card-left { min-width: 0; overflow-wrap: anywhere; }
    .track-card-right { flex: 0 0 auto; text-align: right; white-space: nowrap; }
    .track-actions {
      display: flex; gap: 8px; flex-wrap: wrap; justify-content: space-between;
      grid-column: 2; grid-row: 2; align-items: center; margin-top: 0;
    }
    .track-action-group { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .track-command-group { justify-content: flex-end; margin-left: auto; }
    .cover {
      grid-column: 1; grid-row: 1 / span 2; align-self: stretch;
      width: 112px; min-height: 112px; border: 1px solid var(--line); border-radius: 6px; overflow: hidden;
      background: linear-gradient(135deg, var(--surface-3), var(--surface-1)); display: grid; place-items: center; color: var(--muted);
      font-size: 11px; font-weight: 700; position: relative;
    }
    .cover::before { content: "ART"; }
    .cover img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; }
    .cover.empty img { display: none; }
    .cover.empty::before { content: "ART"; }
    .track.selected { border-color: var(--accent); }
    .title { font-weight: 700; overflow-wrap: anywhere; }
    .meta { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
    .path { color: #829096; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .model-table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
    .model-table th, .model-table td { border-top: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }
    .model-table th { color: var(--muted); font-weight: 600; }
    #erroredFiles .table-region { overflow: auto; }
    .error-table { min-width: 1180px; table-layout: fixed; }
    .error-table .check-col { width: 32px; }
    .error-table .track-col { width: 220px; }
    .error-table .path-col { width: 42%; }
    .error-table .model-col { width: 170px; }
    .error-table .error-col { width: 300px; }
    .error-table .updated-col { width: 150px; }
    .error-table .track-cell, .error-table .model-cell, .error-table .updated-cell { overflow-wrap: anywhere; }
    .error-table .path-cell { max-width: 0; }
    .error-table .error-text {
      white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; max-height: 180px; overflow: auto;
    }
    .score { color: var(--accent-2); font-weight: 700; }
    .jobs { display: grid; gap: 8px; }
    .job { border: 1px solid var(--line); border-radius: 6px; padding: 10px; background: var(--surface-2); }
    .job pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 6px 0 0; }
    .bar { height: 6px; background: var(--surface-0); border-radius: 999px; overflow: hidden; margin-top: 8px; }
    .fill { height: 100%; background: var(--accent); width: 0%; }
    .status-deferred .fill { background: var(--accent-2); }
    .status-failed .fill { background: var(--bad); }
    .pill { display: inline-flex; align-items: center; min-height: 24px; padding: 0 8px; border-radius: 999px; background: var(--panel-2); color: var(--muted); font-size: 12px; }
    .bad-pill { border: 1px solid var(--bad); color: var(--bad); background: transparent; }
    .player {
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 25;
      min-height: 82px; border-top: 1px solid var(--line); background: var(--player-bg);
      padding: 0 18px 10px; display: grid; grid-template-columns: minmax(260px, 1fr) minmax(320px, 42vw) minmax(260px, 1fr);
      grid-template-rows: 16px 56px; column-gap: 18px; align-items: center; box-shadow: 0 -12px 30px rgba(0,0,0,.28);
    }
    .player-seek { grid-column:1 / -1; height:16px; margin:0 -18px; position:relative; display:flex; align-items:start; }
    .player-seek input[type="range"] {
      width:100%; min-height:16px; padding:0; margin:0; background:transparent; border:0; border-radius:0; outline:0;
      -webkit-appearance:none; appearance:none; cursor:pointer;
    }
    .player-seek input[type="range"]:focus { outline:0; box-shadow:none; }
    .player-seek input[type="range"]::-webkit-slider-runnable-track { height:4px; background:linear-gradient(to right, var(--accent) var(--seek-progress, 0%), var(--surface-4) var(--seek-progress, 0%)); }
    .player-seek input[type="range"]::-moz-range-track { height:4px; background:var(--surface-4); }
    .player-seek input[type="range"]::-moz-range-progress { height:4px; background:var(--accent); }
    .player-seek input[type="range"]::-webkit-slider-thumb {
      -webkit-appearance:none; appearance:none; width:16px; height:16px; border-radius:999px;
      background:var(--accent); border:0; margin-top:-6px;
    }
    .player-seek input[type="range"]::-moz-range-thumb { width:16px; height:16px; border-radius:999px; background:var(--accent); border:0; }
    .player-seek-bubble {
      position:absolute; top:-34px; transform:translateX(-50%); min-width:44px; padding:5px 8px;
      border-radius:4px; background:var(--surface-4); color:#fff; text-align:center; font-size:12px; font-weight:700;
      opacity:0; pointer-events:none;
    }
    .player-seek:hover .player-seek-bubble, .player-seek.scrubbing .player-seek-bubble { opacity:1; }
    .player-controls, .player-actions, .player-inline-actions { display:flex; gap:8px; align-items:center; }
    .player-controls { justify-self:start; }
    .player-controls button, .player-actions button, .player-inline-actions button {
      width:38px; height:38px; padding:0; border-radius:999px; border:0; background:transparent;
      color:#d8d8d8; display:inline-grid; place-items:center; font-size:22px;
    }
    .player-controls button:hover, .player-actions button:hover, .player-inline-actions button:hover,
    .player-actions button.active {
      background:var(--surface-hover); color:#fff;
    }
    .player-controls button.player-play { width:52px; height:52px; font-size:30px; color:#fff; }
    .player-now {
      display:inline-grid; grid-template-columns:48px minmax(0, max-content); gap:12px; align-items:center; min-width:0;
      justify-self:center; max-width:100%;
    }
    .player-now > div:last-child { min-width:0; max-width:min(520px, calc(42vw - 72px)); }
    .player-cover {
      width:48px; aspect-ratio:1; border:1px solid var(--line); border-radius:6px;
      background:var(--surface-1); overflow:hidden; display:grid; place-items:center; color:var(--muted); font-size:10px; font-weight:800;
    }
    .player-cover img { width:100%; height:100%; object-fit:cover; display:block; }
    .player-title, .player-subtitle { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .player-title { display:flex; align-items:center; justify-content:flex-start; gap:14px; text-align:left; }
    .player-title strong { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .player-subtitle { text-align:left; }
    .player-inline-actions button { width:34px; min-width:34px; height:34px; font-size:18px; }
    .player-actions { justify-content:flex-end; justify-self:end; }
    .player-actions button[disabled] { opacity:.65; cursor:default; }
    .player-actions button.active { opacity:1; }
    .player-volume {
      display:flex; flex-direction:row-reverse; align-items:center; gap:10px; height:38px;
    }
    .player-volume-slider {
      width:0; opacity:0; pointer-events:none; transition:width .16s ease, opacity .16s ease;
    }
    .player-volume:hover .player-volume-slider,
    .player-volume:focus-within .player-volume-slider {
      width:120px; opacity:1; pointer-events:auto;
    }
    .player-volume-slider input[type="range"] {
      width:120px; min-height:22px; padding:0; margin:0; border:0; background:transparent;
      -webkit-appearance:none; appearance:none; cursor:pointer;
    }
    .player-volume-slider input[type="range"]::-webkit-slider-runnable-track { height:4px; background:#b8b8b8; }
    .player-volume-slider input[type="range"]::-moz-range-track { height:4px; background:#b8b8b8; }
    .player-volume-slider input[type="range"]::-webkit-slider-thumb {
      -webkit-appearance:none; appearance:none; width:18px; height:18px; border-radius:999px;
      background:#f1f1f1; border:0; margin-top:-7px;
    }
    .player-volume-slider input[type="range"]::-moz-range-thumb { width:18px; height:18px; border-radius:999px; background:#f1f1f1; border:0; }
    .navidrome-debug { display:none; }
    .player-progress { display:none; }
    .player-time { display:flex; justify-content:space-between; gap:12px; color:var(--muted); font-size:12px; }
    .player audio { display:none; }
    .expanded-player {
      position:fixed; left:220px; right:0; top:0; bottom:82px; z-index:24;
      background:var(--surface-0); box-shadow:0 -16px 42px rgba(0,0,0,.38);
      padding:34px 64px; display:none; grid-template-columns:minmax(420px, 1fr) minmax(360px, 560px); gap:72px; overflow:hidden;
    }
    .expanded-player.open { display:grid; }
    .expanded-main { min-width:0; display:grid; align-content:center; justify-items:center; gap:18px; }
    .expanded-art {
      width:min(72vh, 760px); max-width:100%; aspect-ratio:1; border:0; border-radius:0; background:#111;
      display:grid; place-items:center; overflow:hidden; color:var(--muted); font-weight:800;
    }
    .expanded-art img { width:100%; height:100%; object-fit:contain; display:block; }
    .expanded-track-text { width:min(72vh, 760px); max-width:100%; }
    .queue-panel { border:0; border-radius:0; background:transparent; padding:0 4px 34px 0; display:grid; gap:14px; align-content:start; min-height:0; overflow:auto; }
    .queue-tabs { display:grid; grid-template-columns:1fr 1fr 1fr; gap:0; border-bottom:1px solid var(--stroke-soft); }
    .queue-tabs button { border:0; border-bottom:2px solid transparent; border-radius:0; background:transparent; color:#777; font-weight:800; }
    .queue-tabs button.active { border-bottom-color:#d8d8d8; color:#f0f0f0; }
    .autoplay-prep { display:grid; gap:4px; color:var(--muted); }
    .autoplay-row { display:flex; justify-content:space-between; align-items:center; gap:12px; }
    .toggle-pill { width:42px; height:22px; border-radius:999px; background:#24313b; position:relative; opacity:.7; border:0; }
    .toggle-pill::after { content:""; position:absolute; left:3px; top:3px; width:16px; height:16px; border-radius:999px; background:#82909a; }
    .toggle-pill.active { background:#dcecff; opacity:1; }
    .toggle-pill.active::after { left:auto; right:3px; background:#0f86d8; }
    .chip-row { display:flex; gap:10px; row-gap:10px; flex-wrap:wrap; padding:10px 0 20px; margin:0; }
    .chip-row button { min-height:34px; border:0; border-radius:7px; background:var(--surface-3); font-weight:700; color:#d0d0d0; }
    .chip-row button.active { background:#eee; color:#111; }
    .queue-list { display:block; min-height:0; }
    .autoplay-pool-section { display:block; clear:both; margin-top:28px; padding-top:20px; border-top:1px solid var(--stroke-soft); }
    .autoplay-pool-list { display:block; margin-top:2px; }
    .autoplay-pool-header { display:block; margin:0 0 2px; font-weight:800; text-transform:none; }
    .queue-item {
      border:0; border-bottom:1px solid var(--stroke-soft); border-radius:0; padding:10px 0; min-height:62px; background:transparent;
      cursor:pointer; display:grid; grid-template-columns:40px minmax(0,1fr) auto auto; gap:12px; align-items:center;
    }
    .queue-item.prepared { cursor:default; opacity:.92; }
    .queue-item.current { background:var(--surface-current); padding-left:8px; padding-right:8px; }
    .queue-item-cover { width:34px; aspect-ratio:1; background:var(--surface-3); overflow:hidden; display:grid; place-items:center; color:var(--muted); font-size:9px; }
    .queue-item-cover img { width:100%; height:100%; object-fit:cover; display:block; }
    .queue-item-title, .queue-item-subtitle { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .queue-track-name { font-weight:800; }
    .queue-track-link, .queue-artist-link { cursor:pointer; }
    .queue-item-duration { color:var(--muted); font-weight:700; }
    .queue-item-actions { display:flex; align-items:center; justify-content:flex-end; gap:6px; min-width:86px; }
    .queue-item-actions .navidrome-like-button {
      width:28px; min-width:28px; min-height:26px; padding:0;
      background:transparent !important; border:0 !important; box-shadow:none !important;
      color:var(--muted);
    }
    .queue-item-actions .navidrome-like-button:hover,
    .queue-item-actions .navidrome-like-button:focus { background:transparent !important; color:var(--text); }
    .queue-item-actions .navidrome-like-button.like-active { color:var(--accent); }
    .settings-page { display:grid; gap:14px; }
    .settings-tabs { display:flex; gap:8px; flex-wrap:wrap; position:sticky; top:0; z-index:2; background:var(--panel); padding-bottom:8px; }
    .settings-tabs button.active { border-color:var(--accent); color:var(--accent); }
    .settings-pane { display:none; }
    .settings-pane.active { display:block; }
    .navidrome-debug {
      display:none; color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .build-marker { color: var(--accent-2); font-size: 12px; font-weight: 700; }
    audio { width: 100%; }
    .error { color: var(--bad); min-height: 20px; }
    .active-track { border-color: var(--blue); }
    .icon-button { width: 36px; padding: 0; display: inline-flex; align-items: center; justify-content: center; }
    .track-menu-button {
      flex:0 0 auto; background:transparent !important; border:0 !important; color:var(--muted);
      box-shadow:none !important;
    }
    .track-menu-button:hover, .track-menu-button:focus { background:transparent !important; color:var(--text); }
    .track-action-menu {
      position:fixed; z-index:40; display:none; min-width:170px; padding:6px;
      border:0; border-radius:7px; background:var(--menu-bg);
      box-shadow:0 14px 36px rgba(0,0,0,.42);
    }
    .track-action-menu.player-menu { background:var(--menu-player-bg); }
    .track-action-menu.open { display:grid; gap:4px; }
    .track-action-menu button {
      width:100%; min-height:34px; justify-content:flex-start; border:0; background:transparent;
      color:var(--text); text-align:left; border-radius:5px; padding:0 10px;
    }
    .track-action-menu button:hover { background:var(--menu-hover); }
    .icon-tablet {
      width: 16px; height: 20px; border: 2px solid currentColor; border-radius: 3px; position: relative;
      display: inline-block;
    }
    .icon-tablet::after {
      content: ""; position: absolute; left: 50%; bottom: 2px; width: 4px; height: 2px;
      transform: translateX(-50%); border-radius: 999px; background: currentColor;
    }
    .modal-backdrop {
      position: fixed; inset: 0; z-index: 30; display: none; align-items: center; justify-content: center;
      padding: 20px; background: rgba(0,0,0,.68);
    }
    .modal-backdrop.open { display: flex; }
    .modal {
      width: min(980px, 96vw); max-height: 88vh; overflow: auto; border: 1px solid var(--line);
      border-radius: 8px; background: var(--panel); box-shadow: 0 20px 60px rgba(0,0,0,.45);
      padding: 14px;
    }
    .analysis-grid { display: grid; gap: 10px; margin-top: 12px; }
    .analysis-output { border-top: 1px solid var(--line); padding-top: 10px; display: grid; gap: 8px; }
    .tag-list { display: flex; gap: 6px; flex-wrap: wrap; }
    .score-list {
      max-height: 130px; overflow: auto; margin: 0; padding: 8px; border: 1px solid var(--line);
      border-radius: 6px; background: #0d0f11; color: var(--muted); font-size: 12px;
    }
    @media (max-width: 920px) {
      .app { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .stats, .layout, .browse-layout, .instant-mix-layout { grid-template-columns: 1fr; }
      .section-fill.active > .layout,
      .section-fill.active > .browse-layout,
      .section-fill.active > .instant-mix-layout {
        grid-template-rows: minmax(180px, .9fr) minmax(240px, 1.1fr);
      }
      .track:has(.track-body) { grid-template-columns: 80px minmax(0, 1fr); }
      .cover { width: 80px; min-height: 80px; }
      .track-card-line { display: block; }
      .track-card-right { text-align: left; white-space: normal; }
      .player { grid-template-columns:1fr; min-height:154px; grid-template-rows:16px auto auto auto; row-gap:6px; }
      .player-controls, .player-now, .player-actions { justify-self:center; }
      .player-now > div:last-child { max-width:calc(100vw - 104px); }
      .player-actions { flex-wrap:wrap; justify-content:center; }
      .expanded-player { left:0; grid-template-columns:1fr; }
    }
    .metrics-layout { display:grid; grid-template-columns:minmax(260px, 340px) minmax(0, 1fr); gap:16px; height:100%; min-height:0; }
    .metrics-controls { flex-shrink:0; }
    .metric-filter-scroll { flex:1; min-height:0; overflow-y:auto; padding-right:4px; padding-bottom:8px; margin-top:8px; }
    .metric-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:8px; margin-bottom:12px; }
    .metric-card { border:1px solid var(--line); border-radius:8px; padding:10px; background:var(--panel-2); cursor:pointer; }
    .metric-card.active { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent); }
    .metric-card strong { display:block; margin-bottom:4px; }
    .metric-filter { border-top:1px solid var(--line); padding-top:10px; margin-top:10px; }
    .metric-filter:first-child { border-top:0; padding-top:0; margin-top:0; }
    .metric-value-list { display:grid; gap:6px; margin-top:8px; max-height:240px; overflow:auto; padding-right:4px; }
    .metric-value-list label { margin:0; display:grid; grid-template-columns:18px minmax(0, 1fr); align-items:center; gap:6px; font-size:12px; }
    .metric-value-list span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .range-pair { display:grid; gap:8px; margin-top:8px; min-width:0; }
    .range-values { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .range-values input { min-height:34px; min-width:0; }
    .range-slider { position:relative; height:30px; margin:0 2px; }
    .range-slider .range-track { position:absolute; left:0; right:0; top:13px; height:4px; border-radius:999px; background:var(--line); }
    .range-slider input[type="range"] {
      position:absolute; left:0; top:0; width:100%; min-height:30px; padding:0; margin:0;
      background:transparent; pointer-events:none; -webkit-appearance:none; appearance:none;
    }
    .range-slider input[type="range"]::-webkit-slider-thumb { pointer-events:auto; -webkit-appearance:none; appearance:none; width:16px; height:16px; border-radius:50%; background:var(--accent); border:0; }
    .range-slider input[type="range"]::-moz-range-thumb { pointer-events:auto; width:16px; height:16px; border-radius:50%; background:var(--accent); border:0; }
    .range-slider input[type="range"]::-webkit-slider-runnable-track { background:transparent; }
    .range-slider input[type="range"]::-moz-range-track { background:transparent; }
    .feature-chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
    .feature-chip { border:1px solid var(--line); border-radius:999px; padding:3px 8px; font-size:12px; color:var(--muted); }
    .home-dashboard { gap:30px; padding-bottom:24px; }
    .home-top { display:grid; gap:18px; }
    .home-search {
      display:grid; grid-template-columns:minmax(0, 1fr) auto; gap:10px;
      width:min(760px, 100%);
    }
    .home-search input {
      min-height:46px; border-radius:999px; padding:0 18px; background:#0b0d0f;
      font-size:15px;
    }
    .listener-hero {
      border:1px solid rgba(65, 211, 167, .35); border-radius:10px; padding:24px;
      background:
        radial-gradient(circle at 18% 15%, rgba(65,211,167,.16), transparent 34%),
        linear-gradient(135deg, #172025, #101315 72%);
      display:grid; grid-template-columns:minmax(0, 1fr) auto; gap:20px; align-items:center;
      min-height:150px;
    }
    .listener-hero h2 { font-size:42px; line-height:1; margin:0 0 8px; }
    .operations-link { color:var(--accent); text-decoration:none; font-weight:700; }
    .operations-link:hover { text-decoration:underline; }
    .surface-header { display:grid; grid-template-columns:220px minmax(0,1fr); gap:22px; align-items:end; }
    .surface-art {
      width:220px; aspect-ratio:1; border:1px solid var(--line); border-radius:8px; overflow:hidden;
      background:linear-gradient(135deg, #20272b, #111518); display:grid; place-items:center;
      color:var(--muted); font-weight:700;
    }
    .surface-art.artist-art { border-radius:999px; }
    .surface-art img { width:100%; height:100%; object-fit:cover; display:block; }
    .surface-title { font-size:38px; line-height:1.05; margin:0; overflow-wrap:anywhere; }
    .surface-subtitle a, .entity-link { color:var(--text); text-decoration:none; }
    .surface-subtitle a:hover, .entity-link:hover { color:var(--accent); }
    .surface-grid { display:grid; gap:32px; }
    #artistSurface > .panel.panel-fill,
    #releaseSurface > .panel.panel-fill,
    #mixSurface > .panel.panel-fill {
      border:0; border-radius:0; background:transparent; padding:0;
    }
    .mix-page {
      display:grid; grid-template-columns:minmax(260px, 340px) minmax(0, 1fr);
      gap:42px; align-items:start; min-height:0;
    }
    .mix-hero {
      position:sticky; top:0; display:grid; justify-items:center; gap:16px; text-align:center;
      padding-top:8px;
    }
    .mix-art {
      width:min(280px, 100%); aspect-ratio:1; border:1px solid var(--line); border-radius:8px;
      background:linear-gradient(135deg, #20272b, #111518); overflow:hidden;
      display:grid; place-items:center; color:var(--muted); font-weight:800;
      box-shadow:0 22px 60px rgba(0,0,0,.35);
    }
    .mix-art img { width:100%; height:100%; object-fit:cover; display:block; }
    .mix-title { font-size:30px; line-height:1.05; margin:0; overflow-wrap:anywhere; }
    .mix-description { max-width:310px; }
    .mix-actions { display:flex; gap:12px; justify-content:center; align-items:center; flex-wrap:wrap; }
    .mix-actions button { border-radius:999px; min-width:44px; }
    .mix-play-button {
      width:64px; height:64px; padding:0; display:grid; place-items:center;
      font-size:28px; border-radius:999px;
    }
    .mix-track-list { display:grid; gap:0; min-width:0; }
    .mix-track-list .queue-item {
      grid-template-columns:48px minmax(0,1fr) minmax(48px, auto) auto;
      min-height:70px; padding:12px 0;
    }
    .mix-track-list .queue-item-cover { width:40px; border-radius:5px; }
    .shelf { display:grid; gap:14px; min-width:0; }
    .shelf-head { display:flex; align-items:baseline; justify-content:space-between; gap:10px; flex-wrap:wrap; }
    .shelf-head h2 { font-size:26px; margin:0; }
    .shelf-row {
      display:flex; gap:22px; overflow-x:auto; overflow-y:hidden; padding:2px 4px 12px 0;
      scroll-snap-type:x proximity;
    }
    .shelf-row::-webkit-scrollbar { height:10px; }
    .shelf-row::-webkit-scrollbar-thumb { background:#273036; border-radius:999px; }
    .media-card {
      border:0; border-radius:8px; padding:0; background:transparent; display:grid; gap:8px;
      min-width:0; flex:0 0 clamp(178px, 14.5vw, 236px); scroll-snap-align:start;
      position:relative; cursor:pointer;
    }
    .media-card-cover {
      aspect-ratio:1; border:1px solid var(--line); border-radius:8px; overflow:hidden;
      background:#111518; display:grid; place-items:center; color:var(--muted);
      transition:filter .12s ease, transform .12s ease;
    }
    .media-card-cover.artist-avatar { border-radius:999px; background:#182024; font-size:34px; font-weight:800; }
    .media-card-cover img { width:100%; height:100%; object-fit:cover; display:block; }
    .media-card:hover .media-card-cover { filter:brightness(.78); }
    .media-card-title {
      font-weight:800; font-size:15px; line-height:1.25; overflow:hidden;
      display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
    }
    .media-card-subtitle, .media-card-reason {
      line-height:1.3; overflow:hidden; display:-webkit-box; -webkit-box-orient:vertical;
    }
    .media-card-subtitle { -webkit-line-clamp:2; }
    .media-card-reason { -webkit-line-clamp:1; }
    .media-card-actions {
      position:absolute; right:10px; top:10px; display:flex; gap:6px; opacity:0;
      transform:translateY(4px); transition:opacity .12s ease, transform .12s ease;
    }
    .media-card:hover .media-card-actions, .media-card:focus-within .media-card-actions {
      opacity:1; transform:translateY(0);
    }
    .media-card-actions button {
      min-height:34px; width:34px; padding:0; border-radius:999px; background:var(--accent);
      border-color:var(--accent); color:#07110e; font-weight:800;
    }
    .entity-tabs { display:flex; gap:8px; flex-wrap:wrap; }
    .entity-tabs button.active { border-color:var(--accent); color:var(--accent); }
    .track-table { width:100%; border-collapse:collapse; font-size:14px; }
    .track-table th, .track-table td { border-top:1px solid var(--line); padding:9px 8px; text-align:left; vertical-align:middle; }
    .track-table th { color:var(--muted); font-weight:600; }
    .track-table tr:hover td { background:#171d21; }
    .search-track-cover { width:48px; }
    .track-table-cover {
      width:44px; aspect-ratio:1; border:1px solid var(--line); border-radius:5px; overflow:hidden;
      background:#111518; display:grid; place-items:center; color:var(--muted); font-size:10px; font-weight:800;
    }
    .track-table-cover img { width:100%; height:100%; object-fit:cover; display:block; }
    .search-page-layout { display:grid; gap:14px; }
    .search-tabs { display:flex; gap:8px; flex-wrap:wrap; }
    .search-tabs button.active { border-color:var(--accent); color:var(--accent); }
    .top-result { border:1px solid var(--line); border-radius:8px; padding:14px; background:#14191d; display:flex; gap:14px; align-items:center; }
    .top-result-avatar {
      width:72px; aspect-ratio:1; border:1px solid var(--line); border-radius:999px; overflow:hidden;
      background:#182024; display:grid; place-items:center; color:var(--muted); font-size:28px; font-weight:800; flex:0 0 auto;
    }
    .top-result-avatar img { width:100%; height:100%; object-fit:cover; display:block; }
    .top-result-body { display:grid; gap:8px; min-width:0; }
    @media (max-width: 1100px) { .metrics-layout { grid-template-columns:minmax(320px, .75fr) minmax(360px, 1.25fr); } }
    @media (max-width: 900px) {
      .metrics-layout { grid-template-columns:1fr; overflow:auto; }
      .metrics-layout > .panel { min-height:420px; }
      .metric-filter-scroll { max-height:none; }
      .surface-header { grid-template-columns:1fr; }
      .surface-art { width:160px; }
      .mix-page { grid-template-columns:1fr; gap:26px; }
      .mix-hero { position:static; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h1>discocs</h1>
      <nav>
        <button class="active" data-nav="dashboard" onclick="showSection('dashboard')">Home</button>
        <button data-nav="listenerSearch" onclick="showSection('listenerSearch')">Search</button>
        <button data-nav="library" onclick="showSection('library')">Library</button>
        <button data-nav="operations" onclick="showSection('operations')">Operations</button>
        <button data-nav="browse" onclick="showSection('browse')">Browse</button>
        <button data-nav="metrics" onclick="showSection('metrics')">Metrics</button>
        <button data-nav="lostFiles" onclick="showSection('lostFiles')">Lost files</button>
        <button data-nav="erroredFiles" onclick="showSection('erroredFiles')">Errored files</button>
        <button data-nav="recommendations" onclick="showSection('recommendations')">Recommendations</button>
        <button data-nav="textSearch" onclick="showSection('textSearch')">Text search</button>
        <button data-nav="navidromeLikes" onclick="showSection('navidromeLikes')">Navidrome likes</button>
        <button data-nav="instantMix" onclick="showSection('instantMix')">Instant mix</button>
        <button data-nav="evaluation" onclick="showSection('evaluation')">Evaluation</button>
        <button data-nav="jobs" onclick="showSection('jobsPage')">Jobs</button>
        <button data-nav="workers" onclick="showSection('workersPage')">Workers</button>
        <button data-nav="settings" onclick="showSection('settings')">Settings</button>
      </nav>
    </aside>
    <main>
      <section id="dashboard" class="section active home-dashboard">
        <div class="home-top">
          <div>
            <h2 style="font-size:34px; margin:0 0 10px">Home</h2>
            <div class="home-search">
              <input id="homeSearchQuery" placeholder="Search artists, releases, tracks">
              <button class="primary" onclick="runHomeSearch()">Search</button>
            </div>
          </div>
        </div>
        <div class="listener-hero">
          <div>
            <h2>Flow</h2>
            <div class="meta">Personal stream placeholder. Playback surfaces are ready; Flow generation comes later.</div>
          </div>
          <div class="actions">
            <button class="primary" disabled>Start Flow</button>
            <a class="operations-link" href="?view=operations" onclick="event.preventDefault(); routeTo({view:'operations'}, {reset:true})">Operations</a>
          </div>
        </div>
        <div class="surface-grid" id="listenerDashboardShelves">
          <div class="meta">Loading listener shelves...</div>
        </div>
      </section>
      <section id="operations" class="section">
        <div class="panel">
          <h2>Operations</h2>
          <div class="meta">Scan, analyze, index, and job controls. The music Home stays focused on listening.</div>
        </div>
        <div class="stats" id="dashboardCards">
          <div class="stat"><strong id="tracks">0</strong><span>tracks</span></div>
          <div class="stat"><strong id="missingHeadPackTracks">0</strong><span>need tags</span></div>
          <div class="stat"><strong id="missingAudioFeatures">0</strong><span>need audio features</span></div>
          <div class="stat"><strong id="missingFiles">0</strong><span>lost files</span></div>
          <div class="stat"><strong id="erroredFilesStat">0</strong><span>errored files</span></div>
          <div class="stat"><strong id="navidromeExternalTracks">0</strong><span>Navidrome mapped</span></div>
        </div>
        <div class="model-cards" id="modelCards"></div>
        <div class="panel">
          <h2>Pipeline</h2>
          <div class="meta" id="modelState"></div>
        </div>
        <details class="panel" id="headPackDetails">
          <summary id="headPackSummary">Head models</summary>
          <div class="actions" style="margin-top:12px">
            <button id="downloadHeadsBtn" onclick="downloadHeadModels()">Download head models</button>
          </div>
          <div class="meta" id="headPackReadiness"></div>
          <div id="headPackModelTable"></div>
        </details>
        <div class="panel">
          <h2>Recent jobs</h2>
          <div id="dashboardJobs" class="jobs"></div>
        </div>
      </section>
      <section id="listenerSearch" class="section section-fill">
        <div class="panel panel-fill search-page-layout">
          <div>
            <h2>Search</h2>
            <div class="search">
              <input id="listenerSearchQuery" placeholder="Search artists, releases, tracks">
              <button onclick="runListenerSearch()">Search</button>
            </div>
            <div class="search-tabs" id="listenerSearchTabs">
              <button class="active" data-search-tab="all" onclick="setListenerSearchTab('all')">All</button>
              <button data-search-tab="artists" onclick="setListenerSearchTab('artists')">Artists</button>
              <button data-search-tab="tracks" onclick="setListenerSearchTab('tracks')">Tracks</button>
              <button data-search-tab="releases" onclick="setListenerSearchTab('releases')">Releases</button>
            </div>
          </div>
          <div class="list-region"><div id="listenerSearchResults" class="surface-grid">
            <div class="meta">Search the normalized library graph.</div>
          </div></div>
        </div>
      </section>
      <section id="artistSurface" class="section section-fill">
        <div class="panel panel-fill">
          <div id="artistSurfaceContent" class="surface-grid">
            <div class="meta">Loading artist...</div>
          </div>
        </div>
      </section>
      <section id="releaseSurface" class="section section-fill">
        <div class="panel panel-fill">
          <div id="releaseSurfaceContent" class="surface-grid">
            <div class="meta">Loading release...</div>
          </div>
        </div>
      </section>
      <section id="mixSurface" class="section section-fill">
        <div class="panel panel-fill">
          <div id="mixSurfaceContent" class="surface-grid">
            <div class="meta">Loading mix...</div>
          </div>
        </div>
      </section>
      <section id="library" class="section section-fill">
        <div class="panel panel-fill">
          <h2>Library</h2>
          <div class="search">
            <input id="query" placeholder="Search artist, title, album, path">
            <button onclick="searchTracks()">Search</button>
          </div>
          <div class="row">
            <label style="margin:0">Embedding status
              <select id="embeddingStatus" onchange="searchTracks()">
                <option value="all">all</option>
                <option value="ready">ready</option>
                <option value="missing">missing</option>
              </select>
            </label>
          </div>
          <div class="list-region"><div id="tracksList" class="list"></div></div>
        </div>
      </section>
      <section id="browse" class="section section-fill">
        <div class="browse-layout">
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Browse</h2>
              <button onclick="clearBrowseFilters()">Clear</button>
            </div>
            <label style="margin-top:0">Embedding status
              <select id="browseEmbeddingStatus" onchange="refreshBrowse()">
                <option value="all">all</option>
                <option value="ready">ready</option>
                <option value="missing">missing</option>
              </select>
            </label>
            <div class="facet-scroll">
              <div class="facet-group">
                <strong>Genres</strong>
                <div id="genreFacets" class="facet-list"></div>
              </div>
              <div class="facet-group">
                <strong>Years</strong>
                <div id="yearFacets" class="facet-list"></div>
              </div>
              <div class="facet-group">
                <strong>Artists</strong>
                <div id="artistFacets" class="facet-list"></div>
              </div>
              <div class="facet-group">
                <strong>Albums</strong>
                <div id="albumFacets" class="facet-list"></div>
              </div>
              <div class="facet-group">
                <strong>Folders</strong>
                <div id="folderFacets" class="facet-list"></div>
              </div>
            </div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Tracks</h2>
              <span class="pill" id="browseFilterLabel">all tracks</span>
            </div>
            <div class="search">
              <input id="browseQuery" placeholder="Search within selected folder or tag">
              <button onclick="loadBrowseTracks()">Search</button>
            </div>
            <div class="list-region"><div id="browseTracks" class="list"></div></div>
          </div>
        </div>
      </section>
      <section id="metrics" class="section section-fill">
        <div class="metrics-layout">
          <div class="panel panel-fill">
            <div class="metrics-controls">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Metrics</h2>
              <button onclick="clearMetricFilters()">Clear</button>
            </div>
            <label style="margin-top:0">Source
              <select id="metricsSource" onchange="loadMetricsExplorer()">
                <option value="audio_features">Audio features</option>
                <option value="heads">Discogs-EffNet heads</option>
              </select>
            </label>
            <label style="margin-top:0">Extractor
              <input id="metricsExtractor" value="audio_features_v1" onchange="loadMetricsExplorer()">
            </label>
            <div class="search">
              <input id="metricsQuery" placeholder="Search artist, title, album, path">
              <button onclick="searchMetrics()">Search</button>
            </div>
            <label>Sort by
              <select id="metricsSort" onchange="searchMetrics()"></select>
            </label>
            <label>Limit
              <input id="metricsLimit" type="number" min="1" max="500" value="50" onchange="searchMetrics()">
            </label>
            </div>
            <div id="metricFilterList" class="metric-filter-scroll"></div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Catalog lab</h2>
              <span class="pill" id="metricsResultCount">0 tracks</span>
            </div>
            <div id="metricsSummary" class="metric-grid"></div>
            <div class="list-region"><div id="metricsResults" class="list"></div></div>
          </div>
        </div>
      </section>
      <section id="lostFiles" class="section section-fill">
        <div class="panel panel-fill">
          <div class="row" style="justify-content:space-between; margin-bottom:10px">
            <h2>Lost files</h2>
            <span class="pill" id="lostFilesCount">0 lost</span>
          </div>
          <div class="actions">
            <button id="checkMissingBtn" onclick="checkMissingFiles()">Check missing files</button>
            <button onclick="toggleLostFilesSelection(true)">Select all</button>
            <button onclick="toggleLostFilesSelection(false)">Clear selection</button>
            <button id="deleteLostBtn" onclick="deleteSelectedLostFiles()">Remove selected</button>
            <button id="deleteAllLostBtn" onclick="deleteAllLostFiles()">Remove all</button>
            <button class="primary" onclick="loadLostFiles()">Refresh</button>
          </div>
          <div class="meta" style="margin-top:8px">Missing records stay in the database until you remove them.</div>
          <div class="row" style="justify-content:flex-end; margin-top:10px">
            <button onclick="previousLostFilesPage()">Previous</button>
            <span class="pill" id="lostFilesPage">page 1 / 1</span>
            <button onclick="nextLostFilesPage()">Next</button>
          </div>
          <div class="table-region"><div id="lostFilesList"></div></div>
        </div>
      </section>
      <section id="erroredFiles" class="section section-fill">
        <div class="panel panel-fill">
          <div class="row" style="justify-content:space-between; margin-bottom:10px">
            <h2>Errored files</h2>
            <span class="pill" id="erroredFilesCount">0 errors</span>
          </div>
          <div class="actions">
            <button onclick="toggleErroredFilesSelection(true)">Select all</button>
            <button onclick="toggleErroredFilesSelection(false)">Clear selection</button>
            <button id="deleteErroredBtn" onclick="deleteSelectedErroredFiles()">Remove selected</button>
            <button id="deleteAllErroredBtn" onclick="deleteAllErroredFiles()">Remove all</button>
            <button class="primary" onclick="loadErroredFiles()">Refresh</button>
          </div>
          <div class="meta" style="margin-top:8px">Files that failed analysis, grouped by track and model.</div>
          <div class="row" style="justify-content:flex-end; margin-top:10px">
            <button onclick="previousErroredFilesPage()">Previous</button>
            <span class="pill" id="erroredFilesPage">page 1 / 1</span>
            <button onclick="nextErroredFilesPage()">Next</button>
          </div>
          <div class="table-region"><div id="erroredFilesList"></div></div>
        </div>
      </section>
      <section id="recommendations" class="section section-fill">
        <div class="layout">
          <div class="panel panel-fill">
            <h2>Seed</h2>
            <div id="seedPanel">
              <div class="track">
                <div class="title">No seed selected</div>
                <div class="meta">Pick a seed from Library or search here.</div>
              </div>
            </div>
            <div class="search" style="margin-top:10px">
              <input id="seedQuery" placeholder="Search seed track">
              <button onclick="searchSeeds()">Search</button>
            </div>
            <div class="list-region"><div id="seedResults" class="list"></div></div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Similar</h2>
              <button onclick="refreshSimilarTracks()" id="refreshSimilarBtn" disabled>Refresh similar</button>
            </div>
            <div class="list-region"><div id="similarList" class="list"></div></div>
          </div>
        </div>
      </section>
      <section id="textSearch" class="section section-fill">
        <div class="layout">
          <div class="panel panel-fill">
            <h2>Text search</h2>
            <label style="margin-top:0">Describe music
              <textarea id="textSearchQuery" rows="5" placeholder="deep dub techno with warm chords and no vocals"></textarea>
            </label>
            <div class="actions">
              <button class="primary" onclick="runTextSearch()">Search</button>
              <button onclick="clearTextSearch()">Clear</button>
            </div>
            <div class="row">
              <label style="margin:0">Count
                <input id="textSearchCount" type="number" min="1" max="500" value="50">
              </label>
              <label style="margin:0">Min similarity
                <input id="textSearchMinSimilarity" type="number" min="0" max="1" step="0.001" value="">
              </label>
            </div>
            <label>Max per artist
              <input id="textSearchMaxPerArtist" type="number" min="1" max="100" value="2">
            </label>
            <label class="row">
              <input id="textSearchExcludeSameAlbum" type="checkbox" checked style="min-height:auto">
              <span>Exclude same album</span>
            </label>
            <div class="meta" id="textSearchStatus">Model: muq_mulan</div>
            <div class="actions">
              <button onclick="setTextSearchQuery('ambient dub, spacious, slow')">ambient dub</button>
              <button onclick="setTextSearchQuery('fast electro, metallic drums')">fast electro</button>
              <button onclick="setTextSearchQuery('warm deep house, late night')">deep house</button>
              <button onclick="setTextSearchQuery('breakbeat, melancholic, 90s')">breakbeat</button>
              <button onclick="setTextSearchQuery('dark minimal techno, hypnotic')">minimal techno</button>
            </div>
            <h2 style="margin-top:18px">Recent queries</h2>
            <div id="textSearchRecent" class="list"></div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Results</h2>
              <span class="pill">muq_mulan</span>
            </div>
            <div class="list-region"><div id="textSearchResults" class="list">
              <div class="meta">Describe the music you want to find.</div>
            </div></div>
          </div>
        </div>
      </section>
      <section id="navidromeLikes" class="section section-fill">
        <div class="panel blend-status">
          <div class="row" style="justify-content:space-between; margin-bottom:10px">
            <h2>Navidrome likes</h2>
            <span class="pill">Blend: Average</span>
          </div>
          <div class="meta" id="likedStatusLine">Source: Navidrome starred · not loaded yet</div>
          <div class="meta" id="likedStatusDetail">Load starred tracks for the configured Navidrome user, then refresh recommendations from their average embedding.</div>
          <div class="error" id="likedStatusError"></div>
          <div class="actions">
            <button class="primary" onclick="loadNavidromeLikes()">Load Navidrome likes</button>
            <button onclick="refreshLikedRecommendations()" id="refreshLikedBtn" disabled>Refresh recommendations</button>
            <button onclick="startAnalyze()">Analyze missing</button>
            <button onclick="startNavidromeSync()">Sync catalog</button>
            <button onclick="clearLikedBlend()">Clear</button>
          </div>
        </div>
        <div class="layout">
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Liked tracks</h2>
              <label style="margin:0">
                Filter
                <select id="likedFilter" onchange="onLikedFilterChange()">
                  <option value="all">all</option>
                  <option value="ready">ready</option>
                  <option value="missing_embedding">needs embedding</option>
                  <option value="not_synced">not synced</option>
                </select>
              </label>
            </div>
            <div class="search">
              <input id="likedLocalQuery" placeholder="Filter loaded likes">
              <button onclick="renderLikedTracks()">Filter</button>
            </div>
            <div class="list-region"><div id="likedTracksList" class="list"></div></div>
            <details style="margin-top:12px">
              <summary>Add extra track</summary>
              <div class="search" style="margin-top:10px">
                <input id="likedExtraQuery" placeholder="Search library to add">
                <button onclick="searchLikedExtra()">Search</button>
              </div>
              <label class="row" style="margin-top:8px">
                <input id="likedExtraShowMissing" type="checkbox" style="min-height:auto">
                <span>Show tracks without embeddings</span>
              </label>
              <div class="list-region"><div id="likedExtraSearchResults" class="list"></div></div>
              <div class="meta" id="likedExtraSummary" style="margin-top:8px"></div>
            </details>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <div>
                <h2>Recommendations</h2>
                <div class="meta" id="likedSimilarSubtitle">Recommendations from liked blend</div>
              </div>
            </div>
            <div class="list-region"><div id="likedSimilarList" class="list"></div></div>
          </div>
        </div>
      </section>
      <section id="instantMix" class="section section-fill">
        <div class="instant-mix-layout">
          <div class="instant-mix-sidebar">
            <div class="panel instant-mix-controls">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Instant mix</h2>
              <button class="primary" onclick="loadInstantMixRequests()">Refresh</button>
            </div>
              <label style="margin-top:0">Model
                <select id="instantMixModel">
                  <option value="discogs_multi">discogs_multi</option>
                  <option value="discogs_track">discogs_track</option>
                  <option value="discogs_release">discogs_release</option>
                  <option value="discogs_label">discogs_label</option>
                  <option value="muq_mulan">muq_mulan</option>
                </select>
              </label>
              <div class="row">
                <label style="margin:0">Count
                  <input id="instantMixCount" type="number" min="1" max="500" value="50">
                </label>
                <label style="margin:0">Min similarity
                  <input id="instantMixMinSimilarity" type="number" min="0" max="1" step="0.001" value="0.5">
                </label>
              </div>
              <label>Max per artist
                <input id="instantMixMaxPerArtist" type="number" min="1" max="100" value="2">
              </label>
              <label class="row">
                <input id="instantMixExcludeSameAlbum" type="checkbox" checked style="min-height:auto">
                <span>Exclude same album</span>
              </label>
              <label class="row">
                <input id="instantMixCountCollaborationArtists" type="checkbox" checked style="min-height:auto">
                <span>Count collaboration artists in artist cap</span>
              </label>
              <div class="actions">
                <button onclick="saveInstantMixSettings()">Save settings</button>
              </div>
              <div class="meta" id="instantMixStatus">Settings are loaded from runtime config.</div>
            </div>
            <div class="panel panel-fill instant-mix-history">
              <h2>Requests</h2>
              <div class="list-region" style="margin-top:12px"><div id="instantMixRequests" class="list"></div></div>
            </div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Request detail</h2>
              <button onclick="backToInstantMixList()">Back</button>
            </div>
            <div class="list-region"><div id="instantMixDetail" class="list">
              <div class="meta">Select a request to inspect returned tracks and parameters.</div>
            </div></div>
          </div>
        </div>
      </section>
      <section id="evaluation" class="section section-fill">
        <div class="layout">
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Seed basket</h2>
              <button onclick="clearSeedBasket()">Clear</button>
            </div>
            <div class="row">
              <button class="primary" onclick="startEvaluationSession()" id="startEvaluationBtn">Start session</button>
              <button onclick="nextEvaluationSeed()">Next seed</button>
              <button onclick="skipEvaluationSeed()">Skip seed</button>
            </div>
            <div class="meta" style="margin-top:8px">Ratings are absolute listening judgements for the current seed: good, okay, or bad.</div>
            <div id="evaluationProgress" class="meta" style="margin-top:8px"></div>
            <div class="list-region"><div id="seedBasket" class="basket"></div></div>
          </div>
          <div class="panel panel-fill">
            <div class="row" style="justify-content:space-between; margin-bottom:10px">
              <h2>Evaluate similar</h2>
              <button onclick="refreshSimilarTracks()" id="evaluationRefreshBtn" disabled>Refresh similar</button>
            </div>
            <div id="evaluationSeedPanel"></div>
            <div class="list-region"><div id="evaluationSimilarList" class="list"></div></div>
          </div>
        </div>
      </section>
      <section id="jobsPage" class="section">
        <div class="panel">
          <h2>Jobs</h2>
          <div id="jobs" class="jobs"></div>
        </div>
        <div class="panel">
          <h2>Job details</h2>
          <div id="jobDetail" class="jobs"><div class="meta">Select a job to inspect queued, leased, and failed tasks.</div></div>
        </div>
      </section>
      <section id="workersPage" class="section">
        <div class="panel">
          <h2>Workers</h2>
          <div id="workersSummary" class="stats"></div>
          <div id="workersList" class="jobs"></div>
        </div>
      </section>
      <section id="settings" class="section">
        <div class="panel settings-page">
        <h2>Settings</h2>
        <div class="settings-tabs" id="settingsTabs">
          <button class="active" data-settings-tab="embeddings" onclick="setSettingsTab('embeddings')">Embeddings and Models</button>
          <button data-settings-tab="analysis" onclick="setSettingsTab('analysis')">Analysis</button>
          <button data-settings-tab="general" onclick="setSettingsTab('general')">General</button>
          <button data-settings-tab="flow" onclick="setSettingsTab('flow')">Flow</button>
          <button data-settings-tab="autoplay" onclick="setSettingsTab('autoplay')">Autoplay</button>
          <button data-settings-tab="mixes" onclick="setSettingsTab('mixes')">Mixes</button>
          <button data-settings-tab="albums" onclick="setSettingsTab('albums')">Albums</button>
          <button data-settings-tab="dashboard" onclick="setSettingsTab('dashboard')">Dashboard</button>
          <button data-settings-tab="player" onclick="setSettingsTab('player')">Player</button>
          <button data-settings-tab="storage" onclick="setSettingsTab('storage')">Storage</button>
          <button data-settings-tab="advanced" onclick="setSettingsTab('advanced')">Advanced / Debug</button>
        </div>
        <div class="settings-pane active" data-settings-pane="embeddings">
        <label><span class="label-title">Model <span class="info" tabindex="0" data-tooltip="Embedding model used for analyze, index, and recommendations. Changing it requires separate embeddings and index.">(i)</span></span>
          <select id="model">
            <option value="discogs_multi">discogs_multi</option>
            <option value="discogs_track">discogs_track</option>
            <option value="discogs_release">discogs_release</option>
            <option value="discogs_label">discogs_label</option>
            <option value="muq_mulan">muq_mulan</option>
          </select>
        </label>
        <label><span class="label-title">Analyze limit <span class="info" tabindex="0" data-tooltip="Maximum number of missing tracks to process in one job. Empty means all missing tracks. Smaller batches are easier to test.">(i)</span></span>
          <input id="limit" type="number" min="1" value="20">
        </label>
        <label><span class="label-title">Analyze execution <span class="info" tabindex="0" data-tooltip="Choose where embedding analyze tasks are executed. Remote only queues tasks for HTTP pull workers; local only uses this server; local + remote lets both claim tasks.">(i)</span></span>
          <select id="analyzeExecutionMode" onchange="refreshWorkerCommand()">
            <option value="both">Local + remote</option>
            <option value="remote">Remote only</option>
            <option value="local">Local only</option>
          </select>
        </label>
        <label><span class="label-title">Analyze workers <span class="info" tabindex="0" data-tooltip="Number of analyzer processes. More workers can improve throughput but use more RAM and may slow each individual prediction. Current measured default: 4.">(i)</span></span>
          <input id="workers" type="number" min="1" value="4">
        </label>
        <label><span class="label-title">Analyze TF threads <span class="info" tabindex="0" data-tooltip="TensorFlow/OMP threads per analyzer process. Too high causes contention; benchmarked default is 4 with 4 workers.">(i)</span></span>
          <input id="tfThreads" type="number" min="1" value="4">
        </label>
        </div>
        <div class="settings-pane" data-settings-pane="analysis">
        <h3>Audio features</h3>
        <label><span class="label-title">Audio feature workers <span class="info" tabindex="0" data-tooltip="Number of local analyzer processes for BPM/key/loudness/dynamics. Separate from Discogs embedding workers so audio feature tuning cannot overload model inference.">(i)</span></span>
          <input id="audioFeatureWorkers" type="number" min="1" value="8">
        </label>
        <label><span class="label-title">Feature extractor <span class="info" tabindex="0" data-tooltip="Audio feature pipeline version stored in track_features. Rescan deletes existing values for this extractor and queues tracks for re-analysis.">(i)</span></span>
          <select id="audioFeaturesExtractor">
            <option value="audio_features_v1">audio_features_v1</option>
          </select>
        </label>
        <div class="meta" id="audioFeaturesStatus">Audio feature status not loaded yet.</div>
        <div class="actions">
          <button id="rescanAudioFeaturesBtn" onclick="rescanAudioFeatures()">Rescan audio features</button>
        </div>
        <div class="meta" id="audioFeaturesRescanStatus"></div>
        </div>
        <div class="settings-pane" data-settings-pane="general">
        <h3>Navidrome</h3>
        <label><span class="label-title">Navidrome URL <span class="info" tabindex="0" data-tooltip="Base URL that this app can reach, for example http://192.168.1.41:4533 or http://navidrome:4533 from Docker.">(i)</span></span>
          <input id="navidromeUrl" placeholder="http://127.0.0.1:4533">
        </label>
        <label><span class="label-title">Navidrome user <span class="info" tabindex="0" data-tooltip="Subsonic/Navidrome username used by this app for sync and audio downloads.">(i)</span></span>
          <input id="navidromeUser" autocomplete="username">
        </label>
        <label><span class="label-title">Navidrome password <span class="info" tabindex="0" data-tooltip="Leave blank when saving to keep the existing saved password.">(i)</span></span>
          <input id="navidromePassword" type="password" autocomplete="current-password" placeholder="leave blank to keep saved password">
        </label>
        <label><span class="label-title">Auth mode</span>
          <select id="navidromeAuthMode">
            <option value="token">token</option>
            <option value="password">password</option>
          </select>
        </label>
        <label><span class="label-title">Timeout seconds</span>
          <input id="navidromeTimeoutSeconds" type="number" min="1" max="600" value="60">
        </label>
        <label><span class="label-title">Download mode</span>
          <select id="navidromeDownloadMode">
            <option value="download">download</option>
            <option value="stream">stream</option>
          </select>
        </label>
        <label><span class="label-title">Temp dir</span>
          <input id="navidromeTempDir" placeholder="data/tmp/navidrome">
        </label>
        <div class="actions">
          <button class="primary" onclick="saveNavidromeSettings()">Save Navidrome</button>
          <button onclick="pingNavidrome()">Ping</button>
        </div>
        <div class="meta" id="navidromeStatus">Navidrome settings are loaded from server config.</div>
        <div class="meta">For Navidrome Instant Mix set <code>ND_AGENTS=discocs-instant-mix,deezer,lastfm,listenbrainz</code>.</div>
        </div>
        <div class="settings-pane" data-settings-pane="advanced">
        <h3>Remote worker</h3>
        <label><span class="label-title">Server URL for worker <span class="info" tabindex="0" data-tooltip="Base URL that the remote machine can reach. Use the host/IP running this web app, not localhost unless the worker runs on the same machine.">(i)</span></span>
          <input id="workerServerUrl" value="http://127.0.0.1:8711" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Worker ID <span class="info" tabindex="0" data-tooltip="Stable name shown in Jobs / Workers. Use a different ID for each remote machine.">(i)</span></span>
          <input id="workerId" value="gpu-4090-1" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Claim batch size <span class="info" tabindex="0" data-tooltip="How many queued tasks the worker asks for in one claim. Higher values reduce API round trips.">(i)</span></span>
          <input id="workerClaimBatchSize" type="number" min="1" value="2" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Max in-flight tasks <span class="info" tabindex="0" data-tooltip="Maximum leased tasks held by the worker while it downloads and processes audio. Keep this high enough to avoid GPU starvation.">(i)</span></span>
          <input id="workerMaxInflightTasks" type="number" min="1" value="2" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Download concurrency <span class="info" tabindex="0" data-tooltip="How many source audio files the worker downloads at the same time. Useful on fast LAN storage.">(i)</span></span>
          <input id="workerDownloadConcurrency" type="number" min="1" value="1" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Submit batch size <span class="info" tabindex="0" data-tooltip="How many results/failures the worker sends back in one request.">(i)</span></span>
          <input id="workerSubmitBatchSize" type="number" min="1" value="1" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Lease seconds <span class="info" tabindex="0" data-tooltip="How long the server waits before returning an unfinished leased task to the queue. Increase for slow models or long tracks.">(i)</span></span>
          <input id="workerLeaseSeconds" type="number" min="30" value="900" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Recycle after tasks <span class="info" tabindex="0" data-tooltip="Optional safety valve for suspected memory leaks. 0 keeps the worker running forever; a positive value exits after that many submitted tasks.">(i)</span></span>
          <input id="workerMaxTasksBeforeExit" type="number" min="0" value="0" oninput="refreshWorkerCommand()">
        </label>
        <label><span class="label-title">Worker command</span>
          <textarea id="workerCommand" rows="5" readonly></textarea>
        </label>
        </div>
        <div class="settings-pane" data-settings-pane="mixes">
        <h3>Generated Mixes</h3>
        <label><span class="label-title">Dashboard mixes <span class="info" tabindex="0" data-tooltip="How many finite generated mixes are kept on the Home dashboard. This controls the number of taste regions selected, not tracks inside each mix.">(i)</span></span>
          <input id="generatedMixDashboardCount" type="number" min="1" max="20" value="8">
        </label>
        <label><span class="label-title">Tracks per mix <span class="info" tabindex="0" data-tooltip="Target final playlist length for each generated mix. Tracks are selected from the candidate pool after scoring, familiar/discovery balancing, and artist/release caps.">(i)</span></span>
          <input id="generatedMixTracksPerMix" type="number" min="1" max="300" value="100">
        </label>
        <label><span class="label-title">Update cadence <span class="info" tabindex="0" data-tooltip="How often new listening preferences may trigger automatic regeneration. Daily waits at least 1 day after the newest active mix; weekly waits 7 days; manual disables preference-triggered refresh.">(i)</span></span>
          <select id="generatedMixUpdateCadence">
            <option value="daily">daily</option>
            <option value="weekly">weekly</option>
            <option value="manual">manual</option>
          </select>
        </label>
        <label><span class="label-title">Seed source <span class="info" tabindex="0" data-tooltip="Which user signals define taste regions. Listening history uses Navidrome/local plays plus likes and completions; track likes only ignores plays; positive history keeps the older liked/completed/replayed/score signals.">(i)</span></span>
          <select id="generatedMixSeedSource">
            <option value="listening_history">listening_history</option>
            <option value="track_likes_only">track_likes_only</option>
            <option value="positive_history">positive_history</option>
          </select>
        </label>
        <label><span class="label-title">Region threshold <span class="info" tabindex="0" data-tooltip="Similarity cutoff used while clustering listened/liked tracks into taste regions. Lower values make larger, broader regions; higher values make smaller, tighter regions. Try 0.76-0.78 if a mix is too narrow.">(i)</span></span>
          <input id="generatedMixRegionThreshold" type="number" min="0" max="1" step="0.01" value="0.82">
        </label>
        <label><span class="label-title">Discovery ratio <span class="info" tabindex="0" data-tooltip="Target share of final mix tracks that are new rather than already present in your listening/like signals. 0 allows mostly known tracks; 1 tries to fill the mix with new nearby tracks.">(i)</span></span>
          <input id="generatedMixDiscoveryRatio" type="number" min="0" max="1" step="0.05" value="0.75">
        </label>
        <label><span class="label-title">Novelty weight <span class="info" tabindex="0" data-tooltip="How strongly candidate scoring prefers unheard or long-unplayed tracks over recently heard tracks. This is separate from Discovery ratio: ratio controls known/new count, novelty weight controls ranking inside the candidate pool. 0 ignores novelty; 1 strongly favors fresher discoveries.">(i)</span></span>
          <input id="generatedMixNoveltyWeight" type="number" min="0" max="1" step="0.05" value="0.6">
        </label>
        <label><span class="label-title">Max per artist <span class="info" tabindex="0" data-tooltip="Maximum tracks by the same artist in the final generated mix. This is applied after candidate scoring, not to the candidate pool. Lower values increase variety but may reject very close matches.">(i)</span></span>
          <input id="generatedMixMaxPerArtist" type="number" min="1" max="50" value="4">
        </label>
        <label><span class="label-title">Max per release <span class="info" tabindex="0" data-tooltip="Maximum tracks from the same album/release in the final generated mix. This is applied after candidate scoring, not to the candidate pool. Use 1 for stronger album variety.">(i)</span></span>
          <input id="generatedMixMaxPerRelease" type="number" min="1" max="50" value="2">
        </label>
        <label><span class="label-title">Candidate pool <span class="info" tabindex="0" data-tooltip="Maximum nearest-neighbor candidates considered per mix before final selection. Larger values give caps and discovery balancing more room, but generation is slower. This is not the final playlist length.">(i)</span></span>
          <input id="generatedMixCandidatePool" type="number" min="10" max="5000" value="1200">
        </label>
        <label><span class="label-title">Duplicate strictness <span class="info" tabindex="0" data-tooltip="Strict prevents the same track from appearing in multiple generated mixes in one generation run. Soft allows overlap when regions are close or the catalog is sparse.">(i)</span></span>
          <select id="generatedMixDuplicateStrictness">
            <option value="strict">strict</option>
            <option value="soft">soft</option>
          </select>
        </label>
        <label><span class="label-title">Mix model <span class="info" tabindex="0" data-tooltip="Embedding model used for taste regions and candidate search. The selected model needs embeddings and preferably a ready index.">(i)</span></span>
          <select id="generatedMixModel">
            <option value="discogs_multi">discogs_multi</option>
            <option value="discogs_track">discogs_track</option>
            <option value="discogs_release">discogs_release</option>
            <option value="discogs_label">discogs_label</option>
            <option value="muq_mulan">muq_mulan</option>
          </select>
        </label>
        <div class="actions">
          <button class="primary" onclick="saveGeneratedMixSettings()">Save generated mix settings</button>
          <button id="regenerateMixesBtn" onclick="forceRegenerateGeneratedMixes()">Regenerate now</button>
          <button onclick="loadGeneratedMixStatus()">Refresh status</button>
        </div>
        <div class="meta" id="generatedMixStatus">Generated mix settings not loaded yet.</div>
        <pre class="meta" id="generatedMixDiagnostics"></pre>
        <h3>Similarity Recommendations</h3>
        <label><span class="label-title">Results <span class="info" tabindex="0" data-tooltip="Number of similar tracks requested for the current seed. Higher values return a wider list but may include weaker matches.">(i)</span></span>
          <input id="k" type="number" min="1" max="100" value="30">
        </label>
        <label><span class="label-title">Max per artist <span class="info" tabindex="0" data-tooltip="Limits repeated artists in recommendations. Lower values make results more diverse.">(i)</span></span>
          <input id="maxPerArtist" type="number" min="1" max="20" value="2">
        </label>
        <label class="row">
          <input id="excludeSameAlbum" type="checkbox" checked style="min-height:auto">
          <span class="label-title">Exclude same album <span class="info" tabindex="0" data-tooltip="Removes tracks from the same album as the seed. Useful when you want discovery instead of near-duplicates.">(i)</span></span>
        </label>
        </div>
        <div class="settings-pane" data-settings-pane="flow">
          <h3>Flow</h3>
          <div class="meta">Reserved for Flow generation settings after the Flow engine lands.</div>
        </div>
        <div class="settings-pane" data-settings-pane="autoplay">
          <h3>Autoplay</h3>
          <label><span class="label-title">Visible buffer</span>
            <input id="autoplayVisibleBuffer" type="number" min="1" max="50" value="5">
          </label>
          <label><span class="label-title">Prepared pool</span>
            <input id="autoplayCandidateCount" type="number" min="1" max="500" value="50">
          </label>
          <div class="meta">Autoplay continues the active source first; chips lightly adjust reranking.</div>
        </div>
        <div class="settings-pane" data-settings-pane="albums">
          <h3>Albums</h3>
          <div class="meta">Reserved for album/release grouping preferences.</div>
        </div>
        <div class="settings-pane" data-settings-pane="dashboard">
          <h3>Dashboard</h3>
          <div class="meta">Reserved for Home shelf visibility and ordering.</div>
        </div>
        <div class="settings-pane" data-settings-pane="player">
          <h3>Player</h3>
          <div class="meta">Player event thresholds are exposed by <code>/api/v1/playback/settings</code>.</div>
        </div>
        <div class="settings-pane" data-settings-pane="storage">
          <h3>Storage</h3>
          <div class="meta">Runtime database, indexes, models, and generated evaluation output remain local and gitignored.</div>
        </div>
        </div>
      </section>
    </main>
  </div>
  <div class="modal-backdrop" id="analysisModal" onclick="closeAnalysisModal(event)">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="analysisTitle" onclick="event.stopPropagation()">
      <div class="row" style="justify-content:space-between">
        <div>
          <h2 id="analysisTitle">Track analysis</h2>
          <div class="meta" id="analysisSubtitle"></div>
        </div>
        <button class="icon-button" onclick="closeAnalysisModal()" aria-label="Close analysis">&times;</button>
      </div>
      <div id="analysisContent" class="analysis-grid"></div>
    </div>
  </div>
  <div class="track-action-menu" id="trackActionMenu" role="menu">
    <button type="button" role="menuitem" onclick="startInstantMixFromOpenMenu()">Instant Mix</button>
  </div>
  <div class="expanded-player" id="expandedPlayer">
    <div class="expanded-main">
      <div class="expanded-art" id="expandedPlayerArt">ART</div>
      <div class="expanded-track-text">
        <h2 id="expandedPlayerTitle" style="font-size:28px; margin:14px 0 4px">Nothing playing</h2>
        <div class="meta" id="expandedPlayerSubtitle">Start playback from search, artist, or release pages.</div>
      </div>
    </div>
    <div class="queue-panel">
      <div class="queue-tabs">
        <button class="active">Up Next</button>
        <button disabled>Lyrics/Text</button>
        <button disabled>Related</button>
      </div>
      <div class="row" style="justify-content:space-between">
        <div class="meta">Source:<br><strong id="queueSourceLabel">No active playback session</strong></div>
        <button class="icon-button" onclick="toggleExpandedPlayer()" aria-label="Close expanded player">&times;</button>
      </div>
      <div class="autoplay-prep">
        <div class="autoplay-row">
          <strong>Autoplay</strong>
          <button id="autoplayToggle" class="toggle-pill" onclick="toggleAutoplay()" title="Toggle autoplay" aria-label="Toggle autoplay"></button>
        </div>
        <div class="meta" id="autoplayStatus">Autoplay follows the active source.</div>
      </div>
      <div class="queue-list" id="playerQueueList"><div class="meta">Queue is empty.</div></div>
    </div>
  </div>
  <div class="player">
    <div class="player-seek" id="playerSeekWrap">
      <input id="playerSeek" type="range" min="0" max="1000" value="0" aria-label="Seek playback">
      <div class="player-seek-bubble" id="playerSeekBubble">0:00</div>
    </div>
    <div class="player-controls">
      <button onclick="playPreviousQueueItem()" title="Previous" aria-label="Previous"><i class="bi bi-skip-start-fill" aria-hidden="true"></i></button>
      <button id="playerPlayButton" class="player-play" onclick="toggleAudioPlayback()" title="Play/Pause" aria-label="Play/Pause"><i class="bi bi-play-fill" aria-hidden="true"></i></button>
      <button onclick="skipCurrentTrack()" title="Skip" aria-label="Skip"><i class="bi bi-skip-end-fill" aria-hidden="true"></i></button>
    </div>
    <div class="player-now">
      <div class="player-cover" id="playerCover">ART</div>
      <div>
        <div class="player-title">
          <strong id="nowPlaying">No track loaded</strong>
          <div class="player-inline-actions">
            <button id="playerNavidromeLikeButton" class="navidrome-like-button" data-navidrome-like="1" onclick="toggleCurrentNavidromeLike(event)" title="Like in Navidrome" aria-label="Like in Navidrome"><i class="bi bi-hand-thumbs-up" aria-hidden="true"></i></button>
            <button onclick="recordCurrentPreference('disliked')" title="Dislike" aria-label="Dislike"><i class="bi bi-hand-thumbs-down" aria-hidden="true"></i></button>
            <button id="playerTrackMenuButton" class="track-menu-button" onclick="openCurrentTrackMenu(event)" title="Track menu" aria-label="Track menu"><i class="bi bi-three-dots-vertical" aria-hidden="true"></i></button>
          </div>
        </div>
        <div class="player-subtitle meta" id="nowPlayingMeta">Persistent player is ready.</div>
        <span class="error" id="playerError"></span>
      </div>
    </div>
    <div class="navidrome-debug" id="navidromeLikeDebug">
      <span class="build-marker">UI build likes-remote-only-20260611-1918</span>
      · Navidrome likes debug: idle
    </div>
    <div class="player-progress">
      <audio id="audioPlayer" preload="none"></audio>
      <div class="player-time"><span id="playerElapsed">0:00</span><span id="playerDuration">0:00</span></div>
    </div>
    <div class="player-actions">
      <div class="player-volume">
        <button id="volumeButton" onclick="toggleMute()" title="Mute" aria-label="Mute"><i class="bi bi-volume-up" aria-hidden="true"></i></button>
        <div class="player-volume-slider">
          <input id="volumeSlider" type="range" min="0" max="100" value="100" aria-label="Volume">
        </div>
      </div>
      <button id="repeatOneButton" onclick="toggleRepeatOne()" title="Repeat one" aria-label="Repeat one"><i class="bi bi-repeat-1" aria-hidden="true"></i></button>
      <button id="shuffleButton" onclick="toggleShuffleMode()" title="Shuffle" aria-label="Shuffle"><i class="bi bi-shuffle" aria-hidden="true"></i></button>
      <button onclick="toggleExpandedPlayer()" title="Expand player" aria-label="Expand player"><i class="bi bi-caret-up-fill" aria-hidden="true"></i></button>
    </div>
  </div>
  <script>
    const SETTINGS_KEY = "discocs.settings.v1";
    const PLAYER_STATE_KEY = "discocs.playerState.v1";
    const BLEND_EXTRA_KEY = "discocs.blendExtra.v1";
    const UI_BUILD_ID = "player-stream-debug-20260623-0825";
    const SETTINGS_FIELDS = [
      "model", "limit", "analyzeExecutionMode", "workers", "tfThreads", "audioFeatureWorkers", "audioFeaturesExtractor",
      "workerServerUrl", "workerId", "workerClaimBatchSize", "workerMaxInflightTasks",
      "workerDownloadConcurrency", "workerSubmitBatchSize", "workerLeaseSeconds",
      "workerMaxTasksBeforeExit",
      "k", "maxPerArtist", "excludeSameAlbum",
      "textSearchCount", "textSearchMinSimilarity", "textSearchMaxPerArtist", "textSearchExcludeSameAlbum"
    ];
    const NAVIDROME_SETTINGS_FIELDS = [
      "navidromeUrl", "navidromeUser", "navidromeAuthMode", "navidromeTimeoutSeconds",
      "navidromeDownloadMode", "navidromeTempDir"
    ];
    let seedId = null;
    let seedTrack = null;
    let activeTrackId = null;
    let activePlaybackSession = null;
    let activePlaybackQueue = null;
    let activeQueueItemId = null;
    let lastAutoplayStatus = null;
    let progressEventSent = false;
    let playerState = {volume: 1, muted: false, shuffle: false, repeatOne: false};
    let currentSimilarTracks = [];
    let lastJobs = [];
    let seedBasket = [];
    let likedCatalog = null;
    let navidromeLikeIdsRefreshScheduled = false;
    let navidromeLikeLastDebug = "idle";
    let extraBlendIds = [];
    let currentInstantMixRequestId = null;
    let openTrackActionTrackId = null;
    let textSearchRecentQueries = [];
    let evaluationIndex = -1;
    let browseFilters = {};
    let lostFilesPage = 1;
    const lostFilesPageSize = 50;
    let erroredFilesPage = 1;
    const erroredFilesPageSize = 50;
    let statsInFlight = false;
    let jobsInFlight = false;
    function model() { return document.getElementById("model").value; }
    function text(value) { return value || ""; }
    function formatDuration(seconds) {
      const value = Number(seconds);
      if (!Number.isFinite(value) || value < 0) return "";
      const total = Math.round(value);
      if (total < 60) return `${total}s`;
      const days = Math.floor(total / 86400);
      const hours = Math.floor((total % 86400) / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secs = total % 60;
      if (days > 0) return `${days}d ${String(hours).padStart(2, "0")}h`;
      if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
      return `${minutes}m ${String(secs).padStart(2, "0")}s`;
    }
    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[char]));
    }
    function trackMenuButton(trackId, compact = false) {
      const id = Number(trackId);
      if (!Number.isFinite(id)) return "";
      const classes = compact ? "stat-icon-button track-menu-button" : "icon-button track-menu-button";
      return `<button class="${classes}" onclick="openTrackMenu(event, ${id})" title="Track menu" aria-label="Track menu">
        <i class="bi bi-three-dots-vertical" aria-hidden="true"></i>
      </button>`;
    }
    function closeTrackMenu() {
      const menu = document.getElementById("trackActionMenu");
      if (menu) menu.classList.remove("open");
      openTrackActionTrackId = null;
    }
    function openTrackMenu(event, trackId) {
      event.preventDefault();
      event.stopPropagation();
      const id = Number(trackId);
      if (!Number.isFinite(id)) return;
      const menu = document.getElementById("trackActionMenu");
      if (!menu) return;
      openTrackActionTrackId = id;
      const rect = event.currentTarget.getBoundingClientRect();
      const playerTone = Boolean(event.currentTarget.closest(".player") || event.currentTarget.closest(".expanded-player"));
      menu.classList.toggle("player-menu", playerTone);
      menu.classList.add("open");
      const width = menu.offsetWidth || 170;
      const height = menu.offsetHeight || 40;
      const left = Math.min(window.innerWidth - width - 8, Math.max(8, rect.right - width));
      const preferAbove = rect.top - height - 8 >= 8;
      const belowTop = rect.bottom + 6;
      const aboveTop = rect.top - height - 8;
      const top = Math.min(
        window.innerHeight - height - 8,
        Math.max(8, preferAbove && aboveTop >= 8 ? aboveTop : belowTop)
      );
      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
    }
    function openCurrentTrackMenu(event) {
      const item = currentQueueItem();
      const trackId = item?.track_id || activeTrackId;
      if (!trackId) return;
      openTrackMenu(event, trackId);
    }
    async function startInstantMixForTrack(trackId) {
      const id = Number(trackId);
      if (!Number.isFinite(id)) return;
      closeTrackMenu();
      document.getElementById("playerError").textContent = "";
      lastAutoplayStatus = "Instant Mix is loading...";
      renderPlayerState();
      try {
        const data = await json(`/tracks/${id}/instant-mix`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          timeoutMs: 60000,
        });
        activePlaybackSession = data.session;
        activePlaybackQueue = data.queue;
        lastAutoplayStatus = `Instant Mix ready - ${(data.queue?.items || []).length} tracks.`;
        const firstItem = data.queue?.current_item || data.queue?.items?.[0];
        activeQueueItemId = firstItem?.id || null;
        renderPlayerState();
        if (firstItem?.track_id) {
          await playTrack(Number(firstItem.track_id), encodedArg(queueTrackLabel(firstItem)), {queueItemId: activeQueueItemId});
        }
        scheduleAutoplayRefill();
      } catch (err) {
        setInstantMixStatus(`Instant mix failed: ${err.message}`, true);
        document.getElementById("playerError").textContent = `Instant mix failed: ${err.message}`;
        lastAutoplayStatus = null;
        renderPlayerState();
      }
    }
    function startInstantMixFromOpenMenu() {
      if (openTrackActionTrackId !== null) startInstantMixForTrack(openTrackActionTrackId);
    }
    function label(t) { return `${text(t.artist)} - ${text(t.title) || t.path}`.replace(/^ - /, ""); }
    function encodedArg(value) { return encodeURIComponent(value).replace(/'/g, "%27"); }
    function compactFolder(path) {
      const parts = String(path).split(/[\\/]+/).filter(Boolean);
      if (parts.length <= 2) return path;
      return parts.slice(-2).join(" / ");
    }
    async function json(url, options = {}) {
      const timeoutMs = options.timeoutMs || 30000;
      const timeoutController = new AbortController();
      const timeoutId = setTimeout(() => timeoutController.abort(), timeoutMs);
      const externalSignal = options.signal;
      if (externalSignal) {
        if (externalSignal.aborted) timeoutController.abort();
        else externalSignal.addEventListener("abort", () => timeoutController.abort(), {once: true});
      }
      const fetchOptions = {...options, signal: timeoutController.signal};
      delete fetchOptions.timeoutMs;
      try {
      const response = await fetch(url, fetchOptions);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || response.statusText);
      return data;
      } catch (err) {
        if (err.name === "AbortError") throw new Error("Request timed out or was cancelled");
        throw err;
      } finally {
        clearTimeout(timeoutId);
      }
    }
    function readSavedSettings() {
      try {
        return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
      } catch (err) {
        return {};
      }
    }
    function saveSettings() {
      const data = {};
      SETTINGS_FIELDS.forEach(id => {
        const element = document.getElementById(id);
        if (!element) return;
        data[id] = element.type === "checkbox" ? element.checked : element.value;
      });
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(data));
    }
    function loadSettings() {
      const data = readSavedSettings();
      SETTINGS_FIELDS.forEach(id => {
        const element = document.getElementById(id);
        if (!element || data[id] === undefined) return;
        if (element.type === "checkbox") {
          element.checked = Boolean(data[id]);
        } else {
          element.value = data[id];
        }
      });
    }
    function bindSettingsAutosave() {
      SETTINGS_FIELDS.forEach(id => {
        const element = document.getElementById(id);
        if (!element) return;
        element.addEventListener("change", saveSettings);
        element.addEventListener("input", saveSettings);
      });
    }
    function readSavedPlayerState() {
      try {
        const saved = JSON.parse(localStorage.getItem(PLAYER_STATE_KEY) || "{}");
        return saved && typeof saved === "object" ? saved : {};
      } catch (_err) {
        return {};
      }
    }
    function savePlayerState() {
      localStorage.setItem(PLAYER_STATE_KEY, JSON.stringify(playerState));
    }
    function loadPlayerState() {
      const saved = readSavedPlayerState();
      const volume = Number(saved.volume);
      playerState = {
        volume: Number.isFinite(volume) ? Math.max(0, Math.min(1, volume)) : 1,
        muted: Boolean(saved.muted),
        shuffle: Boolean(saved.shuffle),
        repeatOne: Boolean(saved.repeatOne),
      };
      applyPlayerVolume();
      renderPlaybackButtons();
    }
    function activeShuffleEnabled() {
      return Boolean(activePlaybackSession?.shuffle_enabled ?? playerState.shuffle);
    }
    function activeRepeatOneEnabled() {
      return (activePlaybackSession?.repeat_mode || (playerState.repeatOne ? "one" : "off")) === "one";
    }
    function applyPlayerVolume() {
      const player = document.getElementById("audioPlayer");
      const slider = document.getElementById("volumeSlider");
      const button = document.getElementById("volumeButton");
      if (player) {
        player.volume = playerState.volume;
        player.muted = playerState.muted || playerState.volume <= 0;
      }
      if (slider) slider.value = String(Math.round(playerState.volume * 100));
      if (button) {
        const muted = playerState.muted || playerState.volume <= 0;
        const icon = muted ? "bi-volume-mute" : (playerState.volume < 0.5 ? "bi-volume-down" : "bi-volume-up");
        button.classList.toggle("active", muted);
        button.innerHTML = `<i class="bi ${icon}" aria-hidden="true"></i>`;
        button.title = muted ? "Unmute" : "Mute";
        button.setAttribute("aria-label", muted ? "Unmute" : "Mute");
      }
    }
    function renderPlaybackButtons() {
      const player = document.getElementById("audioPlayer");
      const playButton = document.getElementById("playerPlayButton");
      if (playButton) {
        const playing = player && !player.paused && !player.ended;
        playButton.innerHTML = `<i class="bi ${playing ? "bi-pause-fill" : "bi-play-fill"}" aria-hidden="true"></i>`;
        playButton.classList.toggle("active", playing);
      }
      const repeatButton = document.getElementById("repeatOneButton");
      if (repeatButton) repeatButton.classList.toggle("active", activeRepeatOneEnabled());
      const shuffleButton = document.getElementById("shuffleButton");
      if (shuffleButton) shuffleButton.classList.toggle("active", activeShuffleEnabled());
      applyPlayerVolume();
    }
    function setPlayerVolume(rawValue) {
      const value = Math.max(0, Math.min(1, Number(rawValue || 0) / 100));
      playerState.volume = value;
      playerState.muted = value <= 0 ? true : false;
      savePlayerState();
      applyPlayerVolume();
    }
    function toggleMute() {
      playerState.muted = !(playerState.muted || playerState.volume <= 0);
      if (!playerState.muted && playerState.volume <= 0) playerState.volume = 0.6;
      savePlayerState();
      applyPlayerVolume();
    }
    async function patchPlaybackMode(payload) {
      if (!activePlaybackSession?.id) {
        renderPlaybackButtons();
        return;
      }
      try {
        const data = await json(`/api/v1/playback/sessions/${activePlaybackSession.id}`, {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
        });
        activePlaybackSession = data.session;
        activePlaybackQueue = data.queue;
      } catch (err) {
        document.getElementById("playerError").textContent = `Playback mode failed: ${err.message}`;
      }
      renderPlayerState();
    }
    async function toggleRepeatOne() {
      const enabled = !activeRepeatOneEnabled();
      playerState.repeatOne = enabled;
      savePlayerState();
      await patchPlaybackMode({repeat_mode: enabled ? "one" : "off"});
    }
    async function toggleShuffleMode() {
      const enabled = !activeShuffleEnabled();
      playerState.shuffle = enabled;
      savePlayerState();
      await patchPlaybackMode({shuffle_enabled: enabled});
    }
    function workerSetting(id, fallback) {
      const element = document.getElementById(id);
      return element && element.value ? element.value : fallback;
    }
    function shellQuote(value) {
      const textValue = String(value || "");
      return textValue.includes(" ") ? `"${textValue.replace(/"/g, '\\"')}"` : textValue;
    }
    function refreshWorkerCommand() {
      const server = workerSetting("workerServerUrl", "http://127.0.0.1:8711");
      const workerId = workerSetting("workerId", "gpu-4090-1");
      const embeddingModels = "discogs_multi,discogs_track,discogs_release,discogs_label,muq_mulan";
      const command = [
        "recs worker",
        "--server", shellQuote(server),
        "--worker-id", shellQuote(workerId),
        "--models", embeddingModels,
        "--models", "audio_features_v1",
        "--models", "discogs-effnet-heads",
        "--claim-batch-size", workerSetting("workerClaimBatchSize", "2"),
        "--max-inflight-tasks", workerSetting("workerMaxInflightTasks", "2"),
        "--download-concurrency", workerSetting("workerDownloadConcurrency", "1"),
        "--submit-batch-size", workerSetting("workerSubmitBatchSize", "1"),
        "--lease-seconds", workerSetting("workerLeaseSeconds", "900"),
        "--max-tasks-before-exit", workerSetting("workerMaxTasksBeforeExit", "0")
      ].join(" ");
      const target = document.getElementById("workerCommand");
      if (target) target.value = command;
    }
    function setSettingsTab(tab) {
      document.querySelectorAll("#settingsTabs button").forEach(button => {
        button.classList.toggle("active", button.dataset.settingsTab === tab);
      });
      document.querySelectorAll(".settings-pane").forEach(pane => {
        pane.classList.toggle("active", pane.dataset.settingsPane === tab);
      });
    }
    const VIEW_TO_SECTION = {
      dashboard: "dashboard",
      operations: "operations",
      listenerSearch: "listenerSearch",
      artist: "artistSurface",
      release: "releaseSurface",
      mix: "mixSurface",
      library: "library",
      browse: "browse",
      metrics: "metrics",
      lostFiles: "lostFiles",
      erroredFiles: "erroredFiles",
      recommendations: "recommendations",
      textSearch: "textSearch",
      navidromeLikes: "navidromeLikes",
      instantMix: "instantMix",
      evaluation: "evaluation",
      jobs: "jobsPage",
      workers: "workersPage",
      settings: "settings",
    };
    const BROWSE_FACET_KEYS = ["folder", "genre", "year", "artist", "album"];
    let metricSummaries = [];
    let metricFilters = {};
    let metricsLoadController = null;
    let metricsSearchController = null;
    let metricsLoadSeq = 0;
    let metricsSearchSeq = 0;
    let applyingRoute = false;

    function sectionIdForView(view) {
      const candidate = VIEW_TO_SECTION[view] || view || "dashboard";
      return document.getElementById(candidate) ? candidate : "dashboard";
    }
    function viewForSection(sectionId) {
      if (sectionId === "artistSurface") return "artist";
      if (sectionId === "releaseSurface") return "release";
      if (sectionId === "mixSurface") return "mix";
      if (sectionId === "jobsPage") return "jobs";
      if (sectionId === "workersPage") return "workers";
      return sectionId;
    }
    function paramsFromSearch(search = location.search) {
      const raw = Object.fromEntries(new URLSearchParams(search));
      let pathView = "dashboard";
      const artistMatch = location.pathname.match(/^\/artists\/(\d+)\/?$/);
      const releaseMatch = location.pathname.match(/^\/releases\/(\d+)\/?$/);
      const mixMatch = location.pathname.match(/^\/mixes\/([^/]+)\/?$/);
      if (location.pathname === "/search") pathView = "listenerSearch";
      else if (location.pathname === "/settings") pathView = "settings";
      else if (artistMatch) pathView = "artist";
      else if (releaseMatch) pathView = "release";
      else if (mixMatch) pathView = "mix";
      const params = {view: raw.view || pathView};
      if (artistMatch && raw.artist_id === undefined) params.artist_id = artistMatch[1];
      if (releaseMatch && raw.release_id === undefined) params.release_id = releaseMatch[1];
      if (mixMatch && raw.mix_id === undefined) params.mix_id = mixMatch[1];
      Object.entries(raw).forEach(([key, value]) => {
        if (value === "" || value === undefined || value === null) return;
        params[key] = value;
      });
      return params;
    }
    function recommendationParams() {
      return {
        k: document.getElementById("k").value,
        max_per_artist: document.getElementById("maxPerArtist").value,
        exclude_same_album: document.getElementById("excludeSameAlbum").checked ? "true" : "false",
      };
    }
    function textSearchParams() {
      return {
        text_query: document.getElementById("textSearchQuery").value.trim(),
        count: document.getElementById("textSearchCount").value,
        min_similarity: document.getElementById("textSearchMinSimilarity").value,
        max_per_artist: document.getElementById("textSearchMaxPerArtist").value,
        exclude_same_album: document.getElementById("textSearchExcludeSameAlbum").checked ? "true" : "false",
      };
    }
    function libraryParams() {
      return {
        query: document.getElementById("query").value,
        embedding_status: document.getElementById("embeddingStatus").value,
      };
    }
    function browseParams() {
      const params = {
        query: document.getElementById("browseQuery").value,
        embedding_status: document.getElementById("browseEmbeddingStatus").value,
      };
      BROWSE_FACET_KEYS.forEach(key => {
        if (browseFilters[key]) params[key] = browseFilters[key];
      });
      return params;
    }
    function buildRouteSearch(params) {
      const search = new URLSearchParams();
      Object.entries(params).forEach(([key, value]) => {
        if (value === "" || value === undefined || value === null) return;
        search.set(key, String(value));
      });
      return search.toString();
    }
    function activateSection(sectionId) {
      document.querySelectorAll(".section").forEach(section => section.classList.toggle("active", section.id === sectionId));
      document.querySelectorAll("nav button").forEach(button => button.classList.toggle(
        "active",
        button.dataset.nav === sectionId
        || (sectionId === "jobsPage" && button.dataset.nav === "jobs")
        || (sectionId === "workersPage" && button.dataset.nav === "workers")
      ));
    }
    function applySettingsFromParams(params) {
      if (params.model) {
        const modelElement = document.getElementById("model");
        if (modelElement) modelElement.value = params.model;
      }
      if (params.k) document.getElementById("k").value = params.k;
      if (params.max_per_artist) document.getElementById("maxPerArtist").value = params.max_per_artist;
      if (params.exclude_same_album !== undefined) {
        document.getElementById("excludeSameAlbum").checked = params.exclude_same_album !== "false";
      }
    }
    async function restoreRecommendations(params) {
      const seed = params.seed ? Number(params.seed) : null;
      if (!Number.isInteger(seed) || seed <= 0) {
        seedId = null;
        seedTrack = null;
        document.getElementById("seedPanel").innerHTML = `
          <div class="track">
            <div class="title">No seed selected</div>
            <div class="meta">Pick a seed from Library or search here.</div>
          </div>`;
        document.getElementById("refreshSimilarBtn").disabled = true;
        document.getElementById("similarList").innerHTML = "";
        return;
      }
      try {
        seedId = seed;
        seedTrack = await json(`/tracks/${seed}`);
        document.getElementById("seedPanel").innerHTML = renderTrack({...seedTrack, has_embedding: true}, "seed");
        document.getElementById("refreshSimilarBtn").disabled = false;
        await loadSimilar(seed, {updateUrl: false});
      } catch (err) {
        seedId = null;
        seedTrack = null;
        document.getElementById("seedPanel").innerHTML = `
          <div class="track">
            <div class="title">Seed #${seed} not found</div>
            <div class="meta">${esc(err.message)}</div>
          </div>`;
        document.getElementById("refreshSimilarBtn").disabled = true;
        document.getElementById("similarList").innerHTML = "";
      }
    }
    async function applyRoute(params) {
      applyingRoute = true;
      try {
        const view = params.view || "dashboard";
        const sectionId = sectionIdForView(view);
        applySettingsFromParams(params);
        activateSection(sectionId);
        if (sectionId === "listenerSearch") {
          if (params.q !== undefined) document.getElementById("listenerSearchQuery").value = params.q;
          await runListenerSearch({updateUrl: false});
        } else if (sectionId === "artistSurface") {
          await loadArtistSurface(Number(params.artist_id || params.id || 0));
        } else if (sectionId === "releaseSurface") {
          await loadReleaseSurface(Number(params.release_id || params.id || 0));
        } else if (sectionId === "mixSurface") {
          await loadMixSurface(params.mix_id || params.id || "");
        } else if (sectionId === "library") {
          if (params.query !== undefined) document.getElementById("query").value = params.query;
          if (params.embedding_status) document.getElementById("embeddingStatus").value = params.embedding_status;
          await searchTracks({updateUrl: false});
        } else if (sectionId === "browse") {
          browseFilters = {};
          BROWSE_FACET_KEYS.forEach(key => {
            if (params[key]) browseFilters[key] = params[key];
          });
          if (params.query !== undefined) document.getElementById("browseQuery").value = params.query;
          if (params.embedding_status) document.getElementById("browseEmbeddingStatus").value = params.embedding_status;
          await refreshBrowse({updateUrl: false});
        } else if (sectionId === "metrics") {
          if (params.query !== undefined) document.getElementById("metricsQuery").value = params.query;
          if (params.source) document.getElementById("metricsSource").value = params.source;
          await loadMetricsExplorer({updateUrl: false});
        } else if (sectionId === "recommendations") {
          if (params.seedQuery !== undefined) document.getElementById("seedQuery").value = params.seedQuery;
          await restoreRecommendations(params);
        } else if (sectionId === "textSearch") {
          if (params.text_query !== undefined) document.getElementById("textSearchQuery").value = params.text_query;
          if (params.count) document.getElementById("textSearchCount").value = params.count;
          if (params.min_similarity !== undefined) document.getElementById("textSearchMinSimilarity").value = params.min_similarity;
          if (params.max_per_artist) document.getElementById("textSearchMaxPerArtist").value = params.max_per_artist;
          if (params.exclude_same_album !== undefined) {
            document.getElementById("textSearchExcludeSameAlbum").checked = params.exclude_same_album !== "false";
          }
          renderTextSearchRecent();
          if ((params.text_query || "").trim()) await runTextSearch({updateUrl: false});
        } else if (sectionId === "settings") {
          setSettingsTab(params.settings_tab || "embeddings");
        } else if (sectionId === "navidromeLikes") {
          if (params.filter) document.getElementById("likedFilter").value = params.filter;
          if (params.autoload === "1") await loadNavidromeLikes({updateUrl: false});
          renderLikedTracks();
          if (params.refresh === "1" && likedReadyTrackIds().length) {
            await refreshLikedRecommendations({updateUrl: false});
          }
        } else if (sectionId === "instantMix") {
          await loadInstantMixSettings();
          const requests = await loadInstantMixRequests({updateUrl: false});
          if (params.request) await loadInstantMixRequestDetail(params.request, {updateUrl: false});
          else if (requests.length) await loadInstantMixRequestDetail(requests[0].id, {updateUrl: true});
          else renderInstantMixEmptyDetail();
        } else if (sectionId === "evaluation") {
          if (params.index !== undefined && seedBasket.length) {
            const index = Math.max(0, Math.min(Number(params.index), seedBasket.length - 1));
            if (Number.isInteger(index)) await selectEvaluationSeed(index, {updateUrl: false});
          }
        } else if (sectionId === "lostFiles") {
          lostFilesPage = params.page ? Math.max(1, Number(params.page) || 1) : 1;
          await loadLostFiles({updateUrl: false});
        } else if (sectionId === "erroredFiles") {
          erroredFilesPage = params.page ? Math.max(1, Number(params.page) || 1) : 1;
          await loadErroredFiles({updateUrl: false});
        } else if (sectionId === "jobsPage") {
          await refreshJobs({history: true});
          if (params.job) await loadJobDetail(params.job, {updateUrl: false});
        } else if (sectionId === "workersPage") {
          await refreshWorkers();
        } else if (sectionId === "settings") {
          await loadAudioFeaturesSettings();
          await loadGeneratedMixSettings();
        }
      } finally {
        applyingRoute = false;
      }
    }
    function mergeRouteParams(params, {reset = false} = {}) {
      if (reset) return {view: params.view || "dashboard", ...params, model: params.model || model()};
      return {...paramsFromSearch(), ...params, model: params.model || model()};
    }
    function pushRouteOnly(params, {replace = false, reset = false} = {}) {
      const merged = mergeRouteParams(params, {reset});
      const search = buildRouteSearch(merged);
      const url = search ? `?${search}` : location.pathname;
      if (replace) history.replaceState({route: merged}, "", url);
      else history.pushState({route: merged}, "", url);
    }
    function syncModelRoute() {
      pushRouteOnly({model: model()}, {replace: true});
    }
    function routeTo(params, {replace = false, reset = false} = {}) {
      const merged = mergeRouteParams(params, {reset});
      const search = buildRouteSearch(merged);
      const url = search ? `?${search}` : location.pathname;
      if (replace) history.replaceState({route: merged}, "", url);
      else history.pushState({route: merged}, "", url);
      return applyRoute(merged);
    }
    function replaceRoute(params, options = {}) {
      return routeTo(params, {...options, replace: true});
    }
    function writeRoutePatch(patch, {replace = false} = {}) {
      return routeTo({...paramsFromSearch(), ...patch, model: patch.model || model()}, {replace});
    }
    function showSection(id) {
      const view = viewForSection(id);
      routeTo({view, model: model()}, {reset: true});
    }
    function syncRecommendationRoute() {
      if (paramsFromSearch().view !== "recommendations" || !seedId) return;
      pushRouteOnly({
        view: "recommendations",
        seed: String(seedId),
        model: model(),
        ...recommendationParams()
      }, {replace: true, reset: true});
    }
    function entityArtistsHtml(artists) {
      const items = artists || [];
      return items.map(artist => `<a class="entity-link" href="/artists/${artist.id}" onclick="event.preventDefault(); openArtist(${artist.id})">${esc(artist.name)}</a>`).join(", ") || "Unknown artist";
    }
    function entityDuration(seconds) {
      if (!seconds && seconds !== 0) return "";
      const total = Math.round(Number(seconds) || 0);
      const minutes = Math.floor(total / 60);
      const rest = String(total % 60).padStart(2, "0");
      return `${minutes}:${rest}`;
    }
    function mediaCard(item) {
      const type = item.entity_type || (item.release_type ? "release" : "artist");
      const id = item.entity_id || item.id;
      const title = item.title || item.name || `#${id}`;
      const subtitle = item.subtitle || entityArtistsHtml(item.artists || []);
      const artwork = item.artwork || item.image || {};
      const url = artwork.url || "";
      const target = item.action?.target || (type === "artist" ? `/artists/${id}` : (type === "generated_mix" ? `/mixes/${id}` : `/releases/${id}`));
      const coverClass = type === "artist" ? "media-card-cover artist-avatar" : "media-card-cover";
      const placeholder = type === "artist" ? esc((title || "?").slice(0, 1).toUpperCase()) : esc(type);
      const play = item.play_action
        ? `<button onclick="event.stopPropagation(); playCardAction('${encodedArg(JSON.stringify(item.play_action))}', '${encodedArg(title)}')" title="Play" aria-label="Play">&#9654;</button>`
        : "";
      return `<div class="media-card" role="button" tabindex="0" onclick="navigateEntityTarget('${encodedArg(target)}')" onkeydown="if(event.key === 'Enter') navigateEntityTarget('${encodedArg(target)}')">
        <div class="${coverClass}">
          ${url ? `<img src="${esc(url)}" loading="lazy" alt="" onerror="this.remove()">` : `<span>${placeholder}</span>`}
        </div>
        <div class="media-card-title">${esc(title)}</div>
        <div class="meta media-card-subtitle">${subtitle}</div>
        ${item.reason ? `<div class="meta media-card-reason">${esc(item.reason)}</div>` : ""}
        ${play ? `<div class="media-card-actions">${play}</div>` : ""}
      </div>`;
    }
    async function playCardAction(encodedAction, encodedLabel) {
      const action = JSON.parse(decodeURIComponent(encodedAction || "{}"));
      if (action.endpoint) {
        try {
          const data = await json(action.endpoint, {method: "POST"});
          activePlaybackSession = data.session;
          activePlaybackQueue = data.queue;
          lastAutoplayStatus = null;
          const firstItem = data.queue?.current_item || data.queue?.items?.[0];
          activeQueueItemId = firstItem?.id || null;
          renderPlayerState();
          if (firstItem?.track_id) await playTrack(Number(firstItem.track_id), encodedArg(firstItem.track?.title || data.session?.source_label || "Mix"), {queueItemId: activeQueueItemId});
          scheduleAutoplayRefill();
        } catch (err) {
          document.getElementById("playerError").textContent = `Playback session failed: ${err.message}`;
        }
        return;
      }
      return playSource(action.source_type, Number(action.source_id), encodedLabel);
    }
    function trackCoverCell(track) {
      const artwork = track.artwork || {};
      const url = artwork.url || `/tracks/${Number(track.id)}/cover`;
      const sizedUrl = `${url}${url.includes("?") ? "&" : "?"}size=96`;
      return `<div class="track-table-cover">
        <img src="${esc(sizedUrl)}" loading="lazy" alt="" onerror="this.remove()">
      </div>`;
    }
    function trackRow(track, {releaseContextId = null} = {}) {
      const artists = entityArtistsHtml(track.artists || []);
      const release = track.release || {};
      const releaseLink = release.id
        ? `<a class="entity-link" href="/releases/${release.id}" onclick="event.preventDefault(); openRelease(${release.id})">${esc(release.title || "")}</a>`
        : "";
      const playSource = releaseContextId
        ? `playSource('release', ${Number(releaseContextId)}, '${encodedArg(release.title || track.title || "Release")}', ${Number(track.id)})`
        : `playSource('track', ${Number(track.id)}, '${encodedArg(track.title || "Track")}')`;
      return `<tr>
        <td>${track.track_number || track.position || ""}</td>
        <td><button class="stat-icon-button" onclick="${playSource}" title="Play" aria-label="Play">&#9654;</button></td>
        <td class="search-track-cover">${trackCoverCell(track)}</td>
        <td><strong>${esc(track.title || `track #${track.id}`)}</strong><div class="meta">${artists}</div></td>
        <td>${releaseLink}</td>
        <td>${entityDuration(track.duration)}</td>
        <td>${trackMenuButton(track.id, true)}</td>
      </tr>`;
    }
    function trackTable(tracks, options = {}) {
      if (!tracks.length) return `<div class="meta">No tracks available.</div>`;
      return `<table class="track-table">
        <thead><tr><th>#</th><th></th><th class="search-track-cover"></th><th>Track</th><th>Release</th><th>Duration</th><th></th></tr></thead>
        <tbody>${tracks.map(track => trackRow(track, options)).join("")}</tbody>
      </table>`;
    }
    function openArtist(id) {
      return routeTo({view: "artist", artist_id: id}, {reset: true});
    }
    function openRelease(id) {
      return routeTo({view: "release", release_id: id}, {reset: true});
    }
    function openMix(id) {
      return routeTo({view: "mix", mix_id: id}, {reset: true});
    }
    function searchListenerForEncoded(encodedQuery) {
      return routeTo({view: "listenerSearch", q: decodeURIComponent(encodedQuery || "")}, {reset: true});
    }
    function runHomeSearch() {
      const query = document.getElementById("homeSearchQuery").value.trim();
      return routeTo({view: "listenerSearch", q: query}, {reset: true});
    }
    function navigateEntityTarget(encodedTarget) {
      const target = decodeURIComponent(encodedTarget || "");
      const artistMatch = target.match(/^\/artists\/(\d+)/);
      const releaseMatch = target.match(/^\/releases\/(\d+)/);
      const mixMatch = target.match(/^\/mixes\/([^/?#]+)/);
      if (artistMatch) return openArtist(Number(artistMatch[1]));
      if (releaseMatch) return openRelease(Number(releaseMatch[1]));
      if (mixMatch) return openMix(mixMatch[1]);
      if (target.includes("view=recommendations")) return routeTo({view: "recommendations"}, {reset: true});
      return routeTo({view: "listenerSearch"}, {reset: true});
    }
    function queueTrackLabel(item) {
      const track = item?.track || {};
      return track.title || `track #${item.track_id}`;
    }
    function queueTrackSubtitle(item) {
      const track = item?.track || {};
      return (track.artists || []).map(artist => artist.name).filter(Boolean).join(", ") || track.artist || "";
    }
    function queueTrackReleaseLink(item) {
      const track = item?.track || {};
      const release = track.release || {};
      const title = queueTrackLabel(item);
      const label = `<span class="queue-track-name">${esc(title)}</span>`;
      if (!release.id) return label;
      return `<a class="entity-link queue-track-link" href="/releases/${release.id}" onclick="event.preventDefault(); event.stopPropagation(); openRelease(${release.id})">${label}</a>`;
    }
    function queueTrackArtistLinks(item) {
      const artists = item?.track?.artists || [];
      const links = artists
        .filter(artist => artist?.id && artist?.name)
        .map(artist => `<a class="entity-link queue-artist-link" href="/artists/${artist.id}" onclick="event.preventDefault(); event.stopPropagation(); openArtist(${artist.id})">${esc(artist.name)}</a>`);
      if (links.length) return links.join(", ");
      return esc(queueTrackSubtitle(item) || item?.origin || "");
    }
    function sizedArtworkUrl(url, size) {
      if (!url) return "";
      const separator = url.includes("?") ? "&" : "?";
      return url.includes("size=")
        ? url.replace(/([?&])size=\d+/, `$1size=${size}`)
        : `${url}${separator}size=${size}`;
    }
    function queueTrackArtwork(item, size = 600) {
      const track = item?.track || {};
      const artwork = track.artwork || {};
      const url = artwork.url || `/tracks/${Number(item.track_id)}/cover`;
      return sizedArtworkUrl(url, size);
    }
    function queueTrackDuration(item) {
      const seconds = item?.track?.duration;
      return seconds || seconds === 0 ? entityDuration(seconds) : "";
    }
    function mixQueueItem(mixItem, mixId) {
      const track = mixItem.track || {};
      return {
        id: `${mixId}:${mixItem.position}:${mixItem.track_id}`,
        track_id: mixItem.track_id,
        track,
        origin: "generated_mix",
      };
    }
    function currentQueueItem() {
      const items = activePlaybackQueue?.items || [];
      return items.find(item => item.id === activeQueueItemId)
        || activePlaybackQueue?.current_item
        || items.find(item => item.track_id === activeTrackId)
        || null;
    }
    function renderQueueListItem(queueItem, options = {}) {
      const currentClass = queueItem.id === activeQueueItemId ? "current" : "";
      const preparedClass = options.prepared ? "prepared" : "";
      const click = options.onClick
        ? ` onclick="${options.onClick}"`
        : (options.prepared ? "" : ` onclick="jumpToQueueItem('${esc(queueItem.id)}')"`);
      const actionTrack = {...(queueItem.track || {}), id: queueItem.track?.id || queueItem.track_id};
      return `
        <div class="queue-item ${currentClass} ${preparedClass}"${click}>
          <div class="queue-item-cover"><img src="${esc(queueTrackArtwork(queueItem, 96))}" alt="" onerror="this.remove()"></div>
          <div>
            <div class="queue-item-title">${queueTrackReleaseLink(queueItem)}</div>
            <div class="queue-item-subtitle meta">${queueTrackArtistLinks(queueItem)}</div>
          </div>
          <div class="queue-item-duration">${esc(queueTrackDuration(queueItem))}</div>
          <div class="queue-item-actions" onclick="event.stopPropagation()">
            ${navidromeLikeButton(actionTrack, {compact: true})}
            ${trackMenuButton(queueItem.track_id, true)}
          </div>
        </div>
      `;
    }
    function renderAutoplayChipRow(activeChip) {
      const chips = ["All", "Familiar", "Recommended", "Party", "Energy", "Training"];
      return `<div class="chip-row">${chips.map(chip => (
        `<button class="${chip === activeChip ? "active" : ""}" data-autoplay-chip="${esc(chip)}" onclick="setAutoplayChip('${esc(chip)}')">${esc(chip)}</button>`
      )).join("")}</div>`;
    }
    function renderPlayerState() {
      const item = currentQueueItem();
      const title = item ? queueTrackLabel(item) : (activeTrackId ? `Track #${activeTrackId}` : "No track loaded");
      const subtitle = item ? queueTrackSubtitle(item) : "Persistent player is ready.";
      const artwork = item ? queueTrackArtwork(item, 600) : "";
      document.getElementById("nowPlaying").innerHTML = item ? queueTrackReleaseLink(item) : esc(title);
      document.getElementById("nowPlayingMeta").innerHTML = item ? queueTrackArtistLinks(item) : esc(subtitle);
      document.getElementById("expandedPlayerTitle").textContent = title;
      document.getElementById("expandedPlayerSubtitle").textContent = subtitle;
      document.getElementById("queueSourceLabel").textContent = activePlaybackSession?.source_label || activePlaybackSession?.source_type || "No active playback session";
      const playerTrackMenuButton = document.getElementById("playerTrackMenuButton");
      if (playerTrackMenuButton) {
        const hasTrack = Boolean(item?.track_id || activeTrackId);
        playerTrackMenuButton.disabled = !hasTrack;
        playerTrackMenuButton.title = hasTrack ? "Track menu" : "Track menu unavailable";
        playerTrackMenuButton.setAttribute("aria-label", playerTrackMenuButton.title);
      }
      const playerLikeButton = document.getElementById("playerNavidromeLikeButton");
      if (playerLikeButton) {
        const trackId = item?.track_id || activeTrackId;
        const navidromeKnownMissing = Boolean(item?.track) && !item.track.navidrome_item_id;
        const available = Boolean(trackId) && !navidromeKnownMissing;
        playerLikeButton.dataset.trackId = trackId || "";
        playerLikeButton.dataset.navidromeUnavailable = available ? "" : "1";
        playerLikeButton.disabled = !available;
        playerLikeButton.classList.toggle("like-active", false);
        playerLikeButton.innerHTML = bootstrapLikeIcon(false);
        playerLikeButton.title = available ? "Like in Navidrome" : "Navidrome like unavailable";
        playerLikeButton.setAttribute("aria-label", playerLikeButton.title);
        if (available) scheduleNavidromeLikeIdsRefresh();
      }
      const coverHtml = artwork ? `<img src="${esc(artwork)}" alt="" onerror="this.remove()">` : "ART";
      document.getElementById("playerCover").innerHTML = coverHtml;
      document.getElementById("expandedPlayerArt").innerHTML = coverHtml;
      const items = activePlaybackQueue?.items || [];
      const poolItems = activePlaybackQueue?.autoplay_pool || [];
      const autoplayEnabled = activePlaybackSession?.autoplay_enabled !== false;
      const autoplayToggle = document.getElementById("autoplayToggle");
      if (autoplayToggle) autoplayToggle.classList.toggle("active", autoplayEnabled);
      const chip = activePlaybackSession?.settings?.autoplay_preference_chip || "All";
      const queueHtml = items.map(queueItem => renderQueueListItem(queueItem)).join("") || `<div class="meta">Queue is empty.</div>`;
      const poolHtml = poolItems.map(queueItem => renderQueueListItem(queueItem, {prepared: true})).join("") || `<div class="meta">Prepared autoplay pool is empty.</div>`;
      document.getElementById("playerQueueList").innerHTML = `
        ${queueHtml}
        <div class="autoplay-pool-section">
          <div class="autoplay-pool-header meta">Autoplay enabled</div>
          ${renderAutoplayChipRow(chip)}
          <div class="autoplay-pool-list">${poolHtml}</div>
        </div>
      `;
      const generatedCount = (activePlaybackQueue?.generated_items || []).filter(item => item.origin === "autoplay").length;
      const poolCount = poolItems.length;
      document.getElementById("autoplayStatus").textContent = autoplayEnabled
        ? (lastAutoplayStatus || `Autoplay ready - ${generatedCount} in queue, ${poolCount} prepared.`)
        : "Autoplay is off for this session.";
      renderPlaybackButtons();
    }
    function toggleExpandedPlayer() {
      document.getElementById("expandedPlayer").classList.toggle("open");
      renderPlayerState();
    }
    function updatePlayerClock() {
      const player = document.getElementById("audioPlayer");
      const seek = document.getElementById("playerSeek");
      const bubble = document.getElementById("playerSeekBubble");
      const duration = Number.isFinite(player.duration) && player.duration > 0 ? player.duration : 0;
      const current = Number.isFinite(player.currentTime) ? player.currentTime : 0;
      const percent = duration ? Math.max(0, Math.min(100, (current / duration) * 100)) : 0;
      seek.value = String(Math.round(percent * 10));
      seek.style.setProperty("--seek-progress", `${percent}%`);
      bubble.textContent = entityDuration(current);
      bubble.style.left = `${percent}%`;
      document.getElementById("playerElapsed").textContent = entityDuration(player.currentTime || 0);
      document.getElementById("playerDuration").textContent = Number.isFinite(player.duration) ? entityDuration(player.duration) : "0:00";
    }
    function seekPlayerToRangeValue(value) {
      const player = document.getElementById("audioPlayer");
      if (!Number.isFinite(player.duration) || player.duration <= 0) return;
      const fraction = Math.max(0, Math.min(1, Number(value || 0) / 1000));
      player.currentTime = player.duration * fraction;
      updatePlayerClock();
    }
    let autoplayRefillInFlight = null;
    function reportBackgroundError(label, err) {
      const target = document.getElementById("playerError");
      if (target) target.textContent = `${label} failed: ${err.message}`;
    }
    function runPlayerBackground(label, task) {
      Promise.resolve()
        .then(task)
        .catch(err => reportBackgroundError(label, err));
    }
    function scheduleAutoplayRefill() {
      if (!activePlaybackSession?.id || activePlaybackSession.autoplay_enabled === false) return;
      if (autoplayRefillInFlight) return;
      autoplayRefillInFlight = refillAutoplay()
        .catch(err => reportBackgroundError("Autoplay refill", err))
        .finally(() => {
          autoplayRefillInFlight = null;
        });
    }
    function refreshPlaybackSurfaces() {
      runPlayerBackground("Library refresh", () => searchTracks({updateUrl: false}));
      if (seedId) runPlayerBackground("Similar refresh", () => loadSimilar(seedId));
    }
    async function recordPlaybackEvent(eventType, extra = {}) {
      if (!activeTrackId && !extra.track_id) return;
      try {
        await json("/api/v1/playback/events", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            session_id: activePlaybackSession?.id || null,
            queue_item_id: activeQueueItemId,
            track_id: activeTrackId,
            event_type: eventType,
            source: "web",
            ...extra,
          }),
        });
        if (["completed", "skipped", "liked", "disliked", "replayed", "removed_from_queue"].includes(eventType)) {
          scheduleAutoplayRefill();
        }
      } catch (err) {
        document.getElementById("playerError").textContent = `Playback event failed: ${err.message}`;
      }
    }
    function autoplaySettingsPayload() {
      return {
        autoplay_visible_buffer: Number(document.getElementById("autoplayVisibleBuffer")?.value || 5),
        autoplay_candidate_count: Number(document.getElementById("autoplayCandidateCount")?.value || 200),
        autoplay_preference_chip: activePlaybackSession?.settings?.autoplay_preference_chip || "All",
      };
    }
    function autoplayStatusFromRefill(result) {
      const added = result?.added_items?.length || 0;
      const debug = result?.debug || {};
      const prepared = debug.pool_after || 0;
      if (added > 0) return `Autoplay added ${added} track${added === 1 ? "" : "s"}; ${prepared} prepared.`;
      if (debug.needed === 0) return `Autoplay ready - queue buffer is full; ${prepared} prepared.`;
      if (debug.empty_reason === "no_seed_embeddings") {
        return `Autoplay needs ${debug.seed_track_ids?.length ? "usable" : "source"} embeddings for this source.`;
      }
      if (debug.candidate_count === 0) return "Autoplay found no eligible candidates for this source.";
      if (debug.needed > 0) return `Autoplay tried to fill ${debug.needed} slot${debug.needed === 1 ? "" : "s"} but added none.`;
      return "Autoplay ready.";
    }
    async function refillAutoplay() {
      if (!activePlaybackSession?.id || activePlaybackSession.autoplay_enabled === false) return;
      try {
        const settings = autoplaySettingsPayload();
        const result = await json("/api/v1/autoplay/refill?include_debug=true", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            session_id: activePlaybackSession.id,
            visible_buffer: settings.autoplay_visible_buffer,
            candidate_count: settings.autoplay_candidate_count,
            settings,
          }),
        });
        lastAutoplayStatus = autoplayStatusFromRefill(result);
        await refreshPlaybackQueue();
      } catch (err) {
        lastAutoplayStatus = null;
        document.getElementById("autoplayStatus").textContent = `Autoplay refill failed: ${err.message}`;
      }
    }
    async function refreshPlaybackQueue() {
      if (!activePlaybackSession?.id) return;
      const data = await json(`/api/v1/playback/sessions/${activePlaybackSession.id}/queue`);
      activePlaybackSession = data.session;
      activePlaybackQueue = data.queue;
      if (!activeQueueItemId && data.queue?.current_item?.id) {
        activeQueueItemId = data.queue.current_item.id;
      }
      renderPlayerState();
    }
    async function toggleAutoplay() {
      if (!activePlaybackSession?.id) return;
      const enabled = activePlaybackSession.autoplay_enabled === false;
      try {
        const data = await json(`/api/v1/playback/sessions/${activePlaybackSession.id}`, {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({autoplay_enabled: enabled}),
        });
        lastAutoplayStatus = null;
        activePlaybackSession = data.session;
        activePlaybackQueue = data.queue;
        renderPlayerState();
        if (enabled) await refillAutoplay();
      } catch (err) {
        document.getElementById("playerError").textContent = `Autoplay toggle failed: ${err.message}`;
      }
    }
    async function setAutoplayChip(chip) {
      if (!activePlaybackSession?.id) return;
      const settings = {...(activePlaybackSession.settings || {}), autoplay_preference_chip: chip};
      try {
        const data = await json(`/api/v1/playback/sessions/${activePlaybackSession.id}`, {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({settings}),
        });
        lastAutoplayStatus = null;
        activePlaybackSession = data.session;
        activePlaybackQueue = data.queue;
        renderPlayerState();
        await refillAutoplay();
      } catch (err) {
        document.getElementById("playerError").textContent = `Autoplay preference failed: ${err.message}`;
      }
    }
    async function jumpToQueueItem(queueItemId) {
      if (!activePlaybackSession?.id) return;
      try {
        const data = await json(`/api/v1/playback/sessions/${activePlaybackSession.id}/queue`, {
          method: "PATCH",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({operation: "jump", queue_item_id: queueItemId}),
        });
        activePlaybackSession = data.session;
        activePlaybackQueue = data.queue;
        activeQueueItemId = queueItemId;
        const item = currentQueueItem();
        if (item) await playTrack(Number(item.track_id), encodedArg(queueTrackLabel(item)), {queueItemId: item.id, recordStarted: false});
      } catch (err) {
        document.getElementById("playerError").textContent = `Queue jump failed: ${err.message}`;
      }
    }
    async function playQueueOffset(offset) {
      const items = activePlaybackQueue?.items || [];
      const index = items.findIndex(item => item.id === activeQueueItemId);
      const item = items[index + offset];
      if (item) await jumpToQueueItem(item.id);
    }
    async function playRandomQueueItem() {
      const items = (activePlaybackQueue?.items || []).filter(item => item.id !== activeQueueItemId);
      if (!items.length) return false;
      const item = items[Math.floor(Math.random() * items.length)];
      await jumpToQueueItem(item.id);
      return true;
    }
    function playPreviousQueueItem() {
      return playQueueOffset(-1);
    }
    function toggleAudioPlayback() {
      const player = document.getElementById("audioPlayer");
      if (!player.src) return;
      if (player.paused) {
        player.play()
          .then(renderPlaybackButtons)
          .catch(async err => {
            document.getElementById("playerError").textContent = await playbackErrorMessage(err);
            renderPlaybackButtons();
          });
      } else {
        player.pause();
        renderPlaybackButtons();
      }
    }
    async function playbackErrorMessage(err) {
      const player = document.getElementById("audioPlayer");
      const mediaError = player?.error;
      const code = mediaError?.code ? ` media=${mediaError.code}` : "";
      const detail = [err?.name, err?.message].filter(Boolean).join(": ");
      const base = detail
        ? `Playback failed: ${detail}${code}`
        : (code ? `Playback failed:${code}` : "Click play in the audio controls if autoplay is blocked.");
      const debug = await audioSourceDebug(player);
      return debug ? `${base} | ${debug}` : base;
    }
    async function audioSourceDebug(player) {
      if (!player) return "";
      const src = player.currentSrc || player.src || "";
      const parts = [
        `build=${UI_BUILD_ID}`,
        `rs=${player.readyState}`,
        `ns=${player.networkState}`,
        `flac=${player.canPlayType ? (player.canPlayType("audio/flac") || "no") : "na"}`,
        `mpeg=${player.canPlayType ? (player.canPlayType("audio/mpeg") || "no") : "na"}`,
        `src=${src || "none"}`,
      ];
      if (src) {
        try {
          const response = await fetch(src, {method: "HEAD", headers: {"Range": "bytes=0-63"}});
          parts.push(`head=${response.status}`);
          parts.push(`type=${response.headers.get("content-type") || "none"}`);
          parts.push(`range=${response.headers.get("content-range") || "none"}`);
          parts.push(`len=${response.headers.get("content-length") || "none"}`);
        } catch (debugErr) {
          parts.push(`head_error=${debugErr?.message || debugErr}`);
        }
      }
      return parts.join(" ");
    }
    async function skipCurrentTrack() {
      const player = document.getElementById("audioPlayer");
      recordPlaybackEvent("skipped", {
        position_seconds: Number.isFinite(player.currentTime) ? player.currentTime : null,
        duration_seconds: Number.isFinite(player.duration) ? player.duration : null,
      });
      await playQueueOffset(1);
    }
    function recordCurrentPreference(eventType) {
      return recordPlaybackEvent(eventType);
    }
    async function playSource(sourceType, sourceId, encodedLabel, preferredTrackId = null) {
      try {
        const data = await json("/api/v1/playback/sessions", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            source_type: sourceType,
            source_id: Number(sourceId),
            source_label: decodeURIComponent(encodedLabel || ""),
            mode: playerState.shuffle ? "shuffle" : "linear",
            shuffle_enabled: playerState.shuffle,
            repeat_mode: playerState.repeatOne ? "one" : "off"
          })
        });
        activePlaybackSession = data.session;
        activePlaybackQueue = data.queue;
        lastAutoplayStatus = null;
        const preferredItem = (data.queue?.items || []).find(item => Number(item.track_id) === Number(preferredTrackId));
        const firstItem = preferredItem || data.queue?.current_item || data.queue?.items?.[0];
        activeQueueItemId = firstItem?.id || null;
        const first = firstItem?.track_id || preferredTrackId;
        const labelText = decodeURIComponent(encodedLabel || "") || data.session?.source_label || `${sourceType} #${sourceId}`;
        renderPlayerState();
        if (first) await playTrack(Number(first), encodedArg(preferredItem ? queueTrackLabel(preferredItem) : labelText), {queueItemId: activeQueueItemId});
        scheduleAutoplayRefill();
      } catch (err) {
        document.getElementById("playerError").textContent = `Playback session failed: ${err.message}`;
      }
    }
    function setListenerSearchTab(tab) {
      document.querySelectorAll("#listenerSearchTabs button").forEach(button => {
        button.classList.toggle("active", button.dataset.searchTab === tab);
      });
      renderListenerSearchResults(lastListenerSearchResults, tab);
    }
    let lastListenerSearchResults = null;
    async function runListenerSearch({updateUrl = true} = {}) {
      const query = document.getElementById("listenerSearchQuery").value.trim();
      if (updateUrl && !applyingRoute) pushRouteOnly({view: "listenerSearch", q: query}, {replace: true, reset: true});
      const target = document.getElementById("listenerSearchResults");
      if (!query) {
        lastListenerSearchResults = null;
        target.innerHTML = `<div class="meta">Search artists, tracks, and releases.</div>`;
        return;
      }
      target.innerHTML = `<div class="meta">Searching...</div>`;
      try {
        lastListenerSearchResults = await json(`/api/v1/search?q=${encodeURIComponent(query)}&limit=12`);
        renderListenerSearchResults(lastListenerSearchResults, activeListenerSearchTab());
      } catch (err) {
        target.innerHTML = `<div class="error">Search failed: ${esc(err.message)}</div>`;
      }
    }
    function activeListenerSearchTab() {
      return document.querySelector("#listenerSearchTabs button.active")?.dataset.searchTab || "all";
    }
    function renderListenerSearchResults(data, tab = "all") {
      const target = document.getElementById("listenerSearchResults");
      if (!data) return;
      const groups = Object.fromEntries((data.groups || []).map(group => [group.type, group]));
      const sections = [];
      if (tab === "all" && data.top_result) {
        const entity = data.top_result.entity;
        const label = data.top_result.entity_type;
        const artwork = entity.image || entity.artwork || {};
        const imageUrl = artwork.url || "";
        const title = entity.name || entity.title || label;
        const initial = esc((title || "?").slice(0, 1).toUpperCase());
        sections.push(`<div class="top-result">
          <div class="top-result-avatar">
            ${imageUrl ? `<img src="${esc(imageUrl)}" loading="lazy" alt="" onerror="this.remove()">` : `<span>${initial}</span>`}
          </div>
          <div class="top-result-body">
            <div class="meta">Top result</div>
            <h2>${esc(title)}</h2>
            <div class="actions">
              <button onclick="${label === "artist" ? `openArtist(${entity.id})` : label === "release" ? `openRelease(${entity.id})` : `playSource('track', ${entity.id}, '${encodedArg(entity.title || "Track")}')`}">Open</button>
            </div>
          </div>
        </div>`);
      }
      const wanted = tab === "all" ? ["artists", "tracks", "releases"] : [tab];
      wanted.forEach(key => {
        const group = groups[key];
        if (!group) return;
        if (key === "tracks") {
          sections.push(`<div class="shelf"><div class="shelf-head"><h2>Tracks</h2><span class="meta">${group.total} found</span></div>${trackTable(group.items || [])}</div>`);
        } else {
          sections.push(`<div class="shelf"><div class="shelf-head"><h2>${esc(group.title)}</h2><span class="meta">${group.total} found</span></div><div class="shelf-row">${(group.items || []).map(item => mediaCard({...item, entity_type: key === "artists" ? "artist" : "release", entity_id: item.id})).join("") || `<div class="meta">No ${esc(group.title.toLowerCase())}.</div>`}</div></div>`);
        }
      });
      target.innerHTML = sections.join("") || `<div class="meta">No results.</div>`;
    }
    async function loadArtistSurface(artistId) {
      const target = document.getElementById("artistSurfaceContent");
      if (!artistId) {
        target.innerHTML = `<div class="error">Artist id is missing.</div>`;
        return;
      }
      target.innerHTML = `<div class="meta">Loading artist...</div>`;
      try {
        const [artistData, discography, topTracks] = await Promise.all([
          json(`/api/v1/artists/${artistId}`),
          json(`/api/v1/artists/${artistId}/discography`),
          json(`/api/v1/artists/${artistId}/top-tracks`)
        ]);
        const artist = artistData.artist;
        const stats = artist.library_stats || {};
        const groups = (discography.groups || []).filter(group => (group.items || []).length);
        target.innerHTML = `
          <div class="surface-header">
            <div class="surface-art artist-art">${artist.image?.url ? `<img src="${esc(artist.image.url)}" alt="">` : esc((artist.name || "?").slice(0, 1))}</div>
            <div>
              <div class="meta">Artist</div>
              <h2 class="surface-title">${esc(artist.name)}</h2>
              <div class="meta">${stats.tracks || 0} tracks - ${stats.releases || 0} releases in library</div>
              <div class="actions" style="margin-top:12px">
                <button onclick="playSource('artist', ${Number(artist.id)}, '${encodedArg(artist.name)}')">Play artist</button>
                <button onclick="searchListenerForEncoded('${encodedArg(artist.name)}')">Search artist</button>
              </div>
            </div>
          </div>
          ${topTracks.available && (topTracks.items || []).length ? `<div class="shelf"><h2>Top Tracks</h2>${trackTable(topTracks.items)}</div>` : ""}
          <div class="entity-tabs"><button class="active">Discography</button><button disabled>Top Tracks</button><button disabled>Similar Artists</button></div>
          ${groups.map(group => `<div class="shelf"><div class="shelf-head"><h2>${esc(group.title)}</h2><span class="meta">${group.items.length}</span></div><div class="shelf-row">${group.items.map(item => mediaCard({...item, entity_type:"release", entity_id:item.id})).join("")}</div></div>`).join("") || `<div class="meta">No discography found for this artist.</div>`}
        `;
      } catch (err) {
        target.innerHTML = `<div class="error">Artist failed to load: ${esc(err.message)}</div>`;
      }
    }
    async function loadReleaseSurface(releaseId) {
      const target = document.getElementById("releaseSurfaceContent");
      if (!releaseId) {
        target.innerHTML = `<div class="error">Release id is missing.</div>`;
        return;
      }
      target.innerHTML = `<div class="meta">Loading release...</div>`;
      try {
        const [releaseData, tracksData, relatedData, recommendationsData] = await Promise.all([
          json(`/api/v1/releases/${releaseId}`),
          json(`/api/v1/releases/${releaseId}/tracks`),
          json(`/api/v1/releases/${releaseId}/related-discography`),
          json(`/api/v1/releases/${releaseId}/recommendations`)
        ]);
        const release = releaseData.release;
        const artwork = release.artwork || {};
        const tracks = tracksData.items || [];
        const related = relatedData.items || [];
        const recommendations = recommendationsData.items || [];
        target.innerHTML = `
          <div class="surface-header">
            <div class="surface-art">${artwork.url ? `<img src="${esc(artwork.url)}" alt="">` : "ART"}</div>
            <div>
              <div class="meta">${esc(release.release_type_label || "Release")}</div>
              <h2 class="surface-title">${esc(release.title)}</h2>
              <div class="surface-subtitle">${entityArtistsHtml(release.artists || [])}</div>
              <div class="meta">${release.track_count || tracks.length} tracks${release.release_year ? ` - ${release.release_year}` : ""}${release.duration ? ` - ${entityDuration(release.duration)}` : ""}</div>
              <div class="actions" style="margin-top:12px">
                <button class="primary" onclick="playSource('release', ${Number(release.id)}, '${encodedArg(release.title)}')">Play release</button>
                <button onclick="searchListenerForEncoded('${encodedArg(release.title)}')">Search title</button>
              </div>
            </div>
          </div>
          <div class="shelf"><h2>Tracks</h2>${trackTable(tracks, {releaseContextId: release.id})}</div>
          ${related.length ? `<div class="shelf"><div class="shelf-head"><h2>Related Discography</h2><span class="meta">${related.length}</span></div><div class="shelf-row">${related.map(item => mediaCard({...item, entity_type:"release", entity_id:item.id})).join("")}</div></div>` : ""}
          ${recommendations.length ? `<div class="shelf"><h2>Recommended Releases</h2><div class="shelf-row">${recommendations.map(item => mediaCard({...item, entity_type:"release", entity_id:item.id})).join("")}</div></div>` : ""}
        `;
      } catch (err) {
        target.innerHTML = `<div class="error">Release failed to load: ${esc(err.message)}</div>`;
      }
    }
    async function saveMix(mixId) {
      mixId = decodeURIComponent(String(mixId || ""));
      try {
        await json(`/api/v1/mixes/${encodeURIComponent(mixId)}/save`, {method: "POST"});
        const status = document.getElementById("mixSurfaceStatus");
        if (status) status.textContent = "Saved";
      } catch (err) {
        const status = document.getElementById("mixSurfaceStatus");
        if (status) status.textContent = `Save failed: ${err.message}`;
      }
    }
    async function playMix(mixId, preferredTrackId = null) {
      mixId = decodeURIComponent(String(mixId || ""));
      try {
        const data = await json(`/api/v1/mixes/${encodeURIComponent(mixId)}/play`, {method: "POST"});
        activePlaybackSession = data.session;
        activePlaybackQueue = data.queue;
        lastAutoplayStatus = null;
        const preferredItem = preferredTrackId
          ? (data.queue?.items || []).find(item => Number(item.track_id) === Number(preferredTrackId))
          : null;
        const firstItem = preferredItem || data.queue?.current_item || data.queue?.items?.[0];
        activeQueueItemId = firstItem?.id || null;
        renderPlayerState();
        if (firstItem?.track_id) {
          await playTrack(Number(firstItem.track_id), encodedArg(queueTrackLabel(firstItem)), {queueItemId: activeQueueItemId});
        }
        scheduleAutoplayRefill();
      } catch (err) {
        document.getElementById("playerError").textContent = `Playback session failed: ${err.message}`;
      }
    }
    function renderMixTrack(mixItem, mixId) {
      const queueItem = mixQueueItem(mixItem, mixId);
      return renderQueueListItem(queueItem, {
        onClick: `playMix('${encodedArg(mixId)}', ${Number(mixItem.track_id)})`,
      });
    }
    async function loadMixSurface(mixId) {
      mixId = decodeURIComponent(String(mixId || ""));
      const target = document.getElementById("mixSurfaceContent");
      if (!mixId) {
        target.innerHTML = `<div class="error">Mix id is missing.</div>`;
        return;
      }
      target.innerHTML = `<div class="meta">Loading mix...</div>`;
      try {
        const mix = await json(`/api/v1/mixes/${encodeURIComponent(mixId)}`);
        const items = mix.items || [];
        const tracks = items.filter(item => item.track);
        const artwork = mix.artwork || {};
        const firstItemArtwork = tracks.length ? queueTrackArtwork(mixQueueItem(tracks[0], mix.id), 600) : "";
        const artworkUrl = artwork.url ? sizedArtworkUrl(artwork.url, 600) : firstItemArtwork;
        const totalDuration = tracks.reduce((sum, item) => sum + (Number(item.track?.duration) || 0), 0);
        const representative = [mix.anchor?.representative_artist, mix.anchor?.representative_album].filter(Boolean).join(" - ");
        const description = representative || mix.subtitle || "";
        target.innerHTML = `
          <div class="mix-page">
            <div class="mix-hero">
              <div class="mix-art">${artworkUrl ? `<img src="${esc(artworkUrl)}" alt="" onerror="this.remove()">` : "MIX"}</div>
              <div>
                <div class="meta">${esc(mix.mix_type || "Generated mix")}</div>
                <h2 class="mix-title">${esc(mix.title || "Mix")}</h2>
              </div>
              <div class="meta">${items.length} tracks${totalDuration ? ` - ${entityDuration(totalDuration)}` : ""}</div>
              ${description ? `<div class="meta mix-description">${esc(description)}</div>` : ""}
              <div class="mix-actions">
                <button onclick="saveMix('${encodedArg(mix.id)}')" title="Save" aria-label="Save"><i class="bi bi-bookmark" aria-hidden="true"></i></button>
                <button class="primary mix-play-button" onclick="playMix('${encodedArg(mix.id)}')" title="Play" aria-label="Play"><i class="bi bi-play-fill" aria-hidden="true"></i></button>
                <button onclick="loadMixSurface('${encodedArg(mix.id)}')" title="Refresh" aria-label="Refresh"><i class="bi bi-arrow-clockwise" aria-hidden="true"></i></button>
              </div>
              <div class="meta" id="mixSurfaceStatus">${esc(mix.status || "")}</div>
            </div>
            <div class="mix-track-list">
              ${tracks.map(item => renderMixTrack(item, mix.id)).join("") || `<div class="meta">No tracks available.</div>`}
            </div>
          </div>
        `;
      } catch (err) {
        target.innerHTML = `<div class="error">Mix failed to load: ${esc(err.message)}</div>`;
      }
    }
    async function loadListenerDashboard() {
      const target = document.getElementById("listenerDashboardShelves");
      if (!target) return;
      try {
        const data = await json("/api/v1/dashboard?limit=8");
        const shelves = (data.shelves || []).filter(shelf => shelf.available);
        target.innerHTML = shelves.map(shelf => `
          <div class="shelf">
            <div class="shelf-head">
              <div><h2>${esc(shelf.title)}</h2><div class="meta">${esc(shelf.subtitle || "")}</div></div>
              <span class="meta">${shelf.total} items</span>
            </div>
            <div class="shelf-row">${(shelf.items || []).map(mediaCard).join("") || `<div class="meta">No items yet.</div>`}</div>
          </div>`).join("") || `<div class="meta">Run a scan or play tracks to fill listener shelves.</div>`;
      } catch (err) {
        target.innerHTML = `<div class="error">Dashboard shelves unavailable: ${esc(err.message)}</div>`;
      }
    }
    async function refreshStats() {
      if (statsInFlight) return;
      statsInFlight = true;
      const listenerDashboardPromise = loadListenerDashboard();
      try {
        const data = await json(`/stats?model=${encodeURIComponent(model())}`, {timeoutMs: 30000});
        renderDashboardCards(data);
        renderModelCards(data);
        const indexStatus = data.index_status || (data.index_exists ? "ready" : "missing");
        const indexCount = data.index_count ?? "?";
        const embeddingCount = data.index_embedding_count ?? data.embeddings;
        document.getElementById("modelState").textContent =
          `Model file: ${data.model_exists ? "ready" : "missing"} · ${data.model_path} · ` +
          `Index: ${indexStatus} (${indexCount}/${embeddingCount}) · ${data.index}`;
        renderHeadPackStatus(data.head_pack);
      } catch (err) {
        document.getElementById("modelState").textContent = `Stats unavailable: ${err.message}`;
      } finally {
        await listenerDashboardPromise.catch(() => {});
        statsInFlight = false;
      }
    }
    function countText(value) {
      const numeric = Number(value || 0);
      return Number.isFinite(numeric) ? numeric.toLocaleString() : text(value);
    }
    function statusPill(status) {
      const value = status || "missing";
      return `<span class="pill status-${text(value)}">${text(value)}</span>`;
    }
    function statCard(title, value, subtitle, detailsHtml = "", actionHtml = "") {
      return `<div class="stat">
        <h3>${text(title)}</h3>
        <div class="stat-count"><strong>${countText(value)}</strong><span>${text(subtitle)}</span></div>
        <div class="stat-lines">${detailsHtml}</div>
        <div class="stat-actions">${actionHtml}</div>
      </div>`;
    }
    function modelStatCard(className, title, value, subtitle, detailsHtml = "", actionHtml = "") {
      return `<div class="stat model-card${className}">
        <h3>${text(title)}</h3>
        <div class="stat-count"><strong>${countText(value)}</strong><span>${text(subtitle)}</span></div>
        <div class="stat-lines">${detailsHtml}</div>
        <div class="stat-actions">${actionHtml}</div>
      </div>`;
    }
    function detailLine(value) {
      return `<div class="meta">${value}</div>`;
    }
    function rebuildIndexButton(modelName) {
      return `<button class="dashboard-index-btn stat-icon-button" data-model="${text(modelName)}" onclick="startIndex('${text(modelName)}')" title="Rebuild index" aria-label="Rebuild index">&#8635;</button>`;
    }
    function jobRunning(kind) {
      return (lastJobs || []).some(job => job.kind === kind && ["queued", "running"].includes(job.status));
    }
    function disabledAttr(kind) {
      return "";
    }
    function indexStatusText(status) {
      const value = status || "missing";
      return `<span class="index-status-${text(value)}">${text(value)}</span>`;
    }
    function renderDashboardCards(data) {
      const audio = data.audio_features || {};
      const headPack = data.head_pack || {};
      const cards = [
        statCard("Tracks", data.tracks, "total tracks", detailLine("Navidrome catalog"), `<button id="navidromeSyncBtn" onclick="startNavidromeSync()"${disabledAttr("navidrome-sync")}>Sync Navidrome</button>`),
        statCard("Audio features", audio.missing_tracks ?? data.audio_features_missing_tracks, "need audio features", detailLine(`${countText(audio.complete_tracks || 0)} ready / ${countText(data.tracks)} total`), `<button id="analyzeAudioFeaturesBtn" onclick="startAnalyzeAudioFeatures()" title="Analyze audio features"${disabledAttr("analyze-audio-features")}>Analyze missing</button>`),
        statCard("Discogs-EffNet heads", headPack.missing_tracks ?? data.head_pack_missing_tracks, "need head outputs", detailLine(`${countText(headPack.complete_tracks || 0)} ready / ${countText(data.tracks)} total`), `<button id="analyzeHeadsBtn" onclick="startAnalyzeHeads()" title="Analyze Discogs-EffNet heads"${disabledAttr("analyze-heads")}>Analyze missing</button>`),
        statCard("Lost files", data.missing_files, "missing on disk", detailLine("Check unavailable paths"), `<button onclick="routeTo({view: 'lostFiles', model: model()}, {reset: true})">Open</button>`),
        statCard("Errored files", data.analysis_error_count || 0, "analysis errors", detailLine("Latest failed track/model pairs"), `<button onclick="routeTo({view: 'erroredFiles', model: model()}, {reset: true})">Open</button>`)
      ];
      document.getElementById("dashboardCards").innerHTML = cards.join("");
    }
    function renderModelCards(data) {
      const selected = model();
      const total = data.tracks || 0;
      const cards = (data.model_stats || []).map(item => {
        const indexStatus = item.index_status || (item.index_exists ? "ready" : "missing");
        const indexCount = item.index_count ?? "?";
        const indexEmbeddingCount = item.index_embedding_count ?? item.embeddings;
        const selectedClass = item.model === selected ? " selected" : "";
        const details = [
          detailLine(`${countText(item.embeddings)} ready / ${countText(total)} total`),
          `<div class="meta stat-index-row">index ${indexStatusText(indexStatus)} ${countText(indexCount)} / ${countText(indexEmbeddingCount)} ${rebuildIndexButton(item.model)}</div>`
        ].join("");
        return modelStatCard(
          selectedClass,
          item.model,
          item.missing_embeddings,
          "need embeddings",
          details,
          `<button class="dashboard-analyze-btn" data-model="${text(item.model)}" onclick="startAnalyze('${text(item.model)}')"${disabledAttr("analyze")}>Analyze missing</button>`
        );
      }).join("");
      document.getElementById("modelCards").innerHTML = cards || `<div class="meta">No models configured.</div>`;
    }
    function renderHeadPackStatus(pack) {
      const modelFiles = pack.model_files || [];
      const ready = modelFiles.filter(file => file.ready).length;
      const required = modelFiles.length;
      const heads = pack.models ? pack.models.length : 0;
      document.getElementById("headPackSummary").textContent =
        `Model files: ${ready}/${required} loaded`;
      document.getElementById("headPackReadiness").textContent =
        `Loaded files ${ready}/${required}; heads ${heads}`;
      const rows = modelFiles.map(file => `
        <tr>
          <td><span class="pill ${file.ready ? "" : "bad-pill"}">${file.ready ? "loaded" : "missing"}</span></td>
          <td>${file.model || ""}</td>
          <td>${file.kind || ""}</td>
          <td class="path" title="${file.path || ""}">${file.filename || ""}</td>
        </tr>`).join("");
      document.getElementById("headPackModelTable").innerHTML = `
        <table class="model-table">
          <thead><tr><th>Status</th><th>Model</th><th>Kind</th><th>File</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }
    function setNavidromeStatus(message, isError = false) {
      const target = document.getElementById("navidromeStatus");
      if (!target) return;
      target.textContent = message;
      target.classList.toggle("error", isError);
    }
    function audioFeaturesExtractor() {
      return document.getElementById("audioFeaturesExtractor").value;
    }
    async function loadAudioFeaturesSettings() {
      const statusTarget = document.getElementById("audioFeaturesStatus");
      if (!statusTarget) return;
      try {
        const data = await json(`/stats?model=${encodeURIComponent(model())}`, {timeoutMs: 30000});
        const audio = data.audio_features || {};
        const extractor = audio.extractor || audioFeaturesExtractor();
        statusTarget.textContent =
          `${extractor}: ${audio.complete_tracks || 0} complete, ${audio.missing_tracks || 0} missing`;
      } catch (err) {
        statusTarget.textContent = `Audio feature status unavailable: ${err.message}`;
      }
    }
    async function loadNavidromeSettings() {
      try {
        const data = await json("/settings/navidrome");
        document.getElementById("navidromeUrl").value = data.url || "";
        document.getElementById("navidromeUser").value = data.user || "";
        document.getElementById("navidromePassword").value = "";
        document.getElementById("navidromeAuthMode").value = data.auth_mode || "token";
        document.getElementById("navidromeTimeoutSeconds").value = data.timeout_seconds || 60;
        document.getElementById("navidromeDownloadMode").value = data.download_mode || "download";
        document.getElementById("navidromeTempDir").value = data.temp_dir || "";
        setNavidromeStatus(data.password_set ? "Navidrome settings loaded; password is saved." : "Navidrome settings loaded; password is not set.");
      } catch (err) {
        setNavidromeStatus(`Failed to load Navidrome settings: ${err.message}`, true);
      }
    }
    async function saveNavidromeSettings() {
      const password = document.getElementById("navidromePassword").value;
      const body = {
        url: document.getElementById("navidromeUrl").value,
        user: document.getElementById("navidromeUser").value,
        auth_mode: document.getElementById("navidromeAuthMode").value,
        timeout_seconds: Number(document.getElementById("navidromeTimeoutSeconds").value || 60),
        download_mode: document.getElementById("navidromeDownloadMode").value,
        temp_dir: document.getElementById("navidromeTempDir").value || null
      };
      if (password) body.password = password;
      try {
        const data = await json("/settings/navidrome", {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(body)
        });
        document.getElementById("navidromePassword").value = "";
        setNavidromeStatus(data.password_set ? "Navidrome settings saved." : "Navidrome settings saved; password is not set.");
      } catch (err) {
        setNavidromeStatus(`Save failed: ${err.message}`, true);
      }
    }
    async function pingNavidrome() {
      try {
        const data = await json("/navidrome/ping", {method: "POST"});
        const version = [data.version, data.server_version].filter(Boolean).join(" / ");
        setNavidromeStatus(`Navidrome ping OK${version ? `: ${version}` : ""}.`);
      } catch (err) {
        setNavidromeStatus(`Navidrome ping failed: ${err.message}`, true);
      }
    }
    async function startNavidromeSync() {
      try {
        await json("/jobs/navidrome-sync", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({page_size: 500, mark_stale: true})
        });
        setNavidromeStatus("Navidrome sync queued.");
        await refreshJobs();
      } catch (err) {
        setNavidromeStatus(`Navidrome sync failed: ${err.message}`, true);
      }
    }
    function setGeneratedMixStatus(message, isError = false) {
      const target = document.getElementById("generatedMixStatus");
      if (!target) return;
      target.textContent = message;
      target.classList.toggle("error", isError);
    }
    function generatedMixSettingsPayload() {
      return {
        mix_dashboard_count: Number(document.getElementById("generatedMixDashboardCount").value || 8),
        mix_tracks_per_mix: Number(document.getElementById("generatedMixTracksPerMix").value || 100),
        mix_update_cadence: document.getElementById("generatedMixUpdateCadence").value || "daily",
        mix_seed_source: document.getElementById("generatedMixSeedSource").value || "listening_history",
        mix_region_threshold: Number(document.getElementById("generatedMixRegionThreshold").value || 0.82),
        mix_discovery_ratio: Number(document.getElementById("generatedMixDiscoveryRatio").value || 0.75),
        mix_novelty_weight: Number(document.getElementById("generatedMixNoveltyWeight").value || 0.6),
        mix_max_per_artist: Number(document.getElementById("generatedMixMaxPerArtist").value || 4),
        mix_max_per_release: Number(document.getElementById("generatedMixMaxPerRelease").value || 2),
        mix_duplicate_strictness: document.getElementById("generatedMixDuplicateStrictness").value || "strict",
        mix_candidate_pool: Number(document.getElementById("generatedMixCandidatePool").value || 1200),
        mix_model: document.getElementById("generatedMixModel").value || "discogs_multi",
      };
    }
    function renderGeneratedMixDiagnostics(generation) {
      const target = document.getElementById("generatedMixDiagnostics");
      if (!target) return;
      if (!generation) {
        target.textContent = "";
        return;
      }
      const summary = {
        reason: generation.reason,
        should_generate: generation.should_generate,
        generation_count: generation.generation_count,
        existing_visible_count: generation.existing_visible_count,
        active_count: generation.active_count,
        saved_count: generation.saved_count,
        expired_active_count: generation.expired_active_count,
        preference_refresh_due: generation.preference_refresh_due,
        newest_active_at: generation.newest_active_at,
        embedding_count: generation.embedding_count,
        settings: generation.settings,
        preference_state: generation.preference_state,
        mixes: (generation.mixes || []).map(item => ({
          title: item.title,
          status: item.status,
          track_count: item.track_count,
          representative: {
            title: item.anchor?.representative_title,
            artist: item.anchor?.representative_artist,
            album: item.anchor?.representative_album,
          },
          label_artists: item.anchor?.label_artists,
          seed_examples: item.anchor?.seed_examples,
          region: item.score_summary ? {
            seed_count: item.score_summary.seed_count,
            signal_strength: item.score_summary.signal_strength,
            candidate_id_count: item.score_summary.candidate_id_count,
            candidate_count: item.score_summary.candidate_count,
            selected_count: item.score_summary.selected_count,
            known_selected: item.score_summary.known_selected,
            new_selected: item.score_summary.new_selected,
            discovery_target: item.score_summary.discovery_target,
            novelty_weight: item.score_summary.novelty_weight,
            average_novelty: item.score_summary.average_novelty,
            novelty_distribution: item.score_summary.novelty_distribution,
            skipped_artist_cap: item.score_summary.skipped_artist_cap,
            skipped_release_cap: item.score_summary.skipped_release_cap,
            skipped_known_quota: item.score_summary.skipped_known_quota,
            skipped_cross_mix_duplicate: item.score_summary.skipped_cross_mix_duplicate,
          } : null,
        })),
      };
      target.textContent = JSON.stringify(summary, null, 2);
    }
    async function loadGeneratedMixSettings() {
      try {
        const data = await json("/api/v1/mixes/settings");
        const settings = data.settings || {};
        document.getElementById("generatedMixDashboardCount").value = settings.mix_dashboard_count ?? 8;
        document.getElementById("generatedMixTracksPerMix").value = settings.mix_tracks_per_mix ?? 100;
        document.getElementById("generatedMixUpdateCadence").value = settings.mix_update_cadence || "daily";
        document.getElementById("generatedMixSeedSource").value = settings.mix_seed_source || "listening_history";
        document.getElementById("generatedMixRegionThreshold").value = settings.mix_region_threshold ?? 0.82;
        document.getElementById("generatedMixDiscoveryRatio").value = settings.mix_discovery_ratio ?? 0.75;
        document.getElementById("generatedMixNoveltyWeight").value = settings.mix_novelty_weight ?? 0.6;
        document.getElementById("generatedMixMaxPerArtist").value = settings.mix_max_per_artist ?? 4;
        document.getElementById("generatedMixMaxPerRelease").value = settings.mix_max_per_release ?? 2;
        document.getElementById("generatedMixDuplicateStrictness").value = settings.mix_duplicate_strictness || "strict";
        document.getElementById("generatedMixCandidatePool").value = settings.mix_candidate_pool ?? 1200;
        document.getElementById("generatedMixModel").value = settings.mix_model || "discogs_multi";
        setGeneratedMixStatus("Generated mix settings loaded.");
        await loadGeneratedMixStatus();
      } catch (err) {
        setGeneratedMixStatus(`Failed to load generated mix settings: ${err.message}`, true);
      }
    }
    async function loadGeneratedMixStatus() {
      try {
        const data = await json("/api/v1/mixes/status");
        renderGeneratedMixDiagnostics(data.generation);
        const generation = data.generation || {};
        setGeneratedMixStatus(`Generated mixes: ${generation.reason || "unknown"}; visible ${generation.existing_visible_count ?? "?"}; cadence ${(generation.settings || {}).update_cadence || "?"}.`);
      } catch (err) {
        setGeneratedMixStatus(`Generated mix status failed: ${err.message}`, true);
      }
    }
    async function saveGeneratedMixSettings() {
      try {
        await json("/api/v1/mixes/settings", {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(generatedMixSettingsPayload()),
        });
        setGeneratedMixStatus("Generated mix settings saved.");
        await loadGeneratedMixSettings();
      } catch (err) {
        setGeneratedMixStatus(`Save failed: ${err.message}`, true);
      }
    }
    async function forceRegenerateGeneratedMixes() {
      const button = document.getElementById("regenerateMixesBtn");
      if (button) button.disabled = true;
      setGeneratedMixStatus("Regenerating generated mixes...");
      try {
        const settings = generatedMixSettingsPayload();
        const result = await json("/api/v1/mixes/generate", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          timeoutMs: 120000,
          body: JSON.stringify({
            count: settings.mix_dashboard_count,
            tracks_per_mix: settings.mix_tracks_per_mix,
            force: true,
            settings,
          }),
        });
        setGeneratedMixStatus(`Regenerated ${result.items?.length || 0} mixes.`);
        renderGeneratedMixDiagnostics(result.diagnostics);
        await loadGeneratedMixStatus();
        if (document.getElementById("dashboard").classList.contains("active")) await loadDashboard();
      } catch (err) {
        setGeneratedMixStatus(`Regeneration failed: ${err.message}`, true);
      } finally {
        if (button) button.disabled = false;
      }
    }
    function setInstantMixStatus(message, isError = false) {
      const target = document.getElementById("instantMixStatus");
      if (!target) return;
      target.textContent = message;
      target.classList.toggle("error", isError);
    }
    async function loadInstantMixSettings() {
      try {
        const data = await json("/instant-mix/settings");
        document.getElementById("instantMixModel").value = data.model || "discogs_multi";
        document.getElementById("instantMixCount").value = data.count ?? 50;
        document.getElementById("instantMixMinSimilarity").value = data.min_similarity ?? "";
        document.getElementById("instantMixMaxPerArtist").value = data.max_per_artist ?? 2;
        document.getElementById("instantMixExcludeSameAlbum").checked = data.exclude_same_album !== false;
        document.getElementById("instantMixCountCollaborationArtists").checked = data.count_collaboration_artists !== false;
        setInstantMixStatus("Instant mix settings loaded.");
      } catch (err) {
        setInstantMixStatus(`Failed to load instant mix settings: ${err.message}`, true);
      }
    }
    async function saveInstantMixSettings() {
      const minSimilarityRaw = document.getElementById("instantMixMinSimilarity").value;
      const body = {
        model: document.getElementById("instantMixModel").value,
        count: Number(document.getElementById("instantMixCount").value || 50),
        min_similarity: minSimilarityRaw === "" ? null : Number(minSimilarityRaw),
        max_per_artist: Number(document.getElementById("instantMixMaxPerArtist").value || 2),
        exclude_same_album: document.getElementById("instantMixExcludeSameAlbum").checked,
        count_collaboration_artists: document.getElementById("instantMixCountCollaborationArtists").checked,
      };
      try {
        await json("/instant-mix/settings", {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(body)
        });
        setInstantMixStatus("Instant mix settings saved.");
        await loadInstantMixSettings();
      } catch (err) {
        setInstantMixStatus(`Save failed: ${err.message}`, true);
      }
    }
    function renderInstantMixEmptyDetail() {
      currentInstantMixRequestId = null;
      document.getElementById("instantMixDetail").innerHTML =
        `<div class="meta">Select a request to inspect returned tracks and parameters.</div>`;
    }
    function renderInstantMixRequestCard(item) {
      const statusClass = item.status === "completed" ? "" : "bad-pill";
      const when = item.created_at ? new Date(item.created_at).toLocaleString() : "";
      const seedTitle = item.seed_track ? label(item.seed_track) : item.seed_item_id;
      const seedMeta = item.seed_track
        ? `#${item.seed_track.id} - ${text(item.seed_track.album) || "no album"}`
        : `Navidrome ${item.seed_item_id}`;
      return `<button class="track" style="text-align:left" onclick="openInstantMixRequest('${encodedArg(item.id)}')">
        <div class="row" style="justify-content:space-between">
          <strong>${esc(seedTitle)}</strong>
          <span class="pill ${statusClass}">${esc(item.status)}</span>
        </div>
        <div class="meta">${esc(seedMeta)}</div>
        <div class="meta">returned ${item.result_count}/${item.effective_count} - min similarity ${item.min_similarity ?? "none"}</div>
        <div class="meta">model ${esc(item.model)}</div>
        <div class="meta">${esc(when)}</div>
      </button>`;
    }
    async function loadInstantMixRequests({updateUrl = true} = {}) {
      try {
        const data = await json("/instant-mix/requests?limit=100");
        const requests = data.results || [];
        const html = requests.map(renderInstantMixRequestCard).join("");
        document.getElementById("instantMixRequests").innerHTML = html || `<div class="meta">No instant mix requests yet.</div>`;
        if (updateUrl && paramsFromSearch().view !== "instantMix") {
          pushRouteOnly({view: "instantMix"}, {replace: true, reset: true});
        }
        return requests;
      } catch (err) {
        document.getElementById("instantMixRequests").innerHTML =
          `<div class="error">Failed to load instant mix requests: ${esc(err.message)}</div>`;
        return [];
      }
    }
    async function openInstantMixRequest(requestId) {
      const decodedId = decodeURIComponent(requestId);
      await routeTo({view: "instantMix", request: decodedId}, {reset: true});
    }
    function backToInstantMixList() {
      routeTo({view: "instantMix"}, {reset: true});
    }
    function renderTrackMetaLine(t) {
      const base = [text(t.genre), t.year || "", text(t.album)].filter(Boolean).map(esc).join(" / ");
      const predictedGenres = (t.genre_discogs400 || [])
        .slice(0, 3)
        .map(prediction => formatPredictionLabel(prediction.label))
        .filter(Boolean)
        .join(", ");
      return [base, predictedGenres].filter(Boolean).join(" · ");
    }
    function renderTrackDetailLine(t) {
      const features = t.card_features || {};
      const left = [];
      const bpm = numericFeatureValue(features.bpm);
      if (bpm !== null) left.push(`BPM ${formatBpm(bpm)}`);
      const key = featureTextValue(features.key);
      const scale = featureTextValue(features.scale);
      if (key || scale) left.push([key, scale].filter(Boolean).map(esc).join(" "));
      if (t.approachability_3c) left.push(`ap ${formatScore(t.approachability_3c.score)}`);
      if (t.engagement_3c) left.push(`eng ${formatScore(t.engagement_3c.score)}`);
      const right = [
        t.year || "",
        t.audio_format ? esc(t.audio_format) : "",
        t.bitrate ? `${esc(t.bitrate)} kbps` : "",
        t.duration ? formatDuration(t.duration) : "",
      ].filter(Boolean);
      if (!left.length && !right.length) return "";
      return `<span class="track-card-left">${left.join(" · ")}</span><span class="track-card-right">${right.join(" · ")}</span>`;
    }
    function numericFeatureValue(feature) {
      if (!feature || feature.value === null || feature.value === undefined) return null;
      const value = Number(feature.value);
      return Number.isFinite(value) ? value : null;
    }
    function featureTextValue(feature) {
      if (!feature) return "";
      return feature.text_value || (feature.value === null || feature.value === undefined ? "" : String(feature.value));
    }
    function formatBpm(value) {
      return Number.isInteger(value) ? String(value) : value.toFixed(1);
    }
    function formatPredictionLabel(label) {
      const parts = String(label || "")
        .split(/---|\//)
        .map(part => part.trim())
        .filter(Boolean);
      return esc(parts.length > 1 ? parts.slice(1).join(" / ") : parts[0] || "");
    }
    function setNavidromeLikeDebug(message, detail = null) {
      const suffix = detail === null || detail === undefined ? "" : ` ${typeof detail === "string" ? detail : JSON.stringify(detail)}`;
      navidromeLikeLastDebug = `${new Date().toLocaleTimeString()} ${message}${suffix}`;
      const target = document.getElementById("navidromeLikeDebug");
      if (target) target.textContent = `Navidrome likes debug: ${navidromeLikeLastDebug}`;
      console.info("[discocs navidrome likes]", message, detail ?? "");
    }
    function bootstrapLikeIcon(filled) {
      return `<i class="bi bi-hand-thumbs-up${filled ? "-fill" : ""}" aria-hidden="true"></i>`;
    }
    function scheduleNavidromeLikeIdsRefresh() {
      if (navidromeLikeIdsRefreshScheduled) return;
      navidromeLikeIdsRefreshScheduled = true;
      setTimeout(() => {
        navidromeLikeIdsRefreshScheduled = false;
        fetchAndApplyNavidromeLikeIds({silent: true});
      }, 0);
    }
    function navidromeLikeButton(t, options = {}) {
      if (!t.navidrome_item_id) return "";
      const classes = `${options.compact ? "stat-icon-button " : ""}navidrome-like-button`;
      scheduleNavidromeLikeIdsRefresh();
      return `<button
        class="${classes}"
        data-track-id="${t.id}"
        data-navidrome-like="1"
        onclick="event.preventDefault(); event.stopPropagation(); toggleNavidromeLike(${t.id})"
        title="Like in Navidrome"
        aria-label="Like in Navidrome"
      >${bootstrapLikeIcon(false)}</button>`;
    }
    function applyNavidromeLikeIds(data) {
      const likedTrackIds = new Set((data?.track_ids || []).map(Number));
      document.querySelectorAll(".navidrome-like-button").forEach(button => {
        if (button.dataset.navidromeUnavailable === "1") {
          button.classList.toggle("like-active", false);
          button.innerHTML = bootstrapLikeIcon(false);
          button.disabled = true;
          return;
        }
        const trackId = Number(button.dataset.trackId);
        const liked = likedTrackIds.has(trackId);
        button.classList.toggle("like-active", liked);
        const label = liked ? "Liked" : "Like";
        button.title = `${label} in Navidrome`;
        button.setAttribute("aria-label", `${label} in Navidrome`);
        button.innerHTML = bootstrapLikeIcon(liked);
        button.disabled = false;
      });
    }
    async function fetchAndApplyNavidromeLikeIds({silent = false} = {}) {
      try {
        setNavidromeLikeDebug("GET /navidrome/starred/ids start");
        const data = await json(`/navidrome/starred/ids?_=${Date.now()}`, {timeoutMs: 15000});
        applyNavidromeLikeIds(data);
        setNavidromeLikeDebug("GET /navidrome/starred/ids ok", {
          user: data.user,
          mapped_count: data.mapped_count,
          track_ids: data.track_ids || [],
        });
        return data;
      } catch (err) {
        setNavidromeLikeDebug("GET /navidrome/starred/ids failed", err.message);
        if (!silent) throw err;
        return null;
      }
    }
    async function toggleNavidromeLike(trackId) {
      const id = Number(trackId);
      const before = await fetchAndApplyNavidromeLikeIds({silent: false});
      const wasLiked = new Set((before?.track_ids || []).map(Number)).has(id);
      const nextLiked = !wasLiked;
      const buttons = document.querySelectorAll(`.navidrome-like-button[data-track-id="${id}"]`);
      buttons.forEach(button => button.disabled = true);
      try {
        setNavidromeLikeDebug(`PUT /tracks/${id}/navidrome-star start`, {starred: nextLiked});
        await json(`/tracks/${id}/navidrome-star`, {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({starred: nextLiked}),
          timeoutMs: 15000
        });
        setNavidromeLikeDebug(`PUT /tracks/${id}/navidrome-star ok`, {starred: nextLiked});
        await fetchAndApplyNavidromeLikeIds({silent: false});
        if (likedCatalog) {
          await loadNavidromeLikes({updateUrl: false});
        }
      } catch (err) {
        await fetchAndApplyNavidromeLikeIds({silent: true});
        setNavidromeLikeDebug(`PUT /tracks/${id}/navidrome-star failed`, err.message);
        const target = document.getElementById("likedStatusError");
        if (target) target.textContent = err.message;
      } finally {
        buttons.forEach(button => button.disabled = false);
      }
    }
    function toggleCurrentNavidromeLike(event) {
      if (event) {
        event.preventDefault();
        event.stopPropagation();
      }
      const item = currentQueueItem();
      const trackId = item?.track_id || activeTrackId;
      if (!trackId) return;
      toggleNavidromeLike(trackId);
    }
    document.addEventListener("click", event => {
      if (!event.target.closest("#trackActionMenu") && !event.target.closest(".track-menu-button")) {
        closeTrackMenu();
      }
      const button = event.target.closest(".navidrome-like-button");
      if (!button || !button.dataset.navidromeLike) return;
      if (button.hasAttribute("onclick")) return;
      event.preventDefault();
      event.stopPropagation();
      toggleNavidromeLike(button.dataset.trackId);
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") closeTrackMenu();
    });
    function renderRecommendationTrack(t, {seedTrackId = null, rank = null, allowRating = true} = {}) {
      const prefix = rank === null || rank === undefined ? "" : `${rank + 1}. `;
      const score = t.similarity === null || t.similarity === undefined
        ? ""
        : `<span class="track-score-inline">| ${formatScore(t.similarity)}</span>`;
      const rating = allowRating && seedTrackId
        ? `<button class="${t.rating === 3 ? "rating-active" : ""}" onclick="rateForSeed(${seedTrackId}, ${t.id}, 3)">good</button>
            <button class="${t.rating === 2 ? "rating-active" : ""}" onclick="rateForSeed(${seedTrackId}, ${t.id}, 2)">okay</button>
            <button class="${t.rating === 0 ? "rating-active" : ""}" onclick="rateForSeed(${seedTrackId}, ${t.id}, 0)">bad</button>`
        : "";
      const details = renderTrackDetailLine(t);
      return `<div class="track ${t.id === activeTrackId ? "active-track" : ""}">
        <div class="track-body">
          ${coverMarkup(t)}
          <div class="track-main">
            <div class="track-head">
              <div class="title track-title-row">
                <span class="track-title-main">${prefix}<span class="track-id">#${t.id}</span> ${esc(label(t))}</span>
                ${score}
              </div>
            </div>
            <div class="meta">${renderTrackMetaLine(t)}</div>
            ${details ? `<div class="meta track-card-line">${details}</div>` : ""}
          </div>
        </div>
        <div class="track-actions">
          <div class="track-action-group">${rating}${navidromeLikeButton(t)}</div>
          <div class="track-action-group track-command-group">
            <button class="icon-button" onclick="openAnalysis(${t.id})" title="Analysis metadata" aria-label="Analysis metadata">
              <span class="icon-tablet" aria-hidden="true"></span>
            </button>
            <button onclick="playTrack(${t.id}, '${encodedArg(label(t))}')">Play</button>
            <button onclick="addSeed(${t.id})">Add seed</button>
            <button onclick="addToBlendExtra(${t.id})">Add to blend</button>
            <button onclick="setSeed(${t.id})">Seed</button>
            ${trackMenuButton(t.id)}
          </div>
        </div>
      </div>`;
    }
    function renderInstantMixResult(item, index, seedTrackId) {
      if (item.id) return renderRecommendationTrack(item, {seedTrackId, rank: index});
      return `<div class="track">
        <div class="row" style="justify-content:space-between">
          <div class="title">${index + 1}. ${esc(item.artist || "")} - ${esc(item.title || item.item_id)}</div>
          <span class="score">${formatScore(item.similarity)}</span>
        </div>
        <div class="meta">Navidrome ${esc(item.item_id)} - track #${item.track_id} - distance ${formatScore(item.distance)}</div>
        <div class="meta">${esc(item.album || "")}</div>
      </div>`;
    }
    async function loadInstantMixRequestDetail(requestId, {updateUrl = true} = {}) {
      try {
        const item = await json(`/instant-mix/requests/${encodeURIComponent(requestId)}`);
        currentInstantMixRequestId = item.id;
        const params = item.params || {};
        const results = item.results || [];
        const seedCard = item.seed_track
          ? renderRecommendationTrack(item.seed_track, {allowRating: false})
          : `<div class="track">
              <div class="title">${esc(item.seed_item_id)}</div>
              <div class="meta">Seed track is not available in the local catalog.</div>
            </div>`;
        document.getElementById("instantMixDetail").innerHTML = `
          <div>
            <h2>Seed track</h2>
            ${seedCard}
          </div>
          <div class="track">
            <div class="row" style="justify-content:space-between">
              <strong>${esc(item.seed_item_id)}</strong>
              <span class="pill ${item.status === "completed" ? "" : "bad-pill"}">${esc(item.status)}</span>
            </div>
            <div class="meta">request ${esc(item.id)}</div>
            <div class="meta">track #${item.seed_track_id || ""} - model ${esc(item.model)} - ${esc(item.created_at || "")}</div>
            <div class="meta">requested ${item.requested_count ?? "none"} - effective ${item.effective_count} - returned ${item.result_count}</div>
            <div class="meta">min similarity ${item.min_similarity ?? "none"} - max per artist ${item.max_per_artist} - exclude same album ${item.exclude_same_album ? "yes" : "no"} - count collaboration artists ${params.count_collaboration_artists !== false ? "yes" : "no"}</div>
            ${item.error ? `<div class="error">${esc(item.error)}</div>` : ""}
            <pre class="meta">${esc(JSON.stringify(params, null, 2))}</pre>
          </div>
          <h2>Returned tracks</h2>
          ${results.map((result, index) => renderInstantMixResult(result, index, item.seed_track_id)).join("") || `<div class="meta">No returned tracks.</div>`}`;
        if (updateUrl) {
          pushRouteOnly({view: "instantMix", request: item.id}, {replace: true, reset: true});
        }
      } catch (err) {
        document.getElementById("instantMixDetail").innerHTML =
          `<div class="error">Failed to load instant mix request: ${esc(err.message)}</div>`;
      }
    }
    function coverMarkup(t) {
      return `<div class="cover">
        <img src="/tracks/${t.id}/cover?size=128" loading="lazy" alt="" onerror="this.parentElement.classList.add('empty'); this.remove()">
      </div>`;
    }
    function renderTrack(t, mode) {
      const addSeedButton = ["browse", "library", "seed"].includes(mode)
        ? `<button onclick="addSeed(${t.id})">Add seed</button>` : "";
      const addBlendButton = ["browse", "library", "seed", "blend"].includes(mode)
        ? `<button onclick="addToBlendExtra(${t.id})">Add to blend</button>` : "";
      const details = renderTrackDetailLine(t);
      return `<div class="track ${t.id === seedId ? "selected" : ""} ${t.id === activeTrackId ? "active-track" : ""}">
        <div class="track-body">
          ${coverMarkup(t)}
          <div class="track-main">
            <div class="track-head">
              <div class="title track-title-row">
                <span class="track-title-main"><span class="track-id">#${t.id}</span> ${esc(label(t))}</span>
              </div>
            </div>
            <div class="meta">${renderTrackMetaLine(t)}</div>
            ${details ? `<div class="meta track-card-line">${details}</div>` : ""}
          </div>
        </div>
        <div class="track-actions">
          <div class="track-action-group">${navidromeLikeButton(t)}</div>
          <div class="track-action-group track-command-group">
            <button class="icon-button" onclick="openAnalysis(${t.id})" title="Analysis metadata" aria-label="Analysis metadata">
              <span class="icon-tablet" aria-hidden="true"></span>
            </button>
            <button onclick="playTrack(${t.id}, '${encodedArg(label(t))}')">Play</button>
            ${addSeedButton}
            ${addBlendButton}
            <button onclick="setSeed(${t.id})">Seed</button>
            ${trackMenuButton(t.id)}
          </div>
        </div>
      </div>`;
    }
    function formatScore(value) {
      if (value === null || value === undefined) return "";
      const number = Number(value);
      return Number.isFinite(number) ? number.toFixed(4) : String(value);
    }
    function formatFeatureValue(feature) {
      const raw = feature.text_value || formatScore(feature.value);
      return [raw, feature.unit || ""].filter(Boolean).join(" ");
    }
    function renderAnalysisOutput(output) {
      const predictions = output.top_predictions || [];
      const tags = predictions.length
        ? `<div class="tag-list">${predictions.map(prediction => `
            <span class="pill" title="${esc(formatScore(prediction.score))}">
              ${prediction.rank}. ${esc(prediction.label)} <span class="score">${esc(formatScore(prediction.score))}</span>
            </span>`).join("")}</div>`
        : `<div class="meta">No top labels for this model.</div>`;
      const scores = output.scores || [];
      const scorePreview = scores.length
        ? `<details>
            <summary class="meta">Scores vector (${scores.length})</summary>
            <pre class="score-list">${esc(scores.map(score => formatScore(score)).join(", "))}</pre>
          </details>`
        : "";
      return `<div class="analysis-output">
        <div class="row" style="justify-content:space-between">
          <strong>${esc(output.model_name)}</strong>
          <span class="pill">${esc(output.dim)} ${esc(output.dtype || "")} ${esc(output.aggregation || "")}</span>
        </div>
        ${tags}
        ${scorePreview}
      </div>`;
    }
    function renderAnalysisFeatures(features) {
      if (!features.length) return `<div class="meta">No audio features stored for this track.</div>`;
      const rows = features.map(feature => `<tr>
        <td>${esc(feature.extractor)}</td>
        <td>${esc(feature.name)}</td>
        <td>${esc(formatFeatureValue(feature))}</td>
        <td>${esc(formatScore(feature.confidence))}</td>
      </tr>`).join("");
      return `<table class="model-table">
        <thead><tr><th>Extractor</th><th>Feature</th><th>Value</th><th>Confidence</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    }
    async function openAnalysis(trackId) {
      const modal = document.getElementById("analysisModal");
      modal.classList.add("open");
      document.getElementById("analysisSubtitle").textContent = `#${trackId}`;
      document.getElementById("analysisContent").innerHTML = `<div class="meta">Loading analysis metadata...</div>`;
      try {
        const data = await json(`/tracks/${trackId}/analysis`);
        const track = data.track;
        document.getElementById("analysisSubtitle").textContent = `#${track.id} ${label(track)}`;
        const outputs = data.outputs || [];
        document.getElementById("analysisContent").innerHTML = `
          <div class="meta path" title="${esc(track.path)}">${esc(track.path)}</div>
          <div class="analysis-output">
            <strong>Stored analysis</strong>
            <div class="meta">${outputs.length} model outputs; ${(data.features || []).length} audio features.</div>
          </div>
          <div class="analysis-output">
            <strong>Audio features</strong>
            ${renderAnalysisFeatures(data.features || [])}
          </div>
          ${outputs.map(renderAnalysisOutput).join("") || `<div class="meta">No model outputs stored for this track.</div>`}
        `;
      } catch (error) {
        document.getElementById("analysisContent").innerHTML = `<div class="error">${esc(error.message)}</div>`;
      }
    }
    function closeAnalysisModal(event) {
      if (event && event.target !== document.getElementById("analysisModal")) return;
      document.getElementById("analysisModal").classList.remove("open");
    }
    function formatBytes(size) {
      if (!size && size !== 0) return "";
      const units = ["B", "KB", "MB", "GB"];
      let value = Number(size);
      let unit = 0;
      while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
      }
      return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
    }
    function formatDate(value) {
      if (!value) return "";
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
    }
    function renderLostFiles(data) {
      document.getElementById("lostFilesCount").textContent = `${data.count} lost`;
      document.getElementById("lostFilesPage").textContent = `page ${data.page} / ${data.pages}`;
      if (!data.results.length) {
        document.getElementById("lostFilesList").innerHTML = `<div class="meta" style="margin-top:12px">No lost files.</div>`;
        return;
      }
      const rows = data.results.map(track => `
        <tr>
          <td><input type="checkbox" class="lost-checkbox" value="${track.id}"></td>
          <td>#${track.id} ${label(track)}<div class="meta">${text(track.album)}</div></td>
          <td class="path" title="${track.path}">${track.path}</td>
          <td>${formatBytes(track.file_size)}</td>
          <td>${formatDate(track.missing_at)}</td>
        </tr>`).join("");
      document.getElementById("lostFilesList").innerHTML = `
        <table class="model-table">
          <thead><tr><th><input type="checkbox" onchange="toggleLostFilesSelection(this.checked)"></th><th>Track</th><th>Path</th><th>Size</th><th>Disappeared on</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }
    async function loadLostFiles() {
      const data = await json(`/lost-files?page=${lostFilesPage}&page_size=${lostFilesPageSize}`);
      if (data.results.length === 0 && data.count > 0 && lostFilesPage > 1) {
        lostFilesPage -= 1;
        return loadLostFiles();
      }
      renderLostFiles(data);
    }
    function renderErroredFiles(data) {
      document.getElementById("erroredFilesCount").textContent = `${data.count} errors`;
      document.getElementById("erroredFilesPage").textContent = `page ${data.page} / ${data.pages}`;
      if (!data.results.length) {
        document.getElementById("erroredFilesList").innerHTML = `<div class="meta" style="margin-top:12px">No errored files.</div>`;
        return;
      }
      const rows = data.results.map(item => {
        const track = `${item.artist || ""}${item.artist && item.title ? " - " : ""}${item.title || ""}` || `track #${item.track_id}`;
        return `
          <tr>
            <td><input type="checkbox" class="errored-checkbox" value="${esc(item.task_id)}"></td>
            <td class="track-cell">#${item.track_id}<div class="meta">${esc(track)}</div></td>
            <td class="path path-cell" title="${esc(item.path)}">${esc(item.path)}</td>
            <td class="model-cell">${esc(item.model_name)}<div class="meta">${esc(item.job_kind)} · ${esc(item.status)} · attempt ${item.attempts}/${item.max_attempts}</div></td>
            <td class="error-cell">
              <pre class="meta error-text">${esc(item.error)}</pre>
              <div class="meta">${esc(item.error_type || "")}${item.stage ? ` · ${esc(item.stage)}` : ""}</div>
            </td>
            <td class="updated-cell">${formatDate(item.updated_at)}</td>
          </tr>`;
      }).join("");
      document.getElementById("erroredFilesList").innerHTML = `
        <table class="model-table error-table">
          <colgroup>
            <col class="check-col">
            <col class="track-col">
            <col class="path-col">
            <col class="model-col">
            <col class="error-col">
            <col class="updated-col">
          </colgroup>
          <thead><tr><th><input type="checkbox" onchange="toggleErroredFilesSelection(this.checked)"></th><th>Track</th><th>Path</th><th>Model</th><th>Error</th><th>Updated</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }
    async function loadErroredFiles() {
      const data = await json(`/analysis/errors?page=${erroredFilesPage}&page_size=${erroredFilesPageSize}`);
      if (data.results.length === 0 && data.count > 0 && erroredFilesPage > 1) {
        erroredFilesPage -= 1;
        return loadErroredFiles();
      }
      renderErroredFiles(data);
    }
    function toggleErroredFilesSelection(checked) {
      document.querySelectorAll(".errored-checkbox").forEach(input => { input.checked = checked; });
    }
    async function deleteSelectedErroredFiles() {
      const ids = Array.from(document.querySelectorAll(".errored-checkbox:checked")).map(input => input.value);
      if (!ids.length) return;
      if (!confirm(`Remove ${ids.length} error record(s) from the error list?`)) return;
      await json("/analysis/errors", {
        method: "DELETE",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({task_ids: ids})
      });
      await loadErroredFiles();
    }
    async function deleteAllErroredFiles() {
      if (!confirm("Remove all current error records from the error list?")) return;
      await json("/analysis/errors", {
        method: "DELETE",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({all_errors: true})
      });
      erroredFilesPage = 1;
      await loadErroredFiles();
    }
    async function previousErroredFilesPage() {
      if (erroredFilesPage <= 1) return;
      routeTo({view: "erroredFiles", page: String(erroredFilesPage - 1), model: model()}, {reset: true});
    }
    async function nextErroredFilesPage() {
      const data = await json(`/analysis/errors?page=${erroredFilesPage}&page_size=${erroredFilesPageSize}`);
      if (erroredFilesPage >= data.pages) return;
      routeTo({view: "erroredFiles", page: String(erroredFilesPage + 1), model: model()}, {reset: true});
    }
    function toggleLostFilesSelection(checked) {
      document.querySelectorAll(".lost-checkbox").forEach(input => { input.checked = checked; });
    }
    async function previousLostFilesPage() {
      if (lostFilesPage <= 1) return;
      routeTo({view: "lostFiles", page: String(lostFilesPage - 1), model: model()}, {reset: true});
    }
    async function nextLostFilesPage() {
      const data = await json(`/lost-files?page=${lostFilesPage}&page_size=${lostFilesPageSize}`);
      if (lostFilesPage >= data.pages) return;
      routeTo({view: "lostFiles", page: String(lostFilesPage + 1), model: model()}, {reset: true});
    }
    async function checkMissingFiles() {
      await json("/jobs/check-missing-files", {method: "POST"});
      await refreshJobs();
    }
    async function deleteSelectedLostFiles() {
      const ids = Array.from(document.querySelectorAll(".lost-checkbox:checked")).map(input => Number(input.value));
      if (!ids.length) return;
      if (!confirm(`Remove ${ids.length} lost file record(s) from the library?`)) return;
      await json("/lost-files", {
        method: "DELETE",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({track_ids: ids})
      });
      await loadLostFiles();
      await refreshStats();
    }
    async function deleteAllLostFiles() {
      if (!confirm("Remove all lost file records from the library?")) return;
      await json("/lost-files", {
        method: "DELETE",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({all_missing: true})
      });
      lostFilesPage = 1;
      await loadLostFiles();
      await refreshStats();
    }
    async function searchTracks({updateUrl = true} = {}) {
      const q = document.getElementById("query").value;
      const status = document.getElementById("embeddingStatus").value;
      if (updateUrl && !applyingRoute) {
        routeTo({view: "library", query: q, embedding_status: status, model: model()}, {reset: true});
        return;
      }
      const data = await json(`/tracks?query=${encodeURIComponent(q)}&limit=80&embedding_status=${status}&model=${encodeURIComponent(model())}`);
      document.getElementById("tracksList").innerHTML = data.results.map(t => renderTrack(t, "library")).join("");
    }
    async function searchSeeds() {
      const q = document.getElementById("seedQuery").value;
      const data = await json(`/tracks?query=${encodeURIComponent(q)}&limit=30&embedding_status=ready&model=${encodeURIComponent(model())}`);
      document.getElementById("seedResults").innerHTML = data.results.map(t => renderTrack(t, "seed")).join("");
    }
    function facetButton(kind, item) {
      const active = String(browseFilters[kind] || "") === String(item.value);
      const encodedValue = encodeURIComponent(item.value).replace(/'/g, "%27");
      const display = kind === "folder" ? compactFolder(item.value) : item.value;
      return `<button class="facet-button ${active ? "active" : ""}" title="${item.value}" onclick="setBrowseFilter('${kind}', '${encodedValue}')">
        <span class="facet-name">${display}</span><span class="pill">${item.count}</span>
      </button>`;
    }
    function renderFacets(data) {
      document.getElementById("folderFacets").innerHTML = data.folders.map(item => facetButton("folder", item)).join("") || `<div class="meta">No folders</div>`;
      document.getElementById("genreFacets").innerHTML = data.genres.map(item => facetButton("genre", item)).join("") || `<div class="meta">No tagged genres</div>`;
      document.getElementById("yearFacets").innerHTML = data.years.map(item => facetButton("year", item)).join("") || `<div class="meta">No years</div>`;
      document.getElementById("artistFacets").innerHTML = data.artists.map(item => facetButton("artist", item)).join("") || `<div class="meta">No artists</div>`;
      document.getElementById("albumFacets").innerHTML = data.albums.map(item => facetButton("album", item)).join("") || `<div class="meta">No albums</div>`;
    }
    async function refreshBrowse({updateUrl = true} = {}) {
      if (updateUrl && !applyingRoute) {
        routeTo({view: "browse", model: model(), ...browseParams()}, {reset: true});
        return;
      }
      const status = document.getElementById("browseEmbeddingStatus").value;
      const data = await json(`/browse/facets?embedding_status=${status}&model=${encodeURIComponent(model())}`);
      renderFacets(data);
      await loadBrowseTracks();
    }
    function setBrowseFilter(kind, encodedValue) {
      const value = decodeURIComponent(encodedValue);
      if (String(browseFilters[kind] || "") === value) {
        delete browseFilters[kind];
      } else {
        browseFilters = {[kind]: value};
      }
      refreshBrowse();
    }
    function clearBrowseFilters() {
      browseFilters = {};
      document.getElementById("browseQuery").value = "";
      refreshBrowse();
    }
    async function loadBrowseTracks() {
      const params = new URLSearchParams({
        query: document.getElementById("browseQuery").value,
        limit: "120",
        embedding_status: document.getElementById("browseEmbeddingStatus").value,
        model: model()
      });
      Object.entries(browseFilters).forEach(([key, value]) => params.set(key, value));
      const data = await json(`/tracks?${params.toString()}`);
      const labels = Object.entries(browseFilters).map(([key, value]) => `${key}: ${value}`);
      document.getElementById("browseFilterLabel").textContent = labels.join(" / ") || "all tracks";
      document.getElementById("browseTracks").innerHTML = data.results.map(t => renderTrack(t, "browse")).join("") || `<div class="meta">No tracks match this browser filter.</div>`;
    }
    async function addSeed(id) {
      if (seedBasket.some(track => track.id === id)) {
        showSection("evaluation");
        return;
      }
      const track = await json(`/tracks/${id}`);
      seedBasket.push(track);
      renderSeedBasket();
      showSection("evaluation");
    }
    function clearSeedBasket() {
      seedBasket = [];
      evaluationIndex = -1;
      renderSeedBasket();
      document.getElementById("evaluationSimilarList").innerHTML = "";
      document.getElementById("evaluationSeedPanel").innerHTML = "";
      document.getElementById("evaluationRefreshBtn").disabled = true;
    }
    function renderSeedBasket() {
      document.getElementById("evaluationProgress").textContent = seedBasket.length
        ? `Seed ${Math.max(evaluationIndex + 1, 0)} of ${seedBasket.length}`
        : "No seeds selected";
      document.getElementById("seedBasket").innerHTML = seedBasket.map((track, index) => `
        <div class="track ${index === evaluationIndex ? "selected" : ""}">
          <div class="row" style="justify-content:space-between">
            <div class="title">#${track.id} ${label(track)}</div>
            <button onclick="selectEvaluationSeed(${index})">Open</button>
          </div>
          <div class="meta">${[text(track.genre), track.year || "", text(track.album)].filter(Boolean).join(" / ")}</div>
        </div>`).join("") || `<div class="meta">Add seed tracks from Browse or Library.</div>`;
    }
    async function startEvaluationSession() {
      if (!seedBasket.length) return;
      await selectEvaluationSeed(0);
      showSection("evaluation");
    }
    async function selectEvaluationSeed(index, {updateUrl = true} = {}) {
      if (index < 0 || index >= seedBasket.length) return;
      evaluationIndex = index;
      seedId = seedBasket[index].id;
      seedTrack = seedBasket[index];
      document.getElementById("evaluationSeedPanel").innerHTML = renderTrack({...seedTrack, has_embedding: true}, "evaluation");
      document.getElementById("evaluationRefreshBtn").disabled = false;
      renderSeedBasket();
      await loadSimilar(seedId);
      if (updateUrl && !applyingRoute) {
        routeTo({view: "evaluation", index: String(index), model: model(), ...recommendationParams()}, {reset: true});
      }
    }
    function onLikedFilterChange() {
      if (!applyingRoute) {
        const patch = {view: "navidromeLikes", filter: document.getElementById("likedFilter").value, model: model()};
        if (likedCatalog) patch.autoload = "1";
        writeRoutePatch(patch, {replace: true});
      }
      renderLikedTracks();
    }
    async function nextEvaluationSeed() {
      if (!seedBasket.length) return;
      const next = Math.min(evaluationIndex + 1, seedBasket.length - 1);
      await selectEvaluationSeed(next);
    }
    async function skipEvaluationSeed() {
      await nextEvaluationSeed();
    }
    function readExtraBlendIds() {
      try {
        const parsed = JSON.parse(localStorage.getItem(BLEND_EXTRA_KEY) || "[]");
        return Array.isArray(parsed) ? parsed.map(Number).filter(id => Number.isInteger(id) && id > 0) : [];
      } catch (err) {
        return [];
      }
    }
    function saveExtraBlendIds() {
      localStorage.setItem(BLEND_EXTRA_KEY, JSON.stringify(extraBlendIds));
    }
    function loadExtraBlendIds() {
      extraBlendIds = readExtraBlendIds();
    }
    function likedStatusLabel(status) {
      if (status === "ready") return "ready";
      if (status === "missing_embedding") return "needs embedding";
      return "not synced";
    }
    function renderLikedStatus() {
      const line = document.getElementById("likedStatusLine");
      const detail = document.getElementById("likedStatusDetail");
      if (!likedCatalog) {
        line.textContent = "Source: Navidrome starred · not loaded yet";
        detail.textContent = "Load starred tracks for the configured Navidrome user, then refresh recommendations from their average embedding.";
        document.getElementById("refreshLikedBtn").disabled = true;
        return;
      }
      line.textContent = [
        `Source: Navidrome starred`,
        `User: ${text(likedCatalog.user)}`,
        `Loaded: ${likedCatalog.count}`,
        `Mapped: ${likedCatalog.mapped_count}`,
        `Ready: ${likedCatalog.ready_count}`,
        `Missing embedding: ${likedCatalog.missing_embedding_count}`,
        `Not synced: ${likedCatalog.not_synced_count}`
      ].join(" · ");
      detail.textContent = likedCatalog.ready_count > 0
        ? `Average blend can use ${likedCatalog.ready_count} liked tracks${extraBlendIds.length ? ` plus ${extraBlendIds.length} extra track(s)` : ""}.`
        : "No liked tracks with embeddings yet. Sync catalog and analyze missing tracks.";
      document.getElementById("refreshLikedBtn").disabled = likedReadyTrackIds().length < 1;
    }
    function likedReadyTrackIds() {
      if (!likedCatalog) return [...extraBlendIds];
      const readyIds = (likedCatalog.results || [])
        .filter(item => item.status === "ready" && item.track && item.track.id)
        .map(item => Number(item.track.id));
      const merged = [...readyIds];
      extraBlendIds.forEach(id => {
        if (!merged.includes(id)) merged.push(id);
      });
      return merged;
    }
    function renderLikedTracks() {
      const list = document.getElementById("likedTracksList");
      if (!likedCatalog || !likedCatalog.results.length) {
        list.innerHTML = `<div class="meta">Load Navidrome likes to see starred tracks here.</div>`;
        return;
      }
      const filter = document.getElementById("likedFilter").value;
      const query = document.getElementById("likedLocalQuery").value.trim().toLowerCase();
      const items = likedCatalog.results.filter(item => {
        if (filter !== "all" && item.status !== filter) return false;
        if (!query) return true;
        const track = item.track || {};
        const haystack = `${track.artist || ""} ${track.title || ""} ${track.album || ""} ${item.item_id || ""}`.toLowerCase();
        return haystack.includes(query);
      });
      if (!items.length) {
        list.innerHTML = `<div class="meta">No liked tracks match this filter.</div>`;
        return;
      }
      list.innerHTML = items.map(item => {
        const track = item.track || {};
        const title = track.id ? `#${track.id} ${label(track)}` : `${label(track)} (${item.item_id})`;
        const playButton = track.id
          ? `<button onclick="playTrack(${track.id}, '${encodedArg(label(track))}')">Play</button>`
          : "";
        const likeButton = track.id ? navidromeLikeButton({...track, navidrome_item_id: item.item_id}) : "";
        return `<div class="track">
          <div class="row" style="justify-content:space-between">
            <div class="title">${title}</div>
            <div class="row">
              <span class="pill">${likedStatusLabel(item.status)}</span>
              ${likeButton}
              ${playButton}
            </div>
          </div>
          <div class="meta">${text(track.album)} · ${text(track.path || `navidrome://${item.item_id}`)}</div>
        </div>`;
      }).join("");
    }
    function renderLikedExtraSummary() {
      const target = document.getElementById("likedExtraSummary");
      target.textContent = extraBlendIds.length
        ? `Extra blend tracks: ${extraBlendIds.join(", ")}`
        : "No extra tracks added.";
    }
    function renderLikedSimilarResults(results, metaHtml = "") {
      const html = (results || []).map(t => `
        <div class="track ${t.id === activeTrackId ? "active-track" : ""}">
          <div class="track-body">
            ${coverMarkup(t)}
            <div class="track-main">
              <div class="row" style="justify-content:space-between">
                <div class="title"><span class="score">${t.similarity.toFixed(3)}</span> #${t.id} ${label(t)}</div>
                <div class="row">
                  ${navidromeLikeButton(t)}
                  <button onclick="playTrack(${t.id}, '${encodedArg(label(t))}')">Play</button>
                  <button onclick="setSeed(${t.id})">Seed</button>
                </div>
              </div>
              <div class="meta">${[text(t.genre), t.year || "", text(t.album)].filter(Boolean).join(" / ")}</div>
              <div class="path" title="${t.path}">${t.path}</div>
            </div>
          </div>
        </div>`).join("");
      document.getElementById("likedSimilarList").innerHTML = `${metaHtml}${html || `<div class="meta">No similar tracks.</div>`}`;
    }
    async function loadNavidromeLikes({updateUrl = true} = {}) {
      const errorTarget = document.getElementById("likedStatusError");
      errorTarget.textContent = "";
      try {
        likedCatalog = await json(`/navidrome/starred?model=${encodeURIComponent(model())}`);
        renderLikedStatus();
        renderLikedTracks();
        await fetchAndApplyNavidromeLikeIds({silent: true});
        if (updateUrl && !applyingRoute) {
          writeRoutePatch({
            view: "navidromeLikes",
            autoload: "1",
            filter: document.getElementById("likedFilter").value,
            model: model()
          }, {replace: true});
        }
      } catch (err) {
        errorTarget.textContent = err.message;
      }
    }
    async function refreshLikedRecommendations({updateUrl = true} = {}) {
      const errorTarget = document.getElementById("likedStatusError");
      errorTarget.textContent = "";
      const readyIds = likedReadyTrackIds();
      if (!readyIds.length) {
        errorTarget.textContent = "No ready liked tracks with embeddings. Sync catalog and analyze missing tracks first.";
        document.getElementById("likedSimilarList").innerHTML = `<div class="meta">Analyze liked tracks first.</div>`;
        return;
      }
      const k = document.getElementById("k").value;
      const max = document.getElementById("maxPerArtist").value;
      const exclude = document.getElementById("excludeSameAlbum").checked;
      try {
        let data;
        if (extraBlendIds.length) {
          const params = new URLSearchParams({
            seed_ids: readyIds.join(","),
            model: model(),
            k: String(k),
            max_per_artist: String(max),
            exclude_same_album: String(exclude)
          });
          data = await json(`/tracks/similar/mix?${params.toString()}`);
          document.getElementById("likedSimilarSubtitle").textContent =
            `Recommendations from liked blend · average of ${readyIds.length} tracks`;
          const skipped = (data.skipped_seed_ids || []).length
            ? `<div class="meta">Skipped seeds without embeddings: ${data.skipped_seed_ids.join(", ")}</div>`
            : "";
          renderLikedSimilarResults(data.results, skipped);
        } else {
          const params = new URLSearchParams({
            model: model(),
            count: String(k),
            max_per_artist: String(max),
            exclude_same_album: String(exclude)
          });
          data = await json(`/navidrome/starred/similar?${params.toString()}`);
          document.getElementById("likedSimilarSubtitle").textContent =
            `Recommendations from liked blend · average of ${data.ready_count} liked tracks`;
          const skipped = (data.skipped_seed_ids || []).length
            ? `<div class="meta">Skipped liked tracks without embeddings: ${data.skipped_seed_ids.join(", ")}</div>`
            : "";
          renderLikedSimilarResults(data.results, skipped);
        }
        if (updateUrl && !applyingRoute) {
          writeRoutePatch({
            view: "navidromeLikes",
            autoload: "1",
            refresh: "1",
            filter: document.getElementById("likedFilter").value,
            model: model()
          }, {replace: true});
        }
      } catch (err) {
        errorTarget.textContent = err.message;
      }
    }
    async function addToBlendExtra(id) {
      if (!extraBlendIds.includes(id)) {
        extraBlendIds.push(id);
        saveExtraBlendIds();
      }
      renderLikedExtraSummary();
      showSection("navidromeLikes");
      if (likedCatalog && likedReadyTrackIds().length) await refreshLikedRecommendations();
    }
    function clearLikedBlend() {
      likedCatalog = null;
      extraBlendIds = [];
      saveExtraBlendIds();
      document.getElementById("likedStatusError").textContent = "";
      document.getElementById("likedTracksList").innerHTML = `<div class="meta">Load Navidrome likes to see starred tracks here.</div>`;
      document.getElementById("likedSimilarList").innerHTML = "";
      document.getElementById("likedExtraSearchResults").innerHTML = "";
      document.getElementById("likedSimilarSubtitle").textContent = "Recommendations from liked blend";
      renderLikedExtraSummary();
      renderLikedStatus();
    }
    async function searchLikedExtra() {
      const q = document.getElementById("likedExtraQuery").value;
      const showMissing = document.getElementById("likedExtraShowMissing").checked;
      const embeddingStatus = showMissing ? "all" : "ready";
      const target = document.getElementById("likedExtraSearchResults");
      const errorTarget = document.getElementById("likedStatusError");
      target.innerHTML = `<div class="meta">Searching...</div>`;
      try {
        const data = await json(`/tracks?query=${encodeURIComponent(q)}&limit=30&embedding_status=${embeddingStatus}&model=${encodeURIComponent(model())}`);
        if (!data.results.length) {
          target.innerHTML = `<div class="meta">${showMissing ? "No tracks found." : "No ready tracks found. Enable 'Show tracks without embeddings' or analyze missing tracks."}</div>`;
          return;
        }
        target.innerHTML = data.results.map(t => renderTrack(t, "blend")).join("");
      } catch (err) {
        errorTarget.textContent = err.message;
        target.innerHTML = "";
      }
    }
    function metricFilterFromInputs(name) {
      const summary = metricSummaries.find(item => item.name === name);
      const filter = {name};
      if (summary && summary.value_count) {
        const min = document.getElementById(`metricMin_${name}`)?.value;
        const max = document.getElementById(`metricMax_${name}`)?.value;
        if (min !== "") filter.min_value = Number(min);
        if (max !== "") filter.max_value = Number(max);
      }
      const checked = Array.from(document.querySelectorAll(`[data-metric-value="${name}"]:checked`)).map(input => input.value);
      if (checked.length) filter.text_values = checked;
      return filter;
    }
    function metricsSource() {
      return document.getElementById("metricsSource").value;
    }
    function syncMetricsSourceControls() {
      document.getElementById("metricsExtractor").disabled = metricsSource() === "heads";
    }
    function metricRangeBounds(summary) {
      const min = metricsSource() === "heads" ? 0 : Number(summary.min_value ?? 0);
      const max = metricsSource() === "heads" ? Number(summary.max_value ?? 1) : Number(summary.max_value ?? 1);
      const span = Math.max(max - min, 0.0001);
      const step = metricsSource() === "heads" ? 0.01 : Math.max(span / 200, 0.01);
      return {min, max, step};
    }
    function metricInitialRange(summary) {
      const bounds = metricRangeBounds(summary);
      if (metricsSource() === "heads") {
        return {min: Math.min(0.5, bounds.max), max: bounds.max};
      }
      return {min: bounds.min, max: bounds.max};
    }
    function updateMetricRangeTrack(name) {
      const summary = metricSummaries.find(item => item.name === name);
      if (!summary) return;
      const bounds = metricRangeBounds(summary);
      const minInput = document.getElementById(`metricMin_${name}`);
      const maxInput = document.getElementById(`metricMax_${name}`);
      const track = document.getElementById(`metricTrack_${name}`);
      if (!minInput || !maxInput || !track) return;
      const left = ((Number(minInput.value) - bounds.min) / (bounds.max - bounds.min || 1)) * 100;
      const right = ((Number(maxInput.value) - bounds.min) / (bounds.max - bounds.min || 1)) * 100;
      track.style.background = `linear-gradient(to right, var(--line) 0%, var(--line) ${left}%, var(--accent) ${left}%, var(--accent) ${right}%, var(--line) ${right}%, var(--line) 100%)`;
    }
    function syncMetricRange(encodedName, side, rawValue, {runSearch = false} = {}) {
      const name = decodeURIComponent(encodedName);
      const summary = metricSummaries.find(item => item.name === name);
      if (!summary) return;
      const bounds = metricRangeBounds(summary);
      const minInput = document.getElementById(`metricMin_${name}`);
      const maxInput = document.getElementById(`metricMax_${name}`);
      const minRange = document.getElementById(`metricMinRange_${name}`);
      const maxRange = document.getElementById(`metricMaxRange_${name}`);
      let minValue = minInput.value === "" ? bounds.min : Number(minInput.value);
      let maxValue = maxInput.value === "" ? bounds.max : Number(maxInput.value);
      if (side === "min") minValue = Number(rawValue);
      if (side === "max") maxValue = Number(rawValue);
      if (minValue > maxValue) {
        if (side === "min") maxValue = minValue;
        else minValue = maxValue;
      }
      minInput.value = Number(minValue.toFixed(4));
      maxInput.value = Number(maxValue.toFixed(4));
      minRange.value = minInput.value;
      maxRange.value = maxInput.value;
      updateMetricRangeTrack(name);
      if (runSearch) searchMetrics();
    }
    function activeMetricFilters() {
      return Object.keys(metricFilters)
        .filter(name => metricFilters[name])
        .map(metricFilterFromInputs)
        .filter(filter => filter.name);
    }
    function renderMetricSummary(features) {
      const target = document.getElementById("metricsSummary");
      target.innerHTML = features.map(item => {
        const isHeads = metricsSource() === "heads";
        const range = isHeads
          ? `labels load on select`
          : (item.value_count
            ? `${Number(item.min_value).toFixed(2)}-${Number(item.max_value).toFixed(2)}${item.unit ? ` ${text(item.unit)}` : ""}`
            : `${item.text_count} text values`);
        return `<div class="metric-card ${metricFilters[item.name] ? "active" : ""}" onclick="toggleMetricFilter('${encodedArg(item.name)}')">
          <strong>${text(item.name)}</strong>
          <div class="meta">${range}</div>
          <div class="meta">${item.track_count} tracks</div>
        </div>`;
      }).join("") || `<div class="meta">No metrics yet. Run audio feature analysis first.</div>`;
      const sort = document.getElementById("metricsSort");
      const current = sort.value;
      sort.innerHTML = `<option value="">artist/title</option>` + features
        .filter(item => item.value_count)
        .map(item => `<option value="${text(item.name)}">${text(item.name)}</option>`)
        .join("");
      if (current && features.some(item => item.name === current)) sort.value = current;
    }
    async function renderMetricFilters({signal = null} = {}) {
      const target = document.getElementById("metricFilterList");
      const names = Object.keys(metricFilters).filter(name => metricFilters[name]);
      if (!names.length) {
        target.innerHTML = `<div class="meta">Select metrics above to build filters.</div>`;
        return;
      }
      const valueLists = new Map();
      await Promise.all(names.map(async name => {
        const summary = metricSummaries.find(item => item.name === name);
        if (!summary || !summary.text_count) return;
        const values = await json(`/metrics/features/${encodeURIComponent(name)}/values?source=${encodeURIComponent(metricsSource())}&extractor=${encodeURIComponent(document.getElementById("metricsExtractor").value)}`, {signal, timeoutMs: 30000});
        valueLists.set(name, values.values || []);
      }));
      const blocks = [];
      for (const name of names) {
        const summary = metricSummaries.find(item => item.name === name);
        if (!summary) continue;
        let controls = "";
        if (summary.value_count) {
          const bounds = metricRangeBounds(summary);
          const initial = metricInitialRange(summary);
          const initialMin = Number(initial.min.toFixed(4));
          const initialMax = Number(initial.max.toFixed(4));
          controls += `<div class="range-pair">
            <div class="range-values">
              <input id="metricMin_${text(name)}" type="number" step="${bounds.step}" min="${bounds.min}" max="${bounds.max}" value="${initialMin}" onchange="syncMetricRange('${encodedArg(name)}', 'min', this.value, {runSearch: true})">
              <input id="metricMax_${text(name)}" type="number" step="${bounds.step}" min="${bounds.min}" max="${bounds.max}" value="${initialMax}" onchange="syncMetricRange('${encodedArg(name)}', 'max', this.value, {runSearch: true})">
            </div>
            <div class="range-slider">
              <div id="metricTrack_${text(name)}" class="range-track"></div>
              <input id="metricMinRange_${text(name)}" type="range" min="${bounds.min}" max="${bounds.max}" step="${bounds.step}" value="${initialMin}" oninput="syncMetricRange('${encodedArg(name)}', 'min', this.value)" onchange="syncMetricRange('${encodedArg(name)}', 'min', this.value, {runSearch: true})">
              <input id="metricMaxRange_${text(name)}" type="range" min="${bounds.min}" max="${bounds.max}" step="${bounds.step}" value="${initialMax}" oninput="syncMetricRange('${encodedArg(name)}', 'max', this.value)" onchange="syncMetricRange('${encodedArg(name)}', 'max', this.value, {runSearch: true})">
            </div>
          </div>`;
        }
        if (summary.text_count) {
          const values = valueLists.get(name) || [];
          controls += `<div class="metric-value-list">${values.map(item => `
            <label><input type="checkbox" data-metric-value="${text(name)}" value="${text(item.value)}" onchange="searchMetrics()"><span>${text(item.value)} (${item.track_count})</span></label>
          `).join("")}</div>`;
        }
        blocks.push(`<div class="metric-filter">
          <div class="row" style="justify-content:space-between">
            <strong>${text(name)}</strong>
            <button onclick="toggleMetricFilter('${encodedArg(name)}')">Remove</button>
          </div>
          ${controls}
        </div>`);
      }
      target.innerHTML = blocks.join("");
      names.forEach(updateMetricRangeTrack);
    }
    async function loadMetricsExplorer({updateUrl = true} = {}) {
      syncMetricsSourceControls();
      if (metricsLoadController) metricsLoadController.abort();
      metricsLoadController = new AbortController();
      const signal = metricsLoadController.signal;
      const seq = ++metricsLoadSeq;
      const extractor = document.getElementById("metricsExtractor").value;
      document.getElementById("metricsSummary").innerHTML = `<div class="meta">Loading metrics...</div>`;
      document.getElementById("metricFilterList").innerHTML = `<div class="meta">Loading filters...</div>`;
      try {
      const data = await json(`/metrics/features?source=${encodeURIComponent(metricsSource())}&extractor=${encodeURIComponent(extractor)}`, {signal, timeoutMs: 30000});
      if (seq !== metricsLoadSeq || signal.aborted) return;
      metricSummaries = data.features || [];
      const known = new Set(metricSummaries.map(item => item.name));
      Object.keys(metricFilters).forEach(name => {
        if (!known.has(name)) delete metricFilters[name];
      });
      renderMetricSummary(metricSummaries);
      await renderMetricFilters({signal});
      if (seq !== metricsLoadSeq || signal.aborted) return;
      await searchMetrics({updateUrl});
      } catch (err) {
        if (seq !== metricsLoadSeq || signal.aborted) return;
        document.getElementById("metricsSummary").innerHTML = `<div class="meta">${text(err.message)}</div>`;
        document.getElementById("metricFilterList").innerHTML = `<div class="meta">Metrics failed to load. Try another source or refresh.</div>`;
        document.getElementById("metricsResults").innerHTML = "";
      }
    }
    async function toggleMetricFilter(encodedName) {
      const name = decodeURIComponent(encodedName);
      metricFilters[name] = !metricFilters[name];
      if (metricsLoadController) metricsLoadController.abort();
      metricsLoadController = new AbortController();
      const signal = metricsLoadController.signal;
      renderMetricSummary(metricSummaries);
      try {
        await renderMetricFilters({signal});
      } catch (err) {
        if (signal.aborted) return;
        document.getElementById("metricFilterList").innerHTML = `<div class="meta">${text(err.message)}</div>`;
      }
      await searchMetrics();
    }
    function clearMetricFilters() {
      metricFilters = {};
      if (metricsLoadController) metricsLoadController.abort();
      document.getElementById("metricsQuery").value = "";
      document.getElementById("metricsSort").value = "";
      renderMetricSummary(metricSummaries);
      renderMetricFilters();
      searchMetrics();
    }
    function relevantMetricFeatures(features) {
      const active = activeMetricFilters();
      const activeNames = new Set(active.map(filter => filter.name));
      const selectedLabels = new Map(active.map(filter => [filter.name, new Set(filter.text_values || [])]));
      let relevant = (features || []).filter(feature => {
        if (!activeNames.size) return false;
        if (!activeNames.has(feature.name)) return false;
        const labels = selectedLabels.get(feature.name);
        return !labels || !labels.size || labels.has(feature.text_value);
      });
      if (!relevant.length) {
        const sortBy = document.getElementById("metricsSort").value;
        if (sortBy) relevant = (features || []).filter(feature => feature.name === sortBy).slice(0, 3);
      }
      return relevant.slice(0, 8);
    }
    function renderFeatureChips(features) {
      const relevant = relevantMetricFeatures(features);
      if (!relevant.length) return "";
      return `<div class="feature-chips">${relevant.map(feature => {
        const value = feature.value !== null && feature.value !== undefined
          ? `${Number(feature.value).toFixed(2)}${feature.unit ? ` ${text(feature.unit)}` : ""}`
          : text(feature.text_value);
        return `<span class="feature-chip">${text(feature.name)}: ${value}</span>`;
      }).join("")}</div>`;
    }
    async function searchMetrics({updateUrl = true} = {}) {
      if (metricsSearchController) metricsSearchController.abort();
      metricsSearchController = new AbortController();
      const signal = metricsSearchController.signal;
      const seq = ++metricsSearchSeq;
      const query = document.getElementById("metricsQuery").value;
      document.getElementById("metricsResultCount").textContent = "searching";
      document.getElementById("metricsResults").innerHTML = `<div class="meta">Searching...</div>`;
      try {
      const data = await json("/metrics/search", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        signal,
        timeoutMs: 20000,
        body: JSON.stringify({
          source: metricsSource(),
          extractor: document.getElementById("metricsExtractor").value,
          query,
          filters: activeMetricFilters(),
          sort_by: document.getElementById("metricsSort").value || null,
          sort_direction: metricsSource() === "heads" ? "desc" : "asc",
          limit: Number(document.getElementById("metricsLimit").value || 50)
        })
      });
      if (seq !== metricsSearchSeq || signal.aborted) return;
      document.getElementById("metricsResultCount").textContent = `${data.count} tracks`;
      document.getElementById("metricsResults").innerHTML = data.results.map(t => `
        <div class="track ${t.id === activeTrackId ? "active-track" : ""}">
          <div class="track-body">
            ${coverMarkup(t)}
            <div class="track-main">
              <div class="row" style="justify-content:space-between">
                <div class="title">#${t.id} ${label(t)}</div>
                <div class="row">
                  <button onclick="playTrack(${t.id}, '${encodedArg(label(t))}')">Play</button>
                  <button onclick="setSeed(${t.id})">Seed</button>
                </div>
              </div>
              <div class="meta">${[text(t.genre), t.year || "", text(t.album)].filter(Boolean).join(" / ")}</div>
              ${renderFeatureChips(t.features)}
              <div class="path" title="${t.path}">${t.path}</div>
            </div>
          </div>
        </div>`).join("") || `<div class="meta">No tracks match these metric filters.</div>`;
      if (updateUrl && !applyingRoute) {
        pushRouteOnly({view: "metrics", source: metricsSource(), query, model: model()}, {replace: true, reset: true});
      }
      } catch (err) {
        if (seq !== metricsSearchSeq || signal.aborted) return;
        document.getElementById("metricsResultCount").textContent = "search failed";
        document.getElementById("metricsResults").innerHTML = `<div class="meta">${text(err.message)}</div>`;
      }
    }
    async function setSeed(id) {
      routeTo({
        view: "recommendations",
        seed: String(id),
        model: model(),
        ...recommendationParams()
      }, {reset: true});
    }
    async function loadSimilar(id, {updateUrl = false} = {}) {
      if (!id) return;
      const k = document.getElementById("k").value;
      const max = document.getElementById("maxPerArtist").value;
      const exclude = document.getElementById("excludeSameAlbum").checked;
      const data = await json(`/tracks/${id}/similar?model=${encodeURIComponent(model())}&k=${k}&max_per_artist=${max}&exclude_same_album=${exclude}`);
      currentSimilarTracks = data.results.map(t => ({id: t.id, label: label(t)}));
      const html = data.results.map(t => renderRecommendationTrack(t, {seedTrackId: id})).join("");
      document.getElementById("similarList").innerHTML = html;
      document.getElementById("evaluationSimilarList").innerHTML = html;
      if (updateUrl && !applyingRoute && paramsFromSearch().view === "recommendations" && seedId) {
        syncRecommendationRoute();
      }
    }
    function setTextSearchQuery(query) {
      document.getElementById("textSearchQuery").value = decodeURIComponent(query);
      saveSettings();
    }
    function renderTextSearchRecent() {
      const target = document.getElementById("textSearchRecent");
      if (!target) return;
      target.innerHTML = textSearchRecentQueries.map(query => `
        <div class="track">
          <div class="track-body">
            <div class="track-main">
              <div class="title">${esc(query)}</div>
            </div>
          </div>
          <div class="track-actions">
            <button onclick="setTextSearchQuery('${encodedArg(query)}'); runTextSearch()">Run</button>
          </div>
        </div>
      `).join("") || `<div class="meta">No recent text searches yet.</div>`;
    }
    function rememberTextSearchQuery(query) {
      textSearchRecentQueries = [query, ...textSearchRecentQueries.filter(item => item !== query)].slice(0, 10);
      renderTextSearchRecent();
    }
    function clearTextSearch() {
      document.getElementById("textSearchQuery").value = "";
      document.getElementById("textSearchResults").innerHTML = `<div class="meta">Describe the music you want to find.</div>`;
      document.getElementById("textSearchStatus").textContent = "Model: muq_mulan";
      pushRouteOnly({view: "textSearch", model: model()}, {replace: true, reset: true});
      saveSettings();
    }
    async function runTextSearch({updateUrl = true} = {}) {
      const query = document.getElementById("textSearchQuery").value.trim();
      const target = document.getElementById("textSearchResults");
      const status = document.getElementById("textSearchStatus");
      if (!query) {
        target.innerHTML = `<div class="error">Enter a text query.</div>`;
        return;
      }
      const minSimilarityRaw = document.getElementById("textSearchMinSimilarity").value;
      const payload = {
        query,
        count: Number(document.getElementById("textSearchCount").value || 50),
        min_similarity: minSimilarityRaw === "" ? null : Number(minSimilarityRaw),
        max_per_artist: Number(document.getElementById("textSearchMaxPerArtist").value || 2),
        exclude_same_album: document.getElementById("textSearchExcludeSameAlbum").checked,
      };
      status.textContent = "Searching muq_mulan...";
      target.innerHTML = `<div class="meta">Searching...</div>`;
      try {
        const started = performance.now();
        const data = await json("/text-search", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
          timeoutMs: 120000,
        });
        const seconds = ((performance.now() - started) / 1000).toFixed(1);
        rememberTextSearchQuery(query);
        currentSimilarTracks = data.results.map(t => ({id: t.id, label: label(t)}));
        target.innerHTML = data.results.map((track, index) => (
          renderRecommendationTrack(track, {rank: index, allowRating: false})
        )).join("") || `<div class="meta">No tracks matched this query.</div>`;
        const simRange = data.similarity_min === null || data.similarity_min === undefined
          ? "similarity n/a"
          : `similarity ${formatScore(data.similarity_min)}-${formatScore(data.similarity_max)} avg ${formatScore(data.similarity_avg)}`;
        status.textContent = `Model: ${data.model} - returned ${data.results.length}/${payload.count} in ${seconds}s - ${simRange} - norm ${formatScore(data.vector_norm)}`;
        if (updateUrl && !applyingRoute) {
          pushRouteOnly({view: "textSearch", model: model(), ...textSearchParams()}, {replace: true, reset: true});
        }
      } catch (err) {
        status.textContent = "Text search failed";
        target.innerHTML = `<div class="error">${esc(err.message)}</div>`;
      }
    }
    async function playTrack(id, encodedLabel, {queueItemId = null, recordStarted = true} = {}) {
      activeTrackId = id;
      activeQueueItemId = queueItemId || activeQueueItemId;
      progressEventSent = false;
      document.getElementById("playerError").textContent = "";
      document.getElementById("nowPlaying").textContent = decodeURIComponent(encodedLabel);
      const player = document.getElementById("audioPlayer");
      player.pause();
      player.removeAttribute("src");
      player.load();
      player.src = `/tracks/${id}/audio?player=${encodeURIComponent(UI_BUILD_ID)}`;
      applyPlayerVolume();
      player.load();
      renderPlayerState();
      try {
        await player.play();
        renderPlaybackButtons();
        if (recordStarted) recordPlaybackEvent("track_started");
      } catch (err) {
        document.getElementById("playerError").textContent = await playbackErrorMessage(err);
        renderPlaybackButtons();
      }
      refreshPlaybackSurfaces();
    }
    async function refreshSimilarTracks() {
      if (seedId) await loadSimilar(seedId, {updateUrl: true});
    }
    async function playNextSimilarTrack() {
      if (activePlaybackQueue?.items?.length) {
        if (activeShuffleEnabled() && await playRandomQueueItem()) return;
        await playQueueOffset(1);
        return;
      }
      const index = currentSimilarTracks.findIndex(track => track.id === activeTrackId);
      if (activeShuffleEnabled()) {
        const candidates = currentSimilarTracks.filter(track => track.id !== activeTrackId);
        if (!candidates.length) return;
        const next = candidates[Math.floor(Math.random() * candidates.length)];
        await playTrack(next.id, encodedArg(next.label));
        return;
      }
      if (index < 0 || index >= currentSimilarTracks.length - 1) return;
      const next = currentSimilarTracks[index + 1];
      await playTrack(next.id, encodedArg(next.label));
    }
    async function rate(resultId, rating) {
      if (!seedId) return;
      await rateForSeed(seedId, resultId, rating);
    }
    async function rateForSeed(seedTrackId, resultId, rating) {
      if (!seedTrackId) return;
      await json("/feedback", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({seed_track_id: seedTrackId, result_track_id: resultId, model: model(), rating})
      });
      if (seedId && Number(seedTrackId) === Number(seedId)) await loadSimilar(seedId);
      if (currentInstantMixRequestId && paramsFromSearch().view === "instantMix") {
        await loadInstantMixRequestDetail(currentInstantMixRequestId, {updateUrl: false});
      }
    }
    async function startAnalyze(modelName = model()) {
      const rawLimit = document.getElementById("limit").value;
      const rawWorkers = document.getElementById("workers").value;
      const rawTfThreads = document.getElementById("tfThreads").value;
      const executionMode = document.getElementById("analyzeExecutionMode").value;
      const parsedLimit = rawLimit ? Number(rawLimit) : null;
      await json("/jobs/analyze", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          model: modelName,
          limit: parsedLimit && parsedLimit > 0 ? parsedLimit : null,
          workers: rawWorkers ? Number(rawWorkers) : 4,
          tf_threads: rawTfThreads ? Number(rawTfThreads) : 4,
          execution_mode: executionMode,
          local_executor_enabled: executionMode !== "remote"
        })
      });
      await refreshJobs();
    }
    async function downloadHeadModels() {
      await json("/jobs/download-head-models", {method: "POST"});
      await refreshJobs();
    }
    async function startAnalyzeHeads() {
      const rawLimit = document.getElementById("limit").value;
      const executionMode = document.getElementById("analyzeExecutionMode").value;
      const parsedLimit = rawLimit ? Number(rawLimit) : null;
      await json("/jobs/analyze-heads", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          limit: parsedLimit && parsedLimit > 0 ? parsedLimit : null,
          execution_mode: executionMode,
          local_executor_enabled: executionMode !== "remote"
        })
      });
      await refreshJobs();
    }
    async function startAnalyzeAudioFeatures() {
      const rawLimit = document.getElementById("limit").value;
      const rawWorkers = document.getElementById("audioFeatureWorkers").value;
      const executionMode = document.getElementById("analyzeExecutionMode").value;
      const parsedLimit = rawLimit ? Number(rawLimit) : null;
      await json("/jobs/analyze-audio-features", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          limit: parsedLimit && parsedLimit > 0 ? parsedLimit : null,
          workers: rawWorkers ? Number(rawWorkers) : 4,
          execution_mode: executionMode,
          local_executor_enabled: executionMode !== "remote"
        })
      });
      await refreshJobs();
    }
    async function rescanAudioFeatures() {
      const rawLimit = document.getElementById("limit").value;
      const rawWorkers = document.getElementById("audioFeatureWorkers").value;
      const executionMode = document.getElementById("analyzeExecutionMode").value;
      const extractor = audioFeaturesExtractor();
      const parsedLimit = rawLimit ? Number(rawLimit) : null;
      const limit = parsedLimit && parsedLimit > 0 ? parsedLimit : null;
      const scope = limit ? `${limit} active track(s)` : "all active tracks";
      const statusTarget = document.getElementById("audioFeaturesRescanStatus");
      if (!confirm(`Delete existing ${extractor} features for ${scope} and queue them for re-analysis?`)) return;
      try {
        const data = await json("/jobs/analyze-audio-features", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            limit,
            workers: rawWorkers ? Number(rawWorkers) : 4,
            execution_mode: executionMode,
            local_executor_enabled: executionMode !== "remote",
            reset_existing: true,
            extractor
          })
        });
        if (statusTarget) {
          statusTarget.textContent =
            `Queued rescan for ${extractor}. Deleted ${data.deleted_features || 0} stored feature row(s).`;
        }
        await refreshStats();
        await loadAudioFeaturesSettings();
        await refreshJobs();
      } catch (err) {
        if (statusTarget) statusTarget.textContent = `Rescan failed: ${err.message}`;
      }
    }
    async function startIndex(modelName = model()) {
      await json("/jobs/index", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({model: modelName})
      });
      await refreshJobs();
    }
    async function cancelJob(jobId) {
      if (!confirm("Cancel this job? Queued and leased tasks will be marked cancelled.")) return;
      await json(`/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({reason: "Cancelled from web UI"})
      });
      await refreshJobs();
    }
    async function loadJobDetail(jobId, {updateUrl = true} = {}) {
      const data = await json(`/jobs/${encodeURIComponent(jobId)}`);
      const tasks = data.tasks || [];
      const job = data.job || {};
      const taskHtml = tasks.length ? tasks.map(task => `
        <div class="job status-${text(task.status)}">
          <div class="row" style="justify-content:space-between">
            <strong>${text(task.status)} · ${text(task.stage || "unknown")}</strong>
            <span class="pill">${text(task.model_name || "")}</span>
          </div>
          <div class="meta">${text(task.path || "")}</div>
          <div class="meta">task ${text(task.task_id || "")}, track ${text(task.track_id || "")}, attempts ${task.attempts || 0}/${task.max_attempts || 0}</div>
          <div class="meta">worker ${text(task.lease_owner || "")}${task.lease_expires_at ? `, lease expires ${text(task.lease_expires_at)}` : ""}</div>
          ${task.error ? `<div class="meta">error: ${text(task.error_type || "Error")} - ${text(task.error)}</div>` : ""}
        </div>`).join("") : `<div class="meta">No queued, leased, retryable, or final-failed tasks for this job.</div>`;
      document.getElementById("jobDetail").innerHTML = `
        <div class="job status-${text(job.status || "")}">
          <div class="row" style="justify-content:space-between">
            <strong>${text(job.kind || "job")}</strong>
            <span class="pill">${text(job.status || "")}</span>
          </div>
          <div class="meta">${text(job.message || "")}</div>
          <div class="meta">done ${job.done || 0}/${job.total || 0}, queued ${job.queued || 0}, leased ${job.leased || 0}, failed ${job.failed || 0}</div>
        </div>
        ${taskHtml}`;
      activateSection("jobsPage");
      if (updateUrl && !applyingRoute) {
        pushRouteOnly({view: "jobs", job: jobId, model: model()}, {reset: true});
      }
    }
    function renderWorkers(workers) {
      const workerList = document.getElementById("workersList");
      const workerSummary = document.getElementById("workersSummary");
      if (!workerList || !workerSummary) return;
      const connectedWorkers = workers.filter(worker => worker.connected);
      const staleWorkers = workers.filter(worker => !worker.connected);
      const available = connectedWorkers.filter(worker => !worker.current_task_id).length;
      const claimed = connectedWorkers.reduce((sum, worker) => sum + Number(worker.claimed_count || 0), 0);
      const completed = connectedWorkers.reduce((sum, worker) => sum + Number(worker.completed_count || 0), 0);
      const failed = connectedWorkers.reduce((sum, worker) => sum + Number(worker.failed_count || 0), 0);
      workerSummary.innerHTML = `
        <div class="stat"><strong>${connectedWorkers.length}</strong><span>connected</span></div>
        <div class="stat"><strong>${available}</strong><span>available</span></div>
        <div class="stat"><strong>${claimed}</strong><span>claimed tasks</span></div>
        <div class="stat"><strong>${completed}</strong><span>completed tasks</span></div>
        <div class="stat"><strong>${failed}</strong><span>failed tasks</span></div>
        <div class="stat"><strong>${staleWorkers.length}</strong><span>stale hidden</span></div>`;
      const workerHtml = connectedWorkers.map(worker => {
        const models = (worker.models || []).length ? (worker.models || []).join(", ") : "no advertised models";
        const stage = worker.stage || "idle";
        const status = worker.display_status || worker.status || "unknown";
        const lastSeen = worker.last_seen_at || "";
        const created = worker.created_at || "";
        const updated = worker.updated_at || "";
        return `<div class="job status-${text(status)}">
          <div class="row" style="justify-content:space-between">
            <strong>${text(worker.worker_id)}</strong><span class="pill">${text(status)}</span>
          </div>
          <div class="meta">models: ${text(models)}</div>
          <div class="meta">stage: ${text(stage)}${worker.message ? ` - ${text(worker.message)}` : ""}</div>
          <div class="meta">claimed ${worker.claimed_count || 0}, completed ${worker.completed_count || 0}, failed ${worker.failed_count || 0}, released ${worker.released_count || 0}</div>
          ${worker.current_task_id ? `<div class="meta">current task: ${text(worker.current_task_id)}</div>` : ""}
          <div class="meta">last seen: ${text(lastSeen)}${updated ? `, updated: ${text(updated)}` : ""}${created ? `, created: ${text(created)}` : ""}</div>
        </div>`;
      }).join("");
      const staleNote = staleWorkers.length
        ? `<div class="meta">${staleWorkers.length} stale worker record(s) hidden; TTL is ${workers[0]?.connected_ttl_seconds || 180}s.</div>`
        : "";
      workerList.innerHTML = workerHtml || `<div class="meta">No connected workers right now.</div>`;
      workerList.innerHTML += staleNote;
    }
    async function refreshWorkers() {
      try {
        const data = await json("/workers", {timeoutMs: 8000});
        renderWorkers(data.workers || []);
      } catch (err) {
        const workerList = document.getElementById("workersList");
        const workerSummary = document.getElementById("workersSummary");
        if (workerSummary) workerSummary.innerHTML = "";
        if (workerList) workerList.innerHTML = `<div class="meta">Workers unavailable: ${text(err.message)}</div>`;
      }
    }
    async function refreshJobs({history = false} = {}) {
      if (jobsInFlight) return;
      jobsInFlight = true;
      try {
      const jobsUrl = history ? "/jobs?include_completed=true&detail=true" : "/jobs";
      const data = await json(jobsUrl, {timeoutMs: 8000});
      lastJobs = data.jobs;
      const setDisabled = (id, disabled) => {
        const element = document.getElementById(id);
        if (element) element.disabled = disabled;
      };
      setDisabled("navidromeSyncBtn", false);
      setDisabled("analyzeHeadsBtn", false);
      setDisabled("downloadHeadsBtn", false);
      setDisabled("analyzeAudioFeaturesBtn", false);
      setDisabled("rescanAudioFeaturesBtn", false);
      setDisabled("checkMissingBtn", false);
      document.querySelectorAll(".dashboard-analyze-btn").forEach(button => {
        button.disabled = false;
      });
      document.querySelectorAll(".dashboard-index-btn").forEach(button => {
        button.disabled = false;
      });
      const html = data.jobs.map(job => {
        const total = job.total || 0;
        const percent = total ? Math.round(((job.done + job.failed) / total) * 100) : (job.status === "completed" ? 100 : 0);
        const waiting = job.status === "deferred";
        const terminal = !["queued", "running", "deferred"].includes(job.status);
        const elapsed = !waiting && job.elapsed_seconds ? `${formatDuration(job.elapsed_seconds)} ${terminal ? "duration" : "elapsed"}` : "";
        const recentWindow = job.recent_rate_window_seconds ? Math.round(job.recent_rate_window_seconds / 60) : 5;
        const currentRate = job.recent_tracks_per_min ? `current ${job.recent_tracks_per_min.toFixed(1)} tracks/min (${recentWindow}m)` : "";
        const avgRate = job.tracks_per_min ? `avg ${job.tracks_per_min.toFixed(1)} tracks/min` : "";
        const rate = [currentRate, avgRate].filter(Boolean).join(" / ");
        const eta = job.eta_seconds ? `${formatDuration(job.eta_seconds)} ETA` : "";
        const timing = [elapsed, rate, eta].filter(Boolean).join(" - ");
        const startedAt = job.created_at || (job.created_at_epoch ? new Date(job.created_at_epoch * 1000).toLocaleString() : "");
        const updatedAt = job.updated_at || "";
        const finishedAt = job.finished_at_iso || "";
        const workerLine = (job.leased_workers || []).length
          ? `workers: ${(job.leased_workers || []).map(item => `${text(item.worker_id)}(${item.count})`).join(", ")}`
          : "";
        const breakdown = (job.status_breakdown || []).map(item => `${text(item.status)}${item.stage ? `/${text(item.stage)}` : ""}: ${item.count}`).join(", ");
        const oldestLease = job.oldest_lease ? `oldest lease: ${text(job.oldest_lease.worker_id || "")}, ${formatDuration(job.oldest_lease_age || 0)}, ${text(job.oldest_lease.stage || "")}` : "";
        const canCancel = ["queued", "running", "deferred"].includes(job.status);
        const queueLabel = waiting && job.queue_position ? `#${job.queue_position} waiting` : job.status;
        return `<div class="job status-${job.status}">
          <div class="row" style="justify-content:space-between">
            <strong>${job.kind}</strong>
            <div class="row">
              <button onclick="loadJobDetail('${job.id}')">Details</button>
              ${canCancel ? `<button onclick="cancelJob('${job.id}')">Cancel</button>` : ""}
              <span class="pill">${queueLabel}</span>
            </div>
          </div>
          <div class="meta">${job.message}</div>
          <div class="meta">started: ${text(startedAt)}${updatedAt ? `, updated: ${text(updatedAt)}` : ""}${finishedAt ? `, finished: ${text(finishedAt)}` : ""}</div>
          ${job.status_hint ? `<div class="meta">${text(job.status_hint)}</div>` : ""}
          ${workerLine ? `<div class="meta">${workerLine}</div>` : ""}
          ${oldestLease ? `<div class="meta">${oldestLease}</div>` : ""}
          ${breakdown ? `<div class="meta">breakdown: ${breakdown}</div>` : ""}
          ${job.last_error ? `<div class="meta">last error: ${text(job.last_error.error_type || "Error")} ${text(job.last_error.stage || "")} - ${text(job.last_error.error || "")}</div>` : ""}
          ${job.error_detail ? `<pre class="meta">${text(job.error_detail)}</pre>` : ""}
          ${job.current ? `<div class="meta">current: ${job.current}</div>` : ""}
          <div class="meta">done ${job.done}${total ? ` / ${total}` : ""}, queued ${job.queued || 0}, leased ${job.leased || 0}, failed ${job.failed || 0}</div>
          ${timing ? `<div class="meta">${timing}</div>` : ""}
          <div class="bar"><div class="fill" style="width:${percent}%"></div></div>
        </div>`;
      }).join("");
      if (history) {
        document.getElementById("jobs").innerHTML = html || `<div class="meta">No jobs yet</div>`;
      } else {
        document.getElementById("dashboardJobs").innerHTML = html || `<div class="meta">No active jobs</div>`;
        if (document.getElementById("jobsPage").classList.contains("active")) {
          document.getElementById("jobs").innerHTML = html || `<div class="meta">No active jobs</div>`;
        }
      }
      } catch (err) {
        const target = history ? document.getElementById("jobs") : document.getElementById("dashboardJobs");
        if (target) target.innerHTML = `<div class="meta">Jobs unavailable: ${text(err.message)}</div>`;
      } finally {
        jobsInFlight = false;
      }
    }
    async function refreshAll() {
      await refreshStats();
      await refreshJobs();
      await loadNavidromeSettings();
      await loadAudioFeaturesSettings();
      await fetchAndApplyNavidromeLikeIds({silent: true});
      await loadGeneratedMixSettings();
      await loadInstantMixSettings();
      renderSeedBasket();
      loadExtraBlendIds();
      renderLikedExtraSummary();
      renderLikedStatus();
      renderLikedTracks();
      await applyRoute(paramsFromSearch());
    }
    async function onModelChange() {
      saveSettings();
      syncModelRoute();
      refreshWorkerCommand();
      await refreshAll();
    }
    async function initFromUrl() {
      const params = paramsFromSearch();
      applySettingsFromParams(params);
      if (location.pathname === "/" && (!location.search || !new URLSearchParams(location.search).get("view"))) {
        await replaceRoute({view: "dashboard", model: model()}, {reset: true});
        await refreshStats();
        await refreshJobs();
        await loadNavidromeSettings();
        await loadAudioFeaturesSettings();
        await fetchAndApplyNavidromeLikeIds({silent: true});
        await loadGeneratedMixSettings();
        await loadInstantMixSettings();
        loadExtraBlendIds();
        renderLikedExtraSummary();
        return;
      }
      await applyRoute(params);
      await refreshStats();
      await refreshJobs();
      await loadNavidromeSettings();
      await loadAudioFeaturesSettings();
      await fetchAndApplyNavidromeLikeIds({silent: true});
      await loadGeneratedMixSettings();
      await loadInstantMixSettings();
      loadExtraBlendIds();
      renderLikedExtraSummary();
      renderLikedStatus();
    }
    document.getElementById("model").addEventListener("change", onModelChange);
    document.getElementById("query").addEventListener("keydown", event => {
      if (event.key === "Enter") searchTracks();
    });
    document.getElementById("listenerSearchQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") runListenerSearch();
    });
    document.getElementById("homeSearchQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") runHomeSearch();
    });
    document.getElementById("seedQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") searchSeeds();
    });
    document.getElementById("likedExtraQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") searchLikedExtra();
    });
    document.getElementById("likedLocalQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") renderLikedTracks();
    });
    document.getElementById("browseQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") refreshBrowse();
    });
    document.getElementById("metricsQuery").addEventListener("keydown", event => {
      if (event.key === "Enter") searchMetrics();
    });
    document.getElementById("textSearchQuery").addEventListener("keydown", event => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) runTextSearch();
    });
    document.getElementById("k").addEventListener("change", syncRecommendationRoute);
    document.getElementById("maxPerArtist").addEventListener("change", syncRecommendationRoute);
    document.getElementById("excludeSameAlbum").addEventListener("change", syncRecommendationRoute);
    window.addEventListener("popstate", () => {
      applyRoute(paramsFromSearch());
    });
    document.getElementById("audioPlayer").addEventListener("error", () => {
      document.getElementById("playerError").textContent = "file not mounted";
      renderPlaybackButtons();
    });
    document.getElementById("audioPlayer").addEventListener("play", renderPlaybackButtons);
    document.getElementById("audioPlayer").addEventListener("pause", renderPlaybackButtons);
    document.getElementById("audioPlayer").addEventListener("volumechange", renderPlaybackButtons);
    document.getElementById("audioPlayer").addEventListener("emptied", renderPlaybackButtons);
    document.getElementById("volumeSlider").addEventListener("input", event => {
      setPlayerVolume(event.currentTarget.value);
    });
    document.getElementById("audioPlayer").addEventListener("timeupdate", event => {
      const player = event.currentTarget;
      updatePlayerClock();
      if (!progressEventSent && Number.isFinite(player.currentTime) && player.currentTime >= 30) {
        progressEventSent = true;
        recordPlaybackEvent("play_threshold_reached", {
          position_seconds: player.currentTime,
          duration_seconds: Number.isFinite(player.duration) ? player.duration : null,
        });
      }
    });
    document.getElementById("audioPlayer").addEventListener("loadedmetadata", updatePlayerClock);
    document.getElementById("audioPlayer").addEventListener("ended", async event => {
      const player = event.currentTarget;
      recordPlaybackEvent("completed", {
        position_seconds: Number.isFinite(player.duration) ? player.duration : player.currentTime,
        duration_seconds: Number.isFinite(player.duration) ? player.duration : null,
        play_fraction: 1,
      });
      if (activeRepeatOneEnabled()) {
        player.currentTime = 0;
        player.play().catch(async err => {
          document.getElementById("playerError").textContent = await playbackErrorMessage(err);
          renderPlaybackButtons();
        });
        return;
      }
      await playNextSimilarTrack();
    });
    document.getElementById("playerSeek").addEventListener("input", event => {
      document.getElementById("playerSeekWrap").classList.add("scrubbing");
      seekPlayerToRangeValue(event.currentTarget.value);
    });
    document.getElementById("playerSeek").addEventListener("change", event => {
      seekPlayerToRangeValue(event.currentTarget.value);
      document.getElementById("playerSeekWrap").classList.remove("scrubbing");
    });
    document.getElementById("playerSeek").addEventListener("blur", () => {
      document.getElementById("playerSeekWrap").classList.remove("scrubbing");
    });
    loadSettings();
    loadPlayerState();
    bindSettingsAutosave();
    renderTextSearchRecent();
    refreshWorkerCommand();
    initFromUrl();
    setInterval(() => {
      if (document.hidden) return;
      refreshStats();
      refreshJobs();
    }, 5000);
  </script>
</body>
</html>
"""
