"""Original-audio downloads and streaming ZIP archives."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
from typing import BinaryIO, Iterator, Mapping
from urllib.error import HTTPError
from urllib.parse import quote, unquote
from urllib.request import Request
from zipfile import ZIP_STORED, ZipFile

from app.audio_source import navidrome_item_id_for_track
from app.models import Track
from app.navidrome import NavidromeClient
from app.store import Store


DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,10}$")
_CONTENT_DISPOSITION_FILENAME = re.compile(
    r"filename\*\s*=\s*UTF-8''([^;]+)|filename\s*=\s*\"([^\"]+)\"|filename\s*=\s*([^;]+)",
    re.IGNORECASE,
)
_CONTENT_TYPE_EXTENSIONS = {
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/x-ms-wma": ".wma",
}
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class DownloadEntry:
    track: Track
    basename: str


@dataclass
class AudioSource:
    stream: BinaryIO
    suffix: str
    content_type: str

    def close(self) -> None:
        self.stream.close()


class StreamingZipSink:
    """Non-seekable file object that exposes bytes as soon as ZipFile writes them."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._position = 0

    def write(self, data: bytes) -> int:
        self._buffer.extend(data)
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        return None

    def drain(self) -> bytes:
        chunk = bytes(self._buffer)
        self._buffer.clear()
        return chunk


def safe_filename_component(
    value: str | None,
    *,
    fallback: str = "download",
    max_length: int = 140,
) -> str:
    clean = unicodedata.normalize("NFC", str(value or ""))
    clean = _INVALID_FILENAME.sub("_", clean)
    clean = " ".join(clean.split()).strip(" .")
    if not clean:
        clean = fallback
    if Path(clean).stem.upper() in _WINDOWS_RESERVED_NAMES:
        clean += "_"
    return clean[:max_length].rstrip(" .") or fallback


def track_download_basename(track: Track) -> str:
    artist = safe_filename_component(track.artist, fallback="Unknown artist")
    title = safe_filename_component(track.title, fallback=f"Track {track.id}")
    return f"{artist} - {title}"


def attachment_filename(basename: str, suffix: str) -> str:
    safe_suffix = _safe_suffix(suffix) or ""
    clean_basename = safe_filename_component(
        basename,
        max_length=max(1, 180 - len(safe_suffix)),
    )
    return f"{clean_basename}{safe_suffix}"


def content_disposition(filename: str) -> str:
    suffix = _safe_suffix(filename) or ""
    clean = attachment_filename(Path(filename).stem, suffix)
    ascii_name = clean.encode("ascii", errors="ignore").decode("ascii")
    if not ascii_name.strip(" ."):
        ascii_name = attachment_filename("download", suffix)
    ascii_name = ascii_name.replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(clean, safe='')}"


def _safe_suffix(value: str | None) -> str | None:
    suffix = Path(value or "").suffix.lower()
    return suffix if _SAFE_EXTENSION.fullmatch(suffix) else None


def _header(headers: Mapping[str, str] | object, name: str) -> str:
    getter = getattr(headers, "get", None)
    return str(getter(name, "") if getter is not None else "")


def source_suffix(track: Track, headers: Mapping[str, str] | object | None = None) -> str:
    path_suffix = _safe_suffix(track.path)
    if path_suffix and not str(track.path).startswith("navidrome://"):
        return path_suffix
    if headers is not None:
        disposition = _header(headers, "Content-Disposition")
        match = _CONTENT_DISPOSITION_FILENAME.search(disposition)
        if match:
            filename = unquote(next(group for group in match.groups() if group)).strip(" \"")
            suffix = _safe_suffix(filename)
            if suffix:
                return suffix
        content_type = _header(headers, "Content-Type").split(";", 1)[0].strip().lower()
        if content_type in _CONTENT_TYPE_EXTENSIONS:
            return _CONTENT_TYPE_EXTENSIONS[content_type]
    return path_suffix or ".audio"


def source_content_type(headers: Mapping[str, str] | object) -> str:
    return _header(headers, "Content-Type").split(";", 1)[0].strip() or "application/octet-stream"


def _navidrome_modes(client: NavidromeClient) -> tuple[str, ...]:
    return ("stream",) if client.settings.download_mode.strip().lower() == "stream" else ("download", "stream")


def open_navidrome_source(client: NavidromeClient, item_id: str, track: Track) -> AudioSource:
    last_error: Exception | None = None
    for mode in _navidrome_modes(client):
        request = Request(client.url(mode, {"id": item_id}), headers={"Accept": "*/*"})
        try:
            response = client.opener(request, timeout=float(client.settings.timeout_seconds))
        except HTTPError as exc:
            last_error = exc
            if exc.code not in {403, 404, 405, 501} or mode == _navidrome_modes(client)[-1]:
                raise
            continue
        content_type = source_content_type(response.headers)
        if content_type in {"application/json", "text/html", "text/plain"}:
            response.close()
            raise RuntimeError(f"Navidrome returned non-audio content type: {content_type}")
        return AudioSource(
            stream=response,
            suffix=source_suffix(track, response.headers),
            content_type=content_type,
        )
    if last_error is not None:
        raise last_error
    raise RuntimeError("No Navidrome download mode configured")


@contextmanager
def open_audio_source(
    store: Store,
    client: NavidromeClient | None,
    track: Track,
) -> Iterator[AudioSource]:
    item_id = navidrome_item_id_for_track(store, track)
    if item_id is not None:
        if client is None:
            raise RuntimeError("Navidrome credentials are required for this track")
        source = open_navidrome_source(client, item_id, track)
        store.mark_track_available(track.id)
    else:
        path = Path(track.path)
        if not path.exists() or not path.is_file():
            store.mark_track_missing(track.id)
            raise FileNotFoundError(f"Audio file not mounted or no longer exists: {path.name}")
        store.mark_track_available(track.id)
        source = AudioSource(
            stream=path.open("rb"),
            suffix=source_suffix(track),
            content_type="application/octet-stream",
        )
    try:
        yield source
    finally:
        source.close()


def archive_filename(root: str, basename: str, suffix: str) -> str:
    safe_root = safe_filename_component(root)
    safe_basename = safe_filename_component(basename, fallback="track")
    return f"{safe_root}/{safe_basename}{suffix}"


def unique_archive_filename(candidate: str, used_names: set[str]) -> str:
    if candidate.casefold() not in used_names:
        used_names.add(candidate.casefold())
        return candidate
    path = Path(candidate)
    index = 2
    while True:
        duplicate = f"{path.parent.as_posix()}/{path.stem} ({index}){path.suffix}"
        if duplicate.casefold() not in used_names:
            used_names.add(duplicate.casefold())
            return duplicate
        index += 1


def public_download_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"Navidrome request failed with HTTP {exc.code}: {exc.reason}"
    if isinstance(exc, (FileNotFoundError, RuntimeError, OSError)):
        return str(exc)
    return type(exc).__name__


def stream_track_archive(
    store: Store,
    client: NavidromeClient | None,
    entries: list[DownloadEntry],
    *,
    root: str,
    chunk_size: int = DOWNLOAD_CHUNK_SIZE,
) -> Iterator[bytes]:
    """Yield a ZIP archive incrementally without buffering full tracks or the archive."""
    sink = StreamingZipSink()
    errors: list[str] = []
    used_names: set[str] = set()
    with ZipFile(sink, mode="w", compression=ZIP_STORED, allowZip64=True) as archive:
        for entry in entries:
            try:
                with open_audio_source(store, client, entry.track) as source:
                    member_name = unique_archive_filename(
                        archive_filename(root, entry.basename, source.suffix),
                        used_names,
                    )
                    with archive.open(member_name, mode="w", force_zip64=True) as member:
                        pending = sink.drain()
                        if pending:
                            yield pending
                        while True:
                            chunk = source.stream.read(chunk_size)
                            if not chunk:
                                break
                            member.write(chunk)
                            pending = sink.drain()
                            if pending:
                                yield pending
                    pending = sink.drain()
                    if pending:
                        yield pending
            except Exception as exc:  # keep the rest of a collection downloadable
                errors.append(f"{entry.basename}: {public_download_error(exc)}")

        if errors:
            archive.writestr(
                f"{safe_filename_component(root)}/DOWNLOAD_ERRORS.txt",
                "Some tracks could not be downloaded:\n\n" + "\n".join(errors) + "\n",
            )
            pending = sink.drain()
            if pending:
                yield pending

    pending = sink.drain()
    if pending:
        yield pending
