import logging
from typing import Any

import httpx

from bot.config import Settings
from bot.services.navidrome import NavidromeClient
from bot.storage.models import SimilarTrack, Track

logger = logging.getLogger(__name__)


class DiscocsError(Exception):
    def __init__(self, message: str, *, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or "Discocs сейчас недоступен."


class DiscocsClient:
    """Discocs /api/v1/navidrome/similar — модель и фильтры берутся из настроек Discocs."""

    def __init__(self, settings: Settings, navidrome: NavidromeClient) -> None:
        self._settings = settings
        self._navidrome = navidrome
        self._base_url = settings.discocs_base_url.rstrip("/")
        headers = {}
        if settings.discocs_service_token:
            headers["X-Discocs-Service-Token"] = settings.discocs_service_token
        self._client = httpx.AsyncClient(timeout=120.0, headers=headers)

    async def close(self) -> None:
        await self._client.aclose()

    async def ping(self) -> None:
        response = await self._client.get(f"{self._base_url}/health")
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "ok":
            raise DiscocsError(f"Unexpected health response: {payload}")
        logger.info("Discocs health OK")

    async def get_similar(self, song_id: str, *, count: int | None = None) -> list[SimilarTrack]:
        params = {
            "item_id": song_id,
            "count": count or self._settings.discocs_count,
        }
        try:
            response = await self._client.get(f"{self._base_url}/api/v1/navidrome/similar", params=params)
        except httpx.HTTPError as exc:
            raise DiscocsError(str(exc)) from exc

        if response.status_code == 404:
            return []
        if response.status_code >= 400:
            raise self._error_from_response(response)

        payload = response.json()
        items = payload.get("results", [])
        limit = count or self._settings.discocs_count
        results: list[SimilarTrack] = []
        for item in items:
            parsed = self._parse_item(item)
            if parsed:
                results.append(parsed)
            if len(results) >= limit:
                break
        return results

    async def get_similar_tracks(
        self,
        song_id: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[Track], bool]:
        page_size = limit or self._settings.discocs_count
        fetch_count = offset + page_size + 1
        similar = await self.get_similar(song_id, count=fetch_count)
        page_items = similar[offset : offset + page_size]
        has_next = len(similar) > offset + page_size

        tracks: list[Track] = []
        for item in page_items:
            try:
                tracks.append(await self._navidrome.get_song(item.song_id))
            except Exception:
                logger.warning("Failed to load similar song %s", item.song_id)
                if item.track:
                    tracks.append(item.track)
        return tracks, has_next

    def _error_from_response(self, response: httpx.Response) -> DiscocsError:
        detail = response.text
        user_message = "Discocs сейчас недоступен."
        try:
            payload = response.json()
            detail = str(payload.get("detail", detail))
        except Exception:
            pass

        logger.error("Discocs API %s: %s", response.status_code, detail)
        lowered = detail.lower()
        if response.status_code == 503 and "stale" in lowered:
            user_message = (
                "Индекс Discocs устарел.\n"
                "На сервере Discocs выполни: recs build-index"
            )
        elif response.status_code == 404:
            user_message = "Трек не найден в Discocs."
        return DiscocsError(detail, user_message=user_message)

    def _parse_item(self, item: dict[str, Any]) -> SimilarTrack | None:
        song_id = item.get("item_id") or item.get("navidrome_song_id")
        if not song_id:
            return None
        score = float(item.get("similarity", item.get("score", 0)))
        track = Track(
            id=str(song_id),
            title=str(item.get("title") or "Unknown"),
            artist=str(item.get("artist") or "Unknown"),
            album=str(item.get("album") or "Unknown"),
        )
        return SimilarTrack(song_id=str(song_id), score=score, track=track)
