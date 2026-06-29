"""Store Flow domain: profiles, regions, region tracks, generation runs, centroid vectors."""
from __future__ import annotations

import logging
from uuid import uuid4

import numpy as np

from app.models import (
    FlowGenerationRun,
    FlowProfile,
    FlowRegion,
    FlowRegionTrack,
    utc_now,
)

logger = logging.getLogger(__name__)


def _row_to_flow_profile(row: object) -> FlowProfile:
    return FlowProfile(
        id=str(row["id"]),  # type: ignore[index]
        status=str(row["status"]),  # type: ignore[index]
        model_key=str(row["model_key"]),  # type: ignore[index]
        settings_json=row["settings_json"],  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
        updated_at=str(row["updated_at"]),  # type: ignore[index]
        last_built_at=row["last_built_at"],  # type: ignore[index]
    )


def _row_to_flow_region(row: object) -> FlowRegion:
    return FlowRegion(
        id=str(row["id"]),  # type: ignore[index]
        profile_id=str(row["profile_id"]),  # type: ignore[index]
        region_index=int(row["region_index"]),  # type: ignore[index]
        centroid_ref=row["centroid_ref"],  # type: ignore[index]
        medoid_track_id=int(row["medoid_track_id"]) if row["medoid_track_id"] is not None else None,  # type: ignore[index]
        weight=float(row["weight"]),  # type: ignore[index]
        seed_count=int(row["seed_count"]),  # type: ignore[index]
        candidate_count=int(row["candidate_count"]),  # type: ignore[index]
        summary_json=row["summary_json"],  # type: ignore[index]
        quality_json=row["quality_json"],  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
        updated_at=str(row["updated_at"]),  # type: ignore[index]
    )


def _row_to_flow_region_track(row: object) -> FlowRegionTrack:
    return FlowRegionTrack(
        region_id=str(row["region_id"]),  # type: ignore[index]
        track_id=int(row["track_id"]),  # type: ignore[index]
        role=str(row["role"]),  # type: ignore[index]
        weight=float(row["weight"]) if row["weight"] is not None else None,  # type: ignore[index]
        distance=float(row["distance"]) if row["distance"] is not None else None,  # type: ignore[index]
    )


def _row_to_flow_generation_run(row: object) -> FlowGenerationRun:
    return FlowGenerationRun(
        id=str(row["id"]),  # type: ignore[index]
        session_id=row["session_id"],  # type: ignore[index]
        profile_id=row["profile_id"],  # type: ignore[index]
        region_id=row["region_id"],  # type: ignore[index]
        settings_json=row["settings_json"],  # type: ignore[index]
        candidate_count=int(row["candidate_count"]) if row["candidate_count"] is not None else None,  # type: ignore[index]
        selected_count=int(row["selected_count"]) if row["selected_count"] is not None else None,  # type: ignore[index]
        score_summary_json=row["score_summary_json"],  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
    )


class FlowStoreMixin:
    # ------------------------------------------------------------------
    # Flow profiles
    # ------------------------------------------------------------------

    def get_flow_profile(self, model_key: str) -> FlowProfile | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT * FROM flow_profiles WHERE model_key = ?",
                (model_key,),
            ).fetchone()
        return _row_to_flow_profile(row) if row is not None else None

    def upsert_flow_profile(
        self,
        model_key: str,
        status: str,
        *,
        settings_json: str | None = None,
        last_built_at: str | None = None,
        profile_id: str | None = None,
    ) -> FlowProfile:
        now = utc_now()
        existing = self.get_flow_profile(model_key)
        pid = existing.id if existing else (profile_id or str(uuid4()))
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO flow_profiles (id, status, model_key, settings_json, created_at, updated_at, last_built_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model_key) DO UPDATE SET
                    status = excluded.status,
                    settings_json = COALESCE(excluded.settings_json, settings_json),
                    updated_at = excluded.updated_at,
                    last_built_at = COALESCE(excluded.last_built_at, last_built_at)
                """,
                (pid, status, model_key, settings_json, now, now, last_built_at),
            )
        return self.get_flow_profile(model_key)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Flow regions
    # ------------------------------------------------------------------

    def list_flow_regions(self, profile_id: str) -> list[FlowRegion]:
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT * FROM flow_regions
                WHERE profile_id = ?
                ORDER BY region_index
                """,
                (profile_id,),
            ).fetchall()
        return [_row_to_flow_region(row) for row in rows]

    def get_flow_region(self, region_id: str) -> FlowRegion | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT * FROM flow_regions WHERE id = ?",
                (region_id,),
            ).fetchone()
        return _row_to_flow_region(row) if row is not None else None

    def upsert_flow_region(
        self,
        profile_id: str,
        region_index: int,
        *,
        region_id: str | None = None,
        centroid_ref: str | None = None,
        medoid_track_id: int | None = None,
        weight: float = 1.0,
        seed_count: int = 0,
        candidate_count: int = 0,
        summary_json: str | None = None,
        quality_json: str | None = None,
    ) -> FlowRegion:
        now = utc_now()
        # Reuse existing region id for same profile+index if present
        with self.connect() as conn:  # type: ignore[attr-defined]
            existing = conn.execute(
                "SELECT id FROM flow_regions WHERE profile_id = ? AND region_index = ?",
                (profile_id, region_index),
            ).fetchone()
        rid = existing["id"] if existing else (region_id or str(uuid4()))
        centroid_ref = centroid_ref or rid
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO flow_regions (
                    id, profile_id, region_index, centroid_ref, medoid_track_id,
                    weight, seed_count, candidate_count,
                    summary_json, quality_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    centroid_ref = excluded.centroid_ref,
                    medoid_track_id = excluded.medoid_track_id,
                    weight = excluded.weight,
                    seed_count = excluded.seed_count,
                    candidate_count = excluded.candidate_count,
                    summary_json = excluded.summary_json,
                    quality_json = excluded.quality_json,
                    updated_at = excluded.updated_at
                """,
                (
                    rid, profile_id, region_index, centroid_ref, medoid_track_id,
                    weight, seed_count, candidate_count,
                    summary_json, quality_json, now, now,
                ),
            )
        return self.get_flow_region(rid)  # type: ignore[return-value]

    def delete_flow_regions_for_profile(self, profile_id: str) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute("DELETE FROM flow_regions WHERE profile_id = ?", (profile_id,))

    def update_flow_region_weight(self, region_id: str, weight: float) -> None:
        now = utc_now()
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                "UPDATE flow_regions SET weight = ?, updated_at = ? WHERE id = ?",
                (max(0.0, weight), now, region_id),
            )

    # ------------------------------------------------------------------
    # Flow region tracks
    # ------------------------------------------------------------------

    def replace_flow_region_tracks(
        self,
        region_id: str,
        tracks: list[FlowRegionTrack],
    ) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute("DELETE FROM flow_region_tracks WHERE region_id = ?", (region_id,))
            conn.executemany(
                """
                INSERT OR IGNORE INTO flow_region_tracks (region_id, track_id, role, weight, distance)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(t.region_id, t.track_id, t.role, t.weight, t.distance) for t in tracks],
            )

    def list_flow_region_tracks(
        self,
        region_id: str,
        role: str | None = None,
    ) -> list[FlowRegionTrack]:
        sql = "SELECT * FROM flow_region_tracks WHERE region_id = ?"
        params: list[object] = [region_id]
        if role is not None:
            sql += " AND role = ?"
            params.append(role)
        sql += " ORDER BY weight DESC NULLS LAST, track_id"
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_flow_region_track(row) for row in rows]

    # ------------------------------------------------------------------
    # Flow region embeddings (centroid vectors)
    # ------------------------------------------------------------------

    def save_flow_region_embedding(
        self,
        region_id: str,
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
                INSERT INTO flow_region_embeddings (region_id, model_name, dim, vector, vector_norm, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(region_id, model_name) DO UPDATE SET
                    dim = excluded.dim,
                    vector = excluded.vector,
                    vector_norm = excluded.vector_norm,
                    created_at = excluded.created_at
                """,
                (region_id, model_name, int(v.shape[0]), v.tobytes(), float(np.linalg.norm(v)), now),
            )

    def load_flow_region_embedding(
        self,
        region_id: str,
        model_name: str,
    ) -> np.ndarray | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT dim, vector FROM flow_region_embeddings WHERE region_id = ? AND model_name = ?",
                (region_id, model_name),
            ).fetchone()
        if row is None:
            return None
        return np.frombuffer(row["vector"], dtype=np.float32, count=int(row["dim"])).copy()

    def load_all_flow_region_embeddings(
        self,
        profile_id: str,
        model_name: str,
    ) -> tuple[list[str], np.ndarray]:
        """Return (region_ids, matrix) for all regions in a profile."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT fre.region_id, fre.dim, fre.vector
                FROM flow_region_embeddings fre
                JOIN flow_regions fr ON fr.id = fre.region_id
                WHERE fr.profile_id = ? AND fre.model_name = ?
                ORDER BY fr.region_index
                """,
                (profile_id, model_name),
            ).fetchall()
        if not rows:
            return [], np.empty((0, 0), dtype=np.float32)
        dim = int(rows[0]["dim"])
        ids = [str(r["region_id"]) for r in rows]
        matrix = np.vstack(
            [np.frombuffer(r["vector"], dtype=np.float32, count=dim) for r in rows]
        ).astype(np.float32)
        return ids, matrix

    # ------------------------------------------------------------------
    # Flow generation runs
    # ------------------------------------------------------------------

    def save_flow_generation_run(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        profile_id: str | None = None,
        region_id: str | None = None,
        settings_json: str | None = None,
        candidate_count: int | None = None,
        selected_count: int | None = None,
        score_summary_json: str | None = None,
    ) -> FlowGenerationRun:
        now = utc_now()
        rid = run_id or str(uuid4())
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO flow_generation_runs (
                    id, session_id, profile_id, region_id,
                    settings_json, candidate_count, selected_count,
                    score_summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    candidate_count = excluded.candidate_count,
                    selected_count = excluded.selected_count,
                    score_summary_json = excluded.score_summary_json
                """,
                (
                    rid, session_id, profile_id, region_id,
                    settings_json, candidate_count, selected_count,
                    score_summary_json, now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM flow_generation_runs WHERE id = ?", (rid,)
            ).fetchone()
        return _row_to_flow_generation_run(row)

    def list_flow_generation_runs(
        self,
        session_id: str,
        *,
        limit: int = 20,
    ) -> list[FlowGenerationRun]:
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT * FROM flow_generation_runs
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [_row_to_flow_generation_run(row) for row in rows]

    def count_flow_regions(self, profile_id: str) -> int:
        with self.connect() as conn:  # type: ignore[attr-defined]
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM flow_regions WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()[0]
            )
