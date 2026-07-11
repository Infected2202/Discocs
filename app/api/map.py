"""Collection-map / embedding-atlas API routes.

Read + job endpoints backing the admin atlas view. This surface is diagnostic
only: the 2D map coordinates are an approximation for viewing. **Real**
nearest-neighbor / similarity always comes from the HNSW/cosine recommender
(the ``/neighbors`` endpoint), never from the projected x/y coordinates.

Extracted as its own router per the app/api package convention.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.analysis_jobs import _build_map_projection_job
from app.api.deps import context
from app.models import MapProjection
from app.projection import PROJECTION_PROFILES
from app.recommender import Recommender
from app.schemas.requests import MapProjectionBuildRequest
from app.serializers.tracks import enriched_similar_track_dict, enriched_track_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/map")

_TRACK_NOT_FOUND = "Track not found"
_PROJECTION_NOT_FOUND = "Projection not found"

# Color/filter dimensions derived from track metadata (always available).
_METADATA_DIMENSIONS = [
    {"key": "artist", "label": "Artist", "kind": "categorical"},
    {"key": "release", "label": "Release", "kind": "categorical"},
    {"key": "genre", "label": "Genre", "kind": "categorical"},
    {"key": "year", "label": "Year", "kind": "numeric"},
]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _projection_dict(store, projection: MapProjection) -> dict[str, object]:
    """Serialize a projection, adding a `stale` flag vs current embeddings.

    Staleness mirrors the recommender's index check: if the live embedding
    count has drifted from what was seen at build time, the map is stale. We
    never auto-rebuild — the admin just surfaces the flag.
    """
    current = int(store.count_embeddings(projection.model_name))
    stale = projection.status == "ready" and current != projection.source_embedding_count
    return {
        "id": projection.id,
        "model": projection.model_name,
        "name": projection.name,
        "method": projection.method,
        "metric": projection.metric,
        "params": json.loads(projection.params_json) if projection.params_json else {},
        "source_embedding_count": projection.source_embedding_count,
        "projected_count": projection.projected_count,
        "skipped_count": projection.skipped_count,
        "embedding_dim": projection.embedding_dim,
        "version": projection.version,
        "status": projection.status,
        "diagnostics": (
            json.loads(projection.diagnostics_json) if projection.diagnostics_json else None
        ),
        "created_at": projection.created_at,
        "completed_at": projection.completed_at,
        "current_embedding_count": current,
        "stale": stale,
    }


def _require_projection(store, projection_id: str) -> MapProjection:
    projection = store.get_map_projection(projection_id)
    if projection is None:
        raise HTTPException(status_code=404, detail=_PROJECTION_NOT_FOUND)
    return projection


def _region_membership(store, model_name: str) -> dict[int, int]:
    """Map track_id -> region_index for the active flow profile of a model."""
    profile = store.get_flow_profile(model_name)
    if profile is None:
        return {}
    membership: dict[int, int] = {}
    for region in store.list_flow_regions(profile.id):
        for region_track in store.list_flow_region_tracks(region.id):
            membership.setdefault(region_track.track_id, region.region_index)
    return membership


def _mix_membership(store) -> dict[int, list[str]]:
    """Map track_id -> list of generated-mix ids that contain it."""
    membership: dict[int, list[str]] = {}
    for mix in store.list_generated_mixes():
        for item in store.list_generated_mix_items(mix.id):
            membership.setdefault(item.track_id, []).append(mix.id)
    return membership


# ---------------------------------------------------------------------------
# Projections: list / build / detail / points
# ---------------------------------------------------------------------------

@router.get("/projections")
def list_projections(model: str | None = None) -> dict[str, object]:
    store, _settings = context()
    projections = store.list_map_projections(model)
    return {"projections": [_projection_dict(store, p) for p in projections]}


@router.post(
    "/projections",
    responses={400: {"description": "Unknown projection profile"}},
)
def build_projection_endpoint(
    request: MapProjectionBuildRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    if request.profile not in PROJECTION_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown projection profile: {request.profile}",
        )
    store, _settings = context()
    job = store.create_progress_job(
        "build-map", request.model,
        message=f"Waiting to build {request.profile} map for {request.model}",
    )
    background_tasks.add_task(
        _build_map_projection_job, job.id, request.model, request.profile, request.force,
    )
    return {
        "status": "accepted",
        "job_id": job.id,
        "model": request.model,
        "profile": request.profile,
        "force": request.force,
    }


@router.get(
    "/projections/{projection_id}",
    responses={404: {"description": _PROJECTION_NOT_FOUND}},
)
def get_projection(projection_id: str) -> dict[str, object]:
    store, _settings = context()
    projection = _require_projection(store, projection_id)
    return {"projection": _projection_dict(store, projection)}


@router.get(
    "/projections/{projection_id}/points",
    responses={404: {"description": _PROJECTION_NOT_FOUND}},
)
def get_projection_points(projection_id: str) -> dict[str, object]:
    """Bulk points as parallel typed arrays (compact, not per-point objects)."""
    store, _settings = context()
    projection = _require_projection(store, projection_id)
    ids, xy = store.load_map_projection_points(projection_id)
    return {
        "projection_id": projection.id,
        "model": projection.model_name,
        "count": int(ids.shape[0]),
        "track_ids": ids.tolist(),
        "x": xy[:, 0].tolist(),
        "y": xy[:, 1].tolist(),
    }


# ---------------------------------------------------------------------------
# Color / filter dimensions
# ---------------------------------------------------------------------------

@router.get(
    "/dimensions",
    responses={404: {"description": _PROJECTION_NOT_FOUND}},
)
def get_dimensions(projection: str = Query(..., description="Projection id")) -> dict[str, object]:
    store, _settings = context()
    proj = _require_projection(store, projection)
    dimensions = list(_METADATA_DIMENSIONS)
    flow_profile = store.get_flow_profile(proj.model_name)
    if flow_profile is not None and store.count_flow_regions(flow_profile.id) > 0:
        dimensions.append({"key": "region", "label": "Taste region", "kind": "categorical"})
    if store.count_generated_mixes():
        dimensions.append({"key": "mix", "label": "Generated mix", "kind": "categorical"})
    return {"projection_id": proj.id, "dimensions": dimensions}


@router.get(
    "/projections/{projection_id}/color/{dimension}",
    responses={
        404: {"description": _PROJECTION_NOT_FOUND},
        400: {"description": "Unknown color dimension"},
    },
)
def get_projection_color(projection_id: str, dimension: str) -> dict[str, object]:
    """Per-point color/filter values aligned to the points array order.

    The returned `values` list is parallel to `track_ids` (same track_id
    ordering as `/points`), so the client can apply colors without re-fetching
    coordinates.
    """
    store, _settings = context()
    projection = _require_projection(store, projection_id)
    ids, _xy = store.load_map_projection_points(projection_id)
    track_ids = ids.tolist()

    values: list[object]
    if dimension in {"artist", "release", "genre", "year"}:
        tracks = store.get_tracks(track_ids)
        field = "album" if dimension == "release" else dimension
        values = [getattr(tracks.get(tid), field, None) if tracks.get(tid) else None for tid in track_ids]
    elif dimension == "region":
        membership = _region_membership(store, projection.model_name)
        values = [membership.get(tid) for tid in track_ids]
    elif dimension == "mix":
        membership = _mix_membership(store)
        values = [(membership.get(tid) or [None])[0] for tid in track_ids]
    else:
        raise HTTPException(status_code=400, detail=f"Unknown color dimension: {dimension}")

    return {
        "projection_id": projection.id,
        "dimension": dimension,
        "track_ids": track_ids,
        "values": values,
    }


# ---------------------------------------------------------------------------
# Track inspection + real (HNSW) neighbors
# ---------------------------------------------------------------------------

@router.get(
    "/tracks/{track_id}",
    responses={404: {"description": _TRACK_NOT_FOUND}},
)
def get_map_track(
    track_id: int,
    projection: str | None = Query(default=None, description="Projection id for map coords"),
) -> dict[str, object]:
    store, _settings = context()
    track = store.get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail=_TRACK_NOT_FOUND)
    data = enriched_track_dict(store, track)

    coords = None
    model_name = None
    if projection is not None:
        proj = store.get_map_projection(projection)
        if proj is not None:
            model_name = proj.model_name
            point = store.get_map_projection_point(projection, track_id)
            if point is not None:
                coords = {"x": point[0], "y": point[1]}

    region_index = None
    if model_name is not None:
        region_index = _region_membership(store, model_name).get(track_id)
    mix_ids = _mix_membership(store).get(track_id, [])
    predictions = {
        model: [{"label": p.label, "score": p.score, "rank": p.rank} for p in preds]
        for model, preds in store.list_predictions(track_id).items()
    }

    return {
        "track": data,
        "projection_id": projection,
        "map_coords": coords,
        "region_index": region_index,
        "mix_ids": mix_ids,
        "predictions": predictions,
    }


@router.get(
    "/tracks/{track_id}/neighbors",
    responses={
        404: {"description": "Track not found or missing embedding"},
        503: {"description": "Similarity index unavailable"},
    },
)
def get_map_track_neighbors(
    track_id: int,
    model: str = "discogs_multi",
    k: int = Query(default=30, ge=1, le=200),
    max_per_artist: int = Query(default=2, ge=1, le=100),
    exclude_same_album: bool = True,
) -> dict[str, object]:
    """Real HNSW/cosine neighbors — never the 2D map coordinates."""
    store, settings = context()
    seed = store.get_track(track_id)
    if seed is None:
        raise HTTPException(status_code=404, detail=_TRACK_NOT_FOUND)
    try:
        results = Recommender(store, settings, model).similar(
            seed, k=k, max_per_artist=max_per_artist, exclude_same_album=exclude_same_album,
        )
        ratings = store.feedback_for_seed(seed.id, model)
        results = [replace(result, rating=ratings.get(result.track.id)) for result in results]
    except FileNotFoundError as exc:
        logger.warning("Map neighbors index missing track_id=%s model=%s error=%s", track_id, model, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        logger.warning("Map neighbors lookup failed track_id=%s model=%s error=%s", track_id, model, exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "seed": enriched_track_dict(store, seed),
        "model": model,
        "source": "hnsw",
        "results": [enriched_similar_track_dict(store, result) for result in results],
    }


# ---------------------------------------------------------------------------
# Overlays: generated mixes + taste regions
# ---------------------------------------------------------------------------

@router.get(
    "/mixes",
    responses={404: {"description": _PROJECTION_NOT_FOUND}},
)
def get_map_mixes(projection: str = Query(..., description="Projection id")) -> dict[str, object]:
    """Generated-mix overlay: which projected points belong to each mix."""
    store, _settings = context()
    _require_projection(store, projection)
    ids, _xy = store.load_map_projection_points(projection)
    point_set = set(ids.tolist())
    mixes: list[dict[str, object]] = []
    for mix in store.list_generated_mixes():
        member_ids = [
            item.track_id
            for item in store.list_generated_mix_items(mix.id)
            if item.track_id in point_set
        ]
        mixes.append({
            "id": mix.id,
            "title": mix.title,
            "mix_type": mix.mix_type,
            "status": mix.status,
            "anchor": json.loads(mix.anchor_json) if mix.anchor_json else None,
            "track_ids": member_ids,
            "track_count": len(member_ids),
        })
    return {"projection_id": projection, "mixes": mixes}


@router.get(
    "/regions",
    responses={404: {"description": _PROJECTION_NOT_FOUND}},
)
def get_map_regions(projection: str = Query(..., description="Projection id")) -> dict[str, object]:
    """Taste-region overlay: which projected points belong to each region."""
    store, _settings = context()
    proj = _require_projection(store, projection)
    ids, _xy = store.load_map_projection_points(projection)
    point_set = set(ids.tolist())
    flow_profile = store.get_flow_profile(proj.model_name)
    if flow_profile is None:
        return {"projection_id": projection, "profile": None, "regions": []}
    regions: list[dict[str, object]] = []
    for region in store.list_flow_regions(flow_profile.id):
        region_tracks = store.list_flow_region_tracks(region.id)
        member_ids = [rt.track_id for rt in region_tracks if rt.track_id in point_set]
        regions.append({
            "id": region.id,
            "region_index": region.region_index,
            "medoid_track_id": region.medoid_track_id,
            "weight": region.weight,
            "seed_count": region.seed_count,
            "candidate_count": region.candidate_count,
            "summary": json.loads(region.summary_json) if region.summary_json else None,
            "track_ids": member_ids,
            "track_count": len(member_ids),
        })
    return {
        "projection_id": projection,
        "profile": {"id": flow_profile.id, "model_key": flow_profile.model_key, "status": flow_profile.status},
        "regions": regions,
    }
