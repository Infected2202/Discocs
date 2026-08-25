"""Similarity lookup seeded by audio that is not in the catalog.

The Telegram bot posts a downloaded file here and gets back catalog tracks that
sound like it. Nothing about the submitted audio is persisted: the temporary
file is deleted before the response is returned and the query vector only ever
lives in memory. See docs/external-audio.md.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import context, instant_mix_settings
from app.recommender import Recommender
from app.schemas.responses import ExternalAudioSimilarResponse, NavidromeSimilarItem
from app.serializers.tracks import navidrome_similar_items
from app.services.external_audio import (
    ExternalAudioBusy,
    ExternalAudioError,
    extract_query_vector,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

DEFAULT_MAX_UPLOAD_MB = 200


def max_upload_bytes() -> int:
    raw = os.getenv("DISCOCS_EXTERNAL_AUDIO_MAX_MB")
    try:
        megabytes = int(raw) if raw else DEFAULT_MAX_UPLOAD_MB
    except ValueError:
        megabytes = DEFAULT_MAX_UPLOAD_MB
    return max(1, megabytes) * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _ExternalSimilarResult:
    items: list[NavidromeSimilarItem]
    skipped_without_external_id: int
    duration_seconds: float | None
    analyzed_seconds: float | None
    analysis_offset_seconds: float
    vector_cached: bool


async def _receive_body(request: Request, dest: Path, limit: int) -> int:
    total = 0
    with dest.open("wb") as handle:
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"Audio is larger than {limit // (1024 * 1024)} MB",
                )
            handle.write(chunk)
    return total


def _similar_for_file(
    path: Path,
    work_dir: Path,
    *,
    model: str,
    effective_count: int,
    min_similarity: float | None,
    max_per_artist: int,
    exclude_same_album: bool,
    count_collaboration_artists: bool,
) -> _ExternalSimilarResult:
    store, settings = context()
    analysis = extract_query_vector(settings, model, path, work_dir)
    candidates = Recommender(store, settings, model).similar_vector(
        analysis.vector,
        exclude_track_ids=set(),
        album_seeds=[],
        k=effective_count,
        max_per_artist=max_per_artist,
        exclude_same_album=exclude_same_album,
        count_collaboration_artists=count_collaboration_artists,
    )
    items, skipped = navidrome_similar_items(
        store,
        candidates,
        min_similarity=min_similarity,
        limit=effective_count,
    )
    return _ExternalSimilarResult(
        items=items,
        skipped_without_external_id=skipped,
        duration_seconds=analysis.duration_seconds,
        analyzed_seconds=analysis.analyzed_seconds,
        analysis_offset_seconds=analysis.analysis_offset_seconds,
        vector_cached=analysis.cached,
    )


@router.post(
    "/similar/by-audio",
    responses={
        400: {"description": "Body is empty or is not decodable audio"},
        413: {"description": "Audio is larger than the configured limit"},
        503: {"description": "Similar index missing, or analysis is busy"},
    },
)
async def similar_by_audio(request: Request) -> ExternalAudioSimilarResponse:
    """Catalog tracks similar to the posted audio body.

    Recommendation parameters come from the shared instant-mix settings, so an
    external seed produces the same kind of radio as a catalog seed.
    """
    started = perf_counter()
    request_id = str(uuid4())
    _store, settings = context()
    mix_settings = instant_mix_settings(settings)
    model = str(mix_settings["model"])
    effective_count = int(mix_settings["count"])
    raw_min_similarity = mix_settings["min_similarity"]
    min_similarity = None if raw_min_similarity is None else float(raw_min_similarity)

    limit = max_upload_bytes()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"Audio is larger than {limit // (1024 * 1024)} MB",
        )

    work_dir = settings.data_dir / "tmp" / "external"
    work_dir.mkdir(parents=True, exist_ok=True)
    temp_path = work_dir / f"{uuid4().hex}.audio"
    try:
        size = await _receive_body(request, temp_path, limit)
        if size == 0:
            raise HTTPException(status_code=400, detail="Request body is empty")
        logger.info(
            "External similar request request_id=%s bytes=%s model=%s effective_count=%s",
            request_id,
            size,
            model,
            effective_count,
        )
        try:
            result = await run_in_threadpool(
                _similar_for_file,
                temp_path,
                work_dir,
                model=model,
                effective_count=effective_count,
                min_similarity=min_similarity,
                max_per_artist=int(mix_settings["max_per_artist"]),
                exclude_same_album=bool(mix_settings["exclude_same_album"]),
                count_collaboration_artists=bool(mix_settings["count_collaboration_artists"]),
            )
        except ExternalAudioError as exc:
            logger.warning("External similar rejected request_id=%s error=%s", request_id, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ExternalAudioBusy as exc:
            logger.warning("External similar busy request_id=%s", request_id)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            logger.warning("External similar index missing request_id=%s error=%s", request_id, exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RuntimeError as exc:
            logger.warning("External similar embedding failed request_id=%s error=%s", request_id, exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)

    logger.info(
        "External similar completed request_id=%s model=%s results=%s skipped_without_external_id=%s "
        "duration=%s analyzed=%s cached=%s seconds=%.3f",
        request_id,
        model,
        len(result.items),
        result.skipped_without_external_id,
        result.duration_seconds,
        result.analyzed_seconds,
        result.vector_cached,
        perf_counter() - started,
    )
    return ExternalAudioSimilarResponse(
        request_id=request_id,
        model=model,
        effective_count=effective_count,
        min_similarity=min_similarity,
        duration_seconds=result.duration_seconds,
        analyzed_seconds=result.analyzed_seconds,
        analysis_offset_seconds=result.analysis_offset_seconds,
        vector_cached=result.vector_cached,
        skipped_without_external_id=result.skipped_without_external_id,
        results=result.items,
    )
