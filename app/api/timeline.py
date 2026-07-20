"""Authenticated timeline waveform artifact API."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import Response

from app.api.deps import context
from app.schemas.requests import AnalyzeTimelineRequest, TimelineStatusRequest
from app.timeline.artifacts import load_valid_artifact
from app.timeline.codec import EXTRACTOR, PACK_NAME, TimelineFormatError
from app.timeline.jobs import run_timeline_job

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


@router.get("/tracks/{track_id}/timeline/manifest")
def timeline_manifest(track_id: int) -> dict[str, object]:
    manifest, _payload, _manifest_path, _payload_path = _artifact(track_id)
    public = dict(manifest)
    source = manifest.get("source", {})
    public["source"] = {"mtime": source.get("mtime"), "file_size": source.get("file_size")}
    return public


@router.get("/tracks/{track_id}/timeline/payload")
def timeline_payload(track_id: int) -> Response:
    _manifest, payload, _manifest_path, _payload_path = _artifact(track_id)
    checksum = hashlib.sha256(payload).hexdigest()
    return Response(
        payload, media_type="application/octet-stream",
        headers={"ETag": f'"{checksum}"', "Cache-Control": "private, max-age=86400"},
    )


@router.post("/timeline/status")
def timeline_status(request: TimelineStatusRequest) -> dict[str, object]:
    if request.extractor != EXTRACTOR:
        raise HTTPException(status_code=400, detail="Unsupported timeline extractor")
    store, settings = context()
    states = store.get_timeline_analysis_states(request.track_ids, PACK_NAME, EXTRACTOR)
    items = []
    for track_id in request.track_ids:
        track = store.get_track(track_id)
        state = states.get(track_id)
        status = "missing"
        error = None
        if track is not None:
            row = store.get_timeline_artifact(track_id, PACK_NAME, EXTRACTOR)
            if row is not None:
                identity_matches = (
                    row["source_path"] == track.path and float(row["source_mtime"]) == float(track.mtime)
                    and int(row["source_file_size"]) == track.file_size
                )
                if not identity_matches:
                    status = "stale"
                else:
                    try:
                        load_valid_artifact(store, settings.data_dir / "timeline", track, PACK_NAME, EXTRACTOR)
                        status = "ready"
                    except TimelineFormatError as exc:
                        status, error = "failed", str(exc)
            elif state and state["status"] in {"queued", "running", "failed"}:
                status = str(state["status"])
                error = state.get("error")
        items.append({"track_id": track_id, "status": status, "error": error})
    return {"pack_name": PACK_NAME, "extractor": EXTRACTOR, "items": items}


@router.post("/jobs/analyze-timeline")
def analyze_timeline(request: AnalyzeTimelineRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    if request.extractor != EXTRACTOR:
        raise HTTPException(status_code=400, detail="Unsupported timeline extractor")
    store, settings = context()
    if request.track_ids is not None:
        tracks = []
        for track_id in dict.fromkeys(request.track_ids):
            track = store.get_track(track_id)
            if track is None:
                raise HTTPException(status_code=404, detail=f"Track {track_id} not found")
            tracks.append(track)
        if request.limit is not None:
            tracks = tracks[:request.limit]
    elif request.reset:
        tracks = store.list_active_tracks(limit=request.limit)
    else:
        tracks = store.list_tracks_needing_timeline(PACK_NAME, EXTRACTOR, limit=request.limit)
    job = store.create_progress_job("analyze-timeline", EXTRACTOR, total=len(tracks), message=f"Queued {len(tracks)} waveform tracks")
    for track in tracks:
        store.set_timeline_analysis_status(track.id, PACK_NAME, EXTRACTOR, "queued", job_id=job.id)
    if tracks:
        background_tasks.add_task(run_timeline_job, store, settings, tracks, job_id=job.id, reset=request.reset)
    else:
        store.update_progress_job(job.id, status="completed", message="Waveforms ready 0, failed 0", finished=True)
    return {"status": "accepted", "job_id": job.id, "total": len(tracks), "extractor": EXTRACTOR}
