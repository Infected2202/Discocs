# Timeline waveform artifacts

Discocs stores versioned waveform analysis outside SQLite under
`data/timeline/<track-id>/timeline_foundation_v2/`. Each artifact consists of a
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
- `POST /api/v1/jobs/analyze-audio-features` is the only analysis command. Its
  durable local/remote worker result contains both scalar features and the
  timeline artifact.

The private `/admin` dashboard exposes one **Audio features + DJ timeline**
task, including combined ready/missing counts. Opening the DJ surface never
starts analysis.

The existing durable worker queue and `track_audio_path` boundary handle local
and Navidrome-backed tracks. The audio-feature analyzer decodes 44.1 kHz PCM
once for rhythm, dynamics, waveform extrema and low/mid/high spectral energy;
its already computed beat timestamps and intervals are encoded into the v2
timeline. A separate 16 kHz decode remains for key and EBU loudness. Existing
v1 artifacts remain isolated until an audio-feature v2 result publishes their
replacement, then legacy rows/artifacts are removed.
The rhythm manifest records `coverage_seconds`; tracks longer than Essentia's
30-minute safe-analysis cap never imply that their later region has a beat grid.

## Browser lifecycle

The DJ surface requests the manifest first and validates the payload through
the TypeScript decoder. A promise cache keeps one payload and one set of typed
array views per track; detailed and overview Pixi surfaces share those arrays.
The detailed view follows a 30-second window and the overview spans the track.
Pointer seek targets the physical deck shown by the surface.

Timeline errors never stop audio loading or manual transport. The surface shows
loading/missing/stale/failed state instead of geometry. Reset queues the
selected active tracks as `audio_features_v2`; the previous timeline stays
readable until a complete replacement result is atomically published.
While an explicitly queued audio-feature task is running, an open DJ surface
derives queued/running/failed state from that durable task and loads the
artifact as soon as publication completes.
