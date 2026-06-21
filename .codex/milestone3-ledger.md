# Milestone 3 Listener Library Surfaces Ledger

## Baseline Status

- Branch starts from Milestone 1 Stable Library Graph and Milestone 2 Playback Foundation.
- Existing app shape is a single FastAPI app with `/api/v1` artist, release, search, and playback endpoints in `app/main.py`.
- Existing frontend is the prototype HTML/CSS/JavaScript served from `app/main.py`; no separate frontend build command is documented in `README.md` or `pyproject.toml`.
- Latest documented gate from Milestone 2: `python -m pytest --basetemp .pytest-tmp` passed with `209 passed, 4 warnings`; `python -m compileall app tests` passed.
- Worktree was clean at kickoff.

## Planned Slices

1. Baseline audit and Phase 4/5 plan extraction.
2. Create this ledger before implementation edits.
3. Inspect current UI structure, routing, and `/api/v1` helper contracts.
4. Add dashboard API foundation and simple shelves using stable library/playback data.
5. Add frontend API client helpers and route handling for `/api/v1/search`, artists, releases, dashboard, and playback session starts.
6. Implement listener-facing search, artist, release, and dashboard surfaces inside the existing prototype shell.
7. Preserve old prototype navigation and track/similar workflows.
8. Add focused backend/API/UI-helper tests.
9. Run quality gates and write final report.
10. Create focused commits after green meaningful slices.

## Risk Notes

- The existing prototype UI is large and operational; changes should be additive and avoid breaking scan/analyze/index/similar workflows.
- Milestone 3 should consume existing playback APIs only for entry points; no autoplay generation, Flow, generated mixes, or MAEST work.
- Dashboard shelves must be useful when playback history exists, but ordinary tests must not require live Navidrome, model files, Essentia, or audio.
- Dashboard `recently_added` should use available timestamps without forcing a broad migration unless necessary for the UI slice.
- Manual browser checks should use `http://192.168.1.41:8711/`, not localhost, if run against the live app.

## Test Commands

- `python -m pytest --basetemp .pytest-tmp`
- `python -m compileall app tests`

## Current Progress

- Baseline audit in progress.
- Phase 4 and Phase 5 specs read.
- Ledger created before implementation edits.
- Implemented dashboard API foundation:
  - `GET /api/v1/dashboard`;
  - `GET /api/v1/dashboard/shelves/{key}`;
  - live Recently Added shelf from normalized releases and track import timestamps;
  - live Listen Again and Long Time No Listen shelves from playback preference counters.
- Implemented first listener-facing UI surfaces inside the existing prototype shell:
  - Flow placeholder and dashboard shelves;
  - `/api/v1/search` backed Search section;
  - Artist surface with header, local stats, discography, optional top-track rendering, and release links;
  - Release surface with header, release artists, track table, related discography, and playback entry points;
  - clean shell routes for `/search`, `/artists/{id}`, and `/releases/{id}`.
- Preserved old Library, Browse, Recommendations, Text search, Navidrome likes, Instant mix, jobs, workers, and settings sections.
- Added focused API/UI smoke tests for dashboard shelves and listener routes.

## Latest Test Results

- `python -m compileall app tests`: passed.
- `python -m pytest tests\test_api.py::test_test_ui_loads --basetemp .pytest-tmp`: passed, `1 passed, 4 warnings`.
- `python -m pytest tests\test_api.py::test_api_v1_dashboard_shelves_use_library_and_playback_history tests\test_api.py::test_listener_surface_routes_serve_shell tests\test_api.py::test_test_ui_loads --basetemp .pytest-tmp`: passed, `3 passed, 4 warnings`.
- `python -m pytest --basetemp .pytest-tmp`: passed, `211 passed, 4 warnings`.
- `python -m compileall app tests`: passed.
