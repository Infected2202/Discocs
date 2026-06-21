# Milestone 3 Listener Library Surfaces Report

## Implemented

- Added `/api/v1/dashboard` and `/api/v1/dashboard/shelves/{key}`.
- Added simple dashboard shelves:
  - Recently Added from normalized releases and track import timestamps;
  - Listen Again from positive playback preference counters;
  - Long Time No Listen from older positive playback history.
- Added listener-facing UI surfaces inside the existing FastAPI-served prototype shell:
  - Flow placeholder and dashboard library shelves;
  - Search page backed by `/api/v1/search`;
  - Artist page with identity/header, local stats, discography, optional top tracks, and release links;
  - Release page with identity/header, release artists, track table, related discography, and playback entry points.
- Added clean shell routes:
  - `/search`;
  - `/artists/{artist_id}`;
  - `/releases/{release_id}`.
- Connected release, artist, and track play buttons to existing `/api/v1/playback/sessions` and the existing audio player.
- Preserved the old prototype sections and workflows:
  - Library;
  - Browse;
  - Recommendations and similar-track seed flow;
  - Text search;
  - Navidrome likes;
  - Instant mix;
  - Evaluation;
  - Jobs, workers, and settings.

## Tests Added

- Dashboard API shelf behavior covering library releases, playback-history shelves, pagination shape, and missing shelf errors.
- Listener route smoke coverage for `/search`, `/artists/{id}`, and `/releases/{id}`.
- UI smoke assertions for the new listener dashboard/search/entity hooks.

## Commits Created

- `50ecb2b Add listener library surfaces`

## Verification

- `python -m pytest tests\test_api.py::test_test_ui_loads --basetemp .pytest-tmp`
  - Passed: `1 passed, 4 warnings`.
- `python -m pytest tests\test_api.py::test_api_v1_dashboard_shelves_use_library_and_playback_history tests\test_api.py::test_listener_surface_routes_serve_shell tests\test_api.py::test_test_ui_loads --basetemp .pytest-tmp`
  - Passed: `3 passed, 4 warnings`.
- `python -m pytest --basetemp .pytest-tmp`
  - Passed: `211 passed, 4 warnings`.
- `python -m compileall app tests`
  - Passed.
- Extracted inline UI script and ran `node --check`.
  - Passed.

## Remaining Risks

- Existing FastAPI `on_event` deprecation warnings remain unchanged.
- Dashboard Recently Added uses current track/release creation timestamps rather than adding the explicit `added_at` schema from the full Phase 5 plan; that larger data-model slice can still land later.
- Top Tracks are rendered when the existing API reports them available, but the backend still returns unavailable until richer local popularity logic is implemented.
- No live browser check against `http://192.168.1.41:8711/` was run in this implementation pass.

## Manual UI Testing

Manual UI testing can start after deploying/restarting the app with this branch. Suggested first paths:

- `/`
- `/search`
- `/artists/{artist_id}`
- `/releases/{release_id}`
