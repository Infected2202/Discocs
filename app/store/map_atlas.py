"""Store Map/Atlas domain: persisted 2D projections of embeddings.

A projection is a snapshot of 2D coordinates for one embedding model, used by
the collection map / embedding atlas diagnostic view. Multiple projections per
model are allowed. This layer only stores coordinates and metadata — real
similarity and nearest-neighbor lookups keep using the HNSW/cosine path.

Part of the app/store package. Do not import this module directly; use
app.store instead.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import uuid4

import numpy as np

from app.models import MapProjection, utc_now

logger = logging.getLogger(__name__)


def _row_to_map_projection(row) -> MapProjection:
    return MapProjection(
        id=str(row["id"]),
        model_name=str(row["model_name"]),
        name=str(row["name"]),
        method=str(row["method"]),
        metric=str(row["metric"]),
        params_json=row["params_json"],
        source_embedding_count=int(row["source_embedding_count"]),
        projected_count=int(row["projected_count"]),
        skipped_count=int(row["skipped_count"]),
        embedding_dim=int(row["embedding_dim"]),
        version=int(row["version"]),
        status=str(row["status"]),
        diagnostics_json=row["diagnostics_json"],
        created_at=str(row["created_at"]),
        completed_at=row["completed_at"],
    )


class MapAtlasStoreMixin:
    # ------------------------------------------------------------------
    # Projections
    # ------------------------------------------------------------------

    def create_map_projection(
        self,
        *,
        model_name: str,
        name: str,
        method: str,
        metric: str = "cosine",
        params_json: str | None = None,
        embedding_dim: int = 0,
        source_embedding_count: int = 0,
        version: int = 1,
        status: str = "pending",
        projection_id: str | None = None,
    ) -> MapProjection:
        pid = projection_id or str(uuid4())
        now = utc_now()
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO map_projections (
                    id, model_name, name, method, metric, params_json,
                    source_embedding_count, projected_count, skipped_count,
                    embedding_dim, version, status, diagnostics_json,
                    created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, NULL, ?, NULL)
                """,
                (
                    pid, model_name, name, method, metric, params_json,
                    source_embedding_count, embedding_dim, version, status, now,
                ),
            )
        return self.get_map_projection(pid)  # type: ignore[return-value]

    def get_map_projection(self, projection_id: str) -> MapProjection | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT * FROM map_projections WHERE id = ?",
                (projection_id,),
            ).fetchone()
        return _row_to_map_projection(row) if row is not None else None

    def list_map_projections(
        self,
        model_name: str | None = None,
    ) -> list[MapProjection]:
        sql = "SELECT * FROM map_projections"
        params: list[object] = []
        if model_name is not None:
            sql += " WHERE model_name = ?"
            params.append(model_name)
        sql += " ORDER BY created_at DESC, id"
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_map_projection(row) for row in rows]

    def find_map_projection(
        self,
        model_name: str,
        name: str,
    ) -> MapProjection | None:
        """Return the most recent projection for a (model, name) pair, if any."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                """
                SELECT * FROM map_projections
                WHERE model_name = ? AND name = ?
                ORDER BY created_at DESC, id
                LIMIT 1
                """,
                (model_name, name),
            ).fetchone()
        return _row_to_map_projection(row) if row is not None else None

    def update_map_projection(
        self,
        projection_id: str,
        *,
        status: str | None = None,
        source_embedding_count: int | None = None,
        projected_count: int | None = None,
        skipped_count: int | None = None,
        embedding_dim: int | None = None,
        diagnostics_json: str | None = None,
        completed_at: str | None = None,
    ) -> MapProjection | None:
        updates: list[str] = []
        params: list[object] = []
        for column, value in (
            ("status", status),
            ("source_embedding_count", source_embedding_count),
            ("projected_count", projected_count),
            ("skipped_count", skipped_count),
            ("embedding_dim", embedding_dim),
            ("diagnostics_json", diagnostics_json),
            ("completed_at", completed_at),
        ):
            if value is not None:
                updates.append(f"{column} = ?")
                params.append(value)
        if updates:
            params.append(projection_id)
            with self.connect() as conn:  # type: ignore[attr-defined]
                conn.execute(
                    f"UPDATE map_projections SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
        return self.get_map_projection(projection_id)

    def delete_map_projection(self, projection_id: str) -> None:
        # Points cascade via FK ON DELETE CASCADE.
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute("DELETE FROM map_projections WHERE id = ?", (projection_id,))

    # ------------------------------------------------------------------
    # Projection points (coordinates)
    # ------------------------------------------------------------------

    def replace_map_projection_points(
        self,
        projection_id: str,
        track_ids: Sequence[int] | np.ndarray,
        coords: np.ndarray,
    ) -> None:
        """Replace all points for a projection with (track_ids, coords).

        coords is an (n, 2) array of x/y. track_ids and coords must have the
        same length.
        """
        ids = np.asarray(track_ids, dtype=np.int64).reshape(-1)
        xy = np.asarray(coords, dtype=np.float32)
        if xy.ndim != 2 or xy.shape[1] != 2:
            raise ValueError(f"coords must be (n, 2), got {xy.shape}")
        if xy.shape[0] != ids.shape[0]:
            raise ValueError(
                f"track_ids ({ids.shape[0]}) and coords ({xy.shape[0]}) length mismatch"
            )
        payload = [
            (projection_id, int(tid), float(xy[i, 0]), float(xy[i, 1]))
            for i, tid in enumerate(ids)
        ]
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                "DELETE FROM map_projection_points WHERE projection_id = ?",
                (projection_id,),
            )
            conn.executemany(
                """
                INSERT INTO map_projection_points (projection_id, track_id, x, y)
                VALUES (?, ?, ?, ?)
                """,
                payload,
            )

    def load_map_projection_points(
        self,
        projection_id: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (track_ids, coords) as parallel arrays, ordered by track_id.

        coords is an (n, 2) float32 array. Empty projection yields empty arrays.
        """
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT track_id, x, y FROM map_projection_points
                WHERE projection_id = ?
                ORDER BY track_id
                """,
                (projection_id,),
            ).fetchall()
        if not rows:
            return np.array([], dtype=np.int64), np.empty((0, 2), dtype=np.float32)
        ids = np.array([int(row["track_id"]) for row in rows], dtype=np.int64)
        xy = np.array([(row["x"], row["y"]) for row in rows], dtype=np.float32)
        return ids, xy

    def load_projection_source(
        self,
        model_name: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (track_ids, vectors) to project for a model.

        Only embeddings of non-missing tracks are returned, so lost files never
        land on the map. Ordered by track_id. Empty catalog yields empty arrays.
        """
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT e.track_id, e.dim, e.vector
                FROM embeddings e
                JOIN tracks t ON t.id = e.track_id
                WHERE e.model_name = ? AND t.missing_at IS NULL
                ORDER BY e.track_id
                """,
                (model_name,),
            ).fetchall()
        if not rows:
            return np.array([], dtype=np.int64), np.empty((0, 0), dtype=np.float32)
        dim = int(rows[0]["dim"])
        ids = np.array([int(row["track_id"]) for row in rows], dtype=np.int64)
        vectors = np.vstack(
            [np.frombuffer(row["vector"], dtype=np.float32, count=dim) for row in rows]
        ).astype(np.float32)
        return ids, vectors

    def count_map_projection_points(self, projection_id: str) -> int:
        with self.connect() as conn:  # type: ignore[attr-defined]
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM map_projection_points WHERE projection_id = ?",
                    (projection_id,),
                ).fetchone()[0]
            )
