# Music downloads

The primary web UI can download a track or a collection of tracks, either as the
original file or re-encoded to MP3 320 (`?format=`, below).

## API

```text
GET /api/v1/tracks/{track_id}/download
GET /api/v1/releases/{release_id}/download
GET /api/v1/playlists/likes/download
GET /api/v1/playlists/{playlist_id}/download
GET /api/v1/mixes/{mix_id}/download
```

Every endpoint takes `?format=original|mp3` (default `original`; anything else is
a 422). `mp3` re-encodes Navidrome-backed tracks through `stream.view`
(`format=mp3&maxBitRate=320`, the same re-encode a public share hands out; see
`docs/sharing.md`), because `download.view` always returns the original and
ignores format parameters. The saved extension follows the response
`Content-Type`, not the indexed path. A track without a Navidrome mapping is a
local fallback file: there is no local transcoder, so it leaves in its own
format even when `mp3` is asked for.

The track endpoint returns the file as an attachment. Collection
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

The choice of format is made at the moment of download, not in a profile
setting. The track overflow menu has two items, **Download original** and
**Download as MP3 320**. Release, playlist (including Liked Tracks), and
generated-mix headers expose an icon-only download button
(`components/common/DownloadMenu.tsx`) whenever the collection has at least one
track; it opens a small menu with **Original** and **MP3 320**, both plain links
(the browser owns progress and the Save dialog). In a release header, download is
placed before the final Like action.
