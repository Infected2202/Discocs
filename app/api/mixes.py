"""Generated mixes and instant-mix API routes.

Extracted from app/main.py — Stage 6c.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from app.api.deps import (
    api_error,
    context,
    generated_mix_settings,
    instant_mix_settings,
    instant_mix_request_dict,
    playback_session_settings,
    record_instant_mix_request,
)
from app.config import load_runtime_settings, save_runtime_settings
from app.mixes import dashboard_mix_generation_plan, generate_mixes
from app.models import utc_now
from app.recommender import Recommender
from app.schemas.requests import GeneratedMixSettingsRequest, MixGenerateRequest
from app.schemas.responses import (
    NavidromeSimilarItem,
    PlaybackSessionEnvelopeResponse,
)
from app.serializers.mixes import generated_mix_detail_dict, generated_mix_summary_dict
from app.serializers.entities import _json_object
from app.serializers.playback import playback_session_response
from app.services.dashboard import ensure_dashboard_mixes_fast

logger = logging.getLogger(__name__)

router = APIRouter()

_GENERATED_MIX_NOT_FOUND = "Generated mix not found"


# ---------------------------------------------------------------------------
# Generated mixes
# ---------------------------------------------------------------------------

@router.get("/api/v1/mixes", response_model=None)
def api_v1_mixes(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
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


@router.get("/api/v1/mixes/settings", response_model=None)
def api_v1_mix_settings() -> dict[str, object]:
    _store, settings = context()
    return {"settings": generated_mix_settings(settings)}


@router.get("/api/v1/mixes/status", response_model=None)
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


@router.put("/api/v1/mixes/settings", response_model=None)
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


@router.get("/api/v1/mixes/{mix_id}", response_model=None)
def api_v1_mix_detail(mix_id: str, include_debug: bool = False) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    mix = store.get_generated_mix(mix_id)
    if mix is None:
        return api_error(404, "not_found", _GENERATED_MIX_NOT_FOUND)
    detail = generated_mix_detail_dict(store, mix)
    if not include_debug:
        detail.pop("score_summary", None)
        detail.pop("settings", None)
        for item in detail["items"]:
            item.pop("score_breakdown", None)
    return detail


@router.get("/api/v1/mixes/{mix_id}/cover", response_model=None)
def api_v1_mix_cover(mix_id: str) -> FileResponse | JSONResponse:
    store, _settings = context()
    mix = store.get_generated_mix(mix_id)
    if mix is None:
        return api_error(404, "not_found", _GENERATED_MIX_NOT_FOUND)
    if not mix.cover_path:
        return api_error(404, "not_found", "Generated mix has no cover")
    path = Path(mix.cover_path)
    if not path.exists() or not path.is_file():
        return api_error(404, "not_found", "Generated mix cover not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/api/v1/mixes/generate", response_model=None)
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


@router.post("/api/v1/mixes/{mix_id}/save", response_model=None)
def api_v1_save_mix(mix_id: str) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    mix = store.save_generated_mix_as_playlist(mix_id)
    if mix is None:
        return api_error(404, "not_found", _GENERATED_MIX_NOT_FOUND)
    return generated_mix_summary_dict(store, mix)


@router.post("/api/v1/mixes/{mix_id}/play", response_model=PlaybackSessionEnvelopeResponse)
def api_v1_play_mix(mix_id: str) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    mix = store.get_generated_mix(mix_id)
    if mix is None:
        return api_error(404, "not_found", _GENERATED_MIX_NOT_FOUND)
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


# ---------------------------------------------------------------------------
# Instant-mix history + track instant-mix creation
# ---------------------------------------------------------------------------

@router.get(
    "/instant-mix/requests",
    responses={503: {"description": "Instant mix history could not be read from SQLite"}},
)
def list_instant_mix_requests(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
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


@router.get(
    "/instant-mix/requests/{request_id}",
    responses={
        404: {"description": "Instant mix request not found"},
        503: {"description": "Instant mix request could not be read from SQLite"},
    },
)
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


@router.post("/tracks/{track_id}/instant-mix", responses={404: {"description": "Track not found"}})
def create_track_instant_mix(track_id: int, background_tasks: BackgroundTasks) -> dict[str, object]:
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

    # Create session immediately with only the seed track so the client can start playback
    session, _queue = store.create_playback_session(
        source_type="track",
        source_id=seed.id,
        source_label=f"Instant Mix: {seed.artist or ''} - {seed.title or Path(seed.path).stem}".strip(" -"),
        mode="radio",
        track_ids=[seed.id],
        autoplay_enabled=True,
        settings=playback_session_settings(
            {
                "instant_mix_request_id": request_id,
                "instant_mix_seed_track_id": seed.id,
                "instant_mix_model": model,
            }
        ),
        state={"instant_mix": True, "queue_loading": True},
    )
    session_id = session.id

    background_tasks.add_task(
        _fill_instant_mix_queue,
        settings=settings,
        session_id=session_id,
        seed_track_id=seed.id,
        seed_item_id=seed_item_id,
        request_id=request_id,
        model=model,
        effective_count=effective_count,
        min_similarity=min_similarity,
        effective_max_per_artist=effective_max_per_artist,
        effective_exclude_same_album=effective_exclude_same_album,
        effective_count_collaboration_artists=effective_count_collaboration_artists,
        started=started,
    )

    return {
        "request_id": request_id,
        "request": None,
        **playback_session_response(store, session),
    }


def _fill_instant_mix_queue(
    *,
    settings: object,
    session_id: str,
    seed_track_id: int,
    seed_item_id: str,
    request_id: str,
    model: str,
    effective_count: int,
    min_similarity: object,
    effective_max_per_artist: int,
    effective_exclude_same_album: bool,
    effective_count_collaboration_artists: bool,
    started: float,
) -> None:
    from app.config import Settings
    from app.store import Store
    from app.recommender import Recommender

    _settings = settings if isinstance(settings, Settings) else Settings.from_env()
    store = Store(_settings.db_path)
    store.init()

    seed = store.get_track(seed_track_id)
    if seed is None:
        logger.warning("Instant mix background: seed track missing track_id=%s", seed_track_id)
        return

    try:
        candidates = Recommender(store, _settings, model).similar(
            seed,
            k=effective_count,
            max_per_artist=effective_max_per_artist,
            exclude_same_album=effective_exclude_same_album,
            count_collaboration_artists=effective_count_collaboration_artists,
        )
    except (FileNotFoundError, LookupError) as exc:
        record_instant_mix_request(
            store,
            request_id=request_id,
            item_id=seed_item_id,
            seed_track_id=seed_track_id,
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
        logger.warning("Instant mix background failed track_id=%s model=%s error=%s", seed_track_id, model, exc)
        return

    sorted_candidates = sorted(candidates, key=lambda item: item.similarity, reverse=True)
    candidate_ids = [c.track.id for c in sorted_candidates]
    ext_ids = store.external_ids_for_tracks("navidrome", candidate_ids)

    results: list[NavidromeSimilarItem] = []
    for candidate in sorted_candidates:
        if min_similarity is not None and candidate.similarity < float(min_similarity):
            continue
        external_id = ext_ids.get(candidate.track.id)
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
        seed_track_id=seed_track_id,
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
    additional_ids = [tid for tid in result_track_ids if tid != seed_track_id]
    if not additional_ids:
        return

    items = [
        {
            "track_id": tid,
            "origin": "source",
            "source_type": "track",
            "reason": "Instant mix result",
            "score": None,
            "debug": {"request_id": request_id, "model": model},
        }
        for tid in additional_ids
    ]
    store.append_queue_items(session_id, items)
    logger.info(
        "Instant mix background filled session=%s tracks=%s duration_ms=%.0f",
        session_id,
        len(additional_ids),
        (perf_counter() - started) * 1000,
    )
