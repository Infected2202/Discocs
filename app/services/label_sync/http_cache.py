"""Кэш ответов внешних API на диске: повторный прогон не дёргает Beatport/Discogs/Википедию заново."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class HttpCache:
    """SQLite-файл рядом с базой (``data/label_sync_cache.db``), а не в ней: это просто кэш."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, data TEXT NOT NULL)"
        )

    def get(self, key: str, ttl_seconds: float) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT fetched_at, data FROM responses WHERE key = ?", (key,)).fetchone()
        if row is None or time.time() - float(row[0]) > ttl_seconds:
            return None
        return json.loads(row[1])

    def put(self, key: str, data: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO responses (key, fetched_at, data) VALUES (?, ?, ?)",
                (key, time.time(), json.dumps(data, ensure_ascii=False)),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def cache_key(prefix: str, path: str, params: dict[str, object]) -> str:
    return f"{prefix}:{path}?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
