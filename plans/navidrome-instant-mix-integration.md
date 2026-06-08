# Navidrome Instant Mix Integration Plan

## Summary

Goal: integrate discocs with Navidrome so Navidrome Instant Mix uses discocs
Discogs-EffNet recommendations instead of Navidrome's default similar-song
source.

Core decision:

- Navidrome is the source of truth for the playable catalog.
- discocs stores Navidrome song IDs as external IDs.
- The Navidrome plugin is intentionally thin: it receives a Navidrome song ID,
  calls discocs over HTTP, and returns Navidrome song IDs.
- No runtime matching by tags or filesystem paths is allowed.
- Audio analysis for Navidrome-sourced tracks downloads audio from Navidrome
  via the Subsonic/OpenSubsonic API, then analyzes a temporary local file.

This follows the AudioMuseAI shape: the plugin passes `item_id` through, while
the backend owns catalog sync, analysis, recommendation, and diagnostics.

## Target Flow

```text
Navidrome Instant Mix
  -> Navidrome calls plugin with seed song ID
  -> plugin logs request and calls discocs /navidrome/similar
  -> discocs maps Navidrome ID to internal track_id
  -> discocs queries HNSW recommendations
  -> discocs maps result track_ids back to Navidrome IDs
  -> plugin returns Navidrome SongRef IDs
  -> Navidrome builds the mix from its own mediafile IDs
```

Analysis flow:

```text
discocs navidrome sync
  -> import all Navidrome songs and external IDs
  -> create/update internal tracks

discocs analyze
  -> for Navidrome track, download/stream audio by Navidrome ID
  -> write temp file
  -> run existing embedding extraction
  -> save embedding under internal track_id
  -> delete temp file
```

## Phase 1: Navidrome Client and Configuration

Add a small Navidrome/Subsonic client in discocs.

Configuration:

- `DISCOCS_NAVIDROME_URL`
- `DISCOCS_NAVIDROME_USER`
- `DISCOCS_NAVIDROME_PASSWORD`
- `DISCOCS_NAVIDROME_AUTH_MODE`, default `token`
- `DISCOCS_NAVIDROME_TIMEOUT_SECONDS`, default `60`
- `DISCOCS_NAVIDROME_DOWNLOAD_MODE`, default `download`, fallback `stream`
- `DISCOCS_NAVIDROME_TEMP_DIR`, default under `data/tmp/navidrome`

Client responsibilities:

- Build Subsonic auth parameters.
- Call `ping.view` for connection checks.
- Fetch the catalog through official API endpoints.
- Download original audio with `download.view?id=<song_id>` when possible.
- Fallback to `stream.view?id=<song_id>` if download fails in a known
  permission/endpoint way.
- Preserve Navidrome response IDs as strings.

Expected CLI checks:

```bash
recs navidrome-ping
recs navidrome-list --limit 10
recs navidrome-download --item-id SONG_ID --out data/tmp/navidrome-test
```

Acceptance criteria:

- Connection failure reports URL, HTTP status, and error body where available.
- A known Navidrome song ID can be downloaded to a local temp file.
- No Essentia import is needed for ping/list/download tests.

## Phase 2: Database Schema for External IDs

Add schema support without breaking the existing local filesystem workflow.

Tables:

```text
external_tracks
  provider TEXT NOT NULL
  external_id TEXT NOT NULL
  track_id INTEGER NOT NULL
  raw_json TEXT
  synced_at TEXT NOT NULL
  PRIMARY KEY (provider, external_id)
  UNIQUE (provider, track_id)
  FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
```

Optional but useful diagnostic table:

```text
navidrome_sync_runs
  id TEXT PRIMARY KEY
  status TEXT NOT NULL
  started_at TEXT NOT NULL
  finished_at TEXT
  seen_count INTEGER NOT NULL DEFAULT 0
  imported_count INTEGER NOT NULL DEFAULT 0
  updated_count INTEGER NOT NULL DEFAULT 0
  removed_count INTEGER NOT NULL DEFAULT 0
  failed_count INTEGER NOT NULL DEFAULT 0
  message TEXT NOT NULL
```

Track representation:

- Existing `tracks.path` remains required for local-mode tracks.
- For Navidrome-source tracks, use a stable synthetic path:
  `navidrome://<song_id>`.
- Store Navidrome metadata in existing columns where possible:
  `artist`, `title`, `album`, `genre`, `year`, `duration`, `file_size`.
- Store the raw API song object in `external_tracks.raw_json`.

Store methods:

- `upsert_external_track(provider, external_id, metadata, raw_json) -> track_id`
- `get_track_by_external_id(provider, external_id) -> Track | None`
- `external_id_for_track(provider, track_id) -> str | None`
- `list_tracks_missing_embedding` must continue to work for both local and
  Navidrome synthetic paths.

Acceptance criteria:

- Syncing the same Navidrome song twice updates the same internal `track_id`.
- Deleting a track removes its external ID mapping.
- Existing local scan/analyze tests continue to pass.

## Phase 3: Catalog Sync from Navidrome

Implement a durable sync command and API job.

Primary command:

```bash
recs navidrome-sync
```

API job:

```text
POST /jobs/navidrome-sync
GET /jobs
GET /jobs/{job_id}
```

Catalog retrieval strategy:

- Prefer a complete official API traversal.
- Use paginated `search3` if it reliably returns the whole song catalog.
- If needed, validate count/coverage by traversing album endpoints:
  `getAlbumList2` -> `getAlbum`.
- Treat Navidrome song `id` as the only identity key.
- Do not match imported Navidrome songs to existing local filesystem tracks by
  tags, duration, or paths.

Sync behavior:

- Upsert every Navidrome song into `tracks` and `external_tracks`.
- Mark songs absent from the latest sync as missing or stale, not deleted by
  default.
- Preserve embeddings when `external_id` is unchanged and metadata changes.
- Invalidate embeddings only if the source content appears changed, based on
  available stable fields such as size, suffix/content type, or changed raw
  metadata if no better signal exists.

Sync report:

```text
seen_count
imported_count
updated_count
stale_count
external_id_count
tracks_without_external_id
duplicate_external_ids
```

Acceptance criteria:

- After sync, `external_id_count == seen_count`.
- No song is skipped because metadata is ambiguous.
- A failed page/request is visible in logs and leaves the sync run failed.
- Re-running sync is idempotent.

## Phase 4: Navidrome-Sourced Analysis

Extend analysis so Navidrome tracks can be analyzed without filesystem access to
the music library.

Behavior:

- If `track.path` starts with `navidrome://`, resolve the external ID from
  `external_tracks`.
- Download audio to `DISCOCS_NAVIDROME_TEMP_DIR`.
- Use the downloaded temp file for existing embedding extraction.
- Save embedding to the same internal `track_id`.
- Remove temp files after success or failure.
- Keep local filesystem tracks working exactly as before.

Failure handling:

- Download failures are normal analysis failures and should be recorded in
  analysis tasks with stage `navidrome-download`.
- Extraction failures keep the existing behavior and stage.
- Interrupted jobs should not leave active leases permanently stuck.
- Leftover temp files may be cleaned by a safe startup/job cleanup that only
  touches the configured temp directory.

Acceptance criteria:

- A Navidrome track can be analyzed without `track.path` pointing to a real
  filesystem file.
- Existing worker mode can process Navidrome tracks if the worker can reach
  Navidrome API credentials or if the server supplies downloaded audio through
  existing worker task download endpoints.
- Analyze resume behavior remains intact: tracks with existing embeddings for
  the selected model are skipped.

## Phase 5: Recommendation API for Plugin

Add a plugin-facing endpoint:

```text
GET /navidrome/similar?item_id=<navidrome_song_id>&count=50&model=discogs_multi
```

Response:

```json
{
  "provider": "navidrome",
  "seed_item_id": "string",
  "model": "discogs_multi",
  "results": [
    {
      "item_id": "string",
      "track_id": 123,
      "artist": "Artist",
      "title": "Title",
      "album": "Album",
      "similarity": 0.91
    }
  ]
}
```

Rules:

- `item_id` is always a Navidrome song ID.
- Seed lookup is by `external_tracks(provider="navidrome", external_id=item_id)`.
- Results without Navidrome external IDs are skipped.
- Existing recommender filters remain enabled:
  `max_per_artist`, `exclude_same_album`, and minimum duration.
- Return an empty `results` list for missing embeddings or missing index only if
  the plugin needs graceful fallback; otherwise return a typed HTTP error for
  easier debugging during development.

Recommended development default:

- Use typed HTTP errors for missing mapping/index/embedding.
- Let the plugin log the error and return no results to Navidrome.

Acceptance criteria:

- Known analyzed Navidrome seed returns Navidrome IDs only.
- Unknown `item_id` is logged with reason `no_external_mapping`.
- Missing seed embedding is logged with reason `missing_embedding`.
- Missing index is logged with reason `missing_index`.

## Phase 6: Logging and Observability

Add a dedicated Navidrome integration logger.

Python logs:

- `data/logs/navidrome.log`
- logger name: `discocs.navidrome`

Log events:

- Subsonic ping/list/download request start and finish.
- Sync run start, page progress, finish, and failure.
- Per-track download failures.
- Plugin API request start and finish.
- Mapping misses.
- Recommendation result counts.
- Skipped recommendation results without external IDs.

Each plugin API request should include:

```text
request_id
seed_item_id
internal_seed_track_id
model
count
result_count
duration_ms
reason on failure
```

Plugin logs:

- Navidrome input ID and requested count.
- discocs URL.
- HTTP status.
- number of returned results.
- error body on failures, truncated to a safe size.

Acceptance criteria:

- It is possible to answer "did Navidrome call the plugin?" from Navidrome logs.
- It is possible to answer "did discocs resolve and recommend?" from
  `data/logs/navidrome.log`.

## Phase 7: Navidrome Plugin

Create a plugin package under a new directory such as:

```text
plugins/navidrome-instant-mix/
```

Plugin contents:

- Go/TinyGo source.
- `manifest.json`.
- build script for `.wasm`.
- package script for `.ndp`.
- README with Navidrome config example.

Plugin behavior:

- Implement the Navidrome similar-songs export used by Instant Mix.
- Read plugin config:
  - discocs base URL
  - model
  - count
  - timeout
- Receive seed Navidrome song ID.
- Call `/navidrome/similar`.
- Convert response items to Navidrome `SongRef` entries.
- Return only Navidrome IDs.
- On error, log and return an empty list.

Manifest permissions:

- Allow outbound HTTP to the configured discocs host.
- Include plugin config schema.

Acceptance criteria:

- Plugin builds reproducibly.
- Plugin logs a test invocation.
- Instant Mix in Navidrome produces IDs from discocs when the seed has an
  embedding and index.

## Phase 8: Deployment Shape

Development:

```text
Navidrome: Docker container
discocs: host process on port 8711
plugin -> http://host.docker.internal:8711 or LAN host IP
discocs -> Navidrome URL reachable from host
```

Later compose deployment:

```text
Navidrome container
discocs container
plugin -> http://discocs:8711
discocs -> http://navidrome:4533
```

Required setup:

- Put `.ndp` into the Navidrome plugin directory.
- Configure Navidrome plugin HTTP host permissions.
- Set plugin config to discocs base URL.
- Set discocs Navidrome credentials through environment variables.
- Run:

```bash
recs navidrome-ping
recs navidrome-sync
recs analyze
recs build-index
```

## Phase 9: Tests

Unit tests:

- Subsonic auth parameter generation.
- Navidrome song response parsing.
- External ID upsert idempotency.
- External ID lookup in both directions.
- `navidrome://` synthetic path handling.
- Plugin-facing similar endpoint maps external seed ID to internal track and
  internal results back to external IDs.

Integration-style tests without real Navidrome:

- Mock Navidrome API catalog pages.
- Mock `download.view` response with small audio fixture or fake analyzer.
- Verify sync imports every song.
- Verify analyze downloads temp file and cleans it.
- Verify recommendation endpoint skips result tracks without external IDs and
  logs the skip.

Manual smoke test:

```bash
recs navidrome-ping
recs navidrome-sync
recs stats
recs analyze --limit 10
recs build-index
curl "http://localhost:8711/navidrome/similar?item_id=<known-id>&count=10"
```

Plugin smoke test:

- Install `.ndp`.
- Enable debug logging.
- Trigger Instant Mix for a known analyzed seed.
- Confirm Navidrome logs show plugin call.
- Confirm `data/logs/navidrome.log` shows resolved seed and returned results.

## Open Decisions Before Implementation

These are the only decisions that should be made before coding starts:

- Which Navidrome catalog traversal is most reliable on the current Navidrome
  version: paginated `search3` only, or album traversal with `getAlbumList2` and
  `getAlbum`.
- Whether worker machines will download audio directly from Navidrome or always
  receive audio from the discocs server through the existing worker task audio
  endpoint.
- Whether missing-from-sync Navidrome tracks should be marked missing or left
  untouched in the first implementation.

Recommended defaults:

- Use `search3` first and add album traversal only if coverage checks fail.
- Keep worker behavior server-mediated at first, using existing task audio
  download mechanics where possible.
- Mark absent Navidrome tracks as stale/missing, do not delete automatically.

## Implementation Order

1. Add Navidrome client, config, and tests.
2. Add external ID schema and store methods.
3. Add `recs navidrome-ping`, `recs navidrome-list`, and `recs navidrome-sync`.
4. Add Navidrome audio download support for analysis.
5. Add `/navidrome/similar` and dedicated logging.
6. Build the minimal Navidrome Instant Mix plugin.
7. Wire docs and deployment examples.
8. Run end-to-end smoke test against the real Navidrome container.

