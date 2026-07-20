# Timeline waveform artifacts

Discocs stores versioned waveform analysis outside SQLite under
`data/timeline/<track-id>/timeline_foundation_v1/`. Each artifact consists of a
canonical `manifest.json` and checksummed little-endian `payload.bin`. SQLite
owns its identity, paths, checksum, byte length and durable analysis state;
generated files remain uncommitted runtime data.

An artifact is usable only while `path + mtime + file_size` matches the current
track. Manifest and payload length/checksum are validated before serving. Writes
use temporary sibling files and atomic replacement, and metadata is committed
only after both final files exist. Cleanup resolves targets under the configured
`data/timeline` root before deletion.

## API and jobs

- `GET /api/v1/tracks/{track_id}/timeline/manifest` returns the public v1
  manifest without the source filesystem path.
- `GET /api/v1/tracks/{track_id}/timeline/payload` returns binary data with a
  checksum ETag and private cache headers.
- `POST /api/v1/timeline/status` accepts 1–100 track IDs and returns
  `missing`, `queued`, `running`, `ready`, `stale`, or `failed`.
- `POST /api/v1/jobs/analyze-timeline` accepts optional `track_ids`, `limit`,
  `reset`, and the frozen `timeline_foundation_v1` extractor.

The private `/admin` dashboard exposes this endpoint as the **DJ waveforms**
analysis task, including ready/missing counts. Waveforms are always generated
offline by that explicit job; opening the DJ surface never starts analysis.

The job uses the shared `track_audio_path` boundary, so local and
Navidrome-backed tracks follow the same path. FFmpeg decodes mono 44.1 kHz audio
in bounded chunks. The extractor produces 512-sample extrema and low/mid/high
spectral-energy buckets before the factor-4 v1 pyramid is published.

## Browser lifecycle

The DJ surface requests the manifest first and validates the payload through
the TypeScript decoder. A promise cache keeps one payload and one set of typed
array views per track; detailed and overview Pixi surfaces share those arrays.
The detailed view follows a 30-second window and the overview spans the track.
Pointer seek targets the physical deck shown by the surface.

Timeline errors never stop audio loading or manual transport. The surface shows
loading/missing/stale/failed state instead of geometry. Reset removes only the
selected foundation extractor immediately before replacement analysis.
While an explicitly queued admin job is running, an open DJ surface polls its
status and loads the artifact as soon as publication completes.
