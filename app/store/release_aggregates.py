"""Store Release Aggregates domain: centroid/medoid storage and lookups."""
from __future__ import annotations

import logging
import sqlite3

import numpy as np

from app.models import ReleaseAggregate, utc_now

logger = logging.getLogger(__name__)


def _row_to_release_aggregate(row: sqlite3.Row) -> ReleaseAggregate:
    return ReleaseAggregate(
        release_id=int(row["release_id"]),
        track_count=int(row["track_count"]),
        available_track_count=int(row["available_track_count"]),
        duration=float(row["duration"]) if row["duration"] is not None else None,
        centroid_model=row["centroid_model"],
        medoid_track_id=int(row["medoid_track_id"]) if row["medoid_track_id"] is not None else None,
        embedding_status=str(row["embedding_status"]),
        top_region_matches_json=row["top_region_matches_json"],
        audio_summary_json=row["audio_summary_json"],
        # Personal preference summaries must never be exposed from the shared
        # release aggregate row. They are computed from scoped preference
        # tables by the recommendation services instead.
        preference_summary_json=None,
        updated_at=str(row["updated_at"]),
    )


class ReleaseAggregatesStoreMixin:
    def upsert_release_aggregate(self, agg: ReleaseAggregate) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO release_aggregates (
                    release_id, track_count, available_track_count, duration,
                    centroid_model, medoid_track_id, embedding_status,
                    top_region_matches_json, audio_summary_json,
                    preference_summary_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(release_id) DO UPDATE SET
                    track_count = excluded.track_count,
                    available_track_count = excluded.available_track_count,
                    duration = excluded.duration,
                    centroid_model = excluded.centroid_model,
                    medoid_track_id = excluded.medoid_track_id,
                    embedding_status = excluded.embedding_status,
                    top_region_matches_json = excluded.top_region_matches_json,
                    audio_summary_json = excluded.audio_summary_json,
                    preference_summary_json = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    agg.release_id,
                    agg.track_count,
                    agg.available_track_count,
                    agg.duration,
                    agg.centroid_model,
                    agg.medoid_track_id,
                    agg.embedding_status,
                    agg.top_region_matches_json,
                    agg.audio_summary_json,
                    None,
                    agg.updated_at,
                ),
            )

    def get_release_aggregate(self, release_id: int) -> ReleaseAggregate | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT * FROM release_aggregates WHERE release_id = ?",
                (release_id,),
            ).fetchone()
        return _row_to_release_aggregate(row) if row is not None else None

    def list_release_ids_for_aggregation(
        self,
        *,
        model_name: str,
        limit: int = 0,
    ) -> list[int]:
        """Return release IDs that have at least one available track with an embedding
        but whose aggregate is missing or computed with a different model."""
        sql = """
            SELECT DISTINCT rt.release_id
            FROM release_tracks rt
            JOIN tracks t ON t.id = rt.track_id
            JOIN embeddings e ON e.track_id = t.id AND e.model_name = ?
            WHERE t.missing_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM release_aggregates ra
                  WHERE ra.release_id = rt.release_id
                    AND ra.centroid_model = ?
                    AND ra.embedding_status = 'ready'
              )
            ORDER BY rt.release_id
        """
        params: list[object] = [model_name, model_name]
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(sql, params).fetchall()
        return [int(row[0]) for row in rows]

    def count_releases_needing_aggregation(self, model_name: str) -> int:
        with self.connect() as conn:  # type: ignore[attr-defined]
            return int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT rt.release_id)
                    FROM release_tracks rt
                    JOIN tracks t ON t.id = rt.track_id
                    JOIN embeddings e ON e.track_id = t.id AND e.model_name = ?
                    WHERE t.missing_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM release_aggregates ra
                          WHERE ra.release_id = rt.release_id
                            AND ra.centroid_model = ?
                            AND ra.embedding_status = 'ready'
                      )
                    """,
                    (model_name, model_name),
                ).fetchone()[0]
            )

    def count_release_aggregates(self, model_name: str | None = None) -> int:
        with self.connect() as conn:  # type: ignore[attr-defined]
            if model_name is None:
                return int(conn.execute("SELECT COUNT(*) FROM release_aggregates").fetchone()[0])
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM release_aggregates WHERE centroid_model = ? AND embedding_status = 'ready'",
                    (model_name,),
                ).fetchone()[0]
            )

    def save_release_embedding(
        self,
        release_id: int,
        model_name: str,
        vector: np.ndarray,
    ) -> None:
        v = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(v))
        if norm > 0:
            v = v / norm
        now = utc_now()
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO release_embeddings (release_id, model_name, dim, vector, vector_norm, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(release_id, model_name) DO UPDATE SET
                    dim = excluded.dim,
                    vector = excluded.vector,
                    vector_norm = excluded.vector_norm,
                    created_at = excluded.created_at
                """,
                (release_id, model_name, int(v.shape[0]), v.tobytes(), float(np.linalg.norm(v)), now),
            )

    def load_release_embedding(self, release_id: int, model_name: str) -> np.ndarray | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT dim, vector FROM release_embeddings WHERE release_id = ? AND model_name = ?",
                (release_id, model_name),
            ).fetchone()
        if row is None:
            return None
        return np.frombuffer(row["vector"], dtype=np.float32, count=int(row["dim"])).copy()

    def list_ready_release_aggregates(
        self,
        model_name: str,
    ) -> list[ReleaseAggregate]:
        """Return all aggregates with embedding_status='ready' for a model."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT * FROM release_aggregates
                WHERE centroid_model = ? AND embedding_status = 'ready'
                ORDER BY release_id
                """,
                (model_name,),
            ).fetchall()
        return [_row_to_release_aggregate(row) for row in rows]

    def list_positive_track_ids_with_embeddings(
        self,
        model_name: str,
        *,
        min_score: float = 1.0,
        limit: int = 500,
    ) -> list[int]:
        """Return track IDs where the user has positive signals and an embedding exists."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT utp.track_id
                FROM user_track_preferences utp
                JOIN embeddings e ON e.track_id = utp.track_id AND e.model_name = ?
                JOIN tracks t ON t.id = utp.track_id
                WHERE t.missing_at IS NULL
                  AND utp.user_id = discocs_user_id()
                  AND (utp.liked = 1 OR utp.score >= ?)
                ORDER BY utp.score DESC, utp.liked DESC
                LIMIT ?
                """,
                (model_name, min_score, limit),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def get_albums_for_you_cache(self, model_name: str) -> str | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT items_json FROM albums_for_you_cache WHERE user_id = discocs_user_id() AND model_name = ?",
                (model_name,),
            ).fetchone()
        return str(row["items_json"]) if row is not None else None

    def set_albums_for_you_cache(self, model_name: str, items_json: str) -> None:
        now = utc_now()
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO albums_for_you_cache (user_id, model_name, items_json, computed_at)
                VALUES (discocs_user_id(), ?, ?, ?)
                ON CONFLICT(user_id, model_name) DO UPDATE SET
                    items_json = excluded.items_json,
                    computed_at = excluded.computed_at
                """,
                (model_name, items_json, now),
            )

    def albums_for_you_cache_age_hours(self, model_name: str) -> float | None:
        """Return age of cache in hours, or None if no cache exists."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT computed_at FROM albums_for_you_cache WHERE user_id = discocs_user_id() AND model_name = ?",
                (model_name,),
            ).fetchone()
        if row is None:
            return None
        try:
            from datetime import datetime, UTC  # noqa: PLC0415
            computed = datetime.fromisoformat(str(row["computed_at"]).replace("Z", "+00:00"))
            now = datetime.now(UTC)
            return (now - computed).total_seconds() / 3600
        except Exception:
            return None

    def list_release_track_preferences(
        self,
        release_id: int,
    ) -> list[object]:
        """Return (track_id, liked, score, skip_count, early_skip_count, play_count) for
        all tracks in a release that have a preference row."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT utp.track_id, utp.liked, utp.score,
                       utp.skip_count, utp.early_skip_count, utp.play_count,
                       utp.completion_count
                FROM release_tracks rt
                JOIN user_track_preferences utp ON utp.track_id = rt.track_id
                WHERE rt.release_id = ? AND utp.user_id = discocs_user_id()
                """,
                (release_id,),
            ).fetchall()
        return list(rows)

    def load_all_release_embeddings(
        self,
        model_name: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (release_ids, matrix) for all stored release centroids of a model."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT release_id, dim, vector FROM release_embeddings
                WHERE model_name = ?
                ORDER BY release_id
                """,
                (model_name,),
            ).fetchall()
        if not rows:
            return np.array([], dtype=np.int64), np.empty((0, 0), dtype=np.float32)
        dim = int(rows[0]["dim"])
        ids = np.array([int(r["release_id"]) for r in rows], dtype=np.int64)
        matrix = np.vstack(
            [np.frombuffer(r["vector"], dtype=np.float32, count=dim) for r in rows]
        ).astype(np.float32)
        return ids, matrix
