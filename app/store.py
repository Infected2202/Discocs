from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4

import numpy as np

from app.scanner import ScannedTrack


logger = logging.getLogger(__name__)
INIT_LOCK = Lock()
INITIALIZED_DB_PATHS: set[Path] = set()


@dataclass(frozen=True)
class Track:
    id: int
    path: str
    artist: str | None
    title: str | None
    album: str | None
    duration: float | None
    file_size: int
    mtime: int
    genre: str | None = None
    year: int | None = None
    missing_at: str | None = None


@dataclass(frozen=True)
class ExternalTrack:
    provider: str
    external_id: str
    track_id: int
    raw_json: str | None
    synced_at: str


@dataclass(frozen=True)
class InstantMixRequest:
    id: str
    provider: str
    seed_item_id: str
    seed_track_id: int | None
    model_name: str
    requested_count: int | None
    effective_count: int
    max_per_artist: int
    exclude_same_album: bool
    min_similarity: float | None
    status: str
    result_count: int
    skipped_without_external_id: int
    duration_ms: float | None
    error: str | None
    params_json: str
    results_json: str
    created_at: str


@dataclass(frozen=True)
class TrackListing:
    track: Track
    has_embedding: bool
    predictions: list["TrackPrediction"] | None = None


@dataclass(frozen=True)
class SimilarTrack:
    track: Track
    distance: float
    similarity: float
    rating: int | None = None


@dataclass(frozen=True)
class TrackPrediction:
    label: str
    score: float
    rank: int


@dataclass(frozen=True)
class TrackModelOutput:
    model_name: str
    scores: np.ndarray
    aggregation: str
    dtype: str = "float32"


@dataclass(frozen=True)
class TrackFeature:
    name: str
    value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    confidence: float | None = None
    extractor: str = ""


@dataclass(frozen=True)
class FeatureSummary:
    name: str
    extractor: str
    value_count: int
    text_count: int
    track_count: int
    min_value: float | None = None
    max_value: float | None = None
    avg_value: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class FeatureFilter:
    name: str
    min_value: float | None = None
    max_value: float | None = None
    text_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeatureTrack:
    track: Track
    features: list[TrackFeature]


@dataclass(frozen=True)
class HeadSummary:
    model_name: str
    output_count: int
    prediction_track_count: int
    label_count: int
    max_score: float | None = None
    avg_score: float | None = None


@dataclass(frozen=True)
class AnalysisJob:
    id: str
    kind: str
    model_name: str
    status: str
    total: int
    done: int
    failed: int
    queued: int
    leased: int
    final_failed: int
    message: str
    created_at: str
    updated_at: str
    finished_at: str | None = None


@dataclass(frozen=True)
class AnalysisTask:
    id: str
    job_id: str
    track_id: int
    model_name: str
    status: str
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: str | None
    path: str
    file_size: int
    mtime: int
    error: str | None = None
    error_type: str | None = None
    stage: str | None = None


@dataclass(frozen=True)
class AnalysisWorker:
    worker_id: str
    models: str
    status: str
    last_seen_at: str
    created_at: str
    claimed_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    released_count: int = 0
    current_task_id: str | None = None
    stage: str | None = None
    message: str | None = None


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level="IMMEDIATE")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA wal_autocheckpoint = 1000")
        return conn

    def init(self) -> None:
        resolved_path = self.db_path.resolve()
        with INIT_LOCK:
            if resolved_path in INITIALIZED_DB_PATHS:
                return
            self._init_schema()
            INITIALIZED_DB_PATHS.add(resolved_path)

    def _init_schema(self) -> None:
        with self.connect() as conn:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                logger.debug("Could not enable SQLite WAL mode for %s", self.db_path, exc_info=True)
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY,
                    path TEXT UNIQUE NOT NULL,
                    artist TEXT,
                    title TEXT,
                    album TEXT,
                    genre TEXT,
                    year INTEGER,
                    duration REAL,
                    file_size INTEGER NOT NULL,
                    mtime INTEGER NOT NULL,
                    audio_hash TEXT,
                    missing_at TEXT,
                    last_seen_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS embeddings (
                    track_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    vector_norm REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (track_id, model_name),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS track_predictions (
                    track_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    label TEXT NOT NULL,
                    score REAL NOT NULL,
                    rank INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (track_id, model_name, label),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS track_model_outputs (
                    track_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    dtype TEXT NOT NULL,
                    aggregation TEXT NOT NULL,
                    scores_blob BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (track_id, model_name),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS track_features (
                    track_id INTEGER NOT NULL,
                    feature_name TEXT NOT NULL,
                    value REAL,
                    text_value TEXT,
                    unit TEXT,
                    confidence REAL,
                    extractor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (track_id, feature_name, extractor),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS scan_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    seed_track_id INTEGER NOT NULL,
                    result_track_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    rating INTEGER NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (seed_track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                    FOREIGN KEY (result_track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS external_tracks (
                    provider TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    track_id INTEGER NOT NULL,
                    raw_json TEXT,
                    synced_at TEXT NOT NULL,
                    PRIMARY KEY (provider, external_id),
                    UNIQUE (provider, track_id),
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS instant_mix_requests (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    seed_item_id TEXT NOT NULL,
                    seed_track_id INTEGER,
                    model_name TEXT NOT NULL,
                    requested_count INTEGER,
                    effective_count INTEGER NOT NULL,
                    max_per_artist INTEGER NOT NULL,
                    exclude_same_album INTEGER NOT NULL,
                    min_similarity REAL,
                    status TEXT NOT NULL,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    skipped_without_external_id INTEGER NOT NULL DEFAULT 0,
                    duration_ms REAL,
                    error TEXT,
                    params_json TEXT NOT NULL,
                    results_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (seed_track_id) REFERENCES tracks(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_instant_mix_requests_created_at
                    ON instant_mix_requests(created_at DESC);

                CREATE TABLE IF NOT EXISTS analysis_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL DEFAULT 'analyze',
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total INTEGER NOT NULL DEFAULT 0,
                    progress_done INTEGER NOT NULL DEFAULT 0,
                    progress_failed INTEGER NOT NULL DEFAULT 0,
                    local_executor_enabled INTEGER NOT NULL DEFAULT 1,
                    workers INTEGER NOT NULL DEFAULT 1,
                    tf_threads INTEGER NOT NULL DEFAULT 1,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS analysis_tasks (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    track_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    mtime INTEGER NOT NULL,
                    error TEXT,
                    error_type TEXT,
                    stage TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (job_id) REFERENCES analysis_jobs(id) ON DELETE CASCADE,
                    FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                    UNIQUE (job_id, track_id, model_name)
                );

                CREATE INDEX IF NOT EXISTS idx_analysis_tasks_claim
                    ON analysis_tasks(status, model_name, lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_analysis_tasks_job
                    ON analysis_tasks(job_id, status);
                CREATE INDEX IF NOT EXISTS idx_track_predictions_model_label_score
                    ON track_predictions(model_name, label, score DESC, track_id);
                CREATE INDEX IF NOT EXISTS idx_track_predictions_model_rank_score
                    ON track_predictions(model_name, rank, score DESC, track_id);
                CREATE INDEX IF NOT EXISTS idx_track_predictions_track_model_rank
                    ON track_predictions(track_id, model_name, rank);
                CREATE INDEX IF NOT EXISTS idx_track_model_outputs_model_track
                    ON track_model_outputs(model_name, track_id);
                CREATE INDEX IF NOT EXISTS idx_track_features_name_value
                    ON track_features(extractor, feature_name, value, track_id);
                CREATE INDEX IF NOT EXISTS idx_track_features_name_text
                    ON track_features(extractor, feature_name, text_value, track_id);
                CREATE INDEX IF NOT EXISTS idx_track_features_extractor_track
                    ON track_features(extractor, track_id);

                CREATE TABLE IF NOT EXISTS analysis_workers (
                    worker_id TEXT PRIMARY KEY,
                    models TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    claimed_count INTEGER NOT NULL DEFAULT 0,
                    completed_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    released_count INTEGER NOT NULL DEFAULT 0,
                    current_task_id TEXT,
                    stage TEXT,
                    message TEXT
                );
                """
            )
            self._ensure_column(conn, "tracks", "genre", "TEXT")
            self._ensure_column(conn, "tracks", "year", "INTEGER")
            self._ensure_column(conn, "tracks", "missing_at", "TEXT")
            self._ensure_column(conn, "tracks", "last_seen_at", "TEXT")
            self._ensure_column(conn, "analysis_jobs", "kind", "TEXT NOT NULL DEFAULT 'analyze'")
            self._ensure_column(conn, "analysis_jobs", "progress_done", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_jobs", "progress_failed", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_workers", "claimed_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_workers", "completed_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_workers", "failed_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_workers", "released_count", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "analysis_workers", "current_task_id", "TEXT")
            self._ensure_column(conn, "analysis_workers", "stage", "TEXT")
            self._ensure_column(conn, "analysis_workers", "message", "TEXT")

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def upsert_track(self, scanned: ScannedTrack) -> tuple[int, bool]:
        now = utc_now()
        path = str(scanned.path)
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id, file_size, mtime FROM tracks WHERE path = ?",
                (path,),
            ).fetchone()
            if existing:
                changed = (
                    int(existing["file_size"]) != scanned.file_size
                    or int(existing["mtime"]) != scanned.mtime
                )
                conn.execute(
                    """
                    UPDATE tracks
                    SET artist = ?, title = ?, album = ?, genre = ?, year = ?, duration = ?,
                        file_size = ?, mtime = ?, missing_at = NULL, last_seen_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        scanned.artist,
                        scanned.title,
                        scanned.album,
                        scanned.genre,
                        scanned.year,
                        scanned.duration,
                        scanned.file_size,
                        scanned.mtime,
                        now,
                        now,
                        existing["id"],
                    ),
                )
                if changed:
                    logger.info(
                        "Track changed, invalidating derived data track_id=%s path=%s",
                        existing["id"],
                        path,
                    )
                    conn.execute("DELETE FROM embeddings WHERE track_id = ?", (existing["id"],))
                    conn.execute(
                        "DELETE FROM track_model_outputs WHERE track_id = ?",
                        (existing["id"],),
                    )
                    conn.execute(
                        "DELETE FROM track_predictions WHERE track_id = ?",
                        (existing["id"],),
                    )
                    conn.execute(
                        "DELETE FROM track_features WHERE track_id = ?",
                        (existing["id"],),
                    )
                return int(existing["id"]), changed

            cursor = conn.execute(
                """
                INSERT INTO tracks (
                    path, artist, title, album, genre, year, duration, file_size, mtime,
                    missing_at, last_seen_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    path,
                    scanned.artist,
                    scanned.title,
                    scanned.album,
                    scanned.genre,
                    scanned.year,
                    scanned.duration,
                    scanned.file_size,
                    scanned.mtime,
                    now,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid), True

    def delete_embedding(self, track_id: int, model_name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM embeddings WHERE track_id = ? AND model_name = ?",
                (track_id, model_name),
            )

    def save_feedback(
        self,
        seed_track_id: int,
        result_track_id: int,
        model_name: str,
        rating: int,
        note: str | None = None,
    ) -> None:
        if rating < 0 or rating > 3:
            raise ValueError("rating must be between 0 and 3")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO feedback (
                    seed_track_id, result_track_id, model_name, rating, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (seed_track_id, result_track_id, model_name, rating, note, utc_now()),
            )

    def feedback_for_seed(self, seed_track_id: int, model_name: str) -> dict[int, int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT f.result_track_id, f.rating
                FROM feedback f
                JOIN (
                    SELECT result_track_id, MAX(created_at) AS created_at
                    FROM feedback
                    WHERE seed_track_id = ? AND model_name = ?
                    GROUP BY result_track_id
                ) latest
                  ON latest.result_track_id = f.result_track_id
                 AND latest.created_at = f.created_at
                WHERE f.seed_track_id = ? AND f.model_name = ?
                """,
                (seed_track_id, model_name, seed_track_id, model_name),
            ).fetchall()
        return {int(row["result_track_id"]): int(row["rating"]) for row in rows}

    def get_track(self, track_id: int) -> Track | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return row_to_track(row) if row else None

    def get_tracks(self, track_ids: list[int]) -> dict[int, Track]:
        ids = list(dict.fromkeys(track_ids))
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tracks WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        tracks = [row_to_track(row) for row in rows]
        return {int(track.id): track for track in tracks}

    def upsert_external_track(
        self,
        provider: str,
        external_id: str,
        track_id: int,
        raw_json: str | None = None,
        synced_at: str | None = None,
    ) -> ExternalTrack:
        provider = _require_external_value(provider, "provider")
        external_id = _require_external_value(external_id, "external_id")
        synced_at = synced_at or utc_now()
        with self.connect() as conn:
            track_exists = conn.execute(
                "SELECT 1 FROM tracks WHERE id = ?",
                (track_id,),
            ).fetchone()
            if track_exists is None:
                raise ValueError(f"Track not found: {track_id}")
            conn.execute(
                """
                DELETE FROM external_tracks
                WHERE provider = ? AND track_id = ? AND external_id != ?
                """,
                (provider, track_id, external_id),
            )
            conn.execute(
                """
                INSERT INTO external_tracks (
                    provider, external_id, track_id, raw_json, synced_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider, external_id) DO UPDATE SET
                    track_id = excluded.track_id,
                    raw_json = excluded.raw_json,
                    synced_at = excluded.synced_at
                """,
                (provider, external_id, track_id, raw_json, synced_at),
            )
            row = conn.execute(
                """
                SELECT * FROM external_tracks
                WHERE provider = ? AND external_id = ?
                """,
                (provider, external_id),
            ).fetchone()
        return row_to_external_track(row)

    def get_external_track(self, provider: str, external_id: str) -> ExternalTrack | None:
        provider = _require_external_value(provider, "provider")
        external_id = _require_external_value(external_id, "external_id")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM external_tracks
                WHERE provider = ? AND external_id = ?
                """,
                (provider, external_id),
            ).fetchone()
        return row_to_external_track(row) if row else None

    def get_track_by_external_id(self, provider: str, external_id: str) -> Track | None:
        provider = _require_external_value(provider, "provider")
        external_id = _require_external_value(external_id, "external_id")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT t.*
                FROM tracks t
                JOIN external_tracks e ON e.track_id = t.id
                WHERE e.provider = ? AND e.external_id = ?
                """,
                (provider, external_id),
            ).fetchone()
        return row_to_track(row) if row else None

    def external_id_for_track(self, provider: str, track_id: int) -> str | None:
        provider = _require_external_value(provider, "provider")
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT external_id FROM external_tracks
                WHERE provider = ? AND track_id = ?
                """,
                (provider, track_id),
            ).fetchone()
        return str(row["external_id"]) if row else None

    def list_external_tracks(self, provider: str | None = None) -> list[ExternalTrack]:
        params: list[object] = []
        where = ""
        if provider is not None:
            provider = _require_external_value(provider, "provider")
            where = "WHERE provider = ?"
            params.append(provider)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM external_tracks
                {where}
                ORDER BY provider, external_id
                """,
                params,
            ).fetchall()
        return [row_to_external_track(row) for row in rows]

    def count_external_tracks(self, provider: str | None = None) -> int:
        params: list[object] = []
        where = ""
        if provider is not None:
            provider = _require_external_value(provider, "provider")
            where = "WHERE provider = ?"
            params.append(provider)
        with self.connect() as conn:
            return int(
                conn.execute(
                    f"SELECT COUNT(*) FROM external_tracks {where}",
                    params,
                ).fetchone()[0]
            )

    def record_instant_mix_request(
        self,
        request_id: str,
        provider: str,
        seed_item_id: str,
        seed_track_id: int | None,
        model_name: str,
        requested_count: int | None,
        effective_count: int,
        max_per_artist: int,
        exclude_same_album: bool,
        min_similarity: float | None,
        status: str,
        result_count: int,
        skipped_without_external_id: int,
        duration_ms: float | None,
        error: str | None,
        params_json: str,
        results_json: str,
        created_at: str | None = None,
    ) -> InstantMixRequest:
        created_at = created_at or utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO instant_mix_requests (
                    id, provider, seed_item_id, seed_track_id, model_name,
                    requested_count, effective_count, max_per_artist, exclude_same_album,
                    min_similarity, status, result_count, skipped_without_external_id,
                    duration_ms, error, params_json, results_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    provider,
                    seed_item_id,
                    seed_track_id,
                    model_name,
                    requested_count,
                    effective_count,
                    max_per_artist,
                    1 if exclude_same_album else 0,
                    min_similarity,
                    status,
                    result_count,
                    skipped_without_external_id,
                    duration_ms,
                    error,
                    params_json,
                    results_json,
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM instant_mix_requests NOT INDEXED WHERE id = ?",
                (request_id,),
            ).fetchone()
        return row_to_instant_mix_request(row)

    def list_instant_mix_requests(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InstantMixRequest]:
        with self.connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM instant_mix_requests NOT INDEXED
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
            except sqlite3.DatabaseError:
                logger.exception("Falling back to rowid scan for instant_mix_requests")
                rows = self._list_instant_mix_requests_by_rowid(conn, limit=limit, offset=offset)
        return [row_to_instant_mix_request(row) for row in rows]

    def _list_instant_mix_requests_by_rowid(
        self,
        conn: sqlite3.Connection,
        *,
        limit: int,
        offset: int,
    ) -> list[sqlite3.Row]:
        bounds = conn.execute(
            "SELECT MIN(rowid), MAX(rowid) FROM instant_mix_requests NOT INDEXED"
        ).fetchone()
        if bounds is None or bounds[0] is None or bounds[1] is None:
            return []
        rows: list[sqlite3.Row] = []
        bad_rowids: list[int] = []
        for rowid in range(int(bounds[0]), int(bounds[1]) + 1):
            try:
                row = conn.execute(
                    "SELECT * FROM instant_mix_requests NOT INDEXED WHERE rowid = ?",
                    (rowid,),
                ).fetchone()
            except sqlite3.DatabaseError:
                bad_rowids.append(rowid)
                continue
            if row is not None:
                rows.append(row)
        if bad_rowids:
            logger.warning("Skipped unreadable instant_mix_requests rowids=%s", bad_rowids)
        rows.sort(key=lambda row: (str(row["created_at"] or ""), str(row["id"] or "")), reverse=True)
        return rows[offset : offset + limit]

    def get_instant_mix_request(self, request_id: str) -> InstantMixRequest | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM instant_mix_requests NOT INDEXED WHERE id = ?",
                (request_id,),
            ).fetchone()
        return row_to_instant_mix_request(row) if row else None

    def mark_track_missing(self, track_id: int, missing_at: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE tracks SET missing_at = ?, updated_at = ? WHERE id = ?",
                (missing_at or utc_now(), utc_now(), track_id),
            )

    def mark_track_available(self, track_id: int) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE tracks SET missing_at = NULL, last_seen_at = ?, updated_at = ? WHERE id = ?",
                (now, now, track_id),
            )

    def check_file_availability(self) -> tuple[int, int]:
        checked = 0
        missing = 0
        now = utc_now()
        with self.connect() as conn:
            rows = conn.execute("SELECT id, path FROM tracks ORDER BY id").fetchall()
            for row in rows:
                checked += 1
                track_id = int(row["id"])
                path = Path(str(row["path"]))
                if path.exists() and path.is_file():
                    conn.execute(
                        """
                        UPDATE tracks
                        SET missing_at = NULL, last_seen_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, track_id),
                    )
                else:
                    missing += 1
                    conn.execute(
                        """
                        UPDATE tracks
                        SET missing_at = COALESCE(missing_at, ?), updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, track_id),
                    )
        return checked, missing

    def list_missing_tracks(self, limit: int = 500, offset: int = 0) -> list[Track]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tracks
                WHERE missing_at IS NOT NULL
                ORDER BY missing_at DESC, id DESC
                LIMIT ?
                OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [row_to_track(row) for row in rows]

    def count_missing_files(self) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM tracks WHERE missing_at IS NOT NULL"
                ).fetchone()[0]
            )

    def delete_tracks(self, track_ids: list[int]) -> int:
        if not track_ids:
            return 0
        placeholders = ",".join("?" for _track_id in track_ids)
        with self.connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM tracks WHERE id IN ({placeholders})",
                [int(track_id) for track_id in track_ids],
            )
            return int(cursor.rowcount)

    def delete_missing_tracks(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM tracks WHERE missing_at IS NOT NULL")
            return int(cursor.rowcount)

    def find_track_by_path(self, path: Path) -> Track | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE path = ?",
                (str(path.resolve()),),
            ).fetchone()
        return row_to_track(row) if row else None

    def list_tracks(
        self,
        query: str = "",
        limit: int = 50,
        model_name: str = "discogs_multi",
        embedding_status: str = "all",
        folder: str | None = None,
        genre: str | None = None,
        year: int | None = None,
        artist: str | None = None,
        album: str | None = None,
    ) -> list[TrackListing]:
        if embedding_status not in {"all", "ready", "missing"}:
            raise ValueError("embedding_status must be all, ready, or missing")
        like = f"%{query}%"
        filters: list[str] = []
        params: list[object] = [model_name]
        if query:
            filters.append(
                "(t.artist LIKE ? OR t.title LIKE ? OR t.album LIKE ? OR "
                "t.genre LIKE ? OR t.path LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        if folder:
            clean_folder = folder.rstrip("\\/")
            filters.append("(t.path LIKE ? OR t.path LIKE ?)")
            params.extend([f"{clean_folder}\\%", f"{clean_folder}/%"])
        if genre:
            filters.append("t.genre = ?")
            params.append(genre)
        if year is not None:
            filters.append("t.year = ?")
            params.append(year)
        if artist:
            filters.append("t.artist = ?")
            params.append(artist)
        if album:
            filters.append("t.album = ?")
            params.append(album)
        if embedding_status == "ready":
            filters.append("e.track_id IS NOT NULL")
        elif embedding_status == "missing":
            filters.append("e.track_id IS NULL")
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, e.track_id AS embedding_track_id
                FROM tracks t
                LEFT JOIN embeddings e
                  ON e.track_id = t.id AND e.model_name = ?
                {where}
                ORDER BY CASE WHEN ? = '' THEN t.id END DESC, t.artist, t.title
                LIMIT ?
                """,
                [*params[:-1], query, params[-1]],
            ).fetchall()
        return [
            TrackListing(
                track=row_to_track(row),
                has_embedding=row["embedding_track_id"] is not None,
            )
            for row in rows
        ]

    def search_tracks(self, query: str, limit: int = 50) -> list[Track]:
        return [listing.track for listing in self.list_tracks(query=query, limit=limit)]

    def browser_facets(
        self,
        model_name: str = "discogs_multi",
        embedding_status: str = "all",
        limit: int = 80,
    ) -> dict[str, list[dict[str, object]]]:
        if embedding_status not in {"all", "ready", "missing"}:
            raise ValueError("embedding_status must be all, ready, or missing")
        filters: list[str] = []
        if embedding_status == "ready":
            filters.append("e.track_id IS NOT NULL")
        elif embedding_status == "missing":
            filters.append("e.track_id IS NULL")
        where = f"WHERE {' AND '.join(filters)}" if filters else ""

        def facet_rows(column: str, extra_where: str, order_by: str) -> list[dict[str, object]]:
            with self.connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT t.{column} AS value, COUNT(*) AS count
                    FROM tracks t
                    LEFT JOIN embeddings e
                      ON e.track_id = t.id AND e.model_name = ?
                    {where}
                    {"AND" if where else "WHERE"} {extra_where}
                    GROUP BY t.{column}
                    ORDER BY {order_by}
                    LIMIT ?
                    """,
                    (model_name, limit),
                ).fetchall()
            return [{"value": row["value"], "count": int(row["count"])} for row in rows]

        with self.connect() as conn:
            path_rows = conn.execute(
                f"""
                SELECT t.path
                FROM tracks t
                LEFT JOIN embeddings e
                  ON e.track_id = t.id AND e.model_name = ?
                {where}
                """,
                (model_name,),
            ).fetchall()

        folder_counts: dict[str, int] = {}
        for row in path_rows:
            folder = str(Path(str(row["path"])).parent)
            folder_counts[folder] = folder_counts.get(folder, 0) + 1

        return {
            "folders": [
                {"value": value, "count": count}
                for value, count in sorted(
                    folder_counts.items(),
                    key=lambda item: (-item[1], item[0].lower()),
                )[:limit]
            ],
            "genres": facet_rows(
                "genre",
                "t.genre IS NOT NULL AND TRIM(t.genre) != ''",
                "count DESC, value",
            ),
            "years": facet_rows("year", "t.year IS NOT NULL", "value DESC"),
            "artists": facet_rows(
                "artist",
                "t.artist IS NOT NULL AND TRIM(t.artist) != ''",
                "count DESC, value",
            ),
            "albums": facet_rows(
                "album",
                "t.album IS NOT NULL AND TRIM(t.album) != ''",
                "count DESC, value",
            ),
        }

    def list_tracks_missing_embedding(self, model_name: str, limit: int | None = None) -> list[Track]:
        sql = """
            SELECT t.* FROM tracks t
            LEFT JOIN embeddings e
              ON e.track_id = t.id AND e.model_name = ?
            WHERE e.track_id IS NULL
              AND t.missing_at IS NULL
            ORDER BY t.id
        """
        params: list[object] = [model_name]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_track(row) for row in rows]

    def list_active_tracks(self, limit: int | None = None) -> list[Track]:
        sql = """
            SELECT t.* FROM tracks t
            WHERE t.missing_at IS NULL
            ORDER BY t.id
        """
        params: list[object] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [row_to_track(row) for row in rows]

    def create_analysis_job(
        self,
        model_name: str,
        limit: int | None,
        *,
        kind: str = "analyze",
        tracks: list[Track] | None = None,
        local_executor_enabled: bool = True,
        workers: int = 1,
        tf_threads: int = 1,
        max_attempts: int = 3,
        job_id: str | None = None,
    ) -> AnalysisJob:
        now = utc_now()
        job_id = job_id or str(uuid4())
        tracks = tracks if tracks is not None else self.list_tracks_missing_embedding(model_name, limit=limit)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_jobs (
                    id, kind, model_name, status, total, local_executor_enabled, workers,
                    tf_threads, max_attempts, message, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    model_name,
                    "running" if tracks else "completed",
                    len(tracks),
                    int(local_executor_enabled),
                    int(workers),
                    int(tf_threads),
                    int(max_attempts),
                    f"Queued {len(tracks)} tracks for {model_name}",
                    now,
                    now,
                ),
            )
            conn.executemany(
                """
                INSERT INTO analysis_tasks (
                    id, job_id, track_id, model_name, status, max_attempts, path,
                    file_size, mtime, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid4()),
                        job_id,
                        track.id,
                        model_name,
                        int(max_attempts),
                        track.path,
                        track.file_size,
                        track.mtime,
                        now,
                        now,
                    )
                    for track in tracks
                ],
            )
            if not tracks:
                conn.execute(
                    "UPDATE analysis_jobs SET finished_at = ?, message = ? WHERE id = ?",
                    (now, f"Analyzed 0 tracks, failed 0", job_id),
                )
        return self.get_analysis_job(job_id)  # type: ignore[return-value]

    def create_progress_job(
        self,
        kind: str,
        model_name: str,
        *,
        total: int = 0,
        message: str = "",
        job_id: str | None = None,
    ) -> AnalysisJob:
        now = utc_now()
        job_id = job_id or str(uuid4())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_jobs (
                    id, kind, model_name, status, total, progress_done,
                    progress_failed, local_executor_enabled, workers, tf_threads,
                    max_attempts, message, created_at, updated_at
                )
                VALUES (?, ?, ?, 'running', ?, 0, 0, 0, 0, 0, 1, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    model_name,
                    max(int(total), 0),
                    message or f"Running {kind}",
                    now,
                    now,
                ),
            )
        return self._get_analysis_job_no_refresh(job_id)  # type: ignore[return-value]

    def update_progress_job(
        self,
        job_id: str,
        *,
        done: int | None = None,
        failed: int | None = None,
        total: int | None = None,
        status: str | None = None,
        message: str | None = None,
        finished: bool = False,
    ) -> AnalysisJob | None:
        now = utc_now()
        updates = ["updated_at = ?"]
        params: list[object] = [now]
        if done is not None:
            updates.append("progress_done = ?")
            params.append(max(int(done), 0))
        if failed is not None:
            updates.append("progress_failed = ?")
            params.append(max(int(failed), 0))
        if total is not None:
            updates.append("total = ?")
            params.append(max(int(total), 0))
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if message is not None:
            updates.append("message = ?")
            params.append(message)
        if finished:
            updates.append("finished_at = ?")
            params.append(now)
        params.append(job_id)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE analysis_jobs SET {', '.join(updates)} WHERE id = ?",
                params,
            )
        return self._get_analysis_job_no_refresh(job_id)

    def expire_analysis_leases(self, now: str | None = None) -> int:
        now = now or utc_now()
        with self.connect() as conn:
            job_rows = conn.execute(
                """
                SELECT DISTINCT job_id
                FROM analysis_tasks
                WHERE status = 'leased'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (now,),
            ).fetchall()
            cursor = conn.execute(
                """
                UPDATE analysis_tasks
                SET status = 'queued',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    stage = 'lease_expired',
                    updated_at = ?
                WHERE status = 'leased'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (now, now),
            )
            expired = int(cursor.rowcount)
        for row in job_rows:
            self.refresh_analysis_job(str(row["job_id"]))
        return expired

    def register_analysis_worker(self, worker_id: str, models: list[str]) -> None:
        now = utc_now()
        models_text = ",".join(sorted(set(models)))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_workers (worker_id, models, status, last_seen_at, created_at)
                VALUES (?, ?, 'online', ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    models = excluded.models,
                    status = 'online',
                    last_seen_at = excluded.last_seen_at,
                    stage = COALESCE(analysis_workers.stage, 'heartbeat')
                """,
                (worker_id, models_text, now, now),
            )

    def heartbeat_analysis_worker(
        self,
        worker_id: str,
        models: list[str],
        *,
        min_interval_seconds: int = 60,
    ) -> bool:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        models_text = ",".join(sorted(set(models)))
        with self.connect() as conn:
            row = conn.execute(
                "SELECT models, last_seen_at FROM analysis_workers WHERE worker_id = ?",
                (worker_id,),
            ).fetchone()
            if row is not None and row["models"] == models_text:
                try:
                    last_seen_at = datetime.fromisoformat(str(row["last_seen_at"]))
                except ValueError:
                    last_seen_at = None
                if last_seen_at is not None:
                    if last_seen_at.tzinfo is None:
                        last_seen_at = last_seen_at.replace(tzinfo=UTC)
                    if now_dt - last_seen_at < timedelta(seconds=max(min_interval_seconds, 0)):
                        return False
            conn.execute(
                """
                INSERT INTO analysis_workers (worker_id, models, status, last_seen_at, created_at)
                VALUES (?, ?, 'online', ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    models = excluded.models,
                    status = 'online',
                    last_seen_at = excluded.last_seen_at,
                    stage = COALESCE(analysis_workers.stage, 'heartbeat')
                """,
                (worker_id, models_text, now, now),
            )
        return True

    def update_analysis_worker(
        self,
        worker_id: str,
        *,
        status: str = "online",
        stage: str | None = None,
        message: str | None = None,
        current_task_id: str | None = None,
        claimed_delta: int = 0,
        completed_delta: int = 0,
        failed_delta: int = 0,
        released_delta: int = 0,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE analysis_workers
                SET status = ?,
                    last_seen_at = ?,
                    stage = COALESCE(?, stage),
                    message = COALESCE(?, message),
                    current_task_id = ?,
                    claimed_count = claimed_count + ?,
                    completed_count = completed_count + ?,
                    failed_count = failed_count + ?,
                    released_count = released_count + ?
                WHERE worker_id = ?
                """,
                (
                    status,
                    now,
                    stage,
                    message,
                    current_task_id,
                    int(claimed_delta),
                    int(completed_delta),
                    int(failed_delta),
                    int(released_delta),
                    worker_id,
                ),
            )

    def list_analysis_workers(self) -> list[AnalysisWorker]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM analysis_workers ORDER BY last_seen_at DESC"
            ).fetchall()
        return [row_to_analysis_worker(row) for row in rows]

    def analysis_job_task_summary(self, job_id: str) -> dict[str, object]:
        with self.connect() as conn:
            status_rows = conn.execute(
                """
                SELECT status, stage, COUNT(*) AS count
                FROM analysis_tasks
                WHERE job_id = ?
                GROUP BY status, stage
                ORDER BY status, stage
                """,
                (job_id,),
            ).fetchall()
            leased_rows = conn.execute(
                """
                SELECT lease_owner, COUNT(*) AS count
                FROM analysis_tasks
                WHERE job_id = ?
                  AND status = 'leased'
                  AND lease_owner IS NOT NULL
                GROUP BY lease_owner
                ORDER BY count DESC, lease_owner
                """,
                (job_id,),
            ).fetchall()
            oldest_lease = conn.execute(
                """
                SELECT lease_owner, lease_expires_at, stage, updated_at, path
                FROM analysis_tasks
                WHERE job_id = ?
                  AND status = 'leased'
                ORDER BY lease_expires_at, updated_at
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            error_rows = conn.execute(
                """
                SELECT id, track_id, status, error, error_type, stage, updated_at, lease_owner, path
                FROM analysis_tasks
                WHERE job_id = ?
                  AND error IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 5
                """,
                (job_id,),
            ).fetchall()
        return {
            "status_breakdown": [
                {
                    "status": str(row["status"]),
                    "stage": row["stage"],
                    "count": int(row["count"] or 0),
                }
                for row in status_rows
            ],
            "leased_workers": [
                {
                    "worker_id": str(row["lease_owner"]),
                    "count": int(row["count"] or 0),
                }
                for row in leased_rows
            ],
            "oldest_lease": (
                {
                    "worker_id": oldest_lease["lease_owner"],
                    "lease_expires_at": oldest_lease["lease_expires_at"],
                    "stage": oldest_lease["stage"],
                    "updated_at": oldest_lease["updated_at"],
                    "path": oldest_lease["path"],
                }
                if oldest_lease is not None
                else None
            ),
            "recent_errors": [
                {
                    "task_id": str(row["id"]),
                    "track_id": int(row["track_id"]),
                    "status": str(row["status"]),
                    "error": str(row["error"]),
                    "error_type": row["error_type"],
                    "stage": row["stage"],
                    "updated_at": row["updated_at"],
                    "worker_id": row["lease_owner"],
                    "path": row["path"],
                }
                for row in error_rows
            ],
        }

    def list_analysis_job_tasks(
        self,
        job_id: str,
        *,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> list[AnalysisTask]:
        params: list[object] = [job_id]
        status_clause = ""
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            status_clause = f" AND status IN ({placeholders})"
            params.extend(statuses)
        params.append(int(limit))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM analysis_tasks
                WHERE job_id = ?{status_clause}
                ORDER BY
                    CASE status
                        WHEN 'leased' THEN 0
                        WHEN 'queued' THEN 1
                        WHEN 'failed_retryable' THEN 2
                        WHEN 'final_failed' THEN 3
                        ELSE 4
                    END,
                    updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [row_to_analysis_task(row) for row in rows]

    def claim_analysis_tasks(
        self,
        worker_id: str,
        models: list[str],
        *,
        limit: int,
        lease_seconds: int = 300,
    ) -> list[AnalysisTask]:
        if not models or limit <= 0:
            return []
        self.expire_analysis_leases()
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        lease_expires_at = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        placeholders = ",".join("?" for _ in models)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"""
                SELECT t.* FROM analysis_tasks t
                JOIN analysis_jobs j ON j.id = t.job_id
                WHERE t.status = 'queued'
                  AND j.status = 'running'
                  AND t.model_name IN ({placeholders})
                ORDER BY t.created_at, t.track_id
                LIMIT ?
                """,
                [*models, int(limit)],
            ).fetchall()
            task_ids = [str(row["id"]) for row in rows]
            if task_ids:
                id_placeholders = ",".join("?" for _ in task_ids)
                conn.execute(
                    f"""
                    UPDATE analysis_tasks
                    SET status = 'leased',
                        attempts = attempts + 1,
                        lease_owner = ?,
                        lease_expires_at = ?,
                        error = NULL,
                        error_type = NULL,
                        stage = 'claimed',
                        updated_at = ?
                    WHERE id IN ({id_placeholders})
                    """,
                    [worker_id, lease_expires_at, now, *task_ids],
                )
        if task_ids:
            self.update_analysis_worker(
                worker_id,
                stage="claimed",
                message=f"claimed {len(task_ids)} task(s)",
                claimed_delta=len(task_ids),
            )
        return [
            row_to_analysis_task(
                {
                    **dict(row),
                    "status": "leased",
                    "attempts": int(row["attempts"]) + 1,
                    "lease_owner": worker_id,
                    "lease_expires_at": lease_expires_at,
                }
            )
            for row in rows
        ]

    def get_analysis_task(self, task_id: str) -> AnalysisTask | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM analysis_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return row_to_analysis_task(row) if row else None

    def complete_analysis_task(
        self,
        task_id: str,
        worker_id: str | None = None,
        *,
        refresh_job: bool = True,
        update_worker: bool = True,
    ) -> None:
        now = utc_now()
        params: list[object] = [now, now, task_id]
        owner_clause = ""
        if worker_id is not None:
            owner_clause = " AND lease_owner = ?"
            params.append(worker_id)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE analysis_tasks
                SET status = 'completed',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error = NULL,
                    error_type = NULL,
                    stage = 'completed',
                    updated_at = ?,
                    completed_at = ?
                WHERE id = ?{owner_clause}
                """,
                params,
            )
        if update_worker and worker_id is not None:
            self.update_analysis_worker(
                worker_id,
                stage="completed",
                completed_delta=1,
                current_task_id=None,
            )
        if not refresh_job:
            return
        task = self.get_analysis_task(task_id)
        if task is not None:
            self.refresh_analysis_job(task.job_id)

    def fail_analysis_task(
        self,
        task_id: str,
        *,
        error: str,
        error_type: str,
        stage: str,
        worker_id: str | None = None,
        retryable: bool = True,
    ) -> None:
        now = utc_now()
        task = self.get_analysis_task(task_id)
        if task is None:
            return
        if task.status == "final_failed" and task.stage == "cancelled":
            return
        status = "queued" if retryable and task.attempts < task.max_attempts else "final_failed"
        params: list[object] = [
            status,
            error,
            error_type,
            stage,
            now,
            task_id,
        ]
        owner_clause = ""
        if worker_id is not None:
            owner_clause = " AND lease_owner = ?"
            params.append(worker_id)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE analysis_tasks
                SET status = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error = ?,
                    error_type = ?,
                    stage = ?,
                    updated_at = ?
                WHERE id = ?{owner_clause}
                """,
                params,
            )
        if worker_id is not None:
            self.update_analysis_worker(
                worker_id,
                stage=stage,
                message=error[:240],
                failed_delta=1 if status == "final_failed" else 0,
                current_task_id=None,
            )
        self.refresh_analysis_job(task.job_id)

    def release_analysis_tasks(self, worker_id: str, task_ids: list[str] | None = None) -> int:
        now = utc_now()
        params: list[object] = [now, worker_id]
        task_clause = ""
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            task_clause = f" AND id IN ({placeholders})"
            params.extend(task_ids)
        with self.connect() as conn:
            job_rows = conn.execute(
                f"""
                SELECT DISTINCT job_id
                FROM analysis_tasks
                WHERE status = 'leased'
                  AND lease_owner = ?{task_clause}
                """,
                [worker_id, *(task_ids or [])],
            ).fetchall()
            cursor = conn.execute(
                f"""
                UPDATE analysis_tasks
                SET status = 'queued',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    stage = 'released',
                    updated_at = ?
                WHERE status = 'leased'
                  AND lease_owner = ?{task_clause}
                """,
                params,
            )
            released = int(cursor.rowcount)
        if released:
            self.update_analysis_worker(
                worker_id,
                stage="released",
                message=f"released {released} task(s)",
                released_delta=released,
                current_task_id=None,
            )
        for row in job_rows:
            self.refresh_analysis_job(str(row["job_id"]))
        return released

    def cancel_analysis_job(self, job_id: str, reason: str = "Cancelled by user") -> AnalysisJob | None:
        now = utc_now()
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if exists is None:
                return None
            conn.execute(
                """
                UPDATE analysis_tasks
                SET status = 'final_failed',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    error = ?,
                    error_type = 'Cancelled',
                    stage = 'cancelled',
                    updated_at = ?
                WHERE job_id = ?
                  AND status IN ('queued', 'leased', 'failed_retryable')
                """,
                (reason, now, job_id),
            )
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS done,
                    SUM(CASE WHEN status = 'final_failed' THEN 1 ELSE 0 END) AS failed
                FROM analysis_tasks
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            total = int(counts["total"] or 0)
            done = int(counts["done"] or 0)
            failed = int(counts["failed"] or 0)
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'cancelled',
                    total = ?,
                    message = ?,
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (total, f"Cancelled: done {done}/{total}, failed {failed}", now, now, job_id),
            )
        return self._get_analysis_job_no_refresh(job_id)

    def refresh_analysis_job(self, job_id: str) -> AnalysisJob | None:
        now = utc_now()
        with self.connect() as conn:
            current = conn.execute(
                "SELECT kind, status, updated_at, finished_at FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if current is None:
                return None
            if not str(current["kind"]).startswith("analyze"):
                return self._get_analysis_job_no_refresh(job_id)
            if str(current["status"]) not in {"queued", "running"}:
                if current["finished_at"] is None:
                    conn.execute(
                        "UPDATE analysis_jobs SET finished_at = ? WHERE id = ?",
                        (current["updated_at"] or now, job_id),
                    )
                return self._get_analysis_job_no_refresh(job_id)
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS done,
                    SUM(CASE WHEN status = 'final_failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued,
                    SUM(CASE WHEN status = 'leased' THEN 1 ELSE 0 END) AS leased
                FROM analysis_tasks
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if counts is None:
                return None
            total = int(counts["total"] or 0)
            done = int(counts["done"] or 0)
            failed = int(counts["failed"] or 0)
            queued = int(counts["queued"] or 0)
            leased = int(counts["leased"] or 0)
            completed = done + failed >= total
            status = "completed" if completed else "running"
            message = f"Analyzed {done}/{total} tracks, failed {failed}"
            finished_at = now if completed else None
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, total = ?, message = ?, updated_at = ?,
                    finished_at = COALESCE(finished_at, ?)
                WHERE id = ?
                """,
                (status, total, message, now, finished_at, job_id),
            )
        return self._get_analysis_job_no_refresh(job_id)

    def get_analysis_job(self, job_id: str) -> AnalysisJob | None:
        self.expire_analysis_leases()
        self.refresh_analysis_job_counts_only(job_id)
        return self._get_analysis_job_no_refresh(job_id)

    def get_analysis_job_status(self, job_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return str(row["status"]) if row else None

    def refresh_active_analysis_jobs(self) -> None:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM analysis_jobs
                WHERE status IN ('queued', 'running')
                """
            ).fetchall()
        for row in rows:
            self.refresh_analysis_job(str(row["id"]))

    def has_active_analysis_job(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM analysis_jobs
                WHERE status IN ('queued', 'running')
                LIMIT 1
                """
            ).fetchone()
        return row is not None

    def _get_analysis_job_no_refresh(self, job_id: str) -> AnalysisJob | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT j.*,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END)
                        ELSE j.progress_done
                    END AS done,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS failed,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'queued' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS queued,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'leased' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS leased,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS final_failed
                FROM analysis_jobs j
                LEFT JOIN analysis_tasks t ON t.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.id
                """,
                (job_id,),
            ).fetchone()
        return row_to_analysis_job(row) if row else None

    def refresh_analysis_job_counts_only(self, job_id: str) -> None:
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if exists is not None:
            self.refresh_analysis_job(job_id)

    def recent_analysis_jobs(
        self,
        limit: int = 20,
        statuses: list[str] | None = None,
    ) -> list[AnalysisJob]:
        status_filter = ""
        params: list[object] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            status_filter = f"WHERE status IN ({placeholders})"
            params.extend(statuses)
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT j.*,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END)
                        ELSE j.progress_done
                    END AS done,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS failed,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'queued' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS queued,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'leased' THEN 1 ELSE 0 END)
                        ELSE 0
                    END AS leased,
                    CASE
                        WHEN COUNT(t.id) > 0 THEN SUM(CASE WHEN t.status = 'final_failed' THEN 1 ELSE 0 END)
                        ELSE j.progress_failed
                    END AS final_failed
                FROM (
                    SELECT *
                    FROM analysis_jobs
                    {status_filter}
                    ORDER BY created_at DESC
                    LIMIT ?
                ) j
                LEFT JOIN analysis_tasks t ON t.job_id = j.id
                GROUP BY j.id
                ORDER BY j.created_at DESC
                """,
                params,
            ).fetchall()
        return [row_to_analysis_job(row) for row in rows]

    def count_recent_finished_analysis_tasks(self, job_id: str, since: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM analysis_tasks
                WHERE job_id = ?
                  AND status IN ('completed', 'final_failed')
                  AND updated_at >= ?
                """,
                (job_id, since),
            ).fetchone()
        return int(row[0]) if row else 0

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
        counts = {name: 0 for name in model_names}
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
            sql += " LIMIT ?"
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
            sql += " LIMIT ?"
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
            sql += " LIMIT ?"
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
        inner_order_sql = "t.artist, t.album, t.title, t.id"
        outer_order_sql = "t.artist, t.album, t.title, t.id"
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
        inner_order_sql = "t.artist, t.album, t.title, t.id"
        outer_order_sql = "t.artist, t.album, t.title, t.id"
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


def row_to_track(row: sqlite3.Row) -> Track:
    return Track(
        id=int(row["id"]),
        path=str(row["path"]),
        artist=row["artist"],
        title=row["title"],
        album=row["album"],
        genre=row["genre"],
        year=int(row["year"]) if row["year"] is not None else None,
        duration=float(row["duration"]) if row["duration"] is not None else None,
        file_size=int(row["file_size"]),
        mtime=int(row["mtime"]),
        missing_at=row["missing_at"] if "missing_at" in row.keys() else None,
    )


def row_to_external_track(row: sqlite3.Row) -> ExternalTrack:
    return ExternalTrack(
        provider=str(row["provider"]),
        external_id=str(row["external_id"]),
        track_id=int(row["track_id"]),
        raw_json=row["raw_json"],
        synced_at=str(row["synced_at"]),
    )


def row_to_instant_mix_request(row: sqlite3.Row) -> InstantMixRequest:
    return InstantMixRequest(
        id=str(row["id"]),
        provider=str(row["provider"]),
        seed_item_id=str(row["seed_item_id"]),
        seed_track_id=int(row["seed_track_id"]) if row["seed_track_id"] is not None else None,
        model_name=str(row["model_name"]),
        requested_count=int(row["requested_count"]) if row["requested_count"] is not None else None,
        effective_count=int(row["effective_count"]),
        max_per_artist=int(row["max_per_artist"]),
        exclude_same_album=bool(row["exclude_same_album"]),
        min_similarity=float(row["min_similarity"]) if row["min_similarity"] is not None else None,
        status=str(row["status"]),
        result_count=int(row["result_count"]),
        skipped_without_external_id=int(row["skipped_without_external_id"]),
        duration_ms=float(row["duration_ms"]) if row["duration_ms"] is not None else None,
        error=row["error"],
        params_json=str(row["params_json"]),
        results_json=str(row["results_json"]),
        created_at=str(row["created_at"]),
    )


def _require_external_value(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be empty")
    return cleaned


def row_to_analysis_task(row: sqlite3.Row | dict[str, object]) -> AnalysisTask:
    return AnalysisTask(
        id=str(row["id"]),
        job_id=str(row["job_id"]),
        track_id=int(row["track_id"]),
        model_name=str(row["model_name"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        path=str(row["path"]),
        file_size=int(row["file_size"]),
        mtime=int(row["mtime"]),
        error=row["error"],
        error_type=row["error_type"],
        stage=row["stage"],
    )


def row_to_analysis_job(row: sqlite3.Row) -> AnalysisJob:
    return AnalysisJob(
        id=str(row["id"]),
        kind=str(row["kind"] or "analyze"),
        model_name=str(row["model_name"]),
        status=str(row["status"]),
        total=int(row["total"] or 0),
        done=int(row["done"] or 0),
        failed=int(row["failed"] or 0),
        queued=int(row["queued"] or 0),
        leased=int(row["leased"] or 0),
        final_failed=int(row["final_failed"] or 0),
        message=str(row["message"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=row["finished_at"],
    )


def row_to_analysis_worker(row: sqlite3.Row) -> AnalysisWorker:
    return AnalysisWorker(
        worker_id=str(row["worker_id"]),
        models=str(row["models"]),
        status=str(row["status"]),
        last_seen_at=str(row["last_seen_at"]),
        created_at=str(row["created_at"]),
        claimed_count=int(row["claimed_count"] or 0),
        completed_count=int(row["completed_count"] or 0),
        failed_count=int(row["failed_count"] or 0),
        released_count=int(row["released_count"] or 0),
        current_task_id=row["current_task_id"],
        stage=row["stage"],
        message=row["message"],
    )


def track_dict(track: Track) -> dict[str, object]:
    return {
        "id": track.id,
        "path": track.path,
        "artist": track.artist,
        "title": track.title,
        "album": track.album,
        "genre": track.genre,
        "year": track.year,
        "duration": track.duration,
        "file_size": track.file_size,
        "missing_at": track.missing_at,
    }


def track_listing_dict(listing: TrackListing) -> dict[str, object]:
    data = track_dict(listing.track)
    data["has_embedding"] = listing.has_embedding
    data["predicted_genres"] = [
        {
            "label": prediction.label,
            "score": prediction.score,
            "rank": prediction.rank,
        }
        for prediction in (listing.predictions or [])
    ]
    return data


def similar_track_dict(result: SimilarTrack) -> dict[str, object]:
    data = track_dict(result.track)
    data["distance"] = result.distance
    data["similarity"] = result.similarity
    data["rating"] = result.rating
    return data
