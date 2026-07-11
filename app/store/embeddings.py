"""Store Embeddings domain: embeddings, predictions, model outputs, audio features.

Part of the app/store package — Stage 7 refactoring.
Do not import this module directly; use app.store instead.
"""
from __future__ import annotations

from __future__ import annotations

import logging
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz; UTC = _tz.utc
from pathlib import Path
from threading import Lock
from uuid import uuid4

import numpy as np

from app.store._helpers import row_to_track
from app.library import (
    ArtistCredit,
    TrackMetadataEnvelope,
    clean_display_text,
    envelope_from_scanned_track,
    envelope_from_track_row,
    normalize_text,
    parse_artist_credit,
    release_identity_key,
    release_title_for_envelope,
)
from app.models import (
    AnalysisJob,
    AnalysisTask,
    AnalysisWorker,
    Artist,
    ArtistSummaryRow,
    COMPLETION_FRACTION,
    EARLY_SKIP_FRACTION,
    EARLY_SKIP_SECONDS,
    ExternalTrack,
    FeatureFilter,
    FeatureSummary,
    FeatureTrack,
    GeneratedMix,
    GeneratedMixItem,
    GENERATED_MIX_STATUSES,
    GENERATED_MIX_TYPES,
    HeadSummary,
    InstantMixRequest,
    LATE_SKIP_FRACTION,
    MEANINGFUL_LISTEN_FRACTION,
    MEANINGFUL_LISTEN_SECONDS,
    NormalizationStatus,
    Playlist,
    PlaylistItem,
    PlaybackEvent,
    PLAYBACK_EVENT_TYPES,
    PlaybackEventResult,
    PLAYBACK_MODES,
    PLAYBACK_REPEAT_MODES,
    PlaybackSession,
    PLAYBACK_SESSION_STATUSES,
    PLAYBACK_SOURCE_TYPES,
    QueueItem,
    QUEUE_ORIGINS,
    QUEUE_STATUSES,
    Release,
    ReleaseSummaryRow,
    ReleaseTrackRow,
    SimilarTrack,
    Track,
    TrackFeature,
    TrackListing,
    TrackModelOutput,
    TrackPrediction,
    UserArtistPreference,
    UserReleasePreference,
    UserTrackPreference,
    utc_now,
)
from app.scanner import ScannedTrack


logger = logging.getLogger(__name__)
INIT_LOCK = Lock()
INITIALIZED_DB_PATHS: set[Path] = set()
_LIMIT_CLAUSE = " LIMIT ?"
_TRACK_ORDER_BY_ARTIST_ALBUM_TITLE = "t.artist, t.album, t.title, t.id"

class EmbeddingsStoreMixin:
    def save_embedding(self, track_id: int, model_name: str, vector: np.ndarray) -> None:
        vector = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        now = utc_now()
        logger.debug(
            "Saving embedding track_id=%s model=%s dim=%s norm=%s",
            track_id,
            model_name,
            vector.shape[0],
            norm,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO embeddings (track_id, model_name, dim, vector, vector_norm, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id, model_name) DO UPDATE SET
                    dim = excluded.dim,
                    vector = excluded.vector,
                    vector_norm = excluded.vector_norm,
                    created_at = excluded.created_at
                """,
                (track_id, model_name, int(vector.shape[0]), vector.tobytes(), norm, now),
            )

    def save_predictions(
        self,
        track_id: int,
        model_name: str,
        predictions: list[TrackPrediction],
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM track_predictions WHERE track_id = ? AND model_name = ?",
                (track_id, model_name),
            )
            conn.executemany(
                """
                INSERT INTO track_predictions (
                    track_id, model_name, label, score, rank, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        track_id,
                        model_name,
                        prediction.label,
                        float(prediction.score),
                        int(prediction.rank),
                        now,
                    )
                    for prediction in predictions
                ],
            )

    def save_model_output(
        self,
        track_id: int,
        model_name: str,
        scores: np.ndarray,
        aggregation: str,
    ) -> None:
        vector = np.asarray(scores, dtype=np.float32).reshape(-1)
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO track_model_outputs (
                    track_id, model_name, dim, dtype, aggregation, scores_blob, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id, model_name) DO UPDATE SET
                    dim = excluded.dim,
                    dtype = excluded.dtype,
                    aggregation = excluded.aggregation,
                    scores_blob = excluded.scores_blob,
                    created_at = excluded.created_at
                """,
                (
                    track_id,
                    model_name,
                    int(vector.shape[0]),
                    "float32",
                    aggregation,
                    vector.tobytes(),
                    now,
                ),
            )

    def load_model_output(self, track_id: int, model_name: str) -> TrackModelOutput | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT model_name, dim, dtype, aggregation, scores_blob
                FROM track_model_outputs
                WHERE track_id = ? AND model_name = ?
                """,
                (track_id, model_name),
            ).fetchone()
        if row is None:
            return None
        dtype = str(row["dtype"])
        if dtype != "float32":
            raise ValueError(f"Unsupported model output dtype: {dtype}")
        scores = np.frombuffer(
            row["scores_blob"],
            dtype=np.float32,
            count=int(row["dim"]),
        ).copy()
        return TrackModelOutput(
            model_name=str(row["model_name"]),
            scores=scores,
            aggregation=str(row["aggregation"]),
            dtype=dtype,
        )

    def list_model_outputs(self, track_id: int) -> list[TrackModelOutput]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT model_name, dim, dtype, aggregation, scores_blob
                FROM track_model_outputs
                WHERE track_id = ?
                ORDER BY model_name
                """,
                (track_id,),
            ).fetchall()
        outputs = []
        for row in rows:
            dtype = str(row["dtype"])
            if dtype != "float32":
                raise ValueError(f"Unsupported model output dtype: {dtype}")
            outputs.append(
                TrackModelOutput(
                    model_name=str(row["model_name"]),
                    scores=np.frombuffer(
                        row["scores_blob"],
                        dtype=np.float32,
                        count=int(row["dim"]),
                    ).copy(),
                    aggregation=str(row["aggregation"]),
                    dtype=dtype,
                )
            )
        return outputs

    def load_predictions(
        self,
        track_id: int,
        model_name: str,
        limit: int = 20,
    ) -> list[TrackPrediction]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT label, score, rank FROM track_predictions
                WHERE track_id = ? AND model_name = ?
                ORDER BY rank
                LIMIT ?
                """,
                (track_id, model_name, limit),
            ).fetchall()
        return [
            TrackPrediction(
                label=str(row["label"]),
                score=float(row["score"]),
                rank=int(row["rank"]),
            )
            for row in rows
        ]

    def list_predictions(self, track_id: int) -> dict[str, list[TrackPrediction]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT model_name, label, score, rank FROM track_predictions
                WHERE track_id = ?
                ORDER BY model_name, rank
                """,
                (track_id,),
            ).fetchall()
        predictions: dict[str, list[TrackPrediction]] = {}
        for row in rows:
            predictions.setdefault(str(row["model_name"]), []).append(
                TrackPrediction(
                    label=str(row["label"]),
                    score=float(row["score"]),
                    rank=int(row["rank"]),
                )
            )
        return predictions

    def top_prediction_by_track(
        self,
        model_name: str,
        track_ids: list[int],
        *,
        rank: int = 1,
    ) -> dict[int, tuple[str, float]]:
        """Return ``{track_id: (label, score)}`` for one rank of a head model.

        Bulk lookup backing the map's genre-gradient coloring: the rank-1
        prediction of, e.g., ``genre_discogs400`` for every projected track in
        a single query (mirrors ``get_tracks``' single-``IN`` shape, which
        already handles the full projection at 49k tracks). Tracks with no
        prediction for the model are simply absent from the result.
        """
        ids = list(dict.fromkeys(int(t) for t in track_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT track_id, label, score FROM track_predictions
                WHERE model_name = ? AND rank = ? AND track_id IN ({placeholders})
                """,
                (model_name, int(rank), *ids),
            ).fetchall()
        return {
            int(row["track_id"]): (str(row["label"]), float(row["score"]))
            for row in rows
        }

    def count_predictions(self, model_name: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(DISTINCT track_id) FROM track_predictions WHERE model_name = ?",
                    (model_name,),
                ).fetchone()[0]
            )

    def count_model_outputs(self, model_name: str | None = None) -> int:
        with self.connect() as conn:
            if model_name is None:
                return int(
                    conn.execute("SELECT COUNT(*) FROM track_model_outputs").fetchone()[0]
                )
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM track_model_outputs WHERE model_name = ?",
                    (model_name,),
                ).fetchone()[0]
            )

    def count_model_outputs_by_model(self, model_names: list[str]) -> dict[str, int]:
        if not model_names:
            return {}
        placeholders = ",".join("?" for _name in model_names)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT model_name, COUNT(*) AS count
                FROM track_model_outputs
                WHERE model_name IN ({placeholders})
                GROUP BY model_name
                """,
                model_names,
            ).fetchall()
        counts = dict.fromkeys(model_names, 0)
        counts.update({str(row["model_name"]): int(row["count"]) for row in rows})
        return counts

    def count_tracks_missing_head_pack(self, model_names: list[str]) -> int:
        if not model_names:
            return 0
        placeholders = ",".join("?" for _name in model_names)
        with self.connect() as conn:
            return int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM tracks t
                    LEFT JOIN (
                        SELECT track_id, COUNT(DISTINCT model_name) AS model_count
                        FROM track_model_outputs
                        WHERE model_name IN ({placeholders})
                        GROUP BY track_id
                    ) o ON o.track_id = t.id
                    WHERE COALESCE(o.model_count, 0) < ?
                      AND t.missing_at IS NULL
                    """,
                    [*model_names, len(model_names)],
                ).fetchone()[0]
            )

    def list_tracks_missing_head_pack(
        self,
        model_names: list[str],
        limit: int | None = None,
    ) -> list[Track]:
        if not model_names:
            return []
        placeholders = ",".join("?" for _name in model_names)
        sql = f"""
            SELECT t.* FROM tracks t
            LEFT JOIN (
                SELECT track_id, COUNT(DISTINCT model_name) AS model_count
                FROM track_model_outputs
                WHERE model_name IN ({placeholders})
                GROUP BY track_id
            ) o ON o.track_id = t.id
            WHERE COALESCE(o.model_count, 0) < ?
              AND t.missing_at IS NULL
            ORDER BY t.id
        """
        params: list[object] = [*model_names, len(model_names)]
        if limit is not None:
            sql += _LIMIT_CLAUSE
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_track(row) for row in rows]

    def save_features(self, track_id: int, features: list[TrackFeature]) -> None:
        now = utc_now()
        with self.connect() as conn:
            extractors = sorted({feature.extractor for feature in features})
            for extractor in extractors:
                conn.execute(
                    "DELETE FROM track_features WHERE track_id = ? AND extractor = ?",
                    (track_id, extractor),
                )
            conn.executemany(
                """
                INSERT INTO track_features (
                    track_id, feature_name, value, text_value, unit, confidence,
                    extractor, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        track_id,
                        feature.name,
                        feature.value,
                        feature.text_value,
                        feature.unit,
                        feature.confidence,
                        feature.extractor,
                        now,
                    )
                    for feature in features
                ],
            )

    def delete_features_for_tracks(self, track_ids: list[int], extractor: str) -> int:
        ids = list(dict.fromkeys(track_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _id in ids)
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                DELETE FROM track_features
                WHERE extractor = ?
                  AND track_id IN ({placeholders})
                """,
                [extractor, *ids],
            )
            return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def load_features(self, track_id: int, extractor: str | None = None) -> list[TrackFeature]:
        sql = """
            SELECT feature_name, value, text_value, unit, confidence, extractor
            FROM track_features
            WHERE track_id = ?
        """
        params: list[object] = [track_id]
        if extractor is not None:
            sql += " AND extractor = ?"
            params.append(extractor)
        sql += " ORDER BY extractor, feature_name"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            TrackFeature(
                name=str(row["feature_name"]),
                value=float(row["value"]) if row["value"] is not None else None,
                text_value=row["text_value"],
                unit=row["unit"],
                confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                extractor=str(row["extractor"]),
            )
            for row in rows
        ]

    def count_feature_tracks(self, extractor: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(DISTINCT track_id) FROM track_features WHERE extractor = ?",
                    (extractor,),
                ).fetchone()[0]
            )

    def count_tracks_missing_features(self, extractor: str) -> int:
        with self.connect() as conn:
            active_tracks = int(
                conn.execute(
                    "SELECT COUNT(*) FROM tracks WHERE missing_at IS NULL"
                ).fetchone()[0]
            )
            complete_tracks = int(
                conn.execute(
                    """
                    SELECT COUNT(DISTINCT f.track_id)
                    FROM track_features f
                    JOIN tracks t ON t.id = f.track_id
                    WHERE f.extractor = ?
                      AND t.missing_at IS NULL
                    """,
                    (extractor,),
                ).fetchone()[0]
            )
        return max(active_tracks - complete_tracks, 0)

    def list_tracks_missing_features(
        self,
        extractor: str,
        limit: int | None = None,
    ) -> list[Track]:
        sql = """
            SELECT t.* FROM tracks t
            WHERE NOT EXISTS (
                SELECT 1 FROM track_features f
                WHERE f.track_id = t.id
                  AND f.extractor = ?
            )
              AND t.missing_at IS NULL
            ORDER BY t.id
        """
        params: list[object] = [extractor]
        if limit is not None:
            sql += _LIMIT_CLAUSE
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_track(row) for row in rows]

    def count_tracks_missing_predictions(self, model_name: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM tracks t
                    LEFT JOIN (
                        SELECT DISTINCT track_id FROM track_predictions
                        WHERE model_name = ?
                    ) p ON p.track_id = t.id
                    WHERE p.track_id IS NULL
                      AND t.missing_at IS NULL
                    """,
                    (model_name,),
                ).fetchone()[0]
            )

    def list_tracks_missing_predictions(
        self,
        model_name: str,
        limit: int | None = None,
    ) -> list[Track]:
        sql = """
            SELECT t.* FROM tracks t
            LEFT JOIN (
                SELECT DISTINCT track_id FROM track_predictions
                WHERE model_name = ?
            ) p ON p.track_id = t.id
            WHERE p.track_id IS NULL
              AND t.missing_at IS NULL
            ORDER BY t.id
        """
        params: list[object] = [model_name]
        if limit is not None:
            sql += _LIMIT_CLAUSE
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_track(row) for row in rows]

    def load_embedding(self, track_id: int, model_name: str) -> np.ndarray | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT dim, vector FROM embeddings WHERE track_id = ? AND model_name = ?",
                (track_id, model_name),
            ).fetchone()
        if row is None:
            return None
        return np.frombuffer(row["vector"], dtype=np.float32, count=int(row["dim"])).copy()

    def load_embeddings(self, model_name: str) -> tuple[np.ndarray, np.ndarray]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT track_id, dim, vector FROM embeddings
                WHERE model_name = ?
                ORDER BY track_id
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

    def count_tracks(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])

    def count_embeddings(self, model_name: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM embeddings WHERE model_name = ?",
                    (model_name,),
                ).fetchone()[0]
            )

    def list_feature_summaries(self, extractor: str | None = None) -> list[FeatureSummary]:
        sql = """
            SELECT
                feature_name,
                extractor,
                COUNT(value) AS value_count,
                COUNT(text_value) AS text_count,
                COUNT(DISTINCT track_id) AS track_count,
                MIN(value) AS min_value,
                MAX(value) AS max_value,
                AVG(value) AS avg_value,
                MAX(unit) AS unit
            FROM track_features
        """
        params: list[object] = []
        if extractor is not None:
            sql += " WHERE extractor = ?"
            params.append(extractor)
        sql += " GROUP BY extractor, feature_name ORDER BY extractor, feature_name"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            FeatureSummary(
                name=str(row["feature_name"]),
                extractor=str(row["extractor"]),
                value_count=int(row["value_count"] or 0),
                text_count=int(row["text_count"] or 0),
                track_count=int(row["track_count"] or 0),
                min_value=float(row["min_value"]) if row["min_value"] is not None else None,
                max_value=float(row["max_value"]) if row["max_value"] is not None else None,
                avg_value=float(row["avg_value"]) if row["avg_value"] is not None else None,
                unit=row["unit"],
            )
            for row in rows
        ]

    def list_feature_text_values(
        self,
        feature_name: str,
        extractor: str | None = None,
        limit: int = 100,
    ) -> list[tuple[str, int]]:
        sql = """
            SELECT text_value, COUNT(DISTINCT track_id) AS track_count
            FROM track_features
            WHERE feature_name = ?
              AND text_value IS NOT NULL
              AND text_value != ''
        """
        params: list[object] = [feature_name]
        if extractor is not None:
            sql += " AND extractor = ?"
            params.append(extractor)
        sql += " GROUP BY text_value ORDER BY track_count DESC, text_value LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(str(row["text_value"]), int(row["track_count"])) for row in rows]

    def search_tracks_by_features(
        self,
        filters: list[FeatureFilter],
        *,
        query: str = "",
        extractor: str | None = None,
        sort_by: str | None = None,
        sort_direction: str = "asc",
        limit: int = 50,
    ) -> list[FeatureTrack]:
        where = ["t.missing_at IS NULL"]
        params: list[object] = []
        cleaned_query = query.strip()
        if cleaned_query:
            like = f"%{cleaned_query}%"
            where.append(
                "(t.artist LIKE ? OR t.title LIKE ? OR t.album LIKE ? OR t.path LIKE ?)"
            )
            params.extend([like, like, like, like])

        for index, item in enumerate(filters):
            alias = f"f{index}"
            join_where = [
                f"{alias}.track_id = t.id",
                f"{alias}.feature_name = ?",
            ]
            params.append(item.name)
            if extractor is not None:
                join_where.append(f"{alias}.extractor = ?")
                params.append(extractor)
            if item.min_value is not None:
                join_where.append(f"{alias}.value >= ?")
                params.append(item.min_value)
            if item.max_value is not None:
                join_where.append(f"{alias}.value <= ?")
                params.append(item.max_value)
            if item.text_values:
                placeholders = ",".join("?" for _value in item.text_values)
                join_where.append(f"{alias}.text_value IN ({placeholders})")
                params.extend(item.text_values)
            where.append(f"EXISTS (SELECT 1 FROM track_features {alias} WHERE {' AND '.join(join_where)})")

        feature_where = ""
        feature_params: list[object] = []
        if extractor is not None:
            feature_where = " AND tf.extractor = ?"
            feature_params.append(extractor)

        sort_direction = "DESC" if sort_direction.lower() == "desc" else "ASC"
        inner_order_sql = _TRACK_ORDER_BY_ARTIST_ALBUM_TITLE
        outer_order_sql = _TRACK_ORDER_BY_ARTIST_ALBUM_TITLE
        sort_select = "NULL AS sort_value"
        if sort_by:
            sort_select = "sf.value AS sort_value"
            inner_order_sql = (
                f"sf.value IS NULL, sf.value {sort_direction}, t.artist, t.title, t.id"
            )
            outer_order_sql = (
                f"t.sort_value IS NULL, t.sort_value {sort_direction}, t.artist, t.title, t.id"
            )

        sort_join = ""
        sort_params: list[object] = []
        if sort_by:
            sort_join = "LEFT JOIN track_features sf ON sf.track_id = t.id AND sf.feature_name = ?"
            sort_params.append(sort_by)
            if extractor is not None:
                sort_join += " AND sf.extractor = ?"
                sort_params.append(extractor)

        sql = f"""
            SELECT t.*, tf.feature_name, tf.value, tf.text_value, tf.unit, tf.confidence, tf.extractor
            FROM (
                SELECT t.*, {sort_select}
                FROM tracks t
                {sort_join}
                WHERE {' AND '.join(where)}
                ORDER BY {inner_order_sql}
                LIMIT ?
            ) t
            LEFT JOIN track_features tf ON tf.track_id = t.id{feature_where}
            ORDER BY {outer_order_sql}, tf.extractor, tf.feature_name
        """
        all_params = [*sort_params, *params, limit, *feature_params]
        with self.connect() as conn:
            rows = conn.execute(sql, all_params).fetchall()

        grouped: dict[int, FeatureTrack] = {}
        for row in rows:
            track = grouped.get(int(row["id"]))
            if track is None:
                track = FeatureTrack(row_to_track(row), [])
                grouped[int(row["id"])] = track
            if row["feature_name"] is not None:
                track.features.append(
                    TrackFeature(
                        name=str(row["feature_name"]),
                        value=float(row["value"]) if row["value"] is not None else None,
                        text_value=row["text_value"],
                        unit=row["unit"],
                        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                        extractor=str(row["extractor"]),
                    )
                )
        return list(grouped.values())

    def list_head_summaries(self) -> list[HeadSummary]:
        with self.connect() as conn:
            output_rows = conn.execute(
                """
                SELECT
                    model_name,
                    COUNT(*) AS output_count
                FROM track_model_outputs o
                GROUP BY o.model_name
                ORDER BY o.model_name
                """
            ).fetchall()
        return [
            HeadSummary(
                model_name=str(row["model_name"]),
                output_count=int(row["output_count"] or 0),
                prediction_track_count=int(row["output_count"] or 0),
                label_count=0,
                max_score=1.0,
                avg_score=None,
            )
            for row in output_rows
        ]

    def list_head_prediction_labels(
        self,
        model_name: str,
        limit: int = 100,
    ) -> list[tuple[str, int, float | None, float | None]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    label,
                    COUNT(DISTINCT track_id) AS track_count,
                    AVG(score) AS avg_score,
                    MAX(score) AS max_score
                FROM track_predictions
                WHERE model_name = ?
                GROUP BY label
                ORDER BY track_count DESC, avg_score DESC, label
                LIMIT ?
                """,
                (model_name, limit),
            ).fetchall()
        return [
            (
                str(row["label"]),
                int(row["track_count"] or 0),
                float(row["avg_score"]) if row["avg_score"] is not None else None,
                float(row["max_score"]) if row["max_score"] is not None else None,
            )
            for row in rows
        ]

    def search_tracks_by_head_predictions(
        self,
        filters: list[FeatureFilter],
        *,
        query: str = "",
        sort_by: str | None = None,
        sort_direction: str = "desc",
        limit: int = 50,
    ) -> list[FeatureTrack]:
        where = ["t.missing_at IS NULL"]
        params: list[object] = []
        cleaned_query = query.strip()
        if cleaned_query:
            like = f"%{cleaned_query}%"
            where.append(
                "(t.artist LIKE ? OR t.title LIKE ? OR t.album LIKE ? OR t.path LIKE ?)"
            )
            params.extend([like, like, like, like])

        selected_models: set[str] = set()
        selected_labels: set[str] = set()
        for index, item in enumerate(filters):
            selected_models.add(item.name)
            alias = f"p{index}"
            prediction_where = [
                f"{alias}.track_id = t.id",
                f"{alias}.model_name = ?",
            ]
            params.append(item.name)
            if item.min_value is not None:
                prediction_where.append(f"{alias}.score >= ?")
                params.append(item.min_value)
            if item.max_value is not None:
                prediction_where.append(f"{alias}.score <= ?")
                params.append(item.max_value)
            if item.text_values:
                selected_labels.update(item.text_values)
                placeholders = ",".join("?" for _value in item.text_values)
                prediction_where.append(f"{alias}.label IN ({placeholders})")
                params.extend(item.text_values)
            where.append(
                f"EXISTS (SELECT 1 FROM track_predictions {alias} WHERE {' AND '.join(prediction_where)})"
            )
        if sort_by:
            selected_models.add(sort_by)

        sort_direction = "ASC" if sort_direction.lower() == "asc" else "DESC"
        sort_select = "NULL AS sort_value"
        sort_join = ""
        sort_params: list[object] = []
        inner_order_sql = _TRACK_ORDER_BY_ARTIST_ALBUM_TITLE
        outer_order_sql = _TRACK_ORDER_BY_ARTIST_ALBUM_TITLE
        if sort_by:
            sort_select = "sp.score AS sort_value"
            sort_join = """
                LEFT JOIN track_predictions sp
                  ON sp.track_id = t.id
                 AND sp.model_name = ?
                 AND sp.rank = 1
            """
            sort_params.append(sort_by)
            inner_order_sql = (
                f"sp.score IS NULL, sp.score {sort_direction}, t.artist, t.title, t.id"
            )
            outer_order_sql = (
                f"t.sort_value IS NULL, t.sort_value {sort_direction}, t.artist, t.title, t.id"
            )

        prediction_join = ""
        prediction_rank_join = "AND p.rank <= 3"
        prediction_params: list[object] = []
        if selected_models:
            placeholders = ",".join("?" for _model in selected_models)
            prediction_join = f" AND p.model_name IN ({placeholders})"
            prediction_params.extend(sorted(selected_models))
            if selected_labels:
                label_placeholders = ",".join("?" for _label in selected_labels)
                prediction_rank_join = f"AND (p.rank <= 3 OR p.label IN ({label_placeholders}))"
                prediction_params.extend(sorted(selected_labels))
        else:
            prediction_join = " AND 0"

        sql = f"""
            SELECT t.*, p.model_name, p.label, p.score, p.rank
            FROM (
                SELECT t.*, {sort_select}
                FROM tracks t
                {sort_join}
                WHERE {' AND '.join(where)}
                ORDER BY {inner_order_sql}
                LIMIT ?
            ) t
            LEFT JOIN track_predictions p
              ON p.track_id = t.id
             {prediction_join}
             {prediction_rank_join}
            ORDER BY {outer_order_sql}, p.model_name, p.rank
        """
        all_params = [*sort_params, *params, limit, *prediction_params]
        with self.connect() as conn:
            rows = conn.execute(sql, all_params).fetchall()

        grouped: dict[int, FeatureTrack] = {}
        for row in rows:
            track = grouped.get(int(row["id"]))
            if track is None:
                track = FeatureTrack(row_to_track(row), [])
                grouped[int(row["id"])] = track
            if row["model_name"] is not None:
                track.features.append(
                    TrackFeature(
                        name=str(row["model_name"]),
                        value=float(row["score"]) if row["score"] is not None else None,
                        text_value=row["label"],
                        unit="score",
                        extractor="discogs_effnet_heads",
                    )
                )
        return list(grouped.values())

    def count_missing_embeddings(self, model_name: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM tracks t
                    LEFT JOIN embeddings e
                      ON e.track_id = t.id AND e.model_name = ?
                    WHERE e.track_id IS NULL
                      AND t.missing_at IS NULL
                    """,
                    (model_name,),
                ).fetchone()[0]
            )

    def recent_tracks(self, limit: int = 50) -> list[Track]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tracks ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row_to_track(row) for row in rows]


