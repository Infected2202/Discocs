"""Navidrome starred/similar API routes.

Extracted from app/main.py — Stage 6c.
"""
from __future__ import annotations

from dataclasses import replace
import logging
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import (
    _navidrome_user_client,
    context,
    instant_mix_settings,
    record_instant_mix_request,
)
from app.models import InstantMixRequestParams, InstantMixRequestRecord
from app.logging_config import get_navidrome_plugin_logger
from app.navidrome import parse_song
from app.navidrome_starred import (
    build_starred_catalog,
    build_starred_track_ids_from_songs,
    ready_tracks_from_starred_catalog,
)
from app.recommender import Recommender
from app.schemas.requests import NavidromeStarRequest
from app.schemas.responses import NavidromeSimilarItem, NavidromeSimilarResponse
from app.serializers.tracks import enriched_similar_track_dict

navidrome_logger = logging.getLogger("discocs.navidrome")
navidrome_plugin_logger = get_navidrome_plugin_logger()

router = APIRouter(prefix="/api/v1")


@router.get("/navidrome/starred", responses={502: {"description": "Navidrome starred lookup failed"}})
def get_navidrome_starred(model: str = "discogs_multi") -> dict[str, object]:
    store, settings = context()
    client, username = _navidrome_user_client(settings)
    try:
        return build_starred_catalog(
            store,
            client,
            model=model,
            user=username,
        )
    except Exception as exc:
        navidrome_logger.warning("Navidrome starred failed model=%s error=%s", model, exc)
        raise HTTPException(status_code=502, detail=f"Navidrome starred failed: {exc}") from exc


@router.get("/navidrome/starred/ids", responses={502: {"description": "Navidrome starred lookup failed"}})
def get_navidrome_starred_ids() -> dict[str, object]:
    store, settings = context()
    client, username = _navidrome_user_client(settings)
    try:
        starred_full = client.get_starred_full()
        songs = [parse_song(raw) for raw in starred_full["songs"]]
        data = build_starred_track_ids_from_songs(
            store,
            songs,
            user=username,
        )
        store.sync_track_liked_from_navidrome(data["track_ids"])

        album_external_ids = [a.get("id", "") for a in starred_full["albums"] if a.get("id")]
        artist_external_ids = [a.get("id", "") for a in starred_full["artists"] if a.get("id")]

        album_ids: list[int] = []
        for ext_id in album_external_ids:
            entity_id = store.entity_id_for_external_id("navidrome", "release", ext_id)
            if entity_id is not None:
                album_ids.append(entity_id)

        artist_ids: list[int] = []
        for ext_id in artist_external_ids:
            entity_id = store.entity_id_for_external_id("navidrome", "artist", ext_id)
            if entity_id is not None:
                artist_ids.append(entity_id)

        data["album_ids"] = album_ids
        data["artist_ids"] = artist_ids

        store.sync_artist_liked_from_navidrome(artist_ids)

        navidrome_logger.info(
            "Navidrome starred ids user=%s count=%s mapped_count=%s track_ids=%s album_ids=%s artist_ids=%s",
            data.get("user"),
            data.get("count"),
            data.get("mapped_count"),
            data.get("track_ids"),
            album_ids,
            artist_ids,
        )
        return data
    except Exception as exc:
        navidrome_logger.warning("Navidrome starred ids failed error=%s", exc)
        raise HTTPException(status_code=502, detail=f"Navidrome starred failed: {exc}") from exc


@router.put(
    "/releases/{release_id}/navidrome-star",
    responses={
        404: {"description": "Release has no Navidrome mapping"},
        502: {"description": "Navidrome star update failed"},
    },
)
def set_release_navidrome_star(release_id: int, request: NavidromeStarRequest) -> dict[str, object]:
    store, settings = context()
    item_id = store.external_id_for_entity("navidrome", "release", release_id)
    if item_id is None:
        raise HTTPException(status_code=404, detail="Release has no Navidrome mapping")
    client, _username = _navidrome_user_client(settings)
    try:
        if request.starred:
            client.star_album(item_id)
        else:
            client.unstar_album(item_id)
    except Exception as exc:
        navidrome_logger.warning(
            "Navidrome album star update failed release_id=%s item_id=%s starred=%s error=%s",
            release_id, item_id, request.starred, exc,
        )
        raise HTTPException(status_code=502, detail=f"Navidrome star update failed: {exc}") from exc
    navidrome_logger.info(
        "Navidrome album star update ok release_id=%s item_id=%s starred=%s",
        release_id, item_id, request.starred,
    )
    return {"release_id": release_id, "item_id": item_id, "starred": request.starred}


@router.put(
    "/artists/{artist_id}/navidrome-star",
    responses={
        404: {"description": "Artist has no Navidrome mapping"},
        502: {"description": "Navidrome star update failed"},
    },
)
def set_artist_navidrome_star(artist_id: int, request: NavidromeStarRequest) -> dict[str, object]:
    store, settings = context()
    item_id = store.external_id_for_entity("navidrome", "artist", artist_id)
    if item_id is None:
        raise HTTPException(status_code=404, detail="Artist has no Navidrome mapping")
    client, _username = _navidrome_user_client(settings)
    try:
        if request.starred:
            client.star_artist(item_id)
        else:
            client.unstar_artist(item_id)
    except Exception as exc:
        navidrome_logger.warning(
            "Navidrome artist star update failed artist_id=%s item_id=%s starred=%s error=%s",
            artist_id, item_id, request.starred, exc,
        )
        raise HTTPException(status_code=502, detail=f"Navidrome star update failed: {exc}") from exc
    store.set_artist_liked(artist_id, request.starred)
    navidrome_logger.info(
        "Navidrome artist star update ok artist_id=%s item_id=%s starred=%s",
        artist_id, item_id, request.starred,
    )
    return {"artist_id": artist_id, "item_id": item_id, "starred": request.starred}


@router.put(
    "/tracks/{track_id}/navidrome-star",
    responses={
        404: {"description": "Track not found or has no Navidrome mapping"},
        502: {"description": "Navidrome star update failed"},
    },
)
def set_track_navidrome_star(track_id: int, request: NavidromeStarRequest) -> dict[str, object]:
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    item_id = store.external_id_for_track("navidrome", track_id)
    if item_id is None:
        raise HTTPException(status_code=404, detail="Track has no Navidrome mapping")
    client, username = _navidrome_user_client(settings)
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
        username,
        track_id,
        item_id,
        request.starred,
    )
    if request.starred:
        store.import_external_track_play_state(track_id, liked=True)
    else:
        with store.connect() as conn:
            conn.execute(
                "UPDATE user_track_preferences SET liked = 0 WHERE user_id = discocs_user_id() AND track_id = ?",
                (track_id,),
            )
    return {
        "track_id": track_id,
        "item_id": item_id,
        "starred": request.starred,
        "user": username,
    }


@router.get(
    "/navidrome/starred/similar",
    responses={
        404: {"description": "No ready liked tracks with embeddings"},
        502: {"description": "Navidrome starred lookup failed"},
        503: {"description": "Similar mix index missing"},
    },
)
def get_navidrome_starred_similar(
    model: str = "discogs_multi",
    count: Annotated[int, Query(ge=1, le=500)] = 50,
    max_per_artist: Annotated[int, Query(ge=1, le=100)] = 2,
    exclude_same_album: bool = True,
) -> dict[str, object]:
    store, settings = context()
    client, username = _navidrome_user_client(settings)
    try:
        catalog = build_starred_catalog(
            store,
            client,
            model=model,
            user=username,
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


@router.get(
    "/navidrome/similar",
    responses={
        404: {"description": "Navidrome item_id is not synced or has no similar tracks"},
        503: {"description": "Similar index missing or Navidrome similar lookup failed"},
    },
)
def get_navidrome_similar(
    item_id: str,
    count: Annotated[int, Query(ge=1, le=500)] = 50,
    model: str | None = None,
    max_per_artist: Annotated[int | None, Query(ge=1, le=100)] = None,
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
    request_params = InstantMixRequestParams(
        requested_model=requested_model,
        effective_model=model,
        requested_count=count,
        requested_max_per_artist=max_per_artist,
        requested_exclude_same_album=exclude_same_album,
        effective_count=effective_count,
        max_per_artist=effective_max_per_artist,
        exclude_same_album=effective_exclude_same_album,
        count_collaboration_artists=effective_count_collaboration_artists,
        min_similarity=min_similarity,
    )
    base_record = InstantMixRequestRecord(
        request_id=request_id,
        seed_item_id=item_id,
        seed_track_id=None,
        model_name=model,
        params=request_params,
        status="pending",
    )
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
            replace(
                base_record,
                status="failed",
                duration_ms=duration_ms,
                error="Navidrome item_id is not synced",
            ),
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
            replace(base_record, seed_track_id=seed.id, status="failed", duration_ms=duration_ms, error=str(exc)),
        )
        navidrome_logger.warning(
            "Navidrome similar failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_index error=%s",
            request_id, item_id, seed.id, model, exc,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_index error=%s",
            request_id, item_id, seed.id, model, exc,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        duration_ms = (perf_counter() - started) * 1000
        record_instant_mix_request(
            store,
            replace(base_record, seed_track_id=seed.id, status="failed", duration_ms=duration_ms, error=str(exc)),
        )
        navidrome_logger.warning(
            "Navidrome similar failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_embedding error=%s",
            request_id, item_id, seed.id, model, exc,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s track_id=%s model=%s reason=missing_embedding error=%s",
            request_id, item_id, seed.id, model, exc,
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        duration_ms = (perf_counter() - started) * 1000
        record_instant_mix_request(
            store,
            replace(base_record, seed_track_id=seed.id, status="failed", duration_ms=duration_ms, error=str(exc)),
        )
        navidrome_logger.exception(
            "Navidrome similar failed request_id=%s item_id=%s track_id=%s model=%s reason=unexpected",
            request_id, item_id, seed.id, model,
        )
        navidrome_plugin_logger.warning(
            "api_failed request_id=%s item_id=%s track_id=%s model=%s reason=unexpected error=%s",
            request_id, item_id, seed.id, model, exc,
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
            request_id, item_id, skipped_without_external_id,
        )
    duration_ms = (perf_counter() - started) * 1000
    record_instant_mix_request(
        store,
        replace(
            base_record,
            seed_track_id=seed.id,
            status="completed",
            results=tuple(results),
            skipped_without_external_id=skipped_without_external_id,
            duration_ms=duration_ms,
        ),
    )
    navidrome_logger.info(
        "Navidrome similar completed request_id=%s item_id=%s track_id=%s model=%s results=%s skipped_without_external_id=%s duration_ms=%.1f",
        request_id, item_id, seed.id, model, len(results), skipped_without_external_id, duration_ms,
    )
    navidrome_plugin_logger.info(
        "api_completed request_id=%s item_id=%s track_id=%s model=%s results=%s skipped_without_external_id=%s duration_ms=%.1f",
        request_id, item_id, seed.id, model, len(results), skipped_without_external_id, duration_ms,
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
