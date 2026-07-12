"""Shared dependencies and helper functions used across API route handlers.

Extracted from app/main.py.
"""
from __future__ import annotations

from dataclasses import replace
import json
import logging

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.autoplay import autoplay_default_settings
from app.config import Settings, load_runtime_settings, save_runtime_settings
from app.embedder import MuqMulanEmbedder
from app.mixes import generated_mix_default_settings
from app.models import (
    COMPLETION_FRACTION,
    EARLY_SKIP_FRACTION,
    EARLY_SKIP_SECONDS,
    InstantMixRequest,
    InstantMixRequestRecord,
    LATE_SKIP_FRACTION,
    MEANINGFUL_LISTEN_FRACTION,
    MEANINGFUL_LISTEN_SECONDS,
)
from app.navidrome import NavidromeClient
from app.state import TEXT_SEARCH_EMBEDDER, TEXT_SEARCH_EMBEDDER_LOCK
from app.store import Store
from app.user_context import current_user_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core context factory
# ---------------------------------------------------------------------------

def context() -> tuple[Store, Settings]:
    settings = Settings.from_env()
    identity_set, user_id = current_user_id()
    store = Store(settings.db_path, user_id=user_id)
    store.init()
    if not identity_set:
        # Calls outside HTTP retain the historical single-user behaviour.
        store = Store(settings.db_path)
        store.init()
    return store, settings


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

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


def playback_settings_defaults() -> dict[str, object]:
    settings: dict[str, object] = {
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


# ---------------------------------------------------------------------------
# Request / response helpers
# ---------------------------------------------------------------------------

def request_field_names(model: BaseModel) -> set[str]:
    if hasattr(model, "model_fields_set"):
        return set(model.model_fields_set)
    return set(getattr(model, "__fields_set__", set()))


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
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
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def model_to_dict(model: BaseModel) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def api_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


# ---------------------------------------------------------------------------
# External services
# ---------------------------------------------------------------------------

def _navidrome_client(settings: Settings) -> NavidromeClient:
    return NavidromeClient(settings.navidrome)


def text_search_embedder(settings: Settings) -> MuqMulanEmbedder:
    global TEXT_SEARCH_EMBEDDER
    with TEXT_SEARCH_EMBEDDER_LOCK:
        if TEXT_SEARCH_EMBEDDER is None:
            TEXT_SEARCH_EMBEDDER = MuqMulanEmbedder(settings)
        return TEXT_SEARCH_EMBEDDER


# ---------------------------------------------------------------------------
# Instant-mix serializers (depend on enriched_track_dict from serializers/tracks)
# ---------------------------------------------------------------------------

def instant_mix_result_dict(
    store: Store,
    raw: dict[str, object],
    ratings: dict[int, int],
) -> dict[str, object]:
    from app.serializers.tracks import enriched_track_dict  # noqa: PLC0415

    track_id = raw.get("track_id")
    try:
        parsed_track_id = int(track_id) if track_id is not None else None  # type: ignore[arg-type]
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
    from app.serializers.tracks import enriched_track_dict  # noqa: PLC0415

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
    record: InstantMixRequestRecord,
) -> None:
    params = record.params
    normalized_results = [
        model_to_dict(item) if isinstance(item, BaseModel) else item
        for item in record.results
    ]
    store.record_instant_mix_request(replace(record, results=tuple(normalized_results)))
