"""Timeline artifact metadata and durable per-track analysis state."""
from __future__ import annotations

from collections.abc import Iterable

from app.models import utc_now


class TimelineStoreMixin:
    def audio_bundle_counts(
        self,
        feature_extractor: str,
        pack_name: str,
        timeline_extractor: str,
    ) -> dict[str, int]:
        """Count active tracks whose scalar features and current timeline are both ready."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN
                    EXISTS (
                        SELECT 1 FROM track_features f
                        WHERE f.track_id=t.id AND f.extractor=?
                    ) AND a.track_id IS NOT NULL AND
                    a.source_path=t.path AND a.source_mtime=t.mtime AND
                    a.source_file_size=t.file_size
                THEN 1 ELSE 0 END), 0) AS ready
                FROM tracks t
                LEFT JOIN track_timeline_artifacts a
                  ON a.track_id=t.id AND a.pack_name=? AND a.extractor=?
                WHERE t.missing_at IS NULL
                """,
                (feature_extractor, pack_name, timeline_extractor),
            ).fetchone()
        total = int(row["total"])
        ready = int(row["ready"])
        return {"total": total, "ready": ready, "missing": total - ready}

    def list_tracks_needing_audio_bundle(
        self,
        feature_extractor: str,
        pack_name: str,
        timeline_extractor: str,
        *,
        limit: int | None = None,
    ):
        query = """
            SELECT t.* FROM tracks t
            LEFT JOIN track_timeline_artifacts a
              ON a.track_id=t.id AND a.pack_name=? AND a.extractor=?
            WHERE t.missing_at IS NULL AND (
                NOT EXISTS (
                    SELECT 1 FROM track_features f
                    WHERE f.track_id=t.id AND f.extractor=?
                ) OR a.track_id IS NULL OR a.source_path != t.path OR
                a.source_mtime != t.mtime OR a.source_file_size != t.file_size
            ) ORDER BY t.id
        """
        params: list[object] = [pack_name, timeline_extractor, feature_extractor]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        from app.store._helpers import row_to_track
        with self.connect() as conn:
            return [row_to_track(row) for row in conn.execute(query, params).fetchall()]

    def timeline_artifact_counts(self, pack_name: str, extractor: str) -> dict[str, int]:
        """Return ready/missing counts for active tracks using exact source identity."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN
                        a.track_id IS NOT NULL AND
                        a.source_path = t.path AND
                        a.source_mtime = t.mtime AND
                        a.source_file_size = t.file_size
                    THEN 1 ELSE 0 END), 0) AS ready,
                    COALESCE(SUM(CASE WHEN
                        a.track_id IS NOT NULL AND
                        a.source_path = t.path AND
                        a.source_mtime = t.mtime AND
                        a.source_file_size = t.file_size
                    THEN a.payload_bytes ELSE 0 END), 0) AS storage_bytes
                FROM tracks t
                LEFT JOIN track_timeline_artifacts a
                  ON a.track_id=t.id AND a.pack_name=? AND a.extractor=?
                WHERE t.missing_at IS NULL
                """,
                (pack_name, extractor),
            ).fetchone()
        total = int(row["total"])
        ready = int(row["ready"])
        return {
            "ready": ready,
            "missing": total - ready,
            "total": total,
            "storage_bytes": int(row["storage_bytes"]),
        }

    def upsert_timeline_artifact(self, metadata: dict[str, object]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO track_timeline_artifacts (
                    track_id, pack_name, extractor, schema_version, source_path,
                    source_mtime, source_file_size, manifest_path, payload_path,
                    payload_bytes, checksum_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id, pack_name, extractor) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    source_path=excluded.source_path,
                    source_mtime=excluded.source_mtime,
                    source_file_size=excluded.source_file_size,
                    manifest_path=excluded.manifest_path,
                    payload_path=excluded.payload_path,
                    payload_bytes=excluded.payload_bytes,
                    checksum_sha256=excluded.checksum_sha256,
                    created_at=excluded.created_at
                """,
                (
                    metadata["track_id"], metadata["pack_name"], metadata["extractor"],
                    metadata["schema_version"], metadata["source_path"], metadata["source_mtime"],
                    metadata["source_file_size"], metadata["manifest_path"], metadata["payload_path"],
                    metadata["payload_bytes"], metadata["checksum_sha256"],
                    metadata.get("created_at") or utc_now(),
                ),
            )

    def get_timeline_artifact(self, track_id: int, pack_name: str, extractor: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM track_timeline_artifacts WHERE track_id=? AND pack_name=? AND extractor=?",
                (track_id, pack_name, extractor),
            ).fetchone()

    def delete_timeline_artifact(self, track_id: int, pack_name: str, extractor: str):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM track_timeline_artifacts WHERE track_id=? AND pack_name=? AND extractor=?",
                (track_id, pack_name, extractor),
            ).fetchone()
            conn.execute(
                "DELETE FROM track_timeline_artifacts WHERE track_id=? AND pack_name=? AND extractor=?",
                (track_id, pack_name, extractor),
            )
        return row

    def set_timeline_analysis_status(
        self, track_id: int, pack_name: str, extractor: str, status: str,
        *, error: str | None = None, job_id: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO track_timeline_analysis
                    (track_id, pack_name, extractor, status, error, job_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id, pack_name, extractor) DO UPDATE SET
                    status=excluded.status, error=excluded.error,
                    job_id=excluded.job_id, updated_at=excluded.updated_at
                """,
                (track_id, pack_name, extractor, status, error, job_id, utc_now()),
            )

    def get_timeline_analysis_states(
        self, track_ids: Iterable[int], pack_name: str, extractor: str,
    ) -> dict[int, dict[str, object]]:
        ids = list(dict.fromkeys(int(track_id) for track_id in track_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM track_timeline_analysis WHERE track_id IN ({placeholders}) AND pack_name=? AND extractor=?",
                (*ids, pack_name, extractor),
            ).fetchall()
        return {int(row["track_id"]): dict(row) for row in rows}

    def timeline_artifact_file_paths(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT manifest_path, payload_path FROM track_timeline_artifacts").fetchall()
        return {str(row[key]) for row in rows for key in ("manifest_path", "payload_path")}

    def list_tracks_needing_timeline(self, pack_name: str, extractor: str, *, limit: int | None = None):
        query = """
            SELECT t.* FROM tracks t
            LEFT JOIN track_timeline_artifacts a
              ON a.track_id=t.id AND a.pack_name=? AND a.extractor=?
            WHERE t.missing_at IS NULL AND (
                a.track_id IS NULL OR a.source_path != t.path OR
                a.source_mtime != t.mtime OR a.source_file_size != t.file_size
            ) ORDER BY t.id
        """
        params: list[object] = [pack_name, extractor]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        from app.store._helpers import row_to_track
        with self.connect() as conn:
            return [row_to_track(row) for row in conn.execute(query, params).fetchall()]
