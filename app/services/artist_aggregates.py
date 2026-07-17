"""Compute artist centroids from equally weighted release centroids."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from app.models import ArtistAggregate, utc_now

if TYPE_CHECKING:
    from app.store import Store

logger = logging.getLogger(__name__)


def compute_artist_aggregate(store: Store, artist_id: int, model_name: str) -> ArtistAggregate:
    """Build one artist profile from owned, ready releases with equal weights."""
    with store.connect() as conn:
        release_count = int(conn.execute(
            """
            SELECT COUNT(DISTINCT release_id)
            FROM release_artists
            WHERE artist_id = ? AND role = 'primary'
            """,
            (artist_id,),
        ).fetchone()[0])

    release_ids, matrix = store.list_artist_release_embeddings(artist_id, model_name)
    available = int(matrix.shape[0])
    now = utc_now()
    if available == 0:
        store.delete_artist_embedding(artist_id, model_name)
        aggregate = ArtistAggregate(
            artist_id=artist_id,
            release_count=release_count,
            available_release_count=0,
            centroid_model=model_name,
            medoid_release_id=None,
            embedding_status="unavailable",
            updated_at=now,
        )
        store.upsert_artist_aggregate(aggregate)
        return aggregate

    centroid = matrix.mean(axis=0).astype(np.float32)
    norm = float(np.linalg.norm(centroid))
    if norm > 0:
        centroid = centroid / norm
    similarities = matrix @ centroid
    medoid_release_id = int(release_ids[int(np.argmax(similarities))])

    store.save_artist_embedding(artist_id, model_name, centroid)
    aggregate = ArtistAggregate(
        artist_id=artist_id,
        release_count=release_count,
        available_release_count=available,
        centroid_model=model_name,
        medoid_release_id=medoid_release_id,
        embedding_status="ready",
        updated_at=now,
    )
    store.upsert_artist_aggregate(aggregate)
    return aggregate


def run_artist_aggregate_job(
    store: Store,
    model_name: str,
    *,
    limit: int = 0,
    job_id: str | None = None,
) -> dict[str, object]:
    """Compute every stale artist aggregate and update the shared job status."""
    artist_ids = store.list_artist_ids_for_aggregation(model_name=model_name, limit=limit)
    total = len(artist_ids)
    if job_id is not None:
        from app.services.jobs import finish_job, update_job
        update_job(
            job_id, status="running", message=f"Computing aggregates for {total} artists",
            total=total, done=0, failed=0,
        )

    done = 0
    failed = 0
    for artist_id in artist_ids:
        try:
            compute_artist_aggregate(store, artist_id, model_name)
            done += 1
        except Exception:
            logger.exception("Failed to compute aggregate for artist_id=%d", artist_id)
            failed += 1
        if job_id is not None:
            update_job(job_id, done=done, failed=failed, current=f"artist {artist_id}")

    from app.services.artist_similarity import invalidate_cache
    invalidate_cache(model_name)
    if job_id is not None:
        if failed and not done:
            finish_job(job_id, "failed", f"All {failed} artists failed")
        else:
            finish_job(job_id, "done", f"Computed artist aggregates: {done} done, {failed} failed")
    logger.info(
        "artist-aggregates job done: done=%d failed=%d model=%s", done, failed, model_name
    )
    return {"total": total, "done": done, "failed": failed, "model_name": model_name}
