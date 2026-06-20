# Phase 9 Spec: Flow Engine

## Purpose

Build the universal personal listening button.

Flow is the main daily entry point:

- open the app;
- press Flow;
- listen.

Flow is not:

- Instant Mix;
- current-track radio;
- Autoplay from a source;
- a generated finite mix;
- a genre shelf;
- a static playlist.

Flow is:

- one persistent personal stream;
- launched from one primary dashboard card/button;
- driven by long-term taste plus short-term session behavior;
- adapted by likes, skips, plays, replays, and explicit dislikes;
- session-aware;
- queue-backed but not a fixed playlist.

## Core Product Rules

Flow should play what the user loves listening to.

Important:

- do not average all likes into one global vector and call it done;
- user taste is multi-modal;
- regions are internal engine state, not separate Flow cards;
- normal user experience is one button;
- advanced/debug settings can expose regions and score breakdowns because the
  owner is a power user.

## Dependencies

Requires:

- Phase 1 normalized entities;
- Phase 2 API response shapes;
- Phase 3 playback sessions/events/preferences;
- Phase 4 player UI;
- Phase 6 queue refill mechanics;
- Phase 7 taste region builder or equivalent;
- HNSW index;
- track embeddings;
- score diagnostics.

Useful:

- release aggregates;
- audio features;
- segment embeddings later.

Does not require:

- MAEST;
- text-prompt models;
- genre tags.

## Available Signals

Strong positive:

- liked/starred tracks;
- explicit likes;
- replays;
- repeated completions.

Weak positive:

- completed play;
- meaningful listen;
- recent accepted tracks.

Neutral/context:

- queue click;
- late skip;
- source navigation.

Negative:

- explicit dislike;
- early skip;
- repeated skips in same direction;
- removed from queue.

Important:

- one skip does not necessarily mean the region is bad;
- it may mean the concrete track is bad or wrong for the moment;
- Flow should maintain track-level weights and session-level penalties before
  changing long-term region weights.

## Taste Model

Flow needs a multi-modal taste model.

### Taste Regions

A taste region is a neighborhood in embedding space.

It includes:

- centroid vector;
- member seed tracks;
- representative tracks;
- positive signal count;
- candidate coverage;
- top artists/releases;
- optional audio summary;
- region weight;
- quality stats.

Regions are not genres.

They can correspond to:

- techno/electro area;
- psytrance area;
- ambient area;
- rock/metal area;
- weird small niche clusters;
- compilation/label micro-styles.

Small regions should not be dropped by default. In a personal library, small
regions can be real.

### Region Builder

Start with similarity-threshold clustering.

Inputs:

- liked/starred tracks;
- high completion tracks;
- replayed tracks;
- recent accepted tracks with lower weight.

Algorithm:

1. Sort seeds by positive signal strength.
2. Pick seed as region anchor if not assigned.
3. Add other seeds within cosine similarity threshold.
4. Compute centroid and medoid/representatives.
5. Query HNSW around centroid/seeds to estimate candidate coverage.
6. Store diagnostics.

Why:

- inspectable;
- tuneable;
- no heavy clustering dependency;
- works for personal library scale.

Later:

- k-means;
- HDBSCAN;
- hybrid region/subregion modeling.

## Track-Level Weights

Track-level preference is important.

A skip should first affect:

- current track;
- near-duplicate candidates;
- immediate session direction.

Only repeated patterns should affect:

- active region weight;
- exploration ratio;
- region switching.

Track state:

- explicit liked/disliked;
- positive score;
- skip count;
- early skip count;
- recent session penalties;
- freshness/fatigue;
- replay/completion.

Session penalties should decay quickly.
Long-term penalties should update only from repeated evidence or explicit
dislike.

## Long-Term Taste + Short-Term Session

Long-term taste:

- regions and weights;
- persistent track preferences;
- historical completion/replay/like data;
- long-term fatigue/recently played windows.

Short-term session:

- tracks played in current run;
- accepted tracks;
- skipped tracks;
- active region;
- recent artist/release exposure;
- current energy/tempo if available;
- whether user is accepting current direction;
- current exploration level.

Flow should combine both:

```text
final_score =
  long_term_region_fit
  + short_term_session_fit
  + track_preference_score
  + novelty_or_familiarity_term
  + continuity_term
  - session_skip_penalty
  - fatigue_penalty
  - repetition_penalty
```

## Candidate Pool

Candidate pool is larger than visible queue.

Recommended defaults:

- candidate pool size: 500-2000;
- visible queue size: 5;
- generate/refill after each meaningful event;
- rerank frequently;
- avoid fixed long static playlist behavior.

Candidate sources:

- active region centroid;
- active region representative tracks;
- recently accepted tracks;
- high-signal long-term tracks;
- exploration from adjacent regions;
- fallback broad taste regions.

Filters:

- unavailable/lost tracks;
- already played in session;
- current track;
- explicit dislikes;
- recent hard skips;
- artist/release overexposure.

## Reranker

Factors:

- region fit;
- recent accepted track similarity;
- explicit track preference;
- novelty/familiarity balance;
- artist/release diversity;
- not too close to previous track;
- optional audio continuity;
- recent skip penalties;
- exploration.

Do not overuse:

- same artist;
- same release;
- same narrow subregion.

Settings:

- candidate pool size;
- visible queue size;
- exploration ratio;
- familiarity mix;
- max per artist;
- max per release;
- skip penalty strength;
- region switch sensitivity;
- long-term vs session weighting.

## Session Loop

Initial start:

1. Load Flow profile.
2. Choose active region based on:
   - region weight;
   - recent listening;
   - cooldown/fatigue;
   - optional user setting.
3. Build candidate pool.
4. Rerank.
5. Fill visible queue to 5.
6. Start playback session.

After events:

- completion: weak positive, continue direction;
- like/replay: strong positive, reinforce direction;
- early skip: track-level/session penalty;
- repeated skips: switch region or reduce active trait;
- explicit dislike: strong track-level negative;
- queue click: navigation only;
- long accepted run: allow slightly more discovery.

Queue refill:

- if 4 tracks remain and visible buffer target is 5, add one;
- if user jumps ahead, fill behind current item;
- generated Flow queue remains adaptive;
- do not treat click selection as negative feedback.

## Flow State

Use generic playback session plus Flow-specific state in `state_json`, and add
optional Flow tables if needed for profiles/regions.

### `flow_profiles`

Fields:

- `id TEXT PRIMARY KEY`
- `status TEXT NOT NULL`
- `model_key TEXT NOT NULL`
- `settings_json TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `last_built_at TEXT`

### `flow_regions`

Fields:

- `id TEXT PRIMARY KEY`
- `profile_id TEXT NOT NULL`
- `region_index INTEGER NOT NULL`
- `centroid_ref TEXT`
- `medoid_track_id INTEGER`
- `weight REAL NOT NULL`
- `seed_count INTEGER NOT NULL`
- `candidate_count INTEGER NOT NULL`
- `summary_json TEXT`
- `quality_json TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

### `flow_region_tracks`

Fields:

- `region_id TEXT NOT NULL`
- `track_id INTEGER NOT NULL`
- `role TEXT NOT NULL`
- `weight REAL`
- `distance REAL`

Roles:

- `seed`;
- `representative`;
- `candidate`;
- `accepted`;
- `rejected`.

### `flow_generation_runs`

Fields:

- `id TEXT PRIMARY KEY`
- `session_id TEXT`
- `profile_id TEXT`
- `region_id TEXT`
- `settings_json TEXT`
- `candidate_count INTEGER`
- `selected_count INTEGER`
- `score_summary_json TEXT`
- `created_at TEXT NOT NULL`

Purpose:

- debug and evaluate generation quality.

## API

### `POST /api/v1/flow/start`

Purpose:

- start the personal stream.

Request:

```json
{
  "settings": {},
  "include_debug": false
}
```

Response:

```json
{
  "session": {},
  "queue": {
    "items": [],
    "visible_buffer": 5
  },
  "flow": {
    "profile_id": "profile-id",
    "active_region_id": "region-id"
  }
}
```

### `POST /api/v1/flow/refill`

Purpose:

- refill/rerank Flow queue after events or when buffer is low.

Request:

```json
{
  "session_id": "session-id",
  "visible_buffer": 5,
  "include_debug": false
}
```

Response:

```json
{
  "added_items": [],
  "active_region": {},
  "generation_run_id": "run-id"
}
```

### `GET /api/v1/flow/profile`

Purpose:

- inspect current Flow profile.

Normal mode:

- summary only.

Debug mode:

- regions;
- candidates;
- quality stats;
- score settings.

### `POST /api/v1/flow/rebuild-profile`

Purpose:

- rebuild regions/profile after major preference/library changes.

Can be a job endpoint if expensive.

## Dashboard Integration

Flow card:

- large primary dashboard card/button;
- action starts `/api/v1/flow/start`;
- shows unavailable state if profile cannot be built;
- should not expose region choice in normal UI.

Advanced/debug:

- active region;
- score breakdown;
- candidate pool size;
- why this track;
- skip/acceptance stats.

## Quality Metrics

Offline:

- region count;
- region sizes;
- candidate coverage;
- average seed-to-centroid similarity;
- representative track coherence;
- artist/release diversity;
- candidate availability.

Online:

- skip rate;
- early skip rate;
- completion rate;
- like rate;
- replay rate;
- average time-to-skip;
- region switches after consecutive skips;
- session length;
- manual queue jumps.

Important:

- low skip rate is an evaluation goal, not a guaranteed property.

## Testing Plan

Unit tests:

- region builder keeps multiple regions;
- small regions preserved;
- centroid normalized;
- candidate pool excludes played/current/disliked tracks;
- track-level skip penalty does not immediately penalize entire region;
- repeated skips trigger region switch condition;
- visible buffer refills to 5.

API tests:

- start creates playback session and Flow queue;
- refill adds items without duplicating played items;
- queue click does not produce skip penalty;
- debug hidden by default;
- profile endpoint exposes region summary.

Quality tests:

- fixed seed generation deterministic;
- max artist/release caps;
- score breakdown sums expected factors;
- generation run stored for diagnostics.

## PR Slices

### Slice 1: Flow Profile And Region Storage

Goal:

- store Flow regions/profile and diagnostics.

Includes:

- `flow_profiles`;
- `flow_regions`;
- `flow_region_tracks`;
- vector references;
- profile rebuild command/job skeleton.

### Slice 2: Region Builder

Goal:

- build multi-modal taste regions from positive signals.

Includes:

- weighted seed selection;
- similarity-threshold clustering;
- centroid/medoid/representatives;
- candidate coverage diagnostics;
- small-region preservation.

### Slice 3: Candidate Pool And Reranker

Goal:

- select tracks for Flow without static playlist behavior.

Includes:

- region candidate generation;
- session context;
- track-level weights;
- skip/session penalties;
- diversity/fatigue caps;
- score breakdown.

### Slice 4: Flow Session APIs

Goal:

- start/refill Flow through playback queue.

Includes:

- `POST /api/v1/flow/start`;
- `POST /api/v1/flow/refill`;
- generic playback session integration;
- visible queue buffer default 5.

### Slice 5: Feedback Loop And Region Switching

Goal:

- adapt Flow during a session.

Includes:

- event hooks;
- acceptance/rejection session state;
- repeated skip detection;
- region switch/lower-weight behavior;
- long accepted run exploration increase.

### Slice 6: Dashboard And Diagnostics

Goal:

- expose Flow card and advanced debug.

Includes:

- Flow dashboard action;
- `GET /api/v1/flow/profile`;
- generation run diagnostics;
- quality metrics summary.

Recommended grouping:

- PR 1: Slice 1.
- PR 2: Slice 2.
- PR 3: Slice 3.
- PR 4: Slice 4.
- PR 5: Slices 5-6.

## Open Decisions

No blocking decisions.

Defaults selected from recommender/session-recommendation practice and prior
product discussion. All must remain configurable in Settings -> Flow because
the real optimum will come from this library and user behavior.

Initial defaults:

- region similarity threshold: start at cosine similarity `0.72` for seed
  clustering, with debug sweep support from `0.65` to `0.85`;
- candidate pool size: `1000`;
- visible queue size: `5`;
- exploration ratio: `0.10`;
- familiarity/discovery target: `70/30` inside Flow, because Flow is primarily
  "what I love", not discovery-first;
- long-term vs session weighting: `70/30` at session start, drifting toward
  `55/45` after several accepted tracks in the same direction;
- early skip threshold: before `30s` or before `25%` of track duration;
- completion threshold: `90%`;
- meaningful listen threshold: `30s` or `50%` for short tracks;
- region switch threshold: `2` early skips in the same active region within the
  last `5` Flow plays, unless the skipped tracks were already explicit dislikes;
- max consecutive tracks per artist: `2`;
- max tracks per release in visible queue: `1`, unless the user is clearly
  playing a release context outside Flow.

Recommended defaults:

- visible queue size: 5;
- candidate pool: start around 500-1000 for Flow;
- region exposure: debug/advanced settings only;
- one-button normal UX.
