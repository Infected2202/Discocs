# Milestone 2 Playback Foundation Ledger

## Baseline Status

- Branch starts from Milestone 1 Stable Library Graph plus the Milestone 2 prompt commit.
- Worktree was clean at kickoff.
- Existing app shape is a single SQLite `Store` plus FastAPI routes in `app/main.py`.
- Existing `/api/v1` normalized artist/release/search routes are present and must remain compatible.
- Phase 3 plan defines playback sessions, queue items, immutable raw playback events, and aggregate preference counters.
- Most recent documented full gate before this milestone: `python -m pytest --basetemp .pytest-tmp` passed with `201 passed, 4 warnings`; `python -m compileall app tests` passed.

## Planned Slices

1. Baseline audit and Phase 3 plan extraction.
2. Playback schema/tables and row models.
3. Store helpers for sessions and queue.
4. Store helpers for raw playback events and idempotency.
5. Preference aggregation and recomputation helpers.
6. `/api/v1/playback` session routes.
7. `/api/v1/playback` queue routes.
8. `/api/v1/playback` event route.
9. Regression tests for queue click, skip strength, completion, threshold, explicit preferences, and recomputation from raw events.
10. Compatibility test gate for existing Milestone 1 and legacy behavior.
11. Final report and documentation notes.

## Risk Notes

- Raw playback events must remain immutable source-of-truth data; aggregate preference behavior must be recomputable.
- Queue item navigation must record `queue_click` without incrementing skip counters.
- Late skips should be weak/neutral; early skips are stronger negative feedback.
- Track deletion or missing-file changes must not erase historical playback events.
- Playback work must not depend on Essentia, model files, live Navidrome, or external services.
- Phase 3 must not accidentally implement autoplay generation, Flow, dashboard UI, or broad frontend redesign.

## Test Commands

- `python -m pytest --basetemp .pytest-tmp`
- `python -m compileall app tests`

## Current Progress

- Baseline audit complete.
- Phase 3 plan extracted.
- Ledger created before code changes.
- Implemented playback schema and store foundation:
  - `playback_sessions`, `queue_items`, `playback_events`, and user preference tables;
  - session create/read/update helpers;
  - queue replace/append/move/remove/jump helpers;
  - raw playback event insert with `client_event_id` idempotency;
  - event interpretation for queue click, skip strength, threshold, completion, like/dislike, replay, save, and remove;
  - recomputation of aggregate preference counters from raw events.
- Added store tests for durable sessions/queues, queue-click navigation semantics, early vs late skip strength, explicit preference signals, duplicate event idempotency, and recomputation parity.

## Latest Test Results

- `python -m pytest tests\test_store.py::test_playback_session_queue_round_trip tests\test_store.py::test_playback_queue_click_is_navigation_not_skip tests\test_store.py::test_playback_skip_strength_and_recompute_from_raw_events tests\test_store.py::test_playback_completion_like_dislike_replay_save_and_duplicate_idempotency --basetemp .pytest-tmp`: passed, `4 passed`.
- `python -m pytest --basetemp .pytest-tmp`: passed, `205 passed, 4 warnings`.
- `python -m compileall app tests`: passed.
