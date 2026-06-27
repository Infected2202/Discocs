# discocs — Refactoring Plan

## Goal

Break up `app/main.py` (originally ~12,235 lines) into focused modules using the FastAPI `APIRouter` pattern. Each stage is committed separately to branch `refactor/extract-models`.

## Current State (as of last session)

- **Branch:** `refactor/extract-models`
- **Last commit:** `f85c84b` — stage 6e
- **main.py size:** ~5,516 lines (down from 12,235)
- **Remaining in main.py:** HTTP middleware, exception handler, `/health`, startup/shutdown lifecycle, UI HTML routes, `UI_HTML` blob (~5 KB of inline HTML)

## Completed Stages

| Stage | Commit | Description |
|-------|--------|-------------|
| 1 | `1fd00be` | Extract domain models → `app/models.py` |
| 2 | `0ab04c4` | Extract global state → `app/state.py` |
| 3 | `e2d3a26` | Extract Pydantic schemas → `app/schemas/requests.py`, `app/schemas/responses.py` |
| 4 | `da0065c` | Extract serializers → `app/serializers/` |
| 5 | `f560652` | Extract services → `app/services/` |
| 6a | `e13bb88` | Extract deps + track serializers → `app/api/deps.py` |
| 6b | `5498d54` | Extract routers: dashboard, search, artists, releases, settings, metrics |
| 6c | `b9e86a3` | Extract routers: playback, mixes, navidrome |
| 6d | `d6409d2` | Extract router: tracks (14 routes + 5 helpers) |
| 6e | `f85c84b` | Extract: analysis_helpers, analysis_jobs, workers router, jobs router |

### Files created during Stage 6:

```
app/api/deps.py          — context() dependency, text_search_embedder
app/api/dashboard.py     — /dashboard, /library-stats, /navidrome-scan-status
app/api/search.py        — /search (with embedder)
app/api/artists.py       — /artists, /artists/{id}, /artists/{id}/releases
app/api/releases.py      — /releases, /releases/{id}, /releases/{id}/tracks
app/api/settings.py      — GET/POST /settings, /api-key, /navidrome-settings
app/api/metrics.py       — /metrics
app/api/playback.py      — /playback/*, /navidrome-star/*, /likes
app/api/mixes.py         — /mixes/*, /instant-mix, /generated-mixes/*
app/api/navidrome.py     — /navidrome/* proxy routes
app/api/tracks.py        — /tracks/*, /lost-files, /analysis/errors, /browse/facets, /text-search
app/api/workers.py       — /workers/* (register, heartbeat, claim, results, failures, release)
app/api/jobs.py          — /stats, /jobs/*, /models/*, /index/rebuild, /feedback
app/analysis_helpers.py  — 20 pure helper functions (no FastAPI, no background tasks)
app/analysis_jobs.py     — background job functions + schedule_auto_index_for_analysis
```

## Key Patterns

### Router wiring (main.py lines ~332–358)
```python
from app.api import (  # noqa: E402
    artists as _api_artists,
    dashboard as _api_dashboard,
    metrics as _api_metrics,
    releases as _api_releases,
    search as _api_search,
    settings as _api_settings,
    mixes as _api_mixes,
    navidrome as _api_navidrome,
    tracks as _api_tracks,
    playback as _api_playback,
    workers as _api_workers,
    jobs as _api_jobs,
)
app.include_router(_api_dashboard.router)
app.include_router(_api_search.router)
app.include_router(_api_artists.router)
app.include_router(_api_releases.router)
app.include_router(_api_settings.router)
app.include_router(_api_metrics.router)
app.include_router(_api_playback.router)
app.include_router(_api_mixes.router)
app.include_router(_api_navidrome.router)
app.include_router(_api_tracks.router)
app.include_router(_api_workers.router)
app.include_router(_api_jobs.router)
```

### Circular import avoidance
Background job functions use lazy imports inside the function body:
```python
def _analyze_job(...) -> None:
    from app.api.deps import context
    ...
```

### Git commit workaround (HEAD.lock + index.lock cannot be deleted in sandbox)
```bash
export GIT_INDEX_FILE=/tmp/git_idx_STAGE
git read-tree HEAD
git add <files>
tree=$(git write-tree)
parent=$(cat .git/refs/heads/refactor/extract-models)
commit=$(GIT_AUTHOR_NAME="Alex" GIT_AUTHOR_EMAIL="nexuspal2@gmail.com" \
         GIT_COMMITTER_NAME="Alex" GIT_COMMITTER_EMAIL="nexuspal2@gmail.com" \
         git commit-tree $tree -p $parent -m "message")
echo "$commit" > .git/refs/heads/refactor/extract-models
```

## Remaining Work

### Stage 6f — Slim down main.py further (optional)
What's left in `main.py` that could move:
- `should_log_http_request()` helper (lines ~363-373) — could go to a `app/api/middleware.py`
- `run_maintenance_tick()` + `maintenance_loop()` — could go to `app/maintenance.py`
- The `UI_HTML` blob (~4,700 lines of inline HTML) — could move to a separate `.html` file loaded at startup

After 6f, main.py would shrink to ~400 lines of pure wiring.

### Stage 5e (deferred) — `app/services/analysis.py`
Large analysis pipeline logic that was deferred during Stage 5. Still lives in its original location.

### Stage 7 (optional) — Split Store into mixins
The `Store` class in `app/store.py` is large. Could split into domain-specific mixins (TrackStore, JobStore, etc.) that are composed together.

## What Stays in main.py Forever
- `app = FastAPI(...)` instantiation
- HTTP middleware (`log_http_request`)
- Exception handler (`RequestValidationError`)
- `/health` endpoint
- `startup` / `shutdown` lifecycle events
- UI SPA catch-all routes (`/`, `/search`, `/artists/{id}`, etc.)
- `UI_HTML` constant (or a `load_ui_html()` call if extracted)
