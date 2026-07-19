# Backend Architecture

This document describes how the major backend subsystems built on top of the
original track-centric MVP work today: library normalization, entity APIs,
playback sessions/queue/events, autoplay, generated mixes, and the Flow engine.

Full column-level schema reference lives in `docs/data-model.md`. This document
names tables and their purpose but does not repeat field lists.

## API Routing

All backend REST routes live under `/api/v1/*`, except `/health` and
`/admin/*` (the legacy HTML admin UI, see `## UI Rule` in `CLAUDE.md`) and the
Navidrome plugin's own routes (`/navidrome/similar`, `/navidrome/plugin-event`
served by a separate Go binary, unaffected by this convention).

Every `APIRouter` in `app/api/*.py` is declared with `prefix="/api/v1"`
(`app/api/auth.py` uses `prefix="/api/v1/auth"`). Route decorators use paths
relative to that prefix.

One deliberate exception: `app/api/map.py` (the collection-map / embedding
atlas, see `docs/collection-map.md`) uses `prefix="/api/map"`. It is still
under the nginx `^/(api|admin|health)` backend prefix, so there is no SPA
collision; it is kept in its own namespace as a self-contained diagnostic
surface rather than versioned alongside the core `/api/v1` REST API.

The reason is routing collision avoidance, not versioning ceremony: the new
UI (`ui/src/router.tsx`) is a client-side SPA router with routes like
`artists/:id`, `releases/:id`, `settings`. Nginx and the Vite dev proxy
forward matching path prefixes straight to the backend. Before the
`/api/v1` convention, a direct browser navigation (full page load, not
client-side routing) to `/artists/3468` collided with the backend's own
`/artists/{id}`-shaped routes and returned the backend's JSON 404 instead of
falling through to the SPA's `index.html`. Scoping every backend REST path
under `/api/v1` lets nginx narrow its backend-proxy regex to
`^/(api|admin|health)(/|$)` and safely fall through everything else to the
SPA's `try_files ... /index.html`.

## Library Normalization

Normalizes the original track-centric catalog (`tracks.artist`,
`tracks.album`, etc., kept for compatibility) into stable artist and release
entities, so pages, search, and recommendations can reference IDs instead of
free-text strings. Normalization runs as a sidecar next to the original
schema — it does not replace or stop maintaining the legacy `tracks` columns.

Text and identity resolution lives in `app/library.py`:

- `normalize_text` / `clean_display_text` — casefolded/whitespace-collapsed
  comparison key vs. cleaned display text.
- `parse_artist_credit` — conservative artist-credit splitter. Splits only on
  `;`, `•`, ` & `, ` feat. `, ` ft. `, ` featuring ` (whitespace-bounded via
  lookaround so it doesn't mis-split names); does not attempt aggressive
  multi-artist parsing. Falls back to `Unknown Artist` for empty credits.
- `release_identity_key` — builds a stable dedup key for a release, in
  priority order: provider release ID (`provider:...:release:...`) → local
  folder + normalized title (+ album artist if present) → normalized title +
  artist + year → provider track ID as a synthetic fallback → path as a last
  resort. Every track ends up attached to *some* release, including a
  synthetic one-track release when there's no album metadata.
- `explicit_release_type` — release type is never inferred from track count
  or duration. It's one of `album`, `ep`, `single`, `compilation`,
  `soundtrack`, `mix`, or `unknown` when the source metadata doesn't say.
- `TrackMetadataEnvelope` — the common shape fed into sidecar upsert,
  built via `envelope_from_track_row`, `envelope_from_scanned_track`, or
  `envelope_from_navidrome_song` depending on the source.

Sidecar maintenance lives in `app/store/library.py`
(`_upsert_normalized_track_sidecars`, called from `upsert_track` and from
Navidrome sync) — every local scan or Navidrome import keeps the normalized
tables current without a separate backfill step.

Key tables:

- `artists` — normalized artist entity (`normalized_name` unique).
- `artist_aliases` — alternate names / provider aliases for an artist.
- `releases` — normalized release/album/EP/single/etc. entity
  (`identity_key` unique).
- `release_tracks` — ordered track membership within a release.
- `track_artists` — track-level artist credits (role, position, confidence).
- `release_artists` — release-level artist credits.
- `external_ids` — generic provider ID mapping (provider, entity_type,
  entity_id) → external ID, superset of the older `external_tracks` table
  which is still maintained for compatibility.

## Entity APIs

Exposes the normalized artist/release/track data as stable JSON contracts
for the current UI, replacing ad hoc track-centric shapes with entities that
carry internal numeric IDs. Response shaping (`ImageRef`, `EntityAction`,
`ArtistSummary`, `ReleaseSummary`, `TrackSummary`) lives in
`app/serializers/entities.py` and `app/serializers/search.py`.

Key endpoints:

```text
GET  /api/v1/search
GET  /api/v1/artists/{artist_id}
GET  /api/v1/artists/{artist_id}/discography
GET  /api/v1/artists/{artist_id}/image
GET  /api/v1/artists/{artist_id}/cover
GET  /api/v1/artists/{artist_id}/top-tracks
GET  /api/v1/artists/{artist_id}/similar
GET  /api/v1/releases/{release_id}
GET  /api/v1/releases/{release_id}/tracks
GET  /api/v1/releases/{release_id}/related-discography
GET  /api/v1/releases/{release_id}/recommendations
GET  /api/v1/releases/{release_id}/cover
```

Implementation notes vs. the original spec:

- `/api/v1/releases/{id}/recommendations` is implemented (spec described it
  as a Phase 8 stub returning `available: false`). It uses a release
  embedding centroid (`app/services/release_similarity.py`,
  `find_similar_releases`) and excludes the source release plus every other
  release by the same artist(s).
- `/api/v1/artists/{id}` and `/api/v1/artists/{id}/top-tracks` both return
  populated `top_tracks`/`items` from `store.top_tracks_for_artist`, driven
  by local playback data (`basis: "local_playback"`), not an empty stub.
- `/api/v1/artists/{id}/similar` uses a release-derived artist centroid to
  select candidates, then reranks them by symmetric release-catalog coverage.
  Artist aggregates give every owned release (including singles) equal weight;
  featured appearances and the synthetic `Various Artists` identity are not
  included. The default response contains 16 artists.
- Artist and release cover art is always **proxied through the backend**,
  never linked directly. `artists.image_url` stores the raw URL that
  Navidrome's `getArtistInfo2` returned — it points at the LAN-internal
  Navidrome address, unreachable from outside the LAN and blocked as mixed
  content on an HTTPS page. Serializers therefore expose
  `/api/v1/artists/{id}/cover` as the image URL; that endpoint downloads the
  bytes (in-process cover cache, `app/services/cover.py`) and serves them.

Implementation files: `app/api/search.py`, `app/api/artists.py`,
`app/api/releases.py`, `app/serializers/entities.py`,
`app/serializers/search.py`, `app/services/release_similarity.py`.

## Playback Sessions, Queue, and Events

First-party playback state: which source is playing, the ordered queue, and
an immutable event log used to derive user preference aggregates. Navidrome
remains the cross-client source of truth for `play_count`/`starred`; Discocs
owns local session telemetry Navidrome can't provide (skip strength, queue
clicks, Flow/autoplay reasons).

Key tables:

- `playback_sessions` — one active listening context (source, mode, status,
  current track/queue item, autoplay/shuffle/repeat state).
- `queue_items` — ordered queue entries for a session, tagged with `origin`
  (`source`, `manual`, `autoplay`, `flow`, `generated_mix`) and status
  (`queued`, `playing`, `played`, `skipped`, `removed`).
- `playback_events` — append-only event log (`track_started`, `progress`,
  `play_threshold_reached`, `completed`, `skipped`, `queue_click`, `liked`,
  `disliked`, `replayed`, `removed_from_queue`, `saved_to_playlist`,
  `autoplay_toggled`, etc.), deduplicated by `client_event_id`.
- `user_track_preferences`, `user_release_preferences`,
  `user_artist_preferences` — aggregate counters (play/skip/completion counts,
  running `score`) recomputed synchronously as events land, plus the `liked` /
  `liked_at` mirror of Navidrome stars, which events never write. See
  `docs/data-model.md` for why the two are kept apart.

Event interpretation (early/mid/late skip, meaningful listen, completion
thresholds, like/dislike mutual exclusion) lives in `app/store/_helpers.py`
and is applied inside `PlaybackStoreMixin.record_playback_event`
(`app/store/playback.py`), which is where session state (queue item status,
current track/queue item) and preference aggregates both update atomically
per event. Queue click (`queue_click`) never lowers preference scores;
`skipped` does, scaled by how early the skip happened.

Key endpoints:

```text
GET   /api/v1/playback/settings
POST  /api/v1/playback/sessions
GET   /api/v1/playback/sessions/{session_id}
PATCH /api/v1/playback/sessions/{session_id}
GET   /api/v1/playback/sessions/{session_id}/queue
PATCH /api/v1/playback/sessions/{session_id}/queue
POST  /api/v1/playback/events
```

The queue `PATCH` endpoint is a single operation-dispatch route (`replace`,
`add`, `remove`, `move`, `jump`, `mark_current`), not one endpoint per
operation.

When a Navidrome-mapped track crosses the meaningful-listen threshold, the
event endpoint also fires a Subsonic `scrobble` call
(`maybe_scrobble_navidrome_play` in `app/serializers/playback.py`); Navidrome
failures never fail local event recording.

Implementation files: `app/api/playback.py`, `app/store/playback.py`,
`app/store/_helpers.py`, `app/serializers/playback.py`, `app/models.py`
(session/queue/event dataclasses and enums).

### Browser audio buffering and transcoding

The web player keeps buffering on the client device. The active
`HTMLAudioElement` uses `preload="auto"` for immediate playback and a parallel
full-response fetch guarantees completion when a mobile browser stops native
buffering early. Once ready, playback adopts the local `blob:` source at the
same timestamp. Only after the active track is fully available does
`playerStore` fetch the next queue item's `/audio` response as another Blob,
so the transition does not wait for mobile networking. Only the active Blob
and one next Blob are retained. Source/queue changes abort stale fetches, and
object URLs are revoked after use or logout. An early skip never waits for
prefetch and falls back to the ordinary network URL.

Playback settings are per-user keys in `user_settings`:

- `transcoding_enabled` (default `false`);
- `transcoding_bitrate_kbps` (`96`, `128`, `192`, `256`, or `320`; default
  `192`).

Raw playback sends `format=raw` to Navidrome. Enabled transcoding sends
`format=mp3`, `maxBitRate=<quality>`, and `estimateContentLength=true` using
the active user's Navidrome credentials. Navidrome must have an applicable MP3
transcoding profile. The browser keeps no persistent/offline audio cache.

Implementation files: `ui/src/engine/AudioEngine.ts`,
`ui/src/store/playerStore.ts`, `ui/src/pages/SettingsPage.tsx`,
`app/api/tracks.py`, `app/api/settings.py`, and `app/store/settings.py`.

## Autoplay

Continues whatever source is currently playing (release, artist, track,
playlist, search result, manual queue, generated mix) once the explicit
queue runs low, biased toward that source rather than general personal
taste — the opposite emphasis from Flow. Implemented in `app/autoplay.py`.

Pipeline:

1. `build_source_context` reconstructs seed tracks from the session's
   source (`_source_seed_track_ids`: release tracks, artist tracks, playlist
   items, generated-mix items, or the accepted/queued tracks so far for
   search/manual/flow sources), plus accepted/skipped track IDs from
   `playback_events` and tracks already in the queue (to exclude).
2. `generate_autoplay_candidates` pulls nearest neighbors via
   `Recommender.similar_mix` from the seed tracks, then scores each
   candidate as a weighted sum: source similarity, similarity to
   session-accepted tracks, a personal-preference term derived from
   `user_track_preferences.score`, a small freshness/continuity bonus, minus
   a skip-similarity penalty (recently skipped tracks suppress similar
   candidates).
3. `apply_autoplay_caps` enforces `max_per_artist` / `max_per_release` caps
   against tracks already queued with `origin="autoplay"`.
4. `refill_autoplay_queue` maintains a candidate pool cached in the
   session's `state_json` (`autoplay_pool` key) so repeated refills don't
   regenerate from scratch, and appends enough queue items to keep the
   visible buffer full.

Preference chips (`All`, `Familiar`, `Recommended`, `Party`, `Energy`,
`Training`) are implemented as scoring-weight multipliers in
`resolve_autoplay_settings`, not separate algorithms.

Key endpoint:

```text
POST /api/v1/autoplay/refill
```

Also relevant: `PATCH /api/v1/playback/sessions/{id}` accepts
`autoplay_enabled` toggling, and `POST /api/v1/tracks/{track_id}/instant-mix`
(`app/api/mixes.py`) starts a track-scoped instant mix session/queue from the
shared track action menu — a related but distinct one-shot flow, not
continuous autoplay. When that seed track is already playing, the web player
adopts the new session and queue without reloading the audio, preserving its
playback position and paused/playing state. The shared **Play next** action
adds a manual queue item and then moves it immediately after the current item;
plain queue `add` remains append-only at the API level.

All `AutoplaySettings` fields (`visible_buffer`, `candidate_count`,
`max_per_artist`, `max_per_release`, `source_weight`, `accepted_weight`,
`personal_weight`, `exploration_ratio`, `recent_skip_penalty`) are editable in
the legacy admin UI's Settings > Autoplay tab (`app/ui.html`) and sent as
`autoplay_*` keys in the `settings` body of `POST /api/v1/autoplay/refill`.

Implementation files: `app/autoplay.py`, `app/api/playback.py`.

## Generated Mixes

Finite (~100-track), inspectable, periodically-refreshed personalized
playlists shown as dashboard cards — distinct from Flow's continuous
adaptive stream. Implemented in `app/mixes.py` (generation pipeline) and
`app/store/mixes.py` (storage).

Pipeline (`app/mixes.py`):

1. `_load_taste_seeds` collects positive-signal tracks (liked, replayed,
   highly completed) with embeddings.
2. `build_taste_regions` clusters seeds by similarity threshold into
   `TasteRegion`s (centroid, seed/member tracks, representative tracks) —
   the same style of inspectable clustering the Flow engine uses, but with
   its own settings and independent of `flow_regions`.
3. `_select_anchor_regions` spreads dashboard mix anchors across regions
   instead of picking near-duplicate regions when taste is tightly
   clustered.
4. `_generate_region_items` builds a candidate pool per anchor region via
   HNSW, scores candidates (region similarity + preference + discovery bonus
   + freshness − skip/overuse/duplicate penalties), and `_sequence_items`
   orders the final list to avoid same-artist/release runs.
5. `ensure_dashboard_mixes` / `ensure_dashboard_mixes_fast` regenerate stale
   mixes on a cadence and are called opportunistically from the mixes list
   endpoint so the dashboard doesn't need a background scheduler to stay
   fresh.

Key tables:

- `generated_mixes` — one row per mix (`mix_type`, `status`, anchor/settings/
  score-summary JSON, optional `saved_playlist_id`).
- `generated_mix_items` — ordered track membership with per-item score and
  reason JSON.

Key endpoints:

```text
GET  /api/v1/mixes
GET  /api/v1/mixes/settings
PUT  /api/v1/mixes/settings
GET  /api/v1/mixes/status
GET  /api/v1/mixes/{mix_id}
GET  /api/v1/mixes/{mix_id}/cover
POST /api/v1/mixes/generate
POST /api/v1/mixes/{mix_id}/save
POST /api/v1/mixes/{mix_id}/play
```

`POST /api/v1/mixes/{mix_id}/save` copies a generated mix into a stable
`playlists` row (`save_generated_mix_as_playlist`); saved mixes stop
refreshing. `POST /api/v1/mixes/{mix_id}/play` creates a playback session
seeded with the mix's tracks (`origin="generated_mix"`).

Also in this area: instant-mix history (`GET/POST
/api/v1/instant-mix/requests...`) lives in the same router file
(`app/api/mixes.py`) but is a separate feature — a debug/history log of
track-scoped instant mixes, not part of the generated-mixes pipeline.

Implementation files: `app/mixes.py`, `app/store/mixes.py`,
`app/api/mixes.py`, `app/serializers/mixes.py`.

## Flow Engine

The single-button continuous personal listening stream — "open the app,
press Flow, listen" — driven by long-term taste regions blended with
short-term session behavior. Distinct from Autoplay (source-anchored) and
Generated Mixes (finite, dashboard-listed).

### Taste regions

`app/services/flow_regions.py` builds `flow_profiles`/`flow_regions` via
similarity-threshold clustering, independent of (but structurally similar
to) the generated-mixes region builder:

- `_collect_seeds` pulls tracks with positive signal (liked, replayed,
  repeatedly completed, or simply played) and computes a per-seed `weight`
  from engagement consistency (`_seed_weight`) — a single play is a weak
  seed (~0.37) that can only refine an existing region, not start one;
  replays and explicit likes dominate.
- `_cluster_seeds` greedily assigns seeds to the first existing region whose
  centroid is within cosine similarity `region_threshold` (default `0.72`),
  else starts a new region, recomputing a weighted centroid
  (`_weighted_centroid`) after each assignment.
- `_finalize_region` picks a medoid and representative tracks per region;
  `_estimate_candidate_coverage` queries HNSW around the centroid to record
  how many reachable candidates the region has.
- **Cold start**: if the user has no positive signal yet, `_build_cold_start_regions`
  uses farthest-point sampling over a random library sample to spread
  exploration regions across embedding space instead of failing — these
  regions carry `seed_count=0` so the engine treats them as maximally
  uncertain and explores aggressively.
- `rebuild_flow_profile` persists everything: profile status
  (`building` → `ready`/`cold_start`/`empty`), regions, region-track roles
  (`seed`, `representative`), and centroid vectors
  (`flow_region_embeddings`).

Key tables:

- `flow_profiles` — one profile per `model_key` (status, settings, last
  build timestamp).
- `flow_regions` — clustered taste regions (centroid ref, medoid track,
  weight, seed/candidate counts, summary/quality JSON).
- `flow_region_tracks` — per-region track roles (`seed`, `representative`).
- `flow_region_embeddings` — normalized centroid vectors per region+model.
- `flow_generation_runs` — one row per start/refill call, for diagnostics
  (candidate/selected counts, score summary).

### Session flow

`app/api/flow.py` + `app/services/flow_candidates.py`:

- `POST /api/v1/flow/start` — loads the profile for `model_key`, picks the
  active region (`_choose_region`: explicit hint or highest-weight region),
  builds an initial candidate pool and fills the visible queue
  (`fill_flow_queue`), creates a `playback_sessions` row with
  `source_type="flow"`, and stores Flow state (active region, session
  skip/accept counters, exploration level) in `state_json`. If the profile's
  `last_built_at` is older than `settings.rebuild_max_age_minutes` (default
  30 minutes; `0` disables), the profile is rebuilt synchronously before
  serving, so Navidrome-synced play signals since the last session are
  reflected without a manual rebuild call.
- `POST /api/v1/flow/refill` — reads accumulated session state from
  `state_json`, tops the queue back up to the visible buffer, and records
  a new `flow_generation_runs` row.
- `POST /api/v1/flow/event` — applies a single playback event to Flow's
  short-term session state (`app/services/flow_feedback.py`,
  `apply_flow_event`) without triggering a refill; the caller decides when
  to refill separately.
- `GET /api/v1/flow/profile` — profile summary; region detail and quality
  stats only with `include_debug=true`.

Candidate scoring blends long-term region fit and short-term session state
(recently accepted/skipped track similarity, artist/release exposure this
session) with configurable weighting (`long_term_weight`/`session_weight`,
default `0.70/0.30`), plus an `exclude_played_days` filter
(`Store.recently_played_track_ids`, default 7 days) that keeps recently
heard tracks out of a fresh Flow pool across sessions.

Key endpoints:

```text
POST /api/v1/flow/start
POST /api/v1/flow/refill
POST /api/v1/flow/event
GET  /api/v1/flow/profile
POST /api/v1/jobs/flow-profile
```

(`POST /api/v1/jobs/flow-profile` triggers `rebuild_flow_profile` as a
background job — see `app/api/jobs.py`.)

Implementation files: `app/api/flow.py`, `app/store/flow.py`,
`app/services/flow_regions.py`, `app/services/flow_candidates.py`,
`app/services/flow_feedback.py`.

## Collection Map / Embedding Atlas

A diagnostic 2D map of the embedding space, rendered in the old admin
(`app/ui.html`, `#atlas`) with a deck.gl WebGL point cloud. It is a **viewing
surface only** — it never feeds ranking, the HNSW index, or the original
similarity math. "Near on the map" is 2D-projection proximity; the inspection
panel's neighbors are **real** HNSW/cosine similarity from the recommender.

A projection reduces each model's embeddings to 2D (UMAP, `metric=cosine`; PCA
baseline) and persists `(track_id, x, y)` rows plus metadata/diagnostics. Lost
tracks (`missing_at`) are excluded. Multiple projections per model are allowed;
a `stale` flag is derived from embedding-count drift (mirrors the HNSW
staleness check). Builds run as a **backend `BackgroundTask`**
(`_build_map_projection_job`), not the per-track analysis worker queue, and
report through the progress-job mechanism. The heavy reducer is lazy-imported
and injectable, so unit tests run without UMAP.

Key endpoints (router prefix `/api/map`, see the API Routing exception above):

```text
GET  /api/map/projections            # list (+ stale)
POST /api/map/projections            # enqueue a build job
GET  /api/map/projections/{id}/points        # parallel typed arrays
GET  /api/map/projections/{id}/color/{dim}   # per-point color values
GET  /api/map/tracks/{id}/neighbors  # REAL HNSW neighbors (not x/y)
GET  /api/map/mixes | /api/map/regions       # overlay membership
```

Implementation files: `app/api/map.py`, `app/projection.py`,
`app/store/map_atlas.py`, `_build_map_projection_job` in
`app/analysis_jobs.py`. Full detail: `docs/collection-map.md`.
