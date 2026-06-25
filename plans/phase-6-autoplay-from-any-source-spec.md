# Phase 6 Spec: Autoplay From Any Source

## Purpose

Continue any playback source with source-aware generated tracks.

Autoplay is not Flow.

Autoplay's product contract is:

- the user starts a concrete source;
- the source finishes or runs low;
- the player continues with tracks that feel like a continuation of that source;
- personal taste can lightly bias the result, but should not override the
  source.

Sources:

- release;
- track;
- artist;
- playlist;
- search result;
- manual queue;
- generated mix;
- Flow session later.

## Dependencies

Requires:

- Phase 1 normalized artists/releases;
- Phase 2 entity APIs;
- Phase 3 playback sessions, queue, events;
- HNSW recommendation primitives;
- track embeddings;
- user preferences/events.

Useful later:

- release aggregates from Phase 8;
- taste regions from Phase 7/9.

Does not require:

- Flow engine;
- generated mixes;
- MAEST;
- segment embeddings.

## Product Behavior

Autoplay should feel like "continue this".

Default:

- strong source similarity;
- weak personal bias;
- no hard switch into general personal radio;
- respect recent skips in this session;
- keep visible queue buffer filled.

Important:

- queue click is navigation, not negative feedback;
- skip/dislike should affect current session;
- one skip should not permanently punish a track/source;
- likes reinforce the current source/session direction.

## Track Action Menu Integration

The web player should expose the same per-track overflow menu everywhere a
track can be acted on:

- current track title in the persistent player;
- Up Next / expanded queue rows;
- playlist or generated-mix track rows;
- release/album track rows.

The first shared menu item is `Instant Mix`. It starts a track-scoped instant
mix from the selected track using the saved instant-mix settings, records the
request in instant-mix history, creates a playback session/queue from the seed
and returned tracks, and starts that queue in the player. It must not route the
listener into the instant-mix history/debug page. The menu component should be
shared by these surfaces so future track actions are added once instead of
reimplemented per list.

## Session Model

Autoplay uses Phase 3 `playback_sessions`.

Session fields:

- `source_type`;
- `source_id`;
- `mode`;
- `autoplay_enabled`;
- `settings_json`;
- `state_json`.

Autoplay-generated queue items use:

- `queue_items.origin = "autoplay"`;
- `queue_items.source_type`;
- `queue_items.source_id`;
- `queue_items.reason`;
- `queue_items.score`;
- `queue_items.debug_json`.

## Source Context Builder

Build a source context from the active session.

### Track Source

Inputs:

- seed track embedding;
- seed track artist/release;
- current session accepted/skipped tracks.

Candidate generation:

- nearest neighbors from seed track;
- nearest neighbors from recently accepted tracks;
- exclude seed/current track;
- apply artist/release caps.

### Release Source

Inputs:

- release tracks;
- release artists;
- release centroid if available;
- currently played/accepted/skipped tracks.

Candidate generation:

- neighbors of multiple representative release tracks;
- release centroid when Phase 8 aggregate exists;
- related discography can be a weak source;
- exclude tracks already in the source release unless repeat behavior allows.

### Artist Source

Inputs:

- artist tracks;
- artist releases;
- artist accepted tracks in current session.

Candidate generation:

- artist's own tracks first if source queue has not finished;
- neighbors of representative artist tracks;
- similar artists later when reliable.

### Playlist/Search/Manual Queue Source

Inputs:

- original source queue items;
- accepted tracks;
- skipped tracks;
- source-level text/query context if available.

Candidate generation:

- neighbors of accepted tracks;
- centroid/medoid of accepted source tracks;
- avoid overfitting to one early clicked track unless accepted.

## Candidate Pool

Recommended default:

- request 100-500 nearest candidates before reranking;
- start with 200 per active source context;
- deduplicate by track id;
- filter unavailable/lost tracks;
- exclude already played in session unless repeat allows.

Candidate pool can be larger than visible queue. The visible queue should stay
small and adaptive.

## Scoring

Default score:

```text
score =
  source_similarity
  + accepted_session_similarity
  + light_personal_preference
  + freshness_bonus
  + continuity_bonus
  - already_played_penalty
  - recent_skip_penalty
  - artist_fatigue_penalty
  - release_fatigue_penalty
  - unavailable_penalty
```

Default weighting:

- source similarity: strong;
- accepted session similarity: medium;
- personal preference: weak;
- exploration: low but non-zero.

Reason:

- Autoplay continues a source.
- Flow is the personal stream.

## Queue Refill

Default visible buffer:

- 5 upcoming tracks.

Refill behavior:

- when upcoming autoplay items drop below threshold, generate more;
- if user clicks a later queue item, fill behind it to maintain buffer;
- manual queue items are not overwritten;
- source items remain visually separate from autoplay items.

Do not generate a fixed long playlist and blindly play through it.

Autoplay should rerank after events:

- like;
- skip;
- completion;
- replay;
- explicit dislike;
- removed from queue.

## Preference Chips

Expanded player can expose simple preference chips.

Initial chips:

- All;
- Familiar;
- Recommended;
- Party;
- Energy;
- Training;

Phase 6 should store chip state as scoring settings but does not need perfect
semantic quality yet.

Rules:

- chips modify reranking weights;
- chips should be optional and reversible;
- chip labels can be adjusted later.

## API

### `POST /api/v1/autoplay/refill`

Purpose:

- refill a playback session queue with autoplay candidates.

Request:

```json
{
  "session_id": "session-id",
  "visible_buffer": 5,
  "candidate_count": 200,
  "settings": {
    "source_weight": 0.75,
    "personal_weight": 0.15,
    "exploration_ratio": 0.1
  }
}
```

Response:

```json
{
  "session_id": "session-id",
  "added_items": [],
  "candidate_count": 200,
  "debug": null
}
```

### `PATCH /api/v1/playback/sessions/{id}`

Phase 3 endpoint should also support:

- `autoplay_enabled`;
- autoplay settings in `settings_json`.

### Debug

When `include_debug=true`:

- score breakdown;
- source vectors used;
- excluded counts;
- artist/release cap effects;
- skip penalties.

## Settings

Autoplay settings:

- enabled by default;
- visible buffer size default 5;
- candidate pool size default 200;
- source-vs-personal weighting;
- exploration ratio;
- max per artist;
- max per release;
- recent skip penalty window;
- preference chip defaults.

## Testing Plan

Unit tests:

- source context builder for track/release/artist/search/manual;
- candidate deduplication;
- source/current track exclusion;
- artist/release caps;
- scoring weight behavior;
- skip penalty affects session but does not permanently blacklist.

API tests:

- refill adds autoplay queue items;
- manual queue items are preserved;
- buffer refills to configured size;
- debug response is hidden by default;
- unavailable tracks are filtered.

Behavior tests:

- strong source/weak personal default;
- liked track reinforces current direction;
- explicit skip reduces similar immediate candidates;
- queue click does not trigger skip penalty.

## PR Slices

### Slice 1: Source Context And Candidate Generation

Goal:

- generate plausible candidate pools from each source type.

Includes:

- source context builder;
- track/release/artist/session seed selection;
- HNSW candidate retrieval;
- dedupe/filter/caps.

Tests:

- source-specific context;
- candidate pool size;
- filtering unavailable/current/played tracks.

### Slice 2: Autoplay Scoring And Settings

Goal:

- score candidates with source-first behavior.

Includes:

- scoring weights;
- personal bias;
- fatigue penalties;
- skip/like session influence;
- settings defaults.

Tests:

- source weight dominates by default;
- skip penalty is session-local;
- artist/release caps work.

### Slice 3: Queue Refill API

Goal:

- connect generation to playback queue.

Includes:

- `POST /api/v1/autoplay/refill`;
- append autoplay queue items;
- maintain visible buffer;
- score reasons/debug.

Tests:

- refill creates queue items;
- manual items preserved;
- debug hidden unless requested.

### Slice 4: Player Integration Hooks

Goal:

- make expanded player controls meaningful.

Includes:

- autoplay enable/disable persistence;
- preference chip setting storage;
- event-triggered refill hooks;
- UI-facing queue item reason fields.

Tests:

- toggling autoplay changes session state;
- chip settings affect scoring input;
- event hooks can request refill without duplicating queue.

Recommended grouping:

- PR 1: Slice 1.
- PR 2: Slice 2.
- PR 3: Slices 3-4.

## Open Decisions

No blocking decisions.

Defaults can be tuned later:

- candidate pool size;
- visible buffer;
- source/personal weight;
- exploration ratio;
- chip labels.

Initial defaults:

- visible buffer: `5`;
- candidate pool: `200` for normal autoplay refill;
- source-vs-personal weighting: `80/20`;
- exploration ratio: `0.08`;
- max consecutive tracks per artist: `2`;
- max tracks per release in generated autoplay tail: `1`;
- early skip penalty window: current session only by default;
- queue click: never negative feedback.
