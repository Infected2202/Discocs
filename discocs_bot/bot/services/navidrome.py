import hashlib
import logging
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from bot.config import Settings
from bot.storage.models import Album, Track

logger = logging.getLogger(__name__)


class NavidromeError(Exception):
    pass


class NavidromeClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.navidrome_base_url.rstrip("/") + "/"
        self._client = httpx.AsyncClient(timeout=60.0)

    async def close(self) -> None:
        await self._client.aclose()

    def _auth_params(self) -> dict[str, str]:
        salt = secrets.token_hex(8)
        token = hashlib.md5(f"{self._settings.navidrome_password}{salt}".encode()).hexdigest()
        return {
            "u": self._settings.navidrome_username,
            "t": token,
            "s": salt,
            "v": self._settings.navidrome_api_version,
            "c": self._settings.navidrome_client_name,
            "f": "json",
        }

    async def _request(self, endpoint: str, **params: Any) -> dict[str, Any]:
        url = urljoin(self._base_url, f"rest/{endpoint}")
        try:
            response = await self._client.get(url, params={**self._auth_params(), **params})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise NavidromeError(str(exc)) from exc
        payload = response.json()
        subsonic = payload.get("subsonic-response", payload)
        if subsonic.get("status") != "ok":
            raise NavidromeError(subsonic.get("error", {}).get("message", "Navidrome API error"))
        return subsonic

    async def ping(self) -> None:
        await self._request("ping.view")
        logger.info("Navidrome ping OK")

    async def search_tracks(
        self, query: str, limit: int = 10, offset: int = 0
    ) -> tuple[list[Track], bool]:
        fetch = limit + 1
        data = await self._request(
            "search2.view",
            query=query,
            songCount=fetch,
            songOffset=offset,
            albumCount=0,
            artistCount=0,
        )
        songs = data.get("searchResult2", {}).get("song") or []
        if isinstance(songs, dict):
            songs = [songs]
        tracks = [self._parse_song(item) for item in songs]
        has_next = len(tracks) > limit
        return tracks[:limit], has_next

    async def get_song(self, song_id: str) -> Track:
        data = await self._request("getSong.view", id=song_id)
        song = data.get("song")
        if not song:
            raise NavidromeError(f"Song not found: {song_id}")
        return self._parse_song(song)

    async def get_random_tracks(self, limit: int = 1) -> list[Track]:
        data = await self._request("getRandomSongs.view", size=limit)
        songs = data.get("randomSongs", {}).get("song") or []
        if isinstance(songs, dict):
            songs = [songs]
        return [self._parse_song(item) for item in songs]

    async def get_album(self, album_id: str) -> tuple[Album, list[Track]]:
        data = await self._request("getAlbum.view", id=album_id)
        album_raw = data.get("album")
        if not album_raw:
            raise NavidromeError(f"Album not found: {album_id}")

        songs = album_raw.get("song") or []
        if isinstance(songs, dict):
            songs = [songs]
        tracks = [self._parse_song(item) for item in songs]
        tracks.sort(key=lambda track: (track.track_number or 0, track.title.lower()))

        year_raw = album_raw.get("year")
        album = Album(
            id=str(album_raw["id"]),
            title=str(album_raw.get("name") or album_raw.get("title") or "Unknown"),
            artist=str(album_raw.get("artist") or "Unknown"),
            year=int(year_raw) if year_raw else None,
            cover_art_id=str(album_raw["coverArt"]) if album_raw.get("coverArt") else None,
            track_count=len(tracks),
        )
        return album, tracks

    async def download_stream(
        self, song_id: str, destination, *, max_bit_rate: int | None = None
    ) -> int:
        url = urljoin(self._base_url, "rest/stream.view")
        params = {**self._auth_params(), "id": song_id}
        if max_bit_rate and max_bit_rate > 0:
            params["maxBitRate"] = str(max_bit_rate)
        bytes_written = 0
        try:
            async with self._client.stream("GET", url, params=params) as response:
                response.raise_for_status()
                with open(destination, "wb") as file:
                    async for chunk in response.aiter_bytes():
                        file.write(chunk)
                        bytes_written += len(chunk)
        except httpx.HTTPError as exc:
            raise NavidromeError(str(exc)) from exc
        logger.debug(
            "Stream downloaded song=%s bytes=%s -> %s",
            song_id,
            bytes_written,
            destination,
        )
        return bytes_written

    def _parse_song(self, song: dict[str, Any]) -> Track:
        year_raw = song.get("year")
        year = int(year_raw) if year_raw else None
        duration_raw = song.get("duration")
        duration = int(duration_raw) if duration_raw else None
        track_raw = song.get("track")
        track_number = int(track_raw) if track_raw else None
        bitrate_raw = song.get("bitRate")
        bitrate_kbps = int(bitrate_raw) if bitrate_raw else None
        return Track(
            id=str(song["id"]),
            title=str(song.get("title") or "Unknown"),
            artist=str(song.get("artist") or "Unknown"),
            album=str(song.get("album") or "Unknown"),
            year=year,
            duration=duration,
            suffix=str(song.get("suffix") or "").lower() or None,
            cover_art_id=str(song["coverArt"]) if song.get("coverArt") else None,
            album_id=str(song["albumId"]) if song.get("albumId") else None,
            track_number=track_number,
            bitrate_kbps=bitrate_kbps,
        )

    async def download_cover_art(
        self, cover_art_id: str, destination: Path, *, size: int | None = 600
    ) -> bool:
        url = urljoin(self._base_url, "rest/getCoverArt.view")
        params = {**self._auth_params(), "id": cover_art_id}
        if size is not None:
            params["size"] = size
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(response.content)
            return True
        except httpx.HTTPError as exc:
            logger.warning("Cover art download failed for %s: %s", cover_art_id, exc)
            return False
