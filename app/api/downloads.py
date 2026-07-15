"""Download endpoints for tracks and track collections."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.api.deps import _navidrome_user_client, api_error, context
from app.api.playlists import _liked_track_ids
from app.audio_source import navidrome_item_id_for_track
from app.downloads import (
    DownloadEntry,
    attachment_filename,
    content_disposition,
    open_navidrome_source,
    public_download_error,
    safe_filename_component,
    stream_track_archive,
    track_download_basename,
)
from app.models import Track
from app.navidrome import NavidromeClient


router = APIRouter(prefix="/api/v1")


def _client_for_tracks(store, settings, tracks: Iterable[Track]) -> NavidromeClient | None:
    if not any(navidrome_item_id_for_track(store, track) is not None for track in tracks):
        return None
    client, _username = _navidrome_user_client(settings)
    return client


def _archive_response(store, settings, entries: list[DownloadEntry], title: str):
    if not entries:
        return api_error(409, "empty_collection", "Collection has no tracks to download")
    try:
        client = _client_for_tracks(store, settings, (entry.track for entry in entries))
    except HTTPException as exc:
        return api_error(exc.status_code, "navidrome_credentials_required", str(exc.detail))
    archive_title = safe_filename_component(title)
    return StreamingResponse(
        stream_track_archive(store, client, entries, root=archive_title),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(f"{archive_title}.zip"),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/tracks/{track_id}/download", response_model=None)
def download_track(track_id: int):
    store, settings = context()
    track = store.get_track(track_id)
    if track is None:
        return api_error(404, "not_found", "Track not found")

    basename = track_download_basename(track)
    item_id = navidrome_item_id_for_track(store, track)
    if item_id is None:
        path = Path(track.path)
        if not path.exists() or not path.is_file():
            store.mark_track_missing(track.id)
            return api_error(410, "audio_missing", "Audio file not mounted or no longer exists")
        store.mark_track_available(track.id)
        return FileResponse(
            path,
            filename=attachment_filename(basename, path.suffix.lower()),
            headers={"Cache-Control": "private, no-store"},
        )

    try:
        client, _username = _navidrome_user_client(settings)
        source = open_navidrome_source(client, item_id, track)
    except HTTPException as exc:
        return api_error(exc.status_code, "navidrome_credentials_required", str(exc.detail))
    except HTTPError as exc:
        return api_error(exc.code, "navidrome_download_failed", f"Navidrome download failed: {exc.reason}")
    except Exception as exc:
        return api_error(
            502,
            "navidrome_download_failed",
            f"Navidrome download failed: {public_download_error(exc)}",
        )

    store.mark_track_available(track.id)

    def body():
        try:
            while chunk := source.stream.read(1024 * 1024):
                yield chunk
        finally:
            source.close()

    return StreamingResponse(
        body(),
        media_type=source.content_type,
        headers={
            "Content-Disposition": content_disposition(
                attachment_filename(basename, source.suffix)
            ),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/releases/{release_id}/download", response_model=None)
def download_release(release_id: int):
    store, settings = context()
    release = store.get_release(release_id)
    if release is None:
        return api_error(404, "not_found", "Release not found")
    entries: list[DownloadEntry] = []
    for index, row in enumerate(store.list_release_tracks(release_id), start=1):
        track_number = row.track_number or index
        prefix = f"{row.disc_number}-{track_number:02d}" if (row.disc_number or 1) > 1 else f"{track_number:02d}"
        entries.append(
            DownloadEntry(
                track=row.track,
                basename=f"{prefix} - {row.track.title or f'Track {row.track.id}'}",
            )
        )
    return _archive_response(store, settings, entries, release.release.title)


@router.get("/playlists/likes/download", response_model=None)
def download_likes():
    store, settings = context()
    try:
        track_ids = _liked_track_ids(store, settings)
    except HTTPException as exc:
        return api_error(exc.status_code, "navidrome_starred_failed", str(exc.detail))
    tracks = [track for track_id in track_ids if (track := store.get_track(track_id)) is not None]
    entries = [
        DownloadEntry(track=track, basename=f"{index:03d} - {track_download_basename(track)}")
        for index, track in enumerate(tracks, start=1)
    ]
    return _archive_response(store, settings, entries, "Liked Tracks")


@router.get("/playlists/{playlist_id}/download", response_model=None)
def download_playlist(playlist_id: int):
    store, settings = context()
    playlist = store.get_playlist(playlist_id)
    if playlist is None:
        return api_error(404, "not_found", "Playlist not found")
    tracks = [
        track for track_id in store.playlist_track_ids(playlist_id)
        if (track := store.get_track(track_id)) is not None
    ]
    entries = [
        DownloadEntry(track=track, basename=f"{index:03d} - {track_download_basename(track)}")
        for index, track in enumerate(tracks, start=1)
    ]
    return _archive_response(store, settings, entries, playlist.title)


@router.get("/mixes/{mix_id}/download", response_model=None)
def download_mix(mix_id: str):
    store, settings = context()
    mix = store.get_generated_mix(mix_id)
    if mix is None:
        return api_error(404, "not_found", "Generated mix not found")
    tracks = [
        track for item in store.list_generated_mix_items(mix_id)
        if (track := store.get_track(item.track_id)) is not None
    ]
    entries = [
        DownloadEntry(track=track, basename=f"{index:03d} - {track_download_basename(track)}")
        for index, track in enumerate(tracks, start=1)
    ]
    return _archive_response(store, settings, entries, mix.title)
