# Milestone 2 Playback Foundation Report

## Implemented

- Added durable playback schema:
  - `playback_sessions`;
  - `queue_items`;
  - `playback_events`;
  - `user_track_preferences`;
  - `user_release_preferences`;
  - `user_artist_preferences`.
- Added store helpers for:
  - session create/read/update;
  - queue replace/append/move/remove/jump;
  - raw playback event insertion;
  - `client_event_id` idempotency;
  - aggregate preference updates;
  - full preference recomputation from raw events.
- Added event semantics from the Phase 3 plan:
  - `queue_click` records navigation and does not increment skip counters;
  - early skips increment `early_skip_count` and score more negatively than late skips;
  - completion and play threshold events increment positive counters;
  - like/dislike are mutually exclusive;
  - replay, saved-to-playlist, and removed-from-queue are represented in raw events and aggregate scoring;
  - `autoplay_toggled` and `preference_changed` are valid raw event types.
- Added `/api/v1/playback` endpoints:
  - `POST /api/v1/playback/sessions`;
  - `GET /api/v1/playback/sessions/{session_id}`;
  - `PATCH /api/v1/playback/sessions/{session_id}`;
  - `GET /api/v1/playback/sessions/{session_id}/queue`;
  - `PATCH /api/v1/playback/sessions/{session_id}/queue`;
  - `POST /api/v1/playback/events`.
- Added initial queue creation for release, artist, track, manual, and caller-supplied track lists.
- Kept Phase 3 boundaries:
  - no dashboard UI;
  - no autoplay generation logic;
  - no Flow;
  - no generated mixes;
  - no Navidrome mutation.

## Commits Created

- `3c4aaf0 Add playback store foundation`
- `530ceed Expose playback session APIs`

## Tests Added

- Store tests for:
  - playback session and queue round trip;
  - queue-click navigation without skip counters;
  - early vs late skip strength;
  - completion/play threshold;
  - like/dislike/replay/save/remove;
  - duplicate client event idempotency;
  - recomputation from raw events.
- API tests for:
  - release session create/get/patch;
  - queue add/move/jump behavior;
  - event ingest and duplicate handling;
  - invalid event type rejection.

## Verification

- `python -m pytest tests\test_store.py::test_playback_session_queue_round_trip tests\test_store.py::test_playback_queue_click_is_navigation_not_skip tests\test_store.py::test_playback_skip_strength_and_recompute_from_raw_events tests\test_store.py::test_playback_completion_like_dislike_replay_save_and_duplicate_idempotency --basetemp .pytest-tmp`
  - Passed: `4 passed`.
- `python -m pytest tests\test_api.py::test_api_v1_playback_create_get_and_patch_release_session tests\test_api.py::test_api_v1_playback_queue_patch_jump_records_navigation_not_skip tests\test_api.py::test_api_v1_playback_event_ingest_updates_preferences_and_is_idempotent tests\test_api.py::test_api_v1_playback_event_rejects_invalid_event_type --basetemp .pytest-tmp`
  - Passed: `4 passed, 4 warnings`.
- `python -m pytest --basetemp .pytest-tmp`
  - Passed: `209 passed, 4 warnings`.
- `python -m compileall app tests`
  - Passed.

## Remaining Risks

- Existing FastAPI `on_event` deprecation warnings remain unchanged.
- Playback aggregate scoring uses conservative initial constants; raw event history is preserved so scoring can be recomputed later.
- API responses are dictionary-shaped like existing `/api/v1` routes rather than strict response-model contracts.
- No live browser/API check against `http://192.168.1.41:8711/` was run because this milestone was backend/API/test focused and did not require mutating or depending on the running LAN service.

## Review Recommendation

A review-only hardening run is recommended before manual app testing. The main areas to scrutinize are API response contract shape, preference scoring weights, and whether future UI clients need additional queue operation metadata.
