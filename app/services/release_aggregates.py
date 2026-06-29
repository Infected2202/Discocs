"""Service: compute and persist release aggregate (centroid, medoid, counts)."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from app.models import ReleaseAggregate, utc_now

if TYPE_CHECKING:
    from app.store import Store

logger = logging.getLogger(__name__)


def compute_release_aggregate(
    store: Store,
    release_id: int,
    model_name: str,
) -> ReleaseAggregate:
    """Compute centroid + medoid for one release and persist both.

    Returns the upserted ReleaseAggregate.
    """
    track_rows = store.list_release_tracks(release_id)
    total_tracks = len(track_rows)

    vectors: list[np.ndarray] = []
    track_ids: list[int] = []
    total_duration = 0.0

    for row in track_rows:
        t = row.track
        if t.missing_at is not None:
            continue
        if t.duration is not None:
            total_duration += t.duration
        v = store.load_embedding(t.id, model_name)
        if v is not None:
            vectors.append(v)
            track_ids.append(t.id)

    available = len(vectors)
    now = utc_now()

    if not vectors:
        agg = ReleaseAggregate(
            release_id=release_id,
            track_count=total_tracks,
            available_track_count=0,
            duration=total_duration if total_duration > 0 else None,
            centroid_model=model_name,
            medoid_track_id=None,
            embedding_status="unavailable",
            top_region_matches_json=None,
            audio_summary_json=None,
            preference_summary_json=None,
            updated_at=now,
        )
        store.upsert_release_aggregate(agg)
        return agg

    matrix = np.vstack(vectors).astype(np.float32)

    # Normalized centroid
    centroid = matrix.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm

    # Medoid: track with highest cosine similarity to centroid
    sims = matrix @ centroid
    medoid_idx = int(np.argmax(sims))
    medoid_track_id = track_ids[medoid_idx]

    store.save_release_embedding(release_id, model_name, centroid)

    agg = ReleaseAggregate(
        release_id=release_id,
        track_count=total_tracks,
        available_track_count=available,
        duration=total_duration if total_duration > 0 else None,
        centroid_model=model_name,
        medoid_track_id=medoid_track_id,
        embedding_status="ready",
        top_region_matches_json=None,
        audio_summary_json=None,
        preference_summary_json=None,
        updated_at=now,
    )
    store.upsert_release_aggregate(agg)
    return agg


def run_release_aggregate_job(
    store: Store,
    model_name: str,
    *,
    limit: int = 0,
    job_id: str | None = None,
) -> dict[str, object]:
    """Compute aggregates for all releases that need (re)computation.

    If job_id is provided, updates JobStatus progress via update_job/finish_job.
    Returns a summary dict with counts.
    """
    release_ids = store.list_release_ids_for_aggregation(
        model_name=model_name,
        limit=limit,
    )
    total = len(release_ids)
    logger.info("release-aggregates job: %d releases to process (model=%s)", total, model_name)

    if job_id is not None:
        from app.services.jobs import finish_job, update_job
        update_job(job_id, status="running", message=f"Computing aggregates for {total} releases", total=total, done=0, failed=0)

    done = 0
    failed = 0
    for release_id in release_ids:
        try:
            compute_release_aggregate(store, release_id, model_name)
            done += 1
        except Exception:
            logger.exception("Failed to compute aggregate for release_id=%d", release_id)
            failed += 1
        if job_id is not None:
            update_job(job_id, done=done, failed=failed, current=f"release {release_id}")

    logger.info(
        "release-aggregates job done: done=%d failed=%d model=%s",
        done,
        failed,
        model_name,
    )

    if job_id is not None:
        if failed and not done:
            finish_job(job_id, "failed", f"All {failed} releases failed")
        else:
            finish_job(job_id, "done", f"Computed aggregates: {done} done, {failed} failed")

    return {
        "total": total,
        "done": done,
        "failed": failed,
        "model_name": model_name,
    }
