"""Authenticated share management and the narrow public share API."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import api_error, context
from app.api.tracks import audio_response_media_type, navidrome_audio_stream_response
from app.audio_source import navidrome_item_id_for_track
from app.downloads import (
    DownloadEntry,
    attachment_filename,
    content_disposition,
    safe_filename_component,
    stream_track_archive,
    track_download_basename,
)
from app.models import Share, Track, utc_now
from app.navidrome import NavidromeClient
from app.store.shares import share_token_hash

router = APIRouter(prefix="/api/v1")

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{40,64}$")
_PREVIEW_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_UNAVAILABLE = "Share unavailable"
_METADATA_CACHE_CONTROL = "private, no-cache"
_MEDIA_CACHE_CONTROL = "private, max-age=3600"
_PREVIEW_CACHE_CONTROL = "public, max-age=300"
_PREVIEW_MEDIA_CACHE_CONTROL = "public, max-age=3600"
_DOWNLOAD_CACHE_CONTROL = "private, no-store"
_PUBLIC_TRANSCODING_PARAMS = {
    "format": "mp3",
    "maxBitRate": 320,
    "estimateContentLength": "true",
}


class ShareCreateRequest(BaseModel):
    source_type: str = Field(pattern="^(track|release)$")
    source_id: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")


class ShareUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")


class _SlidingRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and events[0] <= now - window_seconds:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


class _StreamSlots:
    def __init__(self) -> None:
        self._active: dict[str, int] = defaultdict(int)
        self._total = 0
        self._lock = threading.Lock()

    def acquire(self, key: str) -> bool:
        per_key = int(os.getenv("DISCOCS_SHARE_MAX_STREAMS_PER_CLIENT", "3"))
        global_limit = int(os.getenv("DISCOCS_SHARE_MAX_STREAMS", "16"))
        with self._lock:
            if self._active[key] >= per_key or self._total >= global_limit:
                return False
            self._active[key] += 1
            self._total += 1
            return True

    def release(self, key: str) -> None:
        with self._lock:
            if self._active.get(key, 0) <= 0:
                return
            self._active[key] -= 1
            self._total -= 1
            if self._active[key] == 0:
                self._active.pop(key, None)


_request_limiter = _SlidingRateLimiter()
_stream_slots = _StreamSlots()


class _StreamSlotResponse(Response):
    """Release a stream slot even when the client disconnects mid-response."""

    def __init__(self, inner: Response, key: str) -> None:
        self.inner = inner
        self.key = key
        self.status_code = inner.status_code
        self.media_type = inner.media_type
        self.background = None
        self.body = b""
        self.raw_headers = inner.raw_headers

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        try:
            await self.inner(scope, receive, send)
        finally:
            _stream_slots.release(self.key)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",")[-1].strip() if forwarded else ""
    return address or (request.client.host if request.client else "unknown")


def _rate_limited() -> JSONResponse:
    response = api_error(429, "share_rate_limited", "Too many share requests")
    response.headers["Retry-After"] = "60"
    return _share_headers(
        response,
        cache_control="private, no-store",
    )  # type: ignore[return-value]


def _share_headers(
    response: Response,
    *,
    cache_control: str = _METADATA_CACHE_CONTROL,
    robots: bool = True,
) -> Response:
    response.headers["Cache-Control"] = cache_control
    response.headers["Referrer-Policy"] = "no-referrer"
    if robots:
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _unavailable() -> JSONResponse:
    return _share_headers(
        api_error(404, "share_unavailable", _UNAVAILABLE),
        cache_control="private, no-store",
    )  # type: ignore[return-value]


def _require_creator(request: Request, settings: object) -> None:
    sharing = settings.sharing  # type: ignore[attr-defined]
    if not sharing.enabled:
        raise HTTPException(status_code=404, detail="Sharing is disabled")
    principal = str(getattr(request.state, "principal", ""))
    user_id = getattr(request.state, "user_id", None)
    if principal == "service" or not isinstance(user_id, int):
        raise HTTPException(status_code=401, detail="User session required")


def _normalize_expiration(value: datetime | None, settings: object) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise HTTPException(status_code=422, detail="expires_at must include a timezone")
    now = datetime.now(UTC)
    normalized = value.astimezone(UTC)
    if normalized <= now:
        raise HTTPException(status_code=422, detail="expires_at must be in the future")
    max_hours = settings.sharing.max_ttl_hours  # type: ignore[attr-defined]
    if normalized > now + timedelta(hours=max_hours):
        raise HTTPException(status_code=422, detail="expires_at exceeds maximum share lifetime")
    return normalized.isoformat()


def _default_expiration(settings: object) -> str:
    hours = min(
        settings.sharing.default_ttl_hours,  # type: ignore[attr-defined]
        settings.sharing.max_ttl_hours,  # type: ignore[attr-defined]
    )
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def _share_status(share: Share) -> str:
    if share.revoked_at is not None:
        return "revoked"
    if share.expires_at is not None:
        try:
            if datetime.fromisoformat(share.expires_at) <= datetime.now(UTC):
                return "expired"
        except ValueError:
            return "expired"
    return "active"


def _source_label(store: object, share: Share) -> str:
    if share.title:
        return share.title
    if share.source_type == "track":
        track = store.get_track(share.source_id)  # type: ignore[attr-defined]
        return (track.title or track.artist or "Shared track") if track else "Shared track"
    release = store.get_release(share.source_id)  # type: ignore[attr-defined]
    return release.release.title if release else "Shared release"


def _management_dict(store: object, share: Share) -> dict[str, object]:
    return {
        "id": share.id,
        "source_type": share.source_type,
        "source_id": share.source_id,
        "source_label": _source_label(store, share),
        "title": share.title,
        "item_count": store.share_item_count(share.id),  # type: ignore[attr-defined]
        "created_at": share.created_at,
        "expires_at": share.expires_at,
        "revoked_at": share.revoked_at,
        "last_accessed_at": share.last_accessed_at,
        "access_count": share.access_count,
        "token_prefix": share.token_prefix,
        "status": _share_status(share),
    }


def _public_url(request: Request, token: str) -> str:
    configured = os.getenv("DISCOCS_PUBLIC_URL", "").strip().rstrip("/")
    forwarded_proto = (
        request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    )
    forwarded_host = (
        request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    )
    forwarded_origin = ""
    if (
        forwarded_proto in {"http", "https"}
        and forwarded_host
        and re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", forwarded_host)
    ):
        forwarded_origin = f"{forwarded_proto}://{forwarded_host}"
    base = configured or forwarded_origin or str(request.base_url).rstrip("/")
    return f"{base}/share/{token}"


def _public_asset_url(request: Request, token: str, suffix: str) -> str:
    share_url = _public_url(request, token)
    origin = share_url.rsplit("/share/", 1)[0]
    return f"{origin}/api/v1/public/shares/{token}/{suffix.lstrip('/')}"


def _preview_public_url(request: Request, token: str) -> str:
    share_url = _public_url(request, token)
    version = request.query_params.get("v", "")
    if _PREVIEW_VERSION_PATTERN.fullmatch(version):
        return f"{share_url}?v={version}"
    return share_url


def _formatted_duration(seconds: float | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _preview_values(store: object, share: Share) -> tuple[str, str, float | None] | None:
    items = store.list_share_items(share.id)  # type: ignore[attr-defined]
    tracks = [store.get_track(item.track_id) for item in items]  # type: ignore[attr-defined]
    valid_tracks = [track for track in tracks if track is not None]
    if not valid_tracks:
        return None
    if share.source_type == "track":
        track = valid_tracks[0]
        description = " · ".join(
            value
            for value in (
                track.artist,
                track.album,
                _formatted_duration(track.duration),
            )
            if value
        )
        return track.title or "Shared track", description, track.duration

    release = store.get_release(share.source_id)  # type: ignore[attr-defined]
    title = release.release.title if release else valid_tracks[0].album or "Shared release"
    artists = (
        ", ".join(artist.name for artist in release.artists)
        if release and release.artists
        else ", ".join(dict.fromkeys(track.artist for track in valid_tracks if track.artist))
    )
    year = str(release.release.release_year) if release and release.release.release_year else None
    total_duration = sum(track.duration or 0 for track in valid_tracks) or None
    description = " · ".join(
        value
        for value in (
            artists or None,
            year,
            f"{len(valid_tracks)} tracks",
            _formatted_duration(total_duration),
        )
        if value
    )
    return title, description, None


@router.post("/shares", status_code=201, response_model=None)
def create_share(request: Request, payload: ShareCreateRequest) -> dict[str, object] | JSONResponse:
    store, settings = context()
    _require_creator(request, settings)
    if not _request_limiter.allow(
        f"create:{getattr(request.state, 'user_id', 'unknown')}", limit=20, window_seconds=3600
    ):
        return _rate_limited()
    fields = payload.model_fields_set
    expires_at = (
        _normalize_expiration(payload.expires_at, settings)
        if "expires_at" in fields
        else _default_expiration(settings)
    )
    try:
        share, token = store.create_share(
            source_type=payload.source_type,
            source_id=payload.source_id,
            expires_at=expires_at,
            title=payload.title,
        )
    except ValueError as exc:
        return api_error(422, "invalid_share_source", str(exc))
    return {"share": _management_dict(store, share), "url": _public_url(request, token)}


@router.get("/shares", response_model=None)
def list_shares(request: Request, include_revoked: bool = True) -> dict[str, object]:
    store, settings = context()
    _require_creator(request, settings)
    shares = store.list_user_shares(include_revoked=include_revoked)
    return {"items": [_management_dict(store, share) for share in shares]}


@router.get("/shares/capabilities", response_model=None)
def share_capabilities(request: Request) -> dict[str, bool]:
    _store, settings = context()
    principal = str(getattr(request.state, "principal", ""))
    return {
        "enabled": bool(settings.sharing.enabled),
        "can_create": bool(
            settings.sharing.enabled
            and principal != "service"
            and isinstance(getattr(request.state, "user_id", None), int)
        ),
    }


@router.get("/shares/{share_id}", response_model=None)
def get_share(request: Request, share_id: str) -> dict[str, object] | JSONResponse:
    store, settings = context()
    _require_creator(request, settings)
    share = store.get_user_share(share_id)
    return _management_dict(store, share) if share else api_error(404, "not_found", "Share not found")


@router.patch("/shares/{share_id}", response_model=None)
def update_share(
    request: Request,
    share_id: str,
    payload: ShareUpdateRequest,
) -> dict[str, object] | JSONResponse:
    store, settings = context()
    _require_creator(request, settings)
    current = store.get_user_share(share_id)
    if current is None or current.revoked_at is not None:
        return api_error(404, "not_found", "Share not found")
    fields = payload.model_fields_set
    title = payload.title if "title" in fields else current.title
    expires_at = (
        _normalize_expiration(payload.expires_at, settings)
        if "expires_at" in fields
        else current.expires_at
    )
    share = store.update_user_share(share_id, title=title, expires_at=expires_at)
    return _management_dict(store, share) if share else api_error(404, "not_found", "Share not found")


@router.delete("/shares/{share_id}", status_code=204, response_model=None)
def revoke_share(request: Request, share_id: str) -> Response | JSONResponse:
    store, settings = context()
    _require_creator(request, settings)
    if not store.revoke_user_share(share_id):
        return api_error(404, "not_found", "Share not found")
    return Response(status_code=204)


def _share_source_title(store, share: Share, valid_tracks: list[Track]) -> str:
    if share.source_type == "release":
        release = store.get_release(share.source_id)
        return release.release.title if release else "Shared release"
    return valid_tracks[0].title or "Shared track"


def _share_track(token: str, position: int) -> tuple[object, object, Track] | None:
    """Resolve one playable item of a live share, or nothing at all.

    Every rejection reason — malformed token, sharing disabled, expired or
    revoked link, unknown position, unmounted file — collapses into the same
    empty result so callers answer with one indistinguishable 404.
    """
    if not _TOKEN_PATTERN.fullmatch(token):
        return None
    store, settings = context()
    if not settings.sharing.enabled:
        return None
    resolved = store.get_active_share_item(share_token_hash(token), position, now=utc_now())
    if resolved is None:
        return None
    _share, item = resolved
    track = store.get_track(item.track_id)
    if track is None or track.missing_at is not None:
        return None
    return store, settings, track


def _resolved_public_share(token: str) -> tuple[object, object, Share] | None:
    if not _TOKEN_PATTERN.fullmatch(token):
        return None
    store, settings = context()
    if not settings.sharing.enabled:
        return None
    share = store.resolve_active_share(share_token_hash(token), now=utc_now())
    return (store, settings, share) if share is not None else None


@router.get("/public/shares/{token}", response_model=None)
def public_share_metadata(token: str, request: Request) -> Response:
    if not _request_limiter.allow(
        f"metadata:{_client_key(request)}", limit=120, window_seconds=60
    ):
        return _rate_limited()
    resolved = _resolved_public_share(token)
    if resolved is None:
        return _unavailable()
    store, _settings, share = resolved
    items = store.list_share_items(share.id)
    tracks = [(item, store.get_track(item.track_id)) for item in items]
    valid_tracks = [track for _item, track in tracks if track is not None]
    if not valid_tracks:
        return _unavailable()
    artists = ", ".join(dict.fromkeys(track.artist for track in valid_tracks if track.artist))
    source_title = _share_source_title(store, share, valid_tracks)
    public_items = [
        {
            "position": item.position,
            "title": (track.title or "Unknown track") if track else "Unavailable track",
            "artist": track.artist if track else None,
            "duration": track.duration if track else None,
            "available": bool(track is not None and track.missing_at is None),
            "audio_url": f"/api/v1/public/shares/{token}/items/{item.position}/audio",
            "download_url": f"/api/v1/public/shares/{token}/items/{item.position}/download",
        }
        for item, track in tracks
    ]
    store.touch_share_access(share.id)
    response = JSONResponse(
        {
            "kind": share.source_type,
            "title": share.title or source_title,
            "subtitle": artists or None,
            "expires_at": share.expires_at,
            "artwork_url": f"/api/v1/public/shares/{token}/cover",
            "download_url": f"/api/v1/public/shares/{token}/download",
            "items": public_items,
        }
    )
    return _share_headers(response)


@router.head("/public/shares/{token}/preview", response_model=None)
@router.get("/public/shares/{token}/preview", response_model=None)
def public_share_preview(token: str, request: Request) -> Response:
    """Server-rendered metadata for link-preview crawlers; never exposes audio."""
    if not _request_limiter.allow(
        f"preview:{_client_key(request)}", limit=120, window_seconds=60
    ):
        return _rate_limited()
    resolved = _resolved_public_share(token)
    if resolved is None:
        return _unavailable()
    store, _settings, share = resolved
    values = _preview_values(store, share)
    if values is None:
        return _unavailable()
    title, description, _duration = values
    share_url = _preview_public_url(request, token)
    cover_url = f'{_public_asset_url(request, token, "cover")}?preview=1'
    escaped_title = escape(title, quote=True)
    escaped_description = escape(description, quote=True)
    escaped_share_url = escape(share_url, quote=True)
    escaped_cover_url = escape(cover_url, quote=True)
    document = f"""<!doctype html>
<html lang="en" prefix="og: https://ogp.me/ns#">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="referrer" content="no-referrer">
    <title>{escaped_title}</title>
    <meta name="description" content="{escaped_description}">
    <link rel="canonical" href="{escaped_share_url}">
    <meta property="og:type" content="website">
    <meta property="og:site_name" content="discocs">
    <meta property="og:title" content="{escaped_title}">
    <meta property="og:description" content="{escaped_description}">
    <meta property="og:url" content="{escaped_share_url}">
    <meta property="og:image" content="{escaped_cover_url}">
    <meta property="og:image:alt" content="{escaped_title}">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{escaped_title}">
    <meta name="twitter:description" content="{escaped_description}">
    <meta name="twitter:image" content="{escaped_cover_url}">
  </head>
  <body></body>
</html>
"""
    return _share_headers(
        HTMLResponse(document),
        cache_control=_PREVIEW_CACHE_CONTROL,
        robots=False,
    )


def _cover_art_id(store: object, share: Share) -> str | None:
    if share.source_type == "release":
        release = store.get_release(share.source_id)  # type: ignore[attr-defined]
        if release and release.release.cover_art_id:
            return str(release.release.cover_art_id)
    items = store.list_share_items(share.id)  # type: ignore[attr-defined]
    if not items:
        return None
    external_id = store.external_id_for_track("navidrome", items[0].track_id)  # type: ignore[attr-defined]
    mapping = store.get_external_track("navidrome", external_id) if external_id else None  # type: ignore[attr-defined]
    if mapping is None or not mapping.raw_json:
        return None
    try:
        raw = json.loads(mapping.raw_json)
    except json.JSONDecodeError:
        return None
    return str(raw.get("coverArt")) if isinstance(raw, dict) and raw.get("coverArt") else None


@router.head("/public/shares/{token}/cover", response_model=None)
@router.get("/public/shares/{token}/cover", response_model=None)
def public_share_cover(token: str, request: Request) -> Response:
    if not _request_limiter.allow(
        f"cover:{_client_key(request)}", limit=120, window_seconds=60
    ):
        return _rate_limited()
    resolved = _resolved_public_share(token)
    if resolved is None:
        return _unavailable()
    store, settings, share = resolved
    cover_art_id = _cover_art_id(store, share)
    if cover_art_id is None:
        return _unavailable()
    try:
        cover = NavidromeClient(settings.navidrome).get_cover_art(cover_art_id, size=1000)
    except Exception:
        return _unavailable()
    is_preview_asset = request.query_params.get("preview") == "1"
    return _share_headers(
        Response(content=cover.payload, media_type=cover.content_type),
        cache_control=(
            _PREVIEW_MEDIA_CACHE_CONTROL if is_preview_asset else _MEDIA_CACHE_CONTROL
        ),
        robots=not is_preview_asset,
    )


@router.head("/public/shares/{token}/items/{position}/audio", response_model=None)
@router.get("/public/shares/{token}/items/{position}/audio", response_model=None)
def public_share_audio(token: str, position: int, request: Request) -> Response:
    resolved = _share_track(token, position)
    if resolved is None:
        return _unavailable()
    store, settings, track = resolved
    slot_key = f"{_client_key(request)}:{share_token_hash(token)}"
    if not _stream_slots.acquire(slot_key):
        return _rate_limited()
    try:
        item_id = navidrome_item_id_for_track(store, track)
        if item_id is not None:
            response = navidrome_audio_stream_response(
                settings,
                item_id,
                range_header=request.headers.get("range"),
                method=request.method,
                stream_params=_PUBLIC_TRANSCODING_PARAMS,
            )
        else:
            path = Path(track.path)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError
            response = FileResponse(
                path,
                media_type=audio_response_media_type(path),
                headers={"Content-Disposition": "inline"},
            )
    except Exception:
        _stream_slots.release(slot_key)
        return _unavailable()
    return _StreamSlotResponse(
        _share_headers(response, cache_control=_MEDIA_CACHE_CONTROL),
        slot_key,
    )


@router.get("/public/shares/{token}/items/{position}/download", response_model=None)
def public_share_item_download(token: str, position: int) -> Response:
    """One track of a share, re-encoded to the MP3 the page already streams.

    A guest never receives the original master: the public profile transcodes
    on the way out, so the download is the same audio the player produced.
    """
    resolved = _share_track(token, position)
    if resolved is None:
        return _unavailable()
    store, settings, track = resolved
    basename = track_download_basename(track)
    item_id = navidrome_item_id_for_track(store, track)
    if item_id is None:
        # There is no local transcoder, so a fallback file leaves in its own
        # format — the same asymmetry public streaming already lives with.
        path = Path(track.path)
        if not path.exists() or not path.is_file():
            return _unavailable()
        return _share_headers(
            FileResponse(
                path,
                media_type=audio_response_media_type(path),
                headers={
                    "Content-Disposition": content_disposition(
                        attachment_filename(basename, path.suffix.lower())
                    )
                },
            ),
            cache_control=_DOWNLOAD_CACHE_CONTROL,
        )
    try:
        response = navidrome_audio_stream_response(
            settings,
            item_id,
            stream_params=_PUBLIC_TRANSCODING_PARAMS,
        )
    except Exception:
        return _unavailable()
    response.headers["Content-Disposition"] = content_disposition(
        attachment_filename(basename, ".mp3")
    )
    return _share_headers(response, cache_control=_DOWNLOAD_CACHE_CONTROL)


@router.get("/public/shares/{token}/download", response_model=None)
def public_share_download(token: str) -> Response:
    """The whole share as a ZIP of the same MP3s the page streams."""
    resolved = _resolved_public_share(token)
    if resolved is None:
        return _unavailable()
    store, settings, share = resolved
    entries: list[DownloadEntry] = []
    for item in store.list_share_items(share.id):
        track = store.get_track(item.track_id)
        if track is None or track.missing_at is not None:
            continue
        entries.append(
            DownloadEntry(
                track=track,
                basename=f"{item.position + 1:03d} - {track_download_basename(track)}",
            )
        )
    if not entries:
        return _unavailable()
    archive_title = safe_filename_component(
        share.title or _share_source_title(store, share, [entry.track for entry in entries])
    )
    # The service account streams every member, exactly as playback does; the
    # creator's own Navidrome credentials are never involved.
    needs_navidrome = any(
        navidrome_item_id_for_track(store, entry.track) is not None for entry in entries
    )
    client = NavidromeClient(settings.navidrome) if needs_navidrome else None
    response = StreamingResponse(
        stream_track_archive(
            store,
            client,
            entries,
            root=archive_title,
            stream_params=_PUBLIC_TRANSCODING_PARAMS,
        ),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"{archive_title}.zip")},
    )
    return _share_headers(response, cache_control=_DOWNLOAD_CACHE_CONTROL)
