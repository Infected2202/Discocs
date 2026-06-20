# Milestone 1 Review Report

## Overall Verdict

**Mostly complete.**

The current branch implements the bulk of Milestone 1: stable library graph schema, metadata expansion, normalization helpers, backfill command, sidecar writes during local scan/Navidrome sync, legacy API preservation, and Phase 2-style entity routes. The branch also passes the ordinary compile and unit test commands when using the documented Windows-safe pytest base temp directory.

However, one high-risk data correctness issue should be fixed before manual app testing: release-level artist relationships are overwritten per track instead of aggregated across all participating tracks in a release. That can make release pages and related discography incomplete for split releases, compilations, EPs, and any album where track artists differ.

## Milestone 1 Requirement Checklist

- **Expanded metadata extraction:** Complete. Local scan metadata now carries album artist, track number, disc number, and total tracks while preserving previous fields.
- **Stable normalized schema:** Complete. Artists, releases, track artists, release artists, external IDs, external tracks, and schema migration plumbing are present.
- **Normalization helpers:** Mostly complete. Artist credit parsing, release identity selection, and envelopes exist and are conservative enough for MVP use.
- **Local scan sidecar writes:** Mostly complete. Track sidecars are written during upsert, but release artist rows are not safely aggregated across all tracks in the same release.
- **Changed-file invalidation and analyzer resume:** Preserved by existing tests and unchanged behavior around path/mtime/file size invalidation and embedding skip logic.
- **Backfill/idempotency:** Mostly complete. Backfill is repeatable for active relationships and reports counts. Metadata-derived orphan releases are documented as acceptable for this milestone.
- **Legacy track APIs and fields:** Preserved. Existing `/tracks`, `/tracks/{id}`, `/similar`, jobs, search, analyze, and index tests pass.
- **Phase 2 entity APIs:** Mostly complete. `/api/v1/search`, `/api/v1/artists/{id}`, `/api/v1/artists/{id}/discography`, `/api/v1/releases/{id}`, and related discography routes exist. Track search summaries currently omit normalized artists.
- **Test coverage:** Meaningful but incomplete. Coverage includes schema migration, sidecar round trips, normalization backfill, entity route basics, Navidrome sync sidecars, and existing behavior. Missing coverage around multi-artist release aggregation and a few API contract details leaves real risk.
- **Secret handling:** Passed. `.codex/local-secrets.env` exists locally, is ignored by git, and was not printed. No secret values were emitted during this review.
- **Navidrome read-only/non-destructive behavior:** Mostly passed by inspection. Tests use fake clients and do not mutate Navidrome. Existing connectivity checks remain read-only. No live Navidrome check was run in this review.
- **Heavy optional dependencies:** Passed. Ordinary unit tests pass without Essentia or real model files; optional dependencies remain extras.

## Issues Found

### Blocker

None found that prevents the test suite from running or the application from starting.

### High

1. **Release artists are overwritten per track instead of aggregated per release.**

   In `app/store.py`, `_upsert_normalized_track_sidecars` deletes all `release_artists` for a release and then inserts only the artist credit from the currently processed track or album artist. For releases without a stable album artist, each later track can erase artists discovered from earlier tracks.

   Impact:
   - `/api/v1/releases/{id}` can show an incomplete artist list.
   - Related discography for a release can miss participating artists.
   - The implementation does not satisfy the Phase 1 requirement to expose all known release-level participating artists when explicit release artists are absent.
   - Tests do not cover a multi-track release with different primary artists.

### Medium

1. **`/api/v1/search` track summaries omit normalized artists.**

   Track search results call `track_summary_dict(store, track)` without hydrating track artists, so returned `artists` arrays are empty even when normalized artists exist. This falls short of the Phase 2 `TrackSummary` contract and can weaken manual API/UI testing.

2. **Generic `external_ids` rows can retain stale track mappings after provider ID replacement.**

   `upsert_external_track` removes old rows from `external_tracks` when a provider mapping changes, but it does not remove the old generic `external_ids` row for the same provider/entity pair. This can leave stale external IDs visible to future consumers of the normalized graph.

3. **New API coverage is basic in places.**

   Tests exercise route existence and core shapes, but they miss multi-artist release aggregation, populated search track artists, stale `external_ids` cleanup, and richer artist/release graph edge cases.

### Low

1. **Phase 2 response models are implemented as dictionaries rather than explicit Pydantic response models.**

   The route shapes mostly match the plan, but the contract is not enforced by FastAPI response models.

2. **`include_tracks` is accepted by artist discography but not meaningfully implemented.**

   The default behavior is aligned with the spec, but the optional parameter currently does not appear to expand release entries with tracks.

3. **No live Navidrome check was run during this review.**

   `.codex/local-secrets.env` exists, but a live check was not necessary for the code audit and no secrets were loaded or printed. Prior reports document read-only connectivity checks.

## Test Commands Run

- `git status --short`
  - Passed. Worktree was clean before creating this report.
- `git show --stat --oneline HEAD`
  - Passed. Current HEAD is `28d94b1 Implement stable library graph foundation`.
- `git log --oneline origin/main..HEAD`
  - Passed. Branch contains Milestone 1 implementation plus earlier bootstrap/preflight commits.
- `git diff --stat origin/main...HEAD`
  - Passed. Reviewed changed files and scope.
- `python -m compileall app tests`
  - Passed.
- `python -m pytest --basetemp .pytest-tmp`
  - Passed: `199 passed, 4 warnings`.
- `Test-Path .codex\local-secrets.env`
  - Passed. File exists locally.
- `git ls-files .codex local-secrets.env .codex/local-secrets.env`
  - Passed. `.codex/local-secrets.env` is not tracked.

## Navidrome Checks Run

No live Navidrome connectivity check was run during this review.

Read-only inspection found:

- `.codex/local-secrets.env` exists locally but is ignored and untracked.
- No secret values were printed.
- New Navidrome tests use fake/local clients and do not mutate remote state.
- Navidrome sync sidecar writes occur inside local SQLite only.
- Existing Navidrome star/unstar API routes remain present, but they are not part of the new Milestone 1 connectivity/test path.

## Recommended Next Action

Run a focused fix-up before manual app testing:

1. Change release artist sidecar logic so fallback release artists are aggregated across all active tracks in a release instead of overwritten by each track.
2. Hydrate normalized artists in `/api/v1/search` track summaries.
3. Clean stale generic `external_ids` rows when a track provider mapping is replaced.
4. Add targeted tests for those cases, especially a multi-track release with different primary artists.

## Fix-up Codex Run Needed Before Manual App Testing

**Yes.**

The branch is close, and the test suite is green, but the release artist aggregation bug is high-risk for the first manual evaluation of the stable library graph. A small hardening pass should happen before using the app to judge Milestone 1 behavior.
