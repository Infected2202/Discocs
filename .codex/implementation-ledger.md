# Milestone 1 Implementation Ledger

## Baseline Status

- Branch: `codex/milestone-1-stable-library-graph`.
- Existing app is track-centric: `tracks` stores artist/title/album metadata, vectors and analysis outputs remain track-based, and `external_tracks` maps Navidrome items to tracks.
- Existing Navidrome sync has its own transaction-local upsert path in `app/navidrome_sync.py`, separate from `Store.upsert_track`.
- `.codex/bootstrap-report.md` records a green baseline after installing test dependencies in a temporary venv: `188 passed, 4 warnings`; `compileall app tests` passed.
- Bootstrap report notes clean `.[dev]` installs need the current Starlette test client dependency available.

## Planned Slices

1. Metadata field support required by Phase 1.
2. Normalized schema/tables.
3. Normalization helpers.
4. Store upsert/lookup helpers.
5. Idempotent backfill/migration path.
6. Scanner/Navidrome compatibility.
7. Query methods for artists/releases/search.
8. Minimal `/api/v1` entity routes for search, artists, discography, releases, and release tracks.
9. Regression cleanup and documentation notes.

## Risk Notes

- The Navidrome sync path must preserve migrated local mappings and idempotent raw JSON behavior.
- Release identity must avoid merging unrelated local releases too aggressively; provider IDs and path-aware fallbacks take priority.
- Release type must remain `unknown` unless explicitly supplied by metadata/provider data.
- Existing scan/analyze/index and prototype routes must continue to use legacy track fields.
- Heavy optional model dependencies must remain lazy and absent from ordinary unit tests.

## Test Commands

- `python -m pytest`
- `python -m compileall app tests`

## Current Progress

- Baseline audit complete.
- Ledger created before code changes.
- Implemented Phase 1 metadata expansion:
  - `AudioMetadata` and `ScannedTrack` now carry `album_artist`, `track_number`, `disc_number`, and `total_tracks`.
- Implemented normalized sidecar schema:
  - `artists`, `artist_aliases`, `releases`, `release_tracks`, `track_artists`, `release_artists`, and `external_ids`.
- Implemented normalization helpers:
  - text normalization, conservative artist splitting, provider-first release identity, path-aware fallback, and synthetic one-track release fallback.
- Implemented store maintenance:
  - track upsert now updates normalized sidecars without changing legacy return semantics;
  - `external_tracks` mappings are mirrored into `external_ids`;
  - `backfill_library_normalization` is idempotent for active relationships;
  - orphan metadata-derived releases can remain when a provider-backed identity supersedes them, matching the Phase 1 cleanup decision.
- Implemented runtime compatibility:
  - Navidrome sync updates normalized sidecars from provider raw JSON while preserving existing mapping behavior.
- Implemented minimal Phase 2 read API surface:
  - `GET /api/v1/search`;
  - `GET /api/v1/artists/{id}`;
  - `GET /api/v1/artists/{id}/discography`;
  - `GET /api/v1/artists/{id}/top-tracks`;
  - `GET /api/v1/artists/{id}/similar`;
  - `GET /api/v1/releases/{id}`;
  - `GET /api/v1/releases/{id}/tracks`;
  - `GET /api/v1/releases/{id}/related-discography`;
  - `GET /api/v1/releases/{id}/recommendations`;
  - `GET /api/v1/releases/{id}/cover`.
- Added CLI/operator support:
  - `recs normalize-library`;
  - normalization counts in `recs stats`.
- Added regression coverage for helpers, scanner metadata pass-through, store sidecars/backfill, Navidrome sidecars, and `/api/v1` contracts.

## Latest Test Results

- `python -m compileall app tests`: passed.
- `python -m pytest`: failed in this Windows shell because pytest could not scan the default temp root `C:\Users\nexus\AppData\Local\Temp\pytest-of-nexus` (`PermissionError: [WinError 5]`).
- `python -m pytest --basetemp .pytest-tmp`: passed, `199 passed, 4 warnings`.

## Milestone 1 Fix-up

- Fixed release-level artist sidecar refresh so explicit album/release artists remain authoritative, while releases without explicit album artists aggregate primary artists across all active release tracks.
- Adjusted folder-based local release identity so same-folder/same-title releases without album artists are not split by per-track artist.
- Hydrated normalized artists in `/api/v1/search` track summaries.
- Removed stale generic `external_ids` track rows when a provider track mapping is replaced.
- Added regression coverage for split-release artist aggregation, search track summary artists, and stale `external_ids` cleanup.

## Latest Fix-up Test Results

- `python -m pytest tests\test_store.py::test_release_artists_aggregate_track_artists_without_album_artist tests\test_store.py::test_external_track_replaces_old_provider_mapping_for_track tests\test_api.py::test_api_v1_search_track_summaries_include_normalized_artists --basetemp .pytest-tmp`: passed, `3 passed, 4 warnings`.
- `python -m pytest --basetemp .pytest-tmp`: passed, `201 passed, 4 warnings`.
- `python -m compileall app tests`: passed.
