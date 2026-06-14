from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import secrets
from pathlib import Path
import re
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from app.config import NavidromeSettings


logger = logging.getLogger(__name__)
UrlOpen = Callable[[Request, float], Any]


@dataclass(frozen=True)
class NavidromeSong:
    id: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration: int | None = None
    size: int | None = None
    suffix: str | None = None
    content_type: str | None = None
    genre: str | None = None
    year: int | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class DownloadedTrack:
    path: Path
    bytes_written: int
    content_type: str
    mode: str


@dataclass(frozen=True)
class CoverArt:
    payload: bytes
    content_type: str


class NavidromeClient:
    def __init__(
        self,
        settings: NavidromeSettings,
        *,
        opener: UrlOpen = urlopen,
        app_name: str = "discocs",
        api_version: str = "1.16.1",
    ):
        self.settings = settings
        self.opener = opener
        self.app_name = app_name
        self.api_version = api_version
        self._validate_settings()

    def ping(self) -> dict[str, Any]:
        return self.request("ping")

    def list_songs(
        self,
        *,
        limit: int = 10,
        offset: int = 0,
        query: str = "",
    ) -> list[NavidromeSong]:
        payload = self.request(
            "search3",
            {
                "query": query,
                "songCount": max(limit, 0),
                "songOffset": max(offset, 0),
                "artistCount": 0,
                "albumCount": 0,
            },
        )
        result = payload.get("searchResult3", {})
        songs = _as_list(result.get("song"))
        return [parse_song(song) for song in songs]

    def get_starred_songs(self) -> list[NavidromeSong]:
        try:
            payload = self.request("getStarred2")
        except RuntimeError as exc:
            logger.warning("Navidrome getStarred2 failed, falling back to getStarred: %s", exc)
            payload = self.request("getStarred")
        return songs_from_starred_payload(payload)

    def star_song(self, item_id: str) -> dict[str, Any]:
        return self.request("star", {"id": item_id})

    def unstar_song(self, item_id: str) -> dict[str, Any]:
        return self.request("unstar", {"id": item_id})

    def iter_songs(
        self,
        *,
        page_size: int = 500,
        query: str = "",
        limit: int | None = None,
    ):
        offset = 0
        remaining = limit
        while remaining is None or remaining > 0:
            batch_size = page_size if remaining is None else min(page_size, remaining)
            songs = self.list_songs(limit=batch_size, offset=offset, query=query)
            if not songs:
                break
            for song in songs:
                yield song
            offset += len(songs)
            if remaining is not None:
                remaining -= len(songs)
            if len(songs) < batch_size:
                break

    def request(self, endpoint: str, params: dict[str, object] | None = None) -> dict[str, Any]:
        url = self.url(endpoint, params)
        logger.debug("Navidrome request endpoint=%s url=%s", endpoint, url)
        request = Request(url, headers={"Accept": "application/json"})
        with self.opener(request, timeout=float(self.settings.timeout_seconds)) as response:
            raw = response.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Navidrome returned non-JSON response for {endpoint}") from exc
        subsonic = payload.get("subsonic-response")
        if not isinstance(subsonic, dict):
            raise RuntimeError("Navidrome response did not contain subsonic-response")
        if subsonic.get("status") != "ok":
            error = subsonic.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            suffix = f" code={code}" if code is not None else ""
            raise RuntimeError(f"Navidrome API error{suffix}: {message or subsonic.get('status')}")
        return subsonic

    def download_track(
        self,
        item_id: str,
        target_dir: Path | None = None,
        *,
        suffix: str | None = None,
    ) -> DownloadedTrack:
        target_dir = target_dir or self.settings.temp_dir
        modes = _download_modes(self.settings.download_mode)
        last_error: Exception | None = None
        for mode in modes:
            try:
                return self._download_track_mode(item_id, target_dir, mode=mode, suffix=suffix)
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {403, 404, 405, 501} or mode == modes[-1]:
                    raise
                logger.warning(
                    "Navidrome %s failed for item_id=%s status=%s; trying fallback",
                    mode,
                    item_id,
                    exc.code,
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("No Navidrome download mode configured")

    def get_cover_art(self, cover_art_id: str, *, size: int = 96) -> CoverArt:
        url = self.url("getCoverArt", {"id": cover_art_id, "size": size})
        request = Request(url, headers={"Accept": "image/*"})
        with self.opener(request, timeout=float(self.settings.timeout_seconds)) as response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "")
        if not payload:
            raise RuntimeError(f"Downloaded empty Navidrome cover art payload id={cover_art_id}")
        stripped = payload[:512].lstrip()
        if stripped.startswith((b"{", b"[", b"<")):
            snippet = stripped[:240].decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Downloaded non-image Navidrome cover art payload id={cover_art_id} "
                f"content_type={content_type!r}: {snippet}"
            )
        return CoverArt(payload=payload, content_type=content_type or "image/jpeg")

    def _download_track_mode(
        self,
        item_id: str,
        target_dir: Path,
        *,
        mode: str,
        suffix: str | None,
    ) -> DownloadedTrack:
        url = self.url(mode, {"id": item_id})
        target_dir.mkdir(parents=True, exist_ok=True)
        request = Request(url, headers={"Accept": "*/*"})
        with self.opener(request, timeout=float(self.settings.timeout_seconds)) as response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "")
            filename = _content_disposition_filename(
                response.headers.get("Content-Disposition", "")
            )
        if not payload:
            raise RuntimeError(f"Downloaded empty Navidrome audio payload item_id={item_id}")
        stripped = payload[:512].lstrip()
        if stripped.startswith((b"{", b"[", b"<")):
            snippet = stripped[:240].decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Downloaded non-audio Navidrome payload item_id={item_id} "
                f"content_type={content_type!r}: {snippet}"
            )
        output_suffix = _safe_suffix(suffix) or _safe_suffix(Path(filename or "").suffix) or ".audio"
        target = target_dir / f"{_safe_filename(item_id)}{output_suffix}"
        target.write_bytes(payload)
        return DownloadedTrack(
            path=target,
            bytes_written=len(payload),
            content_type=content_type,
            mode=mode,
        )

    def url(self, endpoint: str, params: dict[str, object] | None = None) -> str:
        endpoint = endpoint.removesuffix(".view")
        base = urljoin(self.settings.url.rstrip("/") + "/", f"rest/{endpoint}.view")
        query = urlencode({**self.auth_params(), **(params or {})})
        return f"{base}?{query}"

    def auth_params(self) -> dict[str, str]:
        base = {
            "u": self.settings.user,
            "v": self.api_version,
            "c": self.app_name,
            "f": "json",
        }
        mode = self.settings.auth_mode.strip().lower()
        if mode == "plain":
            return {**base, "p": self.settings.password}
        if mode != "token":
            raise ValueError("DISCOCS_NAVIDROME_AUTH_MODE must be token or plain")
        salt = secrets.token_hex(8)
        token = hashlib.md5((self.settings.password + salt).encode("utf-8")).hexdigest()
        return {**base, "t": token, "s": salt}

    def _validate_settings(self) -> None:
        missing = []
        if not self.settings.url:
            missing.append("DISCOCS_NAVIDROME_URL")
        if not self.settings.user:
            missing.append("DISCOCS_NAVIDROME_USER")
        if not self.settings.password:
            missing.append("DISCOCS_NAVIDROME_PASSWORD")
        if missing:
            raise ValueError(f"Missing Navidrome settings: {', '.join(missing)}")


def songs_from_starred_payload(payload: dict[str, Any]) -> list[NavidromeSong]:
    starred2 = payload.get("starred2")
    if isinstance(starred2, dict):
        songs = _as_list(starred2.get("song"))
        return [parse_song(song) for song in songs if song.get("id")]
    starred = payload.get("starred")
    if isinstance(starred, dict):
        songs = _as_list(starred.get("song"))
        return [parse_song(song) for song in songs if song.get("id")]
    return []


def parse_song(raw: dict[str, Any]) -> NavidromeSong:
    return NavidromeSong(
        id=str(raw.get("id", "")),
        title=_optional_str(raw.get("title")),
        artist=_optional_str(raw.get("artist")),
        album=_optional_str(raw.get("album")),
        duration=_optional_int(raw.get("duration")),
        size=_optional_int(raw.get("size")),
        suffix=_optional_str(raw.get("suffix")),
        content_type=_optional_str(raw.get("contentType")),
        genre=_optional_str(raw.get("genre")),
        year=_optional_int(raw.get("year")),
        raw=raw,
    )


def _as_list(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _download_modes(mode: str) -> list[str]:
    clean = mode.strip().lower()
    if clean == "download":
        return ["download", "stream"]
    if clean == "stream":
        return ["stream"]
    raise ValueError("DISCOCS_NAVIDROME_DOWNLOAD_MODE must be download or stream")


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return safe or "track"


def _safe_suffix(value: str | None) -> str | None:
    if not value:
        return None
    suffix = value if value.startswith(".") else f".{value}"
    if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix):
        return suffix.lower()
    return None


def _content_disposition_filename(value: str) -> str | None:
    match = re.search(r'filename="?([^";]+)"?', value)
    return match.group(1) if match else None
