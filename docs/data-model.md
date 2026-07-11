# Data Model

This document describes the current backend data model: entities, relationships,
and storage decisions. It reflects `app/store/base.py` (schema) and
`app/models.py` (dataclasses).

The schema lives in a single SQLite database (`data/app.db`). Tables are
created and migrated in `StoreBase._init_schema` via `CREATE TABLE IF NOT
EXISTS` plus `_ensure_column` calls for additive column migrations — there is
no separate migration tool or versioned migration chain.

## Principles

- Stable internal integer/UUID IDs for all core entities; external provider
  IDs live in mapping tables, not on the core rows.
- Releases are modeled explicitly instead of derived from track text fields —
  one-track singles and compilations still get a release row.
- Metadata uncertainty is preserved (`release_type = 'unknown'`,
  `identity_confidence = 'derived'`) rather than guessed away.
- Raw playback events are the source of truth; preference tables store
  derived aggregate state that can be fully rebuilt from the event log
  (`recompute_user_preferences` in `app/store/mixes.py`).
- Embeddings are stored separately from core metadata (dedicated tables,
  `BLOB` vectors) and mirrored into on-disk HNSW index files.
- The `Store` class is assembled from domain mixins under `app/store/` (see
  `CLAUDE.md` for the module layout); this document groups tables the same
  way.

## Core Library Entities

### Tracks

`tracks` is the playable audio unit and the root of nearly every foreign key
in the schema.

Key columns: `id`, `path` (unique), `artist`/`title`/`album`/`genre`/`year`
(raw scanned tag fields, kept even though normalized `artists`/`releases`
exist), `duration`, `file_size`, `mtime`, `audio_hash`, `missing_at`,
`last_seen_at`, `added_at`, `created_at`, `updated_at`.

- Invalidation of derived data (embeddings, predictions, features) is driven
  by `path + mtime + file_size`; when any of these change on rescan, the row
  is treated as modified and downstream analysis is re-run.
- `missing_at` implements soft-delete for files that disappear from disk —
  rows are not deleted immediately (see `docs/analysis-pipeline.md`,
  "Lost Files"). Deleting a track row cascades to `embeddings`,
  `track_predictions`, `track_model_outputs`, `track_features`, `feedback`,
  `release_tracks`, `track_artists`, `playlist_items`,
  `generated_mix_items`, and more via `ON DELETE CASCADE`.
- No dedicated `disc_number`/`track_number`/`explicit` columns on `tracks`
  itself — those live on the `release_tracks` join row instead, since a track
  can appear on more than one release.

### Artists

`artists` normalizes artist identity instead of leaving it as a free-text
field on tracks.

Key columns: `id`, `name`, `sort_name`, `normalized_name` (unique, used for
dedup/matching), `image_url`, `bio`.

`artist_aliases` maps alternate spellings/credits to a canonical artist
(`artist_id`, `alias`, `normalized_alias`, `source`; unique on
`(normalized_alias, source)`).

### Releases

`releases` is the internal container concept (album/EP/single/compilation/
soundtrack/mix/unknown) — not just "album" — so one-track releases,
compilations, and featured-in pages all have a home.

Key columns: `id`, `title`, `normalized_title`, `release_type` (defaults to
`'unknown'`, never inferred/hidden), `release_date`, `release_year`,
`cover_art_id`, `track_count`, `duration`, `label`, `catalog_number`,
`identity_key` (unique — how scanned tracks are grouped into a release),
`identity_confidence` (defaults to `'derived'`), `added_at`.

### Release Tracks

`release_tracks` joins releases and tracks and carries per-release track
ordering: `release_id`, `track_id`, `disc_number`, `track_number`,
`position` (unique per release), `title_override`. Primary key is
`(release_id, track_id)`, so a track can appear on multiple releases (e.g.
also on a compilation) as separate rows.

### Track Artists / Release Artists

`track_artists` and `release_artists` are near-identical join tables giving
artists a role and position on a track or release: `role` (defaults
`'primary'`), `position`, `credit_text` (raw as-tagged string),
`confidence` (defaults `'derived'`). Primary key includes `role` and
`position`, so a track/release can credit the same artist in more than one
role (e.g. primary + remixer). These support featured-in pages, artist
pages, and distinguishing "primary release artist" from "artist appears on a
track only."

## External ID Mapping

Two mapping tables exist, at different points in the schema's history:

- `external_tracks` — the original, track-only mapping: `(provider,
  external_id)` primary key, `UNIQUE(provider, track_id)`, plus `raw_json`
  and `synced_at`. Used for Navidrome track IDs.
- `external_ids` — the generic successor described in the original plan:
  `provider`, `entity_type`, `entity_id`, `external_id`, `raw_json`,
  `synced_at`, primary key `(provider, entity_type, external_id)`, indexed
  on `(entity_type, entity_id)`. Supports mapping artists and releases (not
  just tracks) to external providers.

Both tables are present in the current schema; `external_tracks` has not
been migrated away.

## Embeddings

`embeddings` stores one recommendation vector per `(track_id, model_name)`:
`dim`, `vector` (`BLOB`, normalized `float32`), `vector_norm`, `created_at`.
Cascades on track delete. HNSW indexes (`data/index_*_hnsw.bin`) are built
from this table and use `space="cosine"`; UI/API similarity is reported as
`1 - distance`.

`release_embeddings` mirrors the same shape at release granularity
(`release_id, model_name` primary key) — a derived/aggregated vector per
release rather than a raw per-track vector.

There is no segment-level embedding table (`track_embedding_segments` from
the original plan) and no `artist_embeddings` table — neither is
implemented.

Related analysis tables (not embeddings, but adjacent): `track_predictions`
(top-N labels per model), `track_model_outputs` (full score vectors for
classification heads), `track_features` (audio feature extractor output).
See `docs/analysis-pipeline.md` for the analysis pipeline these feed.

## Collection Map / Atlas Projections

Two tables back the diagnostic 2D embedding map (see
`docs/collection-map.md`). They store *projected coordinates only* — real
similarity keeps using `embeddings` + HNSW, never these x/y.

`map_projections` — one row per projection build: `id` (uuid PK),
`model_name`, `name` (profile key), `method` (`umap` | `pca`), `metric`,
`params_json`, `source_embedding_count`, `projected_count`, `skipped_count`,
`embedding_dim`, `version`, `status` (`pending` | `running` | `ready` |
`failed`), `diagnostics_json`, `created_at`, `completed_at`. Multiple
projections per model are allowed.

`map_projection_points` — coordinates per track: `projection_id` (FK →
`map_projections`, cascade), `track_id` (FK → `tracks`, cascade), `x`, `y`
(`float32`), PK `(projection_id, track_id)`. Only non-missing tracks are
projected, so lost files never appear on the map. Staleness is derived (not
stored) by comparing `source_embedding_count` with the live
`count_embeddings(model_name)`.

## Release Aggregates

`release_aggregates` holds one precomputed row per release for
album-level recommendation and display: `release_id` (PK), `track_count`,
`available_track_count`, `duration`, `centroid_model`, `medoid_track_id`,
`embedding_status` (`'pending' | 'ready' | 'unavailable'`),
`top_region_matches_json`, `audio_summary_json`, `preference_summary_json`,
`updated_at`.

This is narrower than the originally planned split of `release_aggregates`
(audio-derived) + `release_user_stats` (play/like counters) — the current
schema keeps everything in one table, with user-facing counters folded into
`preference_summary_json` rather than broken out into columns. There is no
separate `release_user_stats` table.

Artist-level equivalents (`artist_aggregates`, `artist_user_stats`) from the
original plan are not implemented — artist pages compute their summaries
live rather than from a cached aggregate table.

## Playback Sessions, Queue, and Events

These three tables back Flow, autoplay, album/playlist playback, and manual
queues through one shared model, per the "generic events" decision in the
original plan.

`playback_sessions`: `id` (UUID), `source_type`/`source_id`/`source_label`
(what started the session — release, artist, playlist, search, flow,
autoplay, manual, generated_mix), `mode` (`linear | shuffle | radio | flow |
autoplay`), `status` (`active | paused | ended`), `current_track_id`,
`current_queue_item_id`, `autoplay_enabled`, `shuffle_enabled`,
`repeat_mode`, `settings_json`, `state_json`, timestamps.

`queue_items`: `id` (UUID), `session_id` (FK, cascade delete), `track_id`,
`position` (unique per session), `origin` (`source | manual | autoplay |
flow | generated_mix`), `source_type`/`source_id`, `status` (`queued |
playing | played | skipped | removed`), `locked`, `reason`, `score`,
`debug_json`.

`playback_events`: append-only raw event log — `id` (UUID), `session_id`,
`queue_item_id`, `track_id`, `release_id`, `artist_id`, `event_type` (e.g.
`track_started`, `progress`, `completed`, `skipped`, `liked`, `disliked`,
`replayed`, `queue_click`, `saved_to_playlist`, `autoplay_toggled`),
`position_seconds`, `duration_seconds`, `play_fraction`, `client_event_id`
(unique when present, dedupes client retries), `source` (defaults `'web'`),
`payload_json`. This table is the source of truth; preference tables below
are derived from it and can be rebuilt via `recompute_user_preferences`.

## User Preferences

Explicit and implicit signals are merged into one current-state row per
entity, computed from `playback_events`:

- `user_track_preferences` (`track_id` PK): `liked`, `disliked`,
  `play_count`, `completion_count`, `skip_count`, `early_skip_count`,
  `replay_count`, `last_played_at`, `last_completed_at`, `last_skipped_at`,
  `score`.
- `user_release_preferences` (`release_id` PK): same shape minus
  `disliked`/`early_skip_count`/`replay_count`.
- `user_artist_preferences` (`artist_id` PK): same shape, further reduced
  (no `completion_count` breakdown beyond what's listed).

Thresholds used to classify events live in `app/models.py`:
`MEANINGFUL_LISTEN_SECONDS/FRACTION`, `EARLY_SKIP_SECONDS/FRACTION`,
`LATE_SKIP_FRACTION`, `COMPLETION_FRACTION`.

## Generated Mixes

`generated_mixes`: `id` (string, often `mix-...`), `title`, `mix_type`
(`taste_region | supermix | forgotten | discovery | manual_seed | debug`),
`status` (`active | stale | saved | archived`), `cover_path` (generated
collage image), `anchor_json`, `settings_json`, `score_summary_json`,
`expires_at`, `saved_playlist_id` (set once a mix is saved as a playlist).

`generated_mix_items`: `mix_id`, `position`, `track_id`, `score`,
`score_breakdown_json`, `reason_json`. Primary key `(mix_id, position)`,
unique on `(mix_id, track_id)`.

Unsaved mixes are ephemeral/refreshable (`mark_generated_mixes_stale`);
saving one (`save_generated_mix_as_playlist`) copies its current track list
into a real `playlists`/`playlist_items` pair, links it back via
`saved_playlist_id`, and flips `status` to `'saved'`. Deleting that playlist
clears the link and flips the mix back to `'active'` so it becomes saveable
again.

## Playlists

`playlists`: `id`, `title`, `kind` (`manual | saved_mix`, validated against
`PLAYLIST_KINDS`), `description`, `cover_path` (generated 2x2 collage, same
mechanism as mix covers), `source_json`, timestamps. The `description` and
`cover_path` columns were added via `_ensure_column` migrations and are
already present in the live schema.

`playlist_items`: `playlist_id`, `position`, `track_id`, `created_at`.
Primary key `(playlist_id, position)`, unique on `(playlist_id, track_id)` —
duplicate tracks in a playlist are rejected at the schema level; the store
layer (`add_playlist_tracks` in `app/store/mixes.py`) treats re-adding an
existing track as a no-op rather than an error. Removing tracks repacks
positions to stay contiguous (`_repack_playlist_positions`).

Full CRUD (`create_playlist`, `update_playlist`, `delete_playlist`,
`list_playlists`, `add_playlist_tracks`, `remove_playlist_tracks`,
`reorder_playlist_tracks`, `set_playlist_cover_path`,
`playlist_track_ids`/`playlist_track_counts`) is implemented in
`app/store/mixes.py`. `reorder_playlist_tracks` replaces the whole order and
requires the new list to be an exact permutation of the current tracks
(otherwise `ValueError`, surfaced by the API as 409 `invalid_order`). Every
track mutation bumps `playlists.updated_at`, which drives the "Recent"
section in the add-to-playlist dialog and the shelf ordering. The UI layer
(dialogs, playlist page, dashboard shelf, drag-and-drop reorder) is
implemented in `ui/src` — see `docs/web-ui.md`.

## Flow Engine

Tables backing the Flow radio/region-based recommendation feature:

`flow_profiles`: `id`, `status` (`pending | building | ready | empty |
stale`), `model_key` (unique), `settings_json`, `last_built_at`.

`flow_regions`: `id`, `profile_id` (FK, cascade), `region_index`,
`centroid_ref`, `medoid_track_id`, `weight`, `seed_count`,
`candidate_count`, `summary_json`, `quality_json`. One profile has many
ordered regions.

`flow_region_tracks`: `region_id`, `track_id`, `role` (`seed |
representative | candidate | accepted | rejected`), `weight`, `distance`.
Primary key `(region_id, track_id, role)` — a track can hold more than one
role in the same region over time.

`flow_region_embeddings`: same shape as `embeddings`/`release_embeddings`
but keyed on `(region_id, model_name)` — a centroid-style vector per region.

`flow_generation_runs`: audit/debug log of individual Flow generation
invocations — `session_id`, `profile_id`, `region_id`, `settings_json`,
`candidate_count`, `selected_count`, `score_summary_json`. Not part of the
original plan document; added during Flow implementation.

## Other Supporting Tables

Not covered by the original plan but present and load-bearing in the current
schema:

- `feedback` — manual rating of a `(seed_track_id, result_track_id,
  model_name)` recommendation pair; predates the playback-event preference
  system.
- `instant_mix_requests` — logs each instant-mix generation request and its
  result set for debugging/analysis.
- `analysis_jobs`, `analysis_tasks`, `analysis_workers` — the embedding/
  analysis job queue (see `docs/analysis-pipeline.md`).
- `scan_state` — key/value store for library scan bookkeeping.
- `albums_for_you_cache` — one cached "albums for you" result set per model
  (`model_name` PK, `items_json`, `computed_at`). The only cache table that
  made it out of the originally planned generic `dashboard_shelf_cache` —
  other dashboard shelves are computed live.
- `sessions` — web auth sessions; stores only the SHA-256 of the opaque
  session token, never the token itself, and no password (Navidrome
  verifies credentials at login; see `docs/auth.md`).

## Known Gaps vs. the Original Design

Entities described in the original design that are not present in the
current schema:

- `track_embedding_segments`, `artist_embeddings` — no segment-level or
  artist-level embedding tables.
- `artist_aggregates`, `artist_user_stats`, `release_user_stats` — artist and
  release counters live inside `release_aggregates.preference_summary_json`
  or are computed live; there are no dedicated counter tables.
- `dashboard_shelf_cache` (generic) — only `albums_for_you_cache` exists;
  other shelves query live rather than through a generic cache table.
