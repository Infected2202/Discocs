"""Authenticated timeline waveform artifact API."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.api.deps import context
from app.audio_features import AUDIO_FEATURE_EXTRACTOR
from app.schemas.requests import TimelineStatusRequest
from app.timeline.artifacts import load_valid_artifact
from app.timeline.codec import EXTRACTOR, PACK_NAME, TimelineFormatError

router = APIRouter(prefix="/api/v1")


def _artifact(track_id: int):
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    try:
        artifact = load_valid_artifact(store, settings.data_dir / "timeline", track, PACK_NAME, EXTRACTOR)
    except TimelineFormatError as exc:
        raise HTTPException(status_code=409, detail={"code": "timeline_stale", "message": str(exc)}) from exc
    if artifact is None:
        raise HTTPException(status_code=404, detail="Timeline artifact not found")
    return artifact


_ARTIFACT_RESPONSES = {404: {"description": "Track or artifact not found"}, 409: {"description": "Artifact is stale or corrupt"}}


@router.get("/tracks/{track_id}/timeline/manifest", responses=_ARTIFACT_RESPONSES)
def timeline_manifest(track_id: int) -> dict[str, object]:
    manifest, _payload, _manifest_path, _payload_path = _artifact(track_id)
    public = dict(manifest)
    source = manifest.get("source", {})
    public["source"] = {"mtime": source.get("mtime"), "file_size": source.get("file_size")}
    return public


@router.get("/tracks/{track_id}/timeline/payload", responses=_ARTIFACT_RESPONSES)
def timeline_payload(track_id: int) -> Response:
    _manifest, payload, _manifest_path, _payload_path = _artifact(track_id)
    checksum = hashlib.sha256(payload).hexdigest()
    return Response(
        payload, media_type="application/octet-stream",
        headers={"ETag": f'"{checksum}"', "Cache-Control": "private, max-age=86400"},
    )


def _track_timeline_status(store, settings, track_id: int, state) -> tuple[str, object | None]:
    track = store.get_track(track_id)
    if track is None:
        return "missing", None
    row = store.get_timeline_artifact(track_id, PACK_NAME, EXTRACTOR)
    task_status = str(state["status"]) if state else ""
    active_status = {
        "queued": "queued",
        "failed_retryable": "queued",
        "leased": "running",
        "final_failed": "failed",
    }.get(task_status)
    if row is None:
        if active_status:
            return active_status, state.get("error")
        return "missing", None
    identity_matches = (
        row["source_path"] == track.path
        and float(row["source_mtime"]) == float(track.mtime)
        and int(row["source_file_size"]) == track.file_size
    )
    if not identity_matches:
        if active_status:
            return active_status, state.get("error")
        return "stale", None
    try:
        load_valid_artifact(store, settings.data_dir / "timeline", track, PACK_NAME, EXTRACTOR)
    except TimelineFormatError as exc:
        return "failed", str(exc)
    return "ready", None


@router.post("/timeline/status", responses={400: {"description": "Unsupported extractor"}})
def timeline_status(request: TimelineStatusRequest) -> dict[str, object]:
    if request.extractor != EXTRACTOR:
        raise HTTPException(status_code=400, detail="Unsupported timeline extractor")
    store, settings = context()
    states = store.latest_track_analysis_states(request.track_ids, AUDIO_FEATURE_EXTRACTOR)
    items = []
    for track_id in request.track_ids:
        status, error = _track_timeline_status(store, settings, track_id, states.get(track_id))
        items.append({"track_id": track_id, "status": status, "error": error})
    return {"pack_name": PACK_NAME, "extractor": EXTRACTOR, "items": items}
