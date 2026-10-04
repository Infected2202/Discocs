"""Популярность по Deezer: импорт связей треков и фоновое обновление rank/fans.

Связь «трек → трек/альбом Deezer» приходит из tools/library-tags (ID Deezer лежат
в тегах файлов, Navidrome их не отдаёт) через ``recs deezer-import``. Числа
меняются, поэтому это снимки: ``refresh_deezer_popularity`` порциями
перезапрашивает альбомы, чей снимок старше ``max_age_days``. Один запрос
``album/{id}`` отдаёт fans альбома и rank его треков (до 25; длиннее —
дозапрос ``album/{id}/tracks``).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

DEEZER_API = "https://api.deezer.com"
# Лимит Deezer — 50 запросов за 5 с; держимся с запасом.
REQUEST_INTERVAL_SECONDS = 0.2
DEFAULT_MAX_AGE_DAYS = 7
# «Quota limit exceeded» приходит телом ответа с HTTP 200 — это не снятый альбом.
QUOTA_EXCEEDED_CODE = 4

Fetch = Callable[[str], dict]


class DeezerUnavailable(RuntimeError):
    """Сеть/Deezer недоступны — снимок не трогаем, повторим позже."""


@dataclass
class RefreshResult:
    refreshed: int = 0
    failed: int = 0
    tracks_ranked: int = 0

    def summary(self) -> str:
        return f"refreshed={self.refreshed} failed={self.failed} tracks_ranked={self.tracks_ranked}"


def deezer_fetch(path: str) -> dict:
    try:
        response = httpx.get(
            f"{DEEZER_API}/{path}",
            timeout=20,
            headers={"User-Agent": "discocs (popularity refresh)"},
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DeezerUnavailable(f"{path}: {exc}") from exc


def refresh_deezer_popularity(
    store,
    *,
    limit: int,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    fetch: Fetch = deezer_fetch,
    interval: float = REQUEST_INTERVAL_SECONDS,
    now: datetime | None = None,
) -> RefreshResult:
    now = now or datetime.now(timezone.utc)
    older_than = (now - timedelta(days=max_age_days)).isoformat()
    stamp = now.isoformat()
    result = RefreshResult()
    for album_id in store.stale_deezer_albums(older_than, limit):
        try:
            album = fetch(f"album/{album_id}")
            error = album.get("error")
            if isinstance(error, dict) and error.get("code") == QUOTA_EXCEEDED_CODE:
                raise DeezerUnavailable("quota exceeded")
            if error:
                # Альбом снят/недоступен в Deezer: отмечаем, чтобы не спрашивать каждый тик.
                message = str(error.get("message") or error) if isinstance(error, dict) else str(error)
                store.save_deezer_album_snapshot(album_id, fans=None, track_ranks={}, error=message, now=stamp)
                result.failed += 1
                continue
            tracks = (album.get("tracks") or {}).get("data") or []
            if int(album.get("nb_tracks") or 0) > len(tracks):
                time.sleep(interval)
                tracks = fetch(f"album/{album_id}/tracks?limit=1000").get("data") or tracks
        except DeezerUnavailable:
            logger.warning("Deezer popularity refresh stopped: Deezer unavailable", exc_info=True)
            break
        ranks = {
            int(track["id"]): int(track["rank"])
            for track in tracks
            if track.get("id") is not None and track.get("rank") is not None
        }
        store.save_deezer_album_snapshot(
            album_id, fans=_optional_int(album.get("fans")), track_ranks=ranks, now=stamp
        )
        result.refreshed += 1
        result.tracks_ranked += len(ranks)
        time.sleep(interval)
    return result


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None  # type: ignore[arg-type]
