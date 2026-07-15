# Music downloads

The primary web UI can download an original track or a collection of tracks.
No transcoding is performed.

## API

```text
GET /api/v1/tracks/{track_id}/download
GET /api/v1/releases/{release_id}/download
GET /api/v1/playlists/likes/download
GET /api/v1/playlists/{playlist_id}/download
GET /api/v1/mixes/{mix_id}/download
```

The track endpoint returns the original file as an attachment. Collection
endpoints return an uncompressed ZIP (`ZIP_STORED`): audio is already compressed,
so recompressing FLAC/MP3/Opus would spend CPU without a useful size reduction.

Collection ZIPs are generated as a non-seekable stream. The backend reads one
audio source in 1 MiB chunks and yields each ZIP chunk immediately; neither a
complete track nor a temporary archive is buffered on disk or in memory. ZIP64
is forced for members so large lossless tracks and archives over 4 GiB remain
valid.

Albums preserve release order and use track numbers. Playlists and generated
mixes preserve their explicit item order. Archive paths are generated from
metadata and sanitized for Windows/macOS/Linux extraction; server filesystem
paths are never exposed.

## Audio sources and failures

Local tracks are read directly from their indexed paths. Navidrome-mapped tracks
use the active user's Navidrome credentials when authentication is enabled,
request `download.view` first, and fall back to `stream.view` only when the
configured download mode permits it.

A single-track failure is returned as an HTTP error. A collection response may
already have started when a later source fails, so its HTTP status cannot be
changed. The stream remains a valid ZIP and records skipped/failed items in
`DOWNLOAD_ERRORS.txt`. If a source fails after bytes for that member have already
arrived, the member can be partial and the manifest reports the read failure.

All responses use `Content-Disposition: attachment` and
`Cache-Control: private, no-store`. The normal backend auth middleware protects
the endpoints. The public nginx route already proxies the `/api` surface while
continuing to deny operational/admin endpoints.

## UI

The track overflow menu contains **Download**. Release, playlist (including
Liked Tracks), and generated-mix headers expose a **Download** action whenever
the collection has at least one track.
