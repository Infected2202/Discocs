"""Store Artist Aggregates domain: release-derived centroid storage and lookups."""
from __future__ import annotations

import sqlite3

import numpy as np

from app.models import ArtistAggregate, utc_now


# Ремиксы артиста на чужих релизах: один «юнит» каталога на релиз (как свой
# релиз в центроиде), вектор — среднее эмбеддингов его ремиксов там. Свои
# релизы исключены — они уже входят целиком. Параметр — model_name.
_REMIX_UNITS_SQL = """
    SELECT ta.artist_id, rt.release_id, e.dim, e.vector
    FROM track_artists ta
    JOIN release_tracks rt ON rt.track_id = ta.track_id
    JOIN embeddings e ON e.track_id = ta.track_id AND e.model_name = ?
    WHERE ta.role = 'remixer'
      AND NOT EXISTS (
        SELECT 1 FROM release_artists own
        WHERE own.release_id = rt.release_id
          AND own.artist_id = ta.artist_id
          AND own.role = 'primary'
      )
"""


def _group_remix_units(rows: list[sqlite3.Row]) -> dict[int, dict[int, np.ndarray]]:
    """artist_id → release_id → нормализованное среднее эмбеддингов ремиксов."""
    vectors: dict[int, dict[int, list[np.ndarray]]] = {}
    for row in rows:
        vectors.setdefault(int(row["artist_id"]), {}).setdefault(int(row["release_id"]), []).append(
            np.frombuffer(row["vector"], dtype=np.float32, count=int(row["dim"]))
        )
    units: dict[int, dict[int, np.ndarray]] = {}
    for artist_id, by_release in vectors.items():
        for release_id, items in by_release.items():
            mean = np.vstack(items).mean(axis=0).astype(np.float32)
            norm = float(np.linalg.norm(mean))
            units.setdefault(artist_id, {})[release_id] = mean / norm if norm > 0 else mean
    return units


def _row_to_artist_aggregate(row: sqlite3.Row) -> ArtistAggregate:
    return ArtistAggregate(
        artist_id=int(row["artist_id"]),
        release_count=int(row["release_count"]),
        available_release_count=int(row["available_release_count"]),
        centroid_model=row["centroid_model"],
        medoid_release_id=int(row["medoid_release_id"]) if row["medoid_release_id"] is not None else None,
        embedding_status=str(row["embedding_status"]),
        updated_at=str(row["updated_at"]),
    )


class ArtistAggregatesStoreMixin:
    def upsert_artist_aggregate(self, agg: ArtistAggregate) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO artist_aggregates (
                    artist_id, release_count, available_release_count,
                    centroid_model, medoid_release_id, embedding_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artist_id) DO UPDATE SET
                    release_count = excluded.release_count,
                    available_release_count = excluded.available_release_count,
                    centroid_model = excluded.centroid_model,
                    medoid_release_id = excluded.medoid_release_id,
                    embedding_status = excluded.embedding_status,
                    updated_at = excluded.updated_at
                """,
                (
                    agg.artist_id, agg.release_count, agg.available_release_count,
                    agg.centroid_model, agg.medoid_release_id,
                    agg.embedding_status, agg.updated_at,
                ),
            )

    def get_artist_aggregate(self, artist_id: int) -> ArtistAggregate | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT * FROM artist_aggregates WHERE artist_id = ?", (artist_id,)
            ).fetchone()
        return _row_to_artist_aggregate(row) if row is not None else None

    @staticmethod
    def _artists_needing_aggregation_sql(*, count_only: bool) -> str:
        select = "COUNT(*)" if count_only else "s.artist_id"
        return f"""
            WITH remix_units AS (
                SELECT artist_id, COUNT(DISTINCT release_id) AS unit_count
                FROM ({_REMIX_UNITS_SQL})
                GROUP BY artist_id
            ), candidates AS (
                SELECT DISTINCT ra.artist_id
                FROM release_artists ra
                JOIN artists a ON a.id = ra.artist_id
                WHERE ra.role = 'primary'
                  AND a.normalized_name != 'various artists'
                UNION
                SELECT artist_id FROM remix_units
                UNION
                SELECT aa.artist_id
                FROM artist_aggregates aa
                JOIN artists a ON a.id = aa.artist_id
                WHERE a.normalized_name != 'various artists'
            ), source AS (
                SELECT
                    candidates.artist_id,
                    COUNT(DISTINCT CASE
                        WHEN relagg.centroid_model = ?
                         AND relagg.embedding_status = 'ready'
                         AND re.release_id IS NOT NULL
                        THEN ra.release_id
                    END) AS release_count,
                    MAX(CASE
                        WHEN relagg.centroid_model = ?
                         AND relagg.embedding_status = 'ready'
                         AND re.release_id IS NOT NULL
                        THEN relagg.updated_at
                    END) AS newest_release_update
                FROM candidates
                LEFT JOIN release_artists ra
                  ON ra.artist_id = candidates.artist_id AND ra.role = 'primary'
                LEFT JOIN release_aggregates relagg ON relagg.release_id = ra.release_id
                LEFT JOIN release_embeddings re
                  ON re.release_id = ra.release_id AND re.model_name = ?
                GROUP BY candidates.artist_id
            )
            SELECT {select}
            FROM (
                SELECT source.artist_id,
                    source.release_count + COALESCE(ru.unit_count, 0) AS unit_count,
                    source.newest_release_update
                FROM source
                LEFT JOIN remix_units ru ON ru.artist_id = source.artist_id
            ) s
            LEFT JOIN artist_aggregates aa ON aa.artist_id = s.artist_id
            WHERE aa.artist_id IS NULL
               OR aa.centroid_model != ?
               OR aa.available_release_count != s.unit_count
               OR (s.unit_count > 0 AND aa.embedding_status != 'ready')
               OR (s.unit_count = 0 AND aa.embedding_status != 'unavailable')
               OR (s.newest_release_update IS NOT NULL AND aa.updated_at < s.newest_release_update)
        """

    def list_artist_ids_for_aggregation(self, *, model_name: str, limit: int = 0) -> list[int]:
        sql = self._artists_needing_aggregation_sql(count_only=False) + " ORDER BY s.artist_id"
        params: list[object] = [model_name] * 5
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(sql, params).fetchall()
        return [int(row[0]) for row in rows]

    def count_artists_needing_aggregation(self, model_name: str) -> int:
        sql = self._artists_needing_aggregation_sql(count_only=True)
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(sql, [model_name] * 5).fetchone()
        return int(row[0])

    def count_artist_aggregates(self, model_name: str | None = None) -> int:
        with self.connect() as conn:  # type: ignore[attr-defined]
            if model_name is None:
                return int(conn.execute("SELECT COUNT(*) FROM artist_aggregates").fetchone()[0])
            return int(conn.execute(
                "SELECT COUNT(*) FROM artist_aggregates WHERE centroid_model = ? AND embedding_status = 'ready'",
                (model_name,),
            ).fetchone()[0])

    def list_artist_release_embeddings(
        self, artist_id: int, model_name: str
    ) -> tuple[np.ndarray, np.ndarray]:
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                """
                SELECT DISTINCT re.release_id, re.dim, re.vector
                FROM release_artists ra
                JOIN release_aggregates relagg ON relagg.release_id = ra.release_id
                JOIN release_embeddings re ON re.release_id = ra.release_id
                WHERE ra.artist_id = ? AND ra.role = 'primary'
                  AND relagg.centroid_model = ? AND relagg.embedding_status = 'ready'
                  AND re.model_name = ?
                ORDER BY re.release_id
                """,
                (artist_id, model_name, model_name),
            ).fetchall()
        if not rows:
            return np.array([], dtype=np.int64), np.empty((0, 0), dtype=np.float32)
        dim = int(rows[0]["dim"])
        ids = np.array([int(row["release_id"]) for row in rows], dtype=np.int64)
        matrix = np.vstack([
            np.frombuffer(row["vector"], dtype=np.float32, count=dim) for row in rows
        ]).astype(np.float32)
        return ids, matrix

    def list_artist_remix_embeddings(
        self, artist_id: int, model_name: str
    ) -> tuple[np.ndarray, np.ndarray]:
        """Юниты ремиксов артиста (release_id, матрица) — см. ``_REMIX_UNITS_SQL``."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                f"SELECT * FROM ({_REMIX_UNITS_SQL}) WHERE artist_id = ?",
                (model_name, artist_id),
            ).fetchall()
        units = _group_remix_units(rows).get(artist_id, {})
        if not units:
            return np.array([], dtype=np.int64), np.empty((0, 0), dtype=np.float32)
        release_ids = sorted(units)
        matrix = np.vstack([units[release_id] for release_id in release_ids]).astype(np.float32)
        return np.array(release_ids, dtype=np.int64), matrix

    def load_release_embeddings_for_artists(
        self, artist_ids: list[int], model_name: str
    ) -> dict[int, np.ndarray]:
        if not artist_ids:
            return {}
        placeholders = ",".join("?" for _ in artist_ids)
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                f"""
                SELECT DISTINCT ra.artist_id, re.release_id, re.dim, re.vector
                FROM release_artists ra
                JOIN release_aggregates relagg ON relagg.release_id = ra.release_id
                JOIN release_embeddings re ON re.release_id = ra.release_id
                WHERE ra.artist_id IN ({placeholders}) AND ra.role = 'primary'
                  AND relagg.centroid_model = ? AND relagg.embedding_status = 'ready'
                  AND re.model_name = ?
                ORDER BY ra.artist_id, re.release_id
                """,
                (*artist_ids, model_name, model_name),
            ).fetchall()
            remix_rows = conn.execute(
                f"SELECT * FROM ({_REMIX_UNITS_SQL}) WHERE artist_id IN ({placeholders})",
                (model_name, *artist_ids),
            ).fetchall()
        grouped: dict[int, list[np.ndarray]] = {}
        for row in rows:
            grouped.setdefault(int(row["artist_id"]), []).append(
                np.frombuffer(row["vector"], dtype=np.float32, count=int(row["dim"]))
            )
        # Каталог ремиксера включает и его ремиксы на чужих релизах.
        for artist_id, units in _group_remix_units(remix_rows).items():
            grouped.setdefault(artist_id, []).extend(units[release_id] for release_id in sorted(units))
        return {artist_id: np.vstack(vectors).astype(np.float32) for artist_id, vectors in grouped.items()}

    def save_artist_embedding(self, artist_id: int, model_name: str, vector: np.ndarray) -> None:
        value = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(value))
        if norm > 0:
            value = value / norm
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO artist_embeddings (artist_id, model_name, dim, vector, vector_norm, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(artist_id, model_name) DO UPDATE SET
                    dim = excluded.dim, vector = excluded.vector,
                    vector_norm = excluded.vector_norm, created_at = excluded.created_at
                """,
                (artist_id, model_name, int(value.shape[0]), value.tobytes(), float(np.linalg.norm(value)), utc_now()),
            )

    def delete_artist_embedding(self, artist_id: int, model_name: str) -> None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                "DELETE FROM artist_embeddings WHERE artist_id = ? AND model_name = ?",
                (artist_id, model_name),
            )

    def load_artist_embedding(self, artist_id: int, model_name: str) -> np.ndarray | None:
        with self.connect() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT dim, vector FROM artist_embeddings WHERE artist_id = ? AND model_name = ?",
                (artist_id, model_name),
            ).fetchone()
        if row is None:
            return None
        return np.frombuffer(row["vector"], dtype=np.float32, count=int(row["dim"])).copy()

    def load_all_artist_embeddings(self, model_name: str) -> tuple[np.ndarray, np.ndarray]:
        with self.connect() as conn:  # type: ignore[attr-defined]
            rows = conn.execute(
                "SELECT artist_id, dim, vector FROM artist_embeddings WHERE model_name = ? ORDER BY artist_id",
                (model_name,),
            ).fetchall()
        if not rows:
            return np.array([], dtype=np.int64), np.empty((0, 0), dtype=np.float32)
        dim = int(rows[0]["dim"])
        ids = np.array([int(row["artist_id"]) for row in rows], dtype=np.int64)
        matrix = np.vstack([
            np.frombuffer(row["vector"], dtype=np.float32, count=dim) for row in rows
        ]).astype(np.float32)
        return ids, matrix
