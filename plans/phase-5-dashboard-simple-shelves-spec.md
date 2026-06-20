# Phase 5 Spec: Dashboard Simple Shelves

## Purpose

Add useful dashboard shelves that do not require full recommender complexity.

Phase 5 should make the home page feel alive and useful using reliable,
explainable data:

- Recently Added;
- Listen Again;
- Long Time No Listen.

This phase should not implement:

- Flow engine;
- generated mixes;
- Albums For You scoring;
- region-based recommendation shelves;
- genre/energy/label shelves.

Those belong to later phases.

## Current State

Earlier phases provide:

- normalized artists/releases;
- `/api/v1` entity response shapes;
- playback sessions/events;
- user preference aggregates;
- app shell/dashboard skeleton.

Known product direction:

- dashboard starts with a large Flow entry card;
- shelves use horizontal cover-first cards;
- shelves share visual structure even when logic differs;
- Recently Added is operational and newest-first, not personalized;
- Listen Again and Long Time No Listen depend on first-party playback events.

## Dashboard API

### `GET /api/v1/dashboard`

Purpose:

- fetch dashboard layout and initial shelf payloads.

Query parameters:

- `limit`: optional default items per shelf;
- `include_debug`: default false.

Response:

```json
{
  "hero": {
    "type": "flow",
    "title": "Flow",
    "subtitle": "Start your personal stream",
    "available": false,
    "action": {
      "type": "start_flow",
      "enabled": false,
      "endpoint": null
    }
  },
  "shelves": [
    {
      "key": "recently_added",
      "title": "Recently Added",
      "subtitle": "New in your collection",
      "items": [],
      "total": 0,
      "next_offset": null,
      "available": true,
      "reason": null
    }
  ],
  "settings": {
    "visible_shelves": [],
    "items_per_shelf": 12
  }
}
```

Notes:

- Flow hero can be present but disabled until Phase 9;
- unavailable shelves should be omitted or returned with `available: false`
  depending on UI needs;
- default should omit empty unavailable shelves from the visible dashboard.

### `GET /api/v1/dashboard/shelves/{key}`

Purpose:

- fetch/paginate one shelf.

Path keys:

- `recently_added`;
- `listen_again`;
- `long_time_no_listen`.

Query parameters:

- `limit`;
- `offset`;
- `include_debug`;
- optional shelf-specific settings later.

Response:

```json
{
  "key": "listen_again",
  "title": "Listen Again",
  "items": [],
  "total": 0,
  "limit": 12,
  "offset": 0,
  "next_offset": null,
  "available": true
}
```

## Shared Shelf Item Shape

Use one UI-friendly item shape across shelves:

```json
{
  "id": "release:12",
  "entity_type": "release",
  "entity_id": 12,
  "title": "Release title",
  "subtitle": "Artist name",
  "artwork": {
    "url": "/api/v1/releases/12/cover",
    "source": "local",
    "placeholder": false
  },
  "action": {
    "type": "open",
    "target": "/releases/12"
  },
  "play_action": {
    "type": "play",
    "source_type": "release",
    "source_id": 12
  },
  "badges": [],
  "reason": "Added 2 days ago",
  "debug": null
}
```

Supported `entity_type` values:

- `track`;
- `release`;
- `artist`;
- `playlist`;
- `generated_mix`.

Phase 5 should mostly use `release` and `track`.

Card rules:

- release card opens release page;
- track card can start playback or open containing release depending on context;
- card visuals come from the visual spec `MediaCard`.

## Added Timestamp

Decision:

- add explicit `added_at` to tracks and releases.

Reason:

- `created_at` is database row creation time, not always library-add time;
- rescans/backfills can distort `created_at`;
- release-level Recently Added needs aggregate added time.

Track behavior:

- local scan sets `tracks.added_at` when a track first appears;
- Navidrome sync sets it when a provider track first maps/imports;
- existing rows get backfilled from the best available import/created time;
- metadata-only updates should not change `added_at`.

Release behavior:

- `releases.added_at` should be derived from member tracks;
- default: max member `track.added_at`, so a release resurfaces when new tracks
  are added;
- also store `first_added_at` later if needed for diagnostics.

## Shelf: Recently Added

Purpose:

- show newly added library items, newest first.

This is operational, not personalized.

Default content:

- releases, newest first by `release.added_at`;
- include track-level items only if release grouping is not available or user
  chooses track mode later.

Sorting:

- `release.added_at DESC`;
- tie-breaker by release id desc.

Filters:

- exclude lost/missing files if the whole release is unavailable;
- include items even if embeddings are missing;
- optional badges:
  - missing embedding;
  - missing cover;
  - lost file;
  - unanalyzed.

Reason text examples:

- `Added today`;
- `Added 3 days ago`;
- `New in collection`.

API:

- included in `GET /api/v1/dashboard`;
- paginated through `GET /api/v1/dashboard/shelves/recently_added`.

## Shelf: Listen Again

Purpose:

- quick return to things the user already likes or recently accepted.

Signals:

- liked tracks/releases;
- completed plays;
- replayed tracks;
- high play count;
- recent sessions with good completion.

Default content:

- tracks first, because the signal is usually track-level;
- releases can appear when release-level preference exists later.

Scoring:

```text
score =
  liked_bonus
  + completion_count_weight
  + replay_count_weight
  + recent_completion_bonus
  - recent_skip_penalty
  - too_recently_played_penalty
```

Simple first implementation:

- include liked tracks;
- include tracks with completions;
- sort by recent positive activity and preference score;
- exclude tracks with recent early skip unless liked.

Freshness window:

- default recent window: 30 days;
- default too-recent suppression: 12-24 hours after last play.

Reason text examples:

- `You liked this`;
- `Played recently`;
- `You replayed this`;
- `Completed 4 times`.

## Shelf: Long Time No Listen

Purpose:

- resurface good music that has fallen out of rotation.

Signals:

- liked/starred tracks;
- historical completions;
- historical high play count;
- not played recently.

Default content:

- tracks or releases with positive history;
- prefer releases if multiple positive tracks belong to the same release;
- keep enough variety by artist/release.

Scoring:

```text
score =
  historical_positive_score
  + time_since_last_play_bonus
  + liked_bonus
  - recent_skip_penalty
  - missing_file_penalty
```

Default eligibility:

- has positive signal;
- not played in last 30 days;
- stronger boost after 90+ days.

Reason text examples:

- `Not played in 4 months`;
- `You liked this before`;
- `Long time since last listen`.

## Settings

Dashboard settings should eventually live in Settings -> Dashboard.

Phase 5 settings:

- shelf enable/disable;
- shelf ordering;
- items per shelf;
- Recently Added mode: releases first, tracks later;
- Listen Again recent window;
- Long Time No Listen minimum age;
- show debug reasons.

Recommended defaults:

- visible shelves:
  - Recently Added;
  - Listen Again;
  - Long Time No Listen;
- items per shelf: 12;
- Listen Again window: 30 days;
- Long Time No Listen minimum age: 30 days;
- stronger long-time boost after 90 days.

## Caching

Initial implementation:

- query shelves live.

Reason:

- personal library size is manageable;
- queries are simple;
- correctness and inspectability matter more than premature caching.

Future:

- add `dashboard_shelf_cache` only if live queries are slow.

If cache is added later:

- cache key includes shelf key, settings hash, and user/profile id if users
  exist later;
- cache should be refreshable manually.

## API Dependencies

Requires:

- Phase 1 normalized releases/artists;
- Phase 2 summary response shapes;
- Phase 3 playback events/preferences;
- explicit `added_at` for Recently Added.

Does not require:

- Flow;
- generated mixes;
- album recommendation aggregates;
- segment embeddings;
- MAEST.

## Testing Plan

Schema tests:

- `added_at` is set on first track insert;
- metadata-only update does not change `added_at`;
- release `added_at` is derived from member tracks;
- backfill assigns stable added timestamps to existing rows.

API tests:

- dashboard endpoint returns ordered shelves;
- shelf endpoint paginates;
- Recently Added returns newest releases first;
- Listen Again returns liked/completed tracks;
- Long Time No Listen excludes recent plays;
- empty shelves return clean empty state;
- disabled shelf is omitted or marked unavailable by settings.

Behavior tests:

- recent early skip lowers Listen Again eligibility;
- liked track can survive one weak/late skip;
- missing/lost files are not promoted as playable cards;
- reasons match the shelf logic.

## PR Slices

Keep Phase 5 small. These shelves are intentionally simple.

### Slice 1: Added Timestamps

Goal:

- establish reliable library-added metadata.

Includes:

- `tracks.added_at`;
- `releases.added_at`;
- backfill for existing rows;
- scan/Navidrome sync preservation rules;
- release aggregate update from member tracks.

Tests:

- first insert sets `added_at`;
- update preserves `added_at`;
- release `added_at` updates when new member track appears.

### Slice 2: Dashboard API Foundation

Goal:

- define dashboard and shelf response contracts.

Includes:

- `GET /api/v1/dashboard`;
- `GET /api/v1/dashboard/shelves/{key}`;
- shared shelf item mapper;
- basic settings defaults;
- empty/unavailable shelf behavior.

Tests:

- dashboard response shape stable;
- unknown shelf key returns not found/invalid request;
- pagination fields stable.

### Slice 3: Recently Added Shelf

Goal:

- ship the simplest useful shelf.

Includes:

- release-based newest-first query;
- optional operational badges;
- reason text;
- pagination.

Tests:

- newest releases first;
- missing embeddings do not exclude item;
- fully unavailable/lost release is not promoted as playable.

### Slice 4: Listen Again And Long Time No Listen

Goal:

- add playback-event-based shelves.

Includes:

- Listen Again query from preferences/events;
- Long Time No Listen query from preferences/events;
- simple scoring/reasons;
- recent skip suppression;
- artist/release diversity caps if needed.

Tests:

- completed/liked tracks appear in Listen Again;
- recently played items are suppressed when configured;
- old positive tracks appear in Long Time No Listen;
- recent plays exclude Long Time No Listen;
- early skips reduce eligibility.

Recommended grouping:

- PR 1: Slice 1.
- PR 2: Slices 2-3.
- PR 3: Slice 4.

Reason:

- `added_at` affects data model and should land alone;
- dashboard API plus Recently Added gives a useful first visible result;
- playback-history shelves depend on Phase 3 behavior and deserve separate
  review.

## Open Decisions

No blocking decisions for Phase 5.

Defaults can be tuned later:

- shelf order;
- shelf item count;
- Listen Again window;
- Long Time No Listen minimum age;
- release-vs-track card mix.
