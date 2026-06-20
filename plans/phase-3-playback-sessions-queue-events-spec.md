# Phase 3 Spec: Playback Sessions, Queue, Events

## Purpose

Make playback a first-class backend concept.

Phase 3 creates the foundation for:

- persistent bottom player;
- expanded player/queue;
- first-party skip tracking;
- meaningful listen/completion tracking;
- Flow session state later;
- Autoplay from any source later;
- Listen Again and Long Time No Listen dashboard shelves;
- album/release quality metrics;
- user preferences by track, release, and artist.

Navidrome cannot reliably provide the skip and queue behavior needed for Flow.
The future web player must capture these events itself.

## Current State

Current app has:

- track audio endpoint;
- cover endpoint;
- similar/mix endpoints;
- feedback endpoint;
- Navidrome starred integration;
- no first-party playback session;
- no backend queue model;
- no skip/progress/completion telemetry;
- no user preference aggregate tables.

## Product Rules

Important behavior:

- clicking a queue item is navigation, not negative feedback;
- explicit skip button is feedback;
- early skip is strong track-level negative;
- late skip is weak or neutral;
- completion is weak positive;
- replay is positive;
- like/save are strong positive;
- dislike is strong negative;
- one skip should not lower an entire taste region permanently;
- repeated skips can affect session direction later.

## Schema

### `playback_sessions`

Purpose:

- one active listening context: album play, search result play, artist mix,
  autoplay, Flow, manual queue, playlist, etc.

Fields:

- `id TEXT PRIMARY KEY`
- `source_type TEXT NOT NULL`
- `source_id INTEGER`
- `source_label TEXT`
- `mode TEXT NOT NULL`
- `status TEXT NOT NULL`
- `current_track_id INTEGER`
- `current_queue_item_id TEXT`
- `autoplay_enabled INTEGER NOT NULL DEFAULT 0`
- `shuffle_enabled INTEGER NOT NULL DEFAULT 0`
- `repeat_mode TEXT NOT NULL DEFAULT 'off'`
- `started_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `ended_at TEXT`
- `settings_json TEXT`
- `state_json TEXT`

`source_type` values:

- `release`
- `artist`
- `track`
- `playlist`
- `search`
- `flow`
- `autoplay`
- `manual`
- `generated_mix`

`mode` values:

- `linear`
- `shuffle`
- `radio`
- `flow`
- `autoplay`

`status` values:

- `active`
- `paused`
- `ended`

### `queue_items`

Purpose:

- ordered playback queue for a session.

Fields:

- `id TEXT PRIMARY KEY`
- `session_id TEXT NOT NULL`
- `track_id INTEGER NOT NULL`
- `position INTEGER NOT NULL`
- `origin TEXT NOT NULL`
- `source_type TEXT`
- `source_id INTEGER`
- `status TEXT NOT NULL DEFAULT 'queued'`
- `locked INTEGER NOT NULL DEFAULT 0`
- `reason TEXT`
- `score REAL`
- `debug_json TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

`origin` values:

- `source`
- `manual`
- `autoplay`
- `flow`
- `generated_mix`

`status` values:

- `queued`
- `playing`
- `played`
- `skipped`
- `removed`

Rules:

- source queue items are the original play context;
- generated/autoplay items are visually separated in the expanded player;
- manual queue items should not be overwritten by autoplay refill.

### `playback_events`

Purpose:

- immutable event log for user behavior and evaluation.

Fields:

- `id TEXT PRIMARY KEY`
- `session_id TEXT`
- `queue_item_id TEXT`
- `track_id INTEGER`
- `release_id INTEGER`
- `artist_id INTEGER`
- `event_type TEXT NOT NULL`
- `position_seconds REAL`
- `duration_seconds REAL`
- `play_fraction REAL`
- `created_at TEXT NOT NULL`
- `client_event_id TEXT`
- `source TEXT NOT NULL DEFAULT 'web'`
- `payload_json TEXT`

Indexes:

- `(session_id, created_at)`
- `(track_id, created_at)`
- `(event_type, created_at)`
- unique nullable `(client_event_id)` where supported or guarded in code.

Event types:

- `track_started`
- `progress`
- `play_threshold_reached`
- `completed`
- `skipped`
- `queue_click`
- `liked`
- `unliked`
- `disliked`
- `replayed`
- `removed_from_queue`
- `saved_to_playlist`
- `autoplay_toggled`
- `preference_changed`

### `user_track_preferences`

Purpose:

- current aggregate preference state per track.

Fields:

- `track_id INTEGER PRIMARY KEY`
- `liked INTEGER NOT NULL DEFAULT 0`
- `disliked INTEGER NOT NULL DEFAULT 0`
- `play_count INTEGER NOT NULL DEFAULT 0`
- `completion_count INTEGER NOT NULL DEFAULT 0`
- `skip_count INTEGER NOT NULL DEFAULT 0`
- `early_skip_count INTEGER NOT NULL DEFAULT 0`
- `replay_count INTEGER NOT NULL DEFAULT 0`
- `last_played_at TEXT`
- `last_completed_at TEXT`
- `last_skipped_at TEXT`
- `score REAL NOT NULL DEFAULT 0`
- `updated_at TEXT NOT NULL`

### `user_release_preferences`

Purpose:

- aggregate release behavior for album recommendations and dashboard shelves.

Fields:

- `release_id INTEGER PRIMARY KEY`
- `liked INTEGER NOT NULL DEFAULT 0`
- `play_count INTEGER NOT NULL DEFAULT 0`
- `completion_count INTEGER NOT NULL DEFAULT 0`
- `skip_count INTEGER NOT NULL DEFAULT 0`
- `last_played_at TEXT`
- `last_completed_at TEXT`
- `score REAL NOT NULL DEFAULT 0`
- `updated_at TEXT NOT NULL`

### `user_artist_preferences`

Purpose:

- aggregate artist behavior for future artist mixes and recommendations.

Fields:

- `artist_id INTEGER PRIMARY KEY`
- `liked INTEGER NOT NULL DEFAULT 0`
- `play_count INTEGER NOT NULL DEFAULT 0`
- `completion_count INTEGER NOT NULL DEFAULT 0`
- `skip_count INTEGER NOT NULL DEFAULT 0`
- `last_played_at TEXT`
- `score REAL NOT NULL DEFAULT 0`
- `updated_at TEXT NOT NULL`

## Aggregation Strategy

Decision:

- hybrid aggregation.

Meaning:

- always store raw `playback_events`;
- synchronously update small preference counters needed by UI and near-future
  recommendations;
- keep interpretation helpers pure enough that counters can be recomputed later.

Reason:

- raw events are source of truth;
- UI needs fresh like/play/skip state;
- Flow/autoplay later need session feedback quickly.

## Event Interpretation

### Meaningful Listen

Default threshold:

- count meaningful listen after either:
  - at least 30 seconds played; or
  - at least 50% of short tracks.

Store the actual `play_threshold_reached` event so threshold policy can be
changed later.

### Skip Strength

Default interpretation:

- early skip: before 30 seconds or before 25% of track;
- mid skip: before 70%;
- late skip: after 70%, weak/neutral.

Rules:

- early skip increments `early_skip_count`;
- late skip should not be treated as strong dislike;
- queue click does not count as skip unless the player also emits explicit
  `skipped`.

### Completion

Completion threshold:

- track reaches at least 90% or playback naturally ends.

Completion is weak positive unless paired with replay/like/save.

### Like/Dislike

Like/dislike should update preference immediately.

Rules:

- `liked` and `disliked` should not both be active;
- `unliked` clears liked state;
- explicit dislike clears liked state and marks disliked.

## API Endpoints

### `POST /api/v1/playback/sessions`

Purpose:

- create a playback session from a source.

Request:

```json
{
  "source_type": "release",
  "source_id": 12,
  "mode": "linear",
  "track_id": null,
  "autoplay_enabled": true,
  "shuffle_enabled": false,
  "settings": {}
}
```

Response:

```json
{
  "session": {},
  "queue": {
    "items": [],
    "current_index": 0
  }
}
```

Initial queue behavior:

- release source: ordered release tracks;
- artist source: top/local tracks when available, otherwise artist tracks;
- track source: single source track;
- search source: selected result plus search result context if provided;
- manual source: caller-provided track list if supported later.

### `GET /api/v1/playback/sessions/{session_id}`

Purpose:

- restore current player state.

Response includes:

- session core;
- current track;
- queue summary;
- settings/state.

### `PATCH /api/v1/playback/sessions/{session_id}`

Purpose:

- update session controls.

Request can update:

- status;
- current track/queue item;
- autoplay enabled;
- shuffle enabled;
- repeat mode;
- settings/state.

### `GET /api/v1/playback/sessions/{session_id}/queue`

Purpose:

- fetch queue for expanded player.

Response:

- current item;
- upcoming items;
- played items optionally;
- source/generated separation;
- reason/debug fields only if `include_debug=true`.

### `PATCH /api/v1/playback/sessions/{session_id}/queue`

Purpose:

- reorder/remove/add queue items.

Supported operations:

- add track;
- remove queue item;
- move queue item;
- jump to queue item;
- mark current item.

Jumping to a queue item records `queue_click`, not negative feedback.

### `POST /api/v1/playback/events`

Purpose:

- record first-party telemetry.

Request:

```json
{
  "session_id": "session-id",
  "queue_item_id": "queue-item-id",
  "track_id": 42,
  "event_type": "skipped",
  "position_seconds": 12.4,
  "duration_seconds": 245.2,
  "client_event_id": "uuid-from-client",
  "payload": {}
}
```

Response:

```json
{
  "accepted": true,
  "event_id": "event-id",
  "preference_delta": {}
}
```

Idempotency:

- client should send `client_event_id`;
- duplicate client event should not double-count aggregates.

## Queue Refill Boundary

Phase 3 should not implement real Flow/autoplay recommendation logic.

It should only make room for generated items:

- queue item `origin`;
- session `autoplay_enabled`;
- visible buffer setting;
- API operation to append generated items later.

Actual autoplay generation belongs to Phase 6.
Flow generation belongs to Phase 9.

## Settings

Initial player settings:

- progress event frequency;
- meaningful listen threshold seconds;
- meaningful listen threshold fraction;
- early skip threshold seconds;
- early skip threshold fraction;
- completion threshold fraction;
- default visible queue size;
- default autoplay enabled.

Settings can live in the future Settings page, but Phase 3 should define config
keys and defaults.

## Compatibility

- Existing `/tracks/{id}/audio` can remain the actual audio stream endpoint.
- Playback APIs store and expose state; they do not need to proxy audio.
- Existing `/feedback` endpoint can remain; later it may be bridged to
  playback events.
- Navidrome sync/starred integration remains separate.

## Testing Plan

Schema tests:

- sessions table initializes idempotently;
- queue items preserve order;
- events table stores raw events;
- preference tables update predictably.

API tests:

- create release session builds ordered queue;
- get session restores current item;
- patch session toggles autoplay/shuffle/repeat;
- queue click records navigation event without skip counters;
- skip event updates skip counters;
- early skip updates early skip counters;
- completion updates completion counters;
- like/dislike mutually exclude each other;
- duplicate client event does not double-count.

Behavior tests:

- metadata-only track changes do not affect playback event history;
- deleting/staling tracks does not corrupt old event rows;
- queue manual items are not overwritten by generated origins.

## PR Slices

Keep Phase 3 as backend-first. UI can call these APIs in Phase 4.

### Slice 1: Playback Schema And Store Methods

Goal:

- add durable playback tables and low-level store operations.

Includes:

- `playback_sessions`;
- `queue_items`;
- `playback_events`;
- user preference tables;
- create/get/update session methods;
- queue CRUD methods;
- raw event insert method.

Tests:

- schema idempotence;
- session create/get/update;
- queue order;
- raw event insert.

### Slice 2: Event Interpretation And Aggregates

Goal:

- convert raw events into current preference counters.

Includes:

- meaningful listen helper;
- skip strength helper;
- completion helper;
- like/dislike state helper;
- synchronous aggregate updates;
- idempotency by `client_event_id`.

Tests:

- early/mid/late skip interpretation;
- completion threshold;
- like/dislike mutual exclusion;
- duplicate event does not double-count.

### Slice 3: Playback Session And Queue API

Goal:

- expose session and queue endpoints.

Includes:

- `POST /api/v1/playback/sessions`;
- `GET /api/v1/playback/sessions/{id}`;
- `PATCH /api/v1/playback/sessions/{id}`;
- `GET /api/v1/playback/sessions/{id}/queue`;
- `PATCH /api/v1/playback/sessions/{id}/queue`;
- release-source queue builder;
- track-source queue builder.

Tests:

- create release session builds ordered queue;
- jump to queue item records navigation state;
- manual queue operations keep order.

### Slice 4: Playback Events API And Settings

Goal:

- let the future player record behavior.

Includes:

- `POST /api/v1/playback/events`;
- player thresholds/settings defaults;
- response with accepted event and preference delta;
- compatibility bridge notes for old `/feedback`.

Tests:

- started/progress/threshold/completed/skipped events accepted;
- aggregates update through API;
- invalid event types rejected.

Recommended grouping:

- PR 1: Slice 1.
- PR 2: Slice 2.
- PR 3: Slices 3-4.

Reason:

- schema/store and event semantics deserve separate review;
- APIs can land once the backend behavior is stable.

## Open Decisions

No blocking product decisions remain for Phase 3.

Defaults can be changed later through settings:

- meaningful listen threshold;
- skip thresholds;
- completion threshold;
- progress event frequency;
- visible queue size.
