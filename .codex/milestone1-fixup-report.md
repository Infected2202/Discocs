# Milestone 1 Fix-up Report

## Issues Fixed

- Fixed release artist sidecar updates so fallback release artists are aggregated from all active primary track artists in the release.
- Preserved explicit album/release artist behavior by storing explicit release credits as authoritative and not replacing them with fallback track artists.
- Adjusted local folder release identity for releases without album artists so same-folder/same-title tracks with different primary artists can share one release.
- Hydrated normalized artists in `/api/v1/search` track summaries.
- Cleaned stale generic `external_ids` rows when a provider track mapping is replaced for a track.

## Tests Added

- `test_release_artists_aggregate_track_artists_without_album_artist`
- `test_api_v1_search_track_summaries_include_normalized_artists`
- Extended `test_external_track_replaces_old_provider_mapping_for_track` to assert stale `external_ids` cleanup.

## Test Commands And Results

- `python -m pytest tests\test_store.py::test_release_artists_aggregate_track_artists_without_album_artist tests\test_store.py::test_external_track_replaces_old_provider_mapping_for_track tests\test_api.py::test_api_v1_search_track_summaries_include_normalized_artists --basetemp .pytest-tmp`
  - Passed: `3 passed, 4 warnings`.
- `python -m pytest --basetemp .pytest-tmp`
  - Passed: `201 passed, 4 warnings`.
- `python -m compileall app tests`
  - Passed.

## Remaining Risks

- Existing FastAPI `on_event` deprecation warnings remain unchanged.
- Metadata-only releases without shared provider IDs or shared local folders still use conservative artist-aware identity to avoid accidental cross-release merges.
- No live Navidrome check was run; tests remain local/fake and do not mutate Navidrome data.
