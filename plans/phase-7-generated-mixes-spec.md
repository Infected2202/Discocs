# Phase 7 Spec: Generated Mixes

## Purpose

Generate finite personalized playlists from taste regions.

Generated mixes are not Flow.

Generated mixes are:

- finite;
- inspectable;
- usually around 100 tracks;
- refreshed on a cadence;
- shown as dashboard cards;
- saved as playlists only when the user chooses to save them.

Flow is the adaptive one-button listening stream. Generated mixes are fixed
collections.

## Dependencies

Requires:

- playback events/preferences;
- normalized artists/releases;
- track embeddings and HNSW index;
- dashboard shelf API;
- generated mix storage.

Useful:

- taste region builder;
- release/user preference summaries;
- score diagnostics.

Does not require:

- Flow session reranking;
- release recommendation aggregates;
- MAEST.

## Product Defaults

Defaults:

- mix length: 100 tracks;
- visible dashboard mixes: 8;
- update cadence: daily for active listening, weekly for stable mode;
- familiarity/discovery mix: 40-60% familiar/positive-nearby, rest discovery;
- max tracks per artist: 3-5 per 100-track mix;
- max tracks per release: 2-3 per 100-track mix;
- cross-mix duplicates: low by default.

Mixes should represent different regions/subregions:

- rock-ish region;
- psytrance region;
- drum and bass region;
- ambient region;
- etc.

If the user's taste is clustered tightly, choose spread-out anchors within that
space instead of producing six nearly identical mixes.

## Schema

### `generated_mixes`

Fields:

- `id TEXT PRIMARY KEY`
- `title TEXT NOT NULL`
- `mix_type TEXT NOT NULL`
- `status TEXT NOT NULL`
- `anchor_json TEXT`
- `settings_json TEXT`
- `score_summary_json TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `expires_at TEXT`
- `saved_playlist_id INTEGER`

`mix_type` values:

- `taste_region`;
- `supermix`;
- `forgotten`;
- `discovery`;
- `manual_seed`;
- `debug`.

`status` values:

- `active`;
- `stale`;
- `saved`;
- `archived`.

### `generated_mix_items`

Fields:

- `mix_id TEXT NOT NULL`
- `position INTEGER NOT NULL`
- `track_id INTEGER NOT NULL`
- `score REAL`
- `score_breakdown_json TEXT`
- `reason_json TEXT`
- `created_at TEXT NOT NULL`

Constraints:

- primary key `(mix_id, position)`;
- unique `(mix_id, track_id)`.

## Taste Regions For Mixes

Start with similarity-threshold clustering.

Reason:

- inspectable;
- easy to tune for a personal 45k-track library;
- no heavy clustering dependency;
- can be replaced later while keeping generated mix API.

Inputs:

- liked/starred tracks;
- completed/replayed tracks;
- long-term high-signal tracks;
- optionally recent accepted tracks.

Region output:

- region id;
- centroid;
- seed/member tracks;
- representative tracks;
- top artists/releases;
- coverage stats;
- candidate count;
- quality diagnostics.

Do not drop small regions by default. In a personal library, small regions may
represent real narrow tastes.

## Anchor Selection

Generated mixes should not all start from the same region.

Anchor selection:

- rank regions by signal strength;
- enforce distance/spread between dashboard mix anchors;
- include small high-confidence regions;
- rotate stale/old regions occasionally.

If taste is too concentrated:

- select sub-anchors within a dense region;
- use medoid/representative tracks to spread mixes;
- label mixes by representative artists/tracks, not fake genres.

## Mix Generation

Pipeline:

1. Select anchor region/subregion.
2. Build candidate pool from region centroid and representative seeds.
3. Blend familiar positive-nearby tracks and discovery tracks.
4. Rerank with diversity constraints.
5. Sequence tracks.
6. Store mix and item score breakdowns.

Candidate pool:

- default 500-2000 candidates per mix;
- use HNSW from region centroid and representative tracks;
- exclude unavailable/lost tracks;
- avoid already overused tracks across visible mixes.

Scoring:

```text
score =
  region_similarity
  + user_preference_score
  + discovery_bonus
  + freshness_bonus
  - recent_skip_penalty
  - artist_overuse_penalty
  - release_overuse_penalty
  - cross_mix_duplicate_penalty
```

Sequencing:

- avoid same artist/release back-to-back;
- allow coherent runs but prevent monotony;
- optional BPM/energy continuity later.

## API

### `GET /api/v1/mixes`

Purpose:

- list generated mixes for dashboard or mixes page.

Response:

```json
{
  "items": [],
  "generated_at": "2026-06-20T00:00:00Z"
}
```

### `GET /api/v1/mixes/{mix_id}`

Purpose:

- fetch mix detail.

Response:

- mix metadata;
- items;
- reasons;
- save/play actions.

### `POST /api/v1/mixes/generate`

Purpose:

- manually regenerate mixes.

Request:

```json
{
  "count": 6,
  "tracks_per_mix": 100,
  "force": false
}
```

### `POST /api/v1/mixes/{mix_id}/save`

Purpose:

- convert/copy a generated mix into a stable playlist.

Rule:

- unsaved generated mixes may refresh;
- saved mixes become stable playlists.

## Dashboard Integration

Add `Mixes For You` shelf:

- cards use generated/collage art later;
- subtitle lists representative artists or region summary;
- card opens mix detail;
- play starts playback session from generated mix.

## Settings

Settings:

- number of dashboard mixes;
- tracks per mix;
- update cadence;
- region spread;
- familiar/discovery ratio;
- duplicate strictness;
- max tracks per artist;
- max tracks per release;
- include/exclude small regions.

## Diagnostics

Store and expose in advanced/debug mode:

- anchor region;
- seed tracks;
- candidate counts;
- selected/excluded counts;
- duplicate penalties;
- score summary;
- region coverage;
- top reasons.

## Testing Plan

Unit tests:

- region clustering from tiny vectors;
- small regions preserved;
- anchor spread;
- cross-mix duplicate control;
- max artist/release caps;
- deterministic generation with fixed seed.

API tests:

- list mixes;
- fetch mix detail;
- regenerate;
- save mix as playlist;
- dashboard shelf returns visible mixes.

Quality tests:

- generated mix length;
- no duplicate tracks inside mix;
- score breakdown exists;
- reasons are non-empty.

## PR Slices

### Slice 1: Generated Mix Storage

Goal:

- add durable mix tables and store/query methods.

Includes:

- `generated_mixes`;
- `generated_mix_items`;
- save/copy to playlist placeholder;
- list/fetch methods.

### Slice 2: Taste Region Builder For Mixes

Goal:

- build inspectable taste regions from positive signals.

Includes:

- similarity-threshold clustering;
- region summaries;
- diagnostics;
- small-region preservation.

### Slice 3: Mix Generator

Goal:

- create finite 100-track mixes.

Includes:

- anchor selection;
- candidate pool;
- scoring;
- diversity caps;
- sequencing;
- score breakdown storage.

### Slice 4: Mix APIs And Dashboard Shelf

Goal:

- expose generated mixes to web/dashboard.

Includes:

- `GET /api/v1/mixes`;
- `GET /api/v1/mixes/{id}`;
- `POST /api/v1/mixes/generate`;
- `POST /api/v1/mixes/{id}/save`;
- Mixes For You shelf.

Recommended grouping:

- PR 1: Slice 1.
- PR 2: Slice 2.
- PR 3: Slice 3.
- PR 4: Slice 4.

## Open Decisions

No blocking decisions.

Defaults can be tuned:

- number of mixes;
- tracks per mix;
- update cadence;
- duplicate strictness;
- familiar/discovery ratio.

Initial defaults:

- dashboard mixes: `8`;
- tracks per mix: `100`;
- update cadence: daily when playback history changed, weekly otherwise;
- familiar/discovery: `50/50`;
- max tracks per artist: `4`;
- max tracks per release: `2`;
- cross-mix duplicate strictness: strict for visible dashboard mixes.
