"""Audio from a link: metadata lookup and download via yt-dlp.

yt-dlp is used as a library rather than a subprocess so no user-supplied text
ever reaches a shell, and it is imported lazily inside the worker functions —
the module pulls in a large extractor registry that the rest of the bot has no
use for.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from bot.config import Settings
from bot.services.media_cache import AUDIO_EXTENSIONS
from bot.utils.links import UnsafeLinkError, validate_public_url

logger = logging.getLogger(__name__)

GENERIC_EXTRACTOR_KEYS = {"generic"}
# Кнопка «Поделиться» у SoundCloud (и не только) даёт короткую ссылку-редирект
# вроде on.soundcloud.com/xxxx — её не берёт ни один экстрактор.
MAX_REDIRECT_HOPS = 5
REDIRECT_TIMEOUT_SECONDS = 15.0
ARTIST_TITLE_SEPARATORS = (" - ", " — ", " – ", " ~ ", " | ")
_CHANNEL_SUFFIX = re.compile(r"\s*-\s*topic$", re.IGNORECASE)


class ExternalAudioError(Exception):
    def __init__(self, message: str, *, user_message: str) -> None:
        super().__init__(message)
        self.user_message = user_message


@dataclass(slots=True)
class ExternalTrackInfo:
    media_key: str
    url_key: str
    source: str
    webpage_url: str
    title: str
    artist: str | None = None
    duration: int | None = None
    thumbnail_url: str | None = None

    @property
    def display_line(self) -> str:
        return f"{self.artist} — {self.title}" if self.artist else self.title


@dataclass(slots=True)
class DownloadedAudio:
    info: ExternalTrackInfo
    path: Path
    bitrate_kbps: int | None = None


def media_key_for(url_key: str) -> str:
    """Short stable id: url_key can be long, callback_data cannot."""
    return hashlib.sha1(url_key.encode("utf-8")).hexdigest()[:16]


def clean_uploader(uploader: str | None) -> str | None:
    if not uploader:
        return None
    cleaned = _CHANNEL_SUFFIX.sub("", uploader).strip()
    return cleaned or None


def split_artist_title(title: str, uploader: str | None) -> tuple[str | None, str]:
    """Best-effort "Artist - Title" split, falling back to the uploader.

    Video titles are the only metadata most sources give us, and "Artist - Title"
    is how they are almost always written.
    """
    stripped = title.strip()
    for separator in ARTIST_TITLE_SEPARATORS:
        artist, found, track = stripped.partition(separator)
        if found and artist.strip() and track.strip():
            return artist.strip(), track.strip()
    return clean_uploader(uploader), stripped


def info_from_payload(payload: dict) -> ExternalTrackInfo:
    """Map a yt-dlp info dict onto what the bot needs.

    Music-aware extractors fill `artist`/`track`; everything else only has a
    video title and a channel name.
    """
    entry_type = payload.get("_type")
    if entry_type in {"playlist", "multi_video"}:
        raise ExternalAudioError(
            "Playlists are not supported",
            user_message="Это плейлист — пришли ссылку на конкретный трек.",
        )
    if payload.get("is_live"):
        raise ExternalAudioError(
            "Live streams are not supported",
            user_message="Это прямой эфир, его не скачать.",
        )

    media_id = str(payload.get("id") or "").strip()
    if not media_id:
        raise ExternalAudioError(
            "Payload has no media id",
            user_message="Не удалось распознать трек по ссылке.",
        )

    source = str(payload.get("extractor_key") or payload.get("extractor") or "unknown").lower()
    raw_title = str(payload.get("title") or media_id).strip()
    uploader = payload.get("uploader") or payload.get("channel") or payload.get("artist")

    artist = clean_uploader(payload.get("artist"))
    track_title = (payload.get("track") or "").strip()
    if not artist or not track_title:
        artist, track_title = split_artist_title(raw_title, uploader)

    duration = payload.get("duration")
    url_key = f"{source}:{media_id}"
    return ExternalTrackInfo(
        media_key=media_key_for(url_key),
        url_key=url_key,
        source=source,
        webpage_url=str(payload.get("webpage_url") or payload.get("original_url") or ""),
        title=track_title,
        artist=artist,
        duration=int(duration) if duration else None,
        thumbnail_url=payload.get("thumbnail"),
    )


class LinkAudioService:
    """yt-dlp behind an async facade."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._supported_extractors: list | None = None
        # follow_redirects=False: каждый хоп проверяется отдельно, иначе редирект
        # увёл бы запрос внутрь сети в обход проверок из bot/utils/links.py.
        self._http = httpx.AsyncClient(
            follow_redirects=False,
            timeout=REDIRECT_TIMEOUT_SECONDS,
        )

    async def close(self) -> None:
        await self._http.aclose()

    # -- extractor gate ----------------------------------------------------

    def _known_extractors(self) -> list:
        if self._supported_extractors is None:
            from yt_dlp.extractor import gen_extractor_classes  # noqa: PLC0415

            self._supported_extractors = [
                extractor
                for extractor in gen_extractor_classes()
                if extractor.ie_key().lower() not in GENERIC_EXTRACTOR_KEYS
            ]
        return self._supported_extractors

    def is_supported(self, url: str) -> bool:
        """Whether a real extractor claims this URL.

        The generic extractor accepts everything, which is exactly what we do
        not want: it would happily fetch any address on the internet (and, but
        for the link checks, inside our network) and sniff it for media.
        """
        return any(extractor.suitable(url) for extractor in self._known_extractors())

    # -- yt-dlp options ----------------------------------------------------

    def _base_options(self) -> dict:
        options: dict = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "noprogress": True,
            "restrictfilenames": True,
            "cachedir": False,
            "socket_timeout": 30,
            "retries": 2,
            "extract_flat": False,
            # Belt and braces with is_supported(): even if a URL slips through,
            # yt-dlp refuses to fall back to the generic extractor.
            "allowed_extractors": ["default", "-generic"],
        }
        cookies = self._settings.ytdlp_cookies_file.strip()
        if cookies:
            options["cookiefile"] = cookies
        return options

    # -- public API --------------------------------------------------------

    async def resolve(self, url: str) -> str:
        """Follow a share shortlink until a real extractor claims the target.

        Every hop is validated on its own: following redirects inside httpx
        would let a public shortener bounce the request to an address inside
        our network, past the checks the URL itself passed.
        """
        if await asyncio.to_thread(self.is_supported, url):
            return url
        current = url
        for _hop in range(MAX_REDIRECT_HOPS):
            try:
                response = await self._http.head(current)
            except httpx.HTTPError as exc:
                logger.warning("Could not resolve shortlink %s: %s", current, exc)
                return url
            location = response.headers.get("location")
            if response.status_code not in range(300, 400) or not location:
                return current
            current = str(httpx.URL(current).join(location))
            try:
                current = await asyncio.to_thread(validate_public_url, current)
            except UnsafeLinkError as exc:
                logger.warning("Shortlink %s redirects somewhere unsafe: %s", url, exc)
                raise ExternalAudioError(
                    str(exc),
                    user_message=exc.user_message,
                ) from exc
            if self.is_supported(current):
                logger.info("Resolved shortlink %s -> %s", url, current)
                return current
        return current

    async def fetch_info(self, url: str) -> ExternalTrackInfo:
        url = await self.resolve(url)
        if not await asyncio.to_thread(self.is_supported, url):
            raise ExternalAudioError(
                f"No extractor for {url}",
                user_message="Не знаю такой источник. Работают YouTube, SoundCloud, Bandcamp и подобные.",
            )
        payload = await asyncio.to_thread(self._extract, url, False)
        info = info_from_payload(payload)
        self._check_duration(info)
        return info

    async def download(self, url: str, info: ExternalTrackInfo, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        payload = await asyncio.to_thread(self._extract, url, True, dest_dir, info.media_key)
        path = self._downloaded_path(payload, dest_dir, info.media_key)
        if path is None:
            raise ExternalAudioError(
                "yt-dlp produced no file",
                user_message="Не удалось скачать аудио по ссылке.",
            )
        return path

    # -- internals ---------------------------------------------------------

    def _check_duration(self, info: ExternalTrackInfo) -> None:
        limit_seconds = self._settings.external_max_duration_minutes * 60
        if info.duration and info.duration > limit_seconds:
            raise ExternalAudioError(
                f"Too long: {info.duration}s",
                user_message=(
                    f"Слишком длинное — больше {self._settings.external_max_duration_minutes} минут."
                ),
            )

    def _extract(
        self,
        url: str,
        download: bool,
        dest_dir: Path | None = None,
        media_key: str | None = None,
    ) -> dict:
        from yt_dlp import YoutubeDL  # noqa: PLC0415
        from yt_dlp.utils import DownloadError, ExtractorError  # noqa: PLC0415

        options = self._base_options()
        if download:
            options.update(
                {
                    "format": "bestaudio/best",
                    "max_filesize": self._settings.external_max_download_bytes,
                    "outtmpl": str((dest_dir or Path(".")) / f"{media_key}.%(ext)s"),
                    "overwrites": True,
                    # Cheaper than a second HTTP client in the bot just for cover art.
                    "writethumbnail": True,
                }
            )
        else:
            options["skip_download"] = True

        try:
            with YoutubeDL(options) as ydl:
                payload = ydl.extract_info(url, download=download)
        except (DownloadError, ExtractorError) as exc:
            logger.warning("yt-dlp failed url=%s error=%s", url, exc)
            raise ExternalAudioError(
                str(exc),
                user_message="Источник не отдал этот трек. Возможно, ссылка закрыта или устарела.",
            ) from exc
        if not payload:
            raise ExternalAudioError(
                "yt-dlp returned nothing",
                user_message="Источник ничего не вернул по этой ссылке.",
            )
        if payload.get("_type") in {"playlist", "multi_video"}:
            entries = [entry for entry in (payload.get("entries") or []) if entry]
            if len(entries) != 1:
                raise ExternalAudioError(
                    "Playlists are not supported",
                    user_message="Это плейлист — пришли ссылку на конкретный трек.",
                )
            payload = entries[0]
        return payload

    @staticmethod
    def _downloaded_path(payload: dict, dest_dir: Path, media_key: str) -> Path | None:
        downloads = payload.get("requested_downloads") or []
        for entry in downloads:
            filepath = entry.get("filepath") or entry.get("_filename")
            if filepath and Path(filepath).exists():
                return Path(filepath)
        filename = payload.get("_filename")
        if filename and Path(filename).exists():
            return Path(filename)
        matches = sorted(
            path
            for path in dest_dir.glob(f"{media_key}.*")
            if path.suffix.lower() in AUDIO_EXTENSIONS
        )
        return matches[0] if matches else None

    @staticmethod
    def thumbnail_path(dest_dir: Path, media_key: str) -> Path | None:
        for path in sorted(dest_dir.glob(f"{media_key}.*")):
            if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                return path
        return None
