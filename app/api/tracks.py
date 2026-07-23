"""Tracks, browse, analysis, and search API routes.

Extracted from app/main.py — Stage 6d.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Annotated
from urllib.error import HTTPError
from urllib.request import Request as UrlRequest, urlopen

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.api.deps import context, text_search_embedder
from app.audio_source import navidrome_item_id_for_track
from app.config import MUQ_MULAN_MODEL, NavidromeSettings
from app.navidrome import NavidromeClient
from app.recommender import Recommender
from app.schemas.requests import (
    DeleteAnalysisErrorsRequest,
    DeleteTracksRequest,
    TextSearchRequest,
)
from app.serializers.tracks import (
    enriched_similar_track_dict,
    enriched_track_dict,
    enriched_track_listing_dict,
    feature_dict,
    prediction_dict,
)
from app.services.cover import (
    cached_cover_error,
    cached_cover_response,
    cover_response,
    remember_cover,
    remember_cover_error,
)
from app.state import COVER_TIMEOUT_SECONDS, MAX_MIX_SEEDS
from app.store import track_dict
from app.user_context import current_navidrome_credentials
from app.models import Track

logger = logging.getLogger(__name__)
navidrome_logger = logging.getLogger("discocs.navidrome")

router = APIRouter(prefix="/api/v1")

_EMBEDDING_STATUS_PATTERN = "^(all|ready|missing)$"
_TRACK_NOT_FOUND = "Track not found"


# ---------------------------------------------------------------------------
# Track listing / search / browse
# ---------------------------------------------------------------------------

@router.get("/tracks", responses={400: {"description": "Invalid track filters"}})
def list_tracks(
    query: str = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    embedding_status: Annotated[str, Query(pattern=_EMBEDDING_STATUS_PATTERN)] = "all",
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


@router.get("/tracks/search", responses={400: {"description": "Invalid track filters"}})
def search_tracks(
    q: str = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    embedding_status: Annotated[str, Query(pattern=_EMBEDDING_STATUS_PATTERN)] = "all",
    model: str = "discogs_multi",
) -> dict[str, object]:
    return list_tracks(
        query=q,
        limit=limit,
        embedding_status=embedding_status,
        model=model,
    )


@router.get("/browse/facets", responses={400: {"description": "Invalid browse facets filters"}})
def browse_facets(
    model: str = "discogs_multi",
    embedding_status: Annotated[str, Query(pattern=_EMBEDDING_STATUS_PATTERN)] = "all",
) -> dict[str, object]:
    store, _settings = context()
    try:
        return store.browser_facets(model_name=model, embedding_status=embedding_status)
    except ValueError as exc:
        logger.warning("Invalid browse facets filters model=%s embedding_status=%s error=%s", model, embedding_status, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Lost files / analysis errors
# ---------------------------------------------------------------------------

@router.get("/lost-files")
def list_lost_files(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
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


@router.delete("/lost-files")
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


@router.get("/analysis/errors")
def list_analysis_errors(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 50,
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


@router.delete("/analysis/errors")
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


# ---------------------------------------------------------------------------
# Individual track routes
# ---------------------------------------------------------------------------

@router.get("/tracks/{track_id}", responses={404: {"description": _TRACK_NOT_FOUND}})
def get_track(track_id: int) -> dict[str, object]:
    store, _settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Track not found track_id=%s", track_id)
        raise HTTPException(status_code=404, detail=_TRACK_NOT_FOUND)
    return enriched_track_dict(store, track)


@router.get("/tracks/{track_id}/analysis", responses={404: {"description": _TRACK_NOT_FOUND}})
def get_track_analysis(track_id: int) -> dict[str, object]:
    store, _settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Track analysis requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail=_TRACK_NOT_FOUND)
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


_TRACK_AUDIO_RESPONSES = {
    404: {"description": _TRACK_NOT_FOUND},
    410: {"description": "Audio file not mounted or no longer exists"},
    502: {"description": "Navidrome audio stream unavailable"},
}


@router.head("/tracks/{track_id}/audio", responses=_TRACK_AUDIO_RESPONSES)
@router.get("/tracks/{track_id}/audio", responses=_TRACK_AUDIO_RESPONSES)
def get_track_audio(
    track_id: int,
    request: Request,
    profile: Annotated[str | None, Query()] = None,
) -> Response:
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Audio requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail=_TRACK_NOT_FOUND)
    item_id = navidrome_item_id_for_track(store, track)
    if item_id is not None:
        stream_params, profile_key = playback_stream_profile(store)
        if profile is not None and profile != profile_key:
            raise HTTPException(status_code=409, detail="Playback profile changed")
        navidrome_settings = active_user_navidrome_settings(settings)
        try:
            response = navidrome_audio_stream_response(
                settings,
                item_id,
                range_header=request.headers.get("range"),
                method=request.method,
                navidrome_settings=navidrome_settings,
                stream_params=stream_params,
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


@router.get(
    "/tracks/{track_id}/cover",
    responses={
        404: {"description": "Track, Navidrome mapping, or cover art not found"},
        502: {"description": "Navidrome cover art unavailable"},
    },
)
def get_track_cover(track_id: int, size: Annotated[int, Query(ge=32, le=600)] = 96) -> Response:
    started = perf_counter()
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        logger.warning("Cover requested for missing track track_id=%s", track_id)
        raise HTTPException(status_code=404, detail=_TRACK_NOT_FOUND)
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
            track_id, external_id, cover_art_id, size, perf_counter() - started,
        )
        return cover_response(payload, content_type)
    cached_error = cached_cover_error(cache_key)
    if cached_error is not None:
        logger.info(
            "Cover negative cache hit track_id=%s item_id=%s cover_art_id=%s size=%s error=%s seconds=%.3f",
            track_id, external_id, cover_art_id, size, cached_error, perf_counter() - started,
        )
        raise HTTPException(status_code=502, detail=cached_error)
    logger.info(
        "Cover fetch started track_id=%s item_id=%s cover_art_id=%s size=%s timeout_seconds=%s",
        track_id, external_id, cover_art_id, size,
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
            track_id, external_id, cover_art_id, exc_info=True,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    remember_cover(cache_key, cover.payload, cover.content_type)
    logger.info(
        "Cover fetch completed track_id=%s item_id=%s cover_art_id=%s size=%s bytes=%s seconds=%.3f",
        track_id, external_id, cover_art_id, size, len(cover.payload), perf_counter() - started,
    )
    return cover_response(cover.payload, cover.content_type)


@router.get(
    "/tracks/{track_id}/similar",
    responses={404: {"description": _TRACK_NOT_FOUND}, 503: {"description": "Similar index missing"}},
)
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
        raise HTTPException(status_code=404, detail=_TRACK_NOT_FOUND)
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


# ---------------------------------------------------------------------------
# Text search + multi-seed mix
# ---------------------------------------------------------------------------

@router.post(
    "/text-search",
    responses={
        400: {"description": "Text query is empty"},
        503: {"description": "Text search embedding unavailable"},
    },
)
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
        query, len(results), len(filtered), perf_counter() - started,
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


@router.get(
    "/tracks/similar/mix",
    responses={
        400: {"description": "Invalid seed_ids parameter"},
        404: {"description": "One or more seed tracks not found"},
        503: {"description": "Similar mix index missing"},
    },
)
def get_similar_mix_tracks(
    seed_ids: Annotated[str, Query(description="Comma-separated track ids")],
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


# ---------------------------------------------------------------------------
# Audio streaming helpers
# ---------------------------------------------------------------------------

def navidrome_audio_stream_response(
    settings: object,
    item_id: str,
    *,
    range_header: str | None = None,
    method: str = "GET",
    navidrome_settings: NavidromeSettings | None = None,
    stream_params: dict[str, object] | None = None,
) -> StreamingResponse:
    navidrome_settings = navidrome_settings or settings.navidrome  # type: ignore[attr-defined]
    client = NavidromeClient(navidrome_settings)
    headers = {"Accept": "*/*"}
    if range_header:
        headers["Range"] = range_header
    method = "HEAD" if method.upper() == "HEAD" else "GET"
    request = UrlRequest(
        client.url("stream", {"id": item_id, **(stream_params or {})}),
        headers=headers,
        method=method,
    )
    try:
        upstream = urlopen(request, timeout=float(navidrome_settings.timeout_seconds))
    except HTTPError as exc:
        raise HTTPException(
            status_code=exc.code,
            detail=f"Navidrome stream unavailable: {exc.reason}",
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Navidrome stream unavailable: {exc}") from exc

    response_headers = navidrome_stream_headers(upstream.headers)
    if stream_params and stream_params.get("estimateContentLength"):
        # Navidrome's Content-Length for an on-the-fly transcode is an
        # estimate derived from bitrate * duration, not the real encoded
        # size. Forwarding it verbatim makes Starlette count bytes against a
        # declared length that the actual stream can fall short of, which it
        # treats as a broken response ("Response content shorter than
        # Content-Length") and aborts mid-stream — surfacing to clients as a
        # premature connection close. Dropping it here makes this a normal
        # chunked response instead.
        response_headers.pop("Content-Length", None)
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


def playback_stream_profile(store: object) -> tuple[dict[str, object], str]:
    user_settings = store.get_user_settings()  # type: ignore[attr-defined]
    enabled = bool(user_settings.get("transcoding_enabled", False))
    if not enabled:
        return {"format": "raw"}, "raw"
    bitrate = int(user_settings.get("transcoding_bitrate_kbps", 192))
    if bitrate not in {96, 128, 192, 256, 320}:
        bitrate = 192
    return (
        {
            "format": "mp3",
            "maxBitRate": bitrate,
            "estimateContentLength": "true",
        },
        f"mp3-{bitrate}",
    )


def active_user_navidrome_settings(settings: object) -> NavidromeSettings:
    navidrome_settings = settings.navidrome  # type: ignore[attr-defined]
    credentials = current_navidrome_credentials()
    if credentials is not None:
        return replace(
            navidrome_settings,
            user=credentials.username,
            password=credentials.password,
            auth_mode="token",
        )
    if settings.auth.enabled:  # type: ignore[attr-defined]
        raise HTTPException(
            status_code=401,
            detail="Active user Navidrome credentials are required; sign in again",
        )
    return navidrome_settings


def navidrome_stream_headers(headers: object) -> dict[str, str]:
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
        if (value := headers.get(name)) is not None  # type: ignore[attr-defined]
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
