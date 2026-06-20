# Master Implementation Order

## Purpose

This is the master build sequence for turning the recommendation/dashboard
specs into implementation work.

Use this file as the execution map. Use the phase specs for details.

Primary rule:

- build stable data and API contracts first;
- keep the current prototype working while new APIs/pages are added;
- avoid coupling backend decisions to the current temporary UI;
- keep expensive analysis/model work behind selectable jobs and model metadata.

## Source Specs

- [Product and recommendation notes](recommendation-cards-flow.md)
- [Web UI visual spec](web-ui-visual-spec.md)
- [Data model overview](data-model-overview.md)
- [Implementation roadmap](implementation-roadmap.md)
- [Phase 1: Library Entity Normalization](phase-1-library-normalization-spec.md)
- [Phase 2: Entity APIs](phase-2-entity-apis-spec.md)
- [Phase 3: Playback Sessions, Queue, Events](phase-3-playback-sessions-queue-events-spec.md)
- [Phase 4: Web Shell And Core Pages](phase-4-web-shell-core-pages-spec.md)
- [Phase 5: Dashboard Simple Shelves](phase-5-dashboard-simple-shelves-spec.md)
- [Phase 6: Autoplay From Any Source](phase-6-autoplay-from-any-source-spec.md)
- [Phase 7: Generated Mixes](phase-7-generated-mixes-spec.md)
- [Phase 8: Release/Album Recommendations](phase-8-release-album-recommendations-spec.md)
- [Phase 9: Flow Engine](phase-9-flow-engine-spec.md)
- [Phase 10: Segment Embeddings And MAEST](phase-10-segment-embeddings-maest-spec.md)

## Final Decisions To Preserve

- Public API namespace is `/api/v1`.
- Existing prototype routes may remain during transition.
- Backend is API-first because the future web may use a different stack.
- Use `release` internally, even when UI says album.
- Do not infer release type in the product. If provider/tags do not give a type,
  show the release under generic `Releases`.
- Album/release cards show all available release artists.
- Artist pages split releases into Albums, EPs, Singles, Compilations, Featured
  In, and fallback Releases.
- Playback uses generic `playback_sessions`, `queue_items`, and
  `playback_events`.
- Flow is a source type on top of generic playback sessions, not a separate
  player/event system.
- Queue click is navigation, not negative feedback.
- One skip first affects track/session state, not the whole taste region.
- Generated mixes default to `8` dashboard mixes, each finite, around `100`
  tracks.
- Autoplay continues the current source. Default source/personal scoring is
  `80/20`.
- Flow visible queue target is `5`.
- Flow candidate pool starts around `1000`.
- Flow region similarity threshold starts at cosine similarity `0.72` and should
  be tunable.
- Segment embeddings are added for existing Discogs-EffNet models.
- MAEST is an additional model family, stored separately and selectable through
  analysis/model settings.

## Critical Path

The critical path is:

1. Normalize library entities.
2. Expose entity APIs.
3. Add playback/session/event capture.
4. Build player and core web pages on those APIs.
5. Add simple dashboard shelves.
6. Add source-aware autoplay.
7. Add generated mixes.
8. Add release recommendation aggregates.
9. Add Flow.
10. Add segment embeddings and MAEST.

Flow depends on playback events, queue behavior, regions, settings, and
diagnostics. Do not implement Flow before Phase 3 and the reusable parts of
Phases 6-7 exist.

MAEST and segment embeddings are important, but they should plug into stable
analysis/vector storage contracts. Do not let them block entity APIs, playback,
dashboard, autoplay, or Flow scaffolding.

## Milestone Map

### Milestone 1: Stable Library Graph

Goal:

- turn the local library from track-only data into track, artist, and release
  entities.

Includes:

- Phase 1 complete;
- Phase 2 read APIs for artists, releases, search;
- compatibility with existing scan/analyze/index flows.

Exit criteria:

- current track APIs still work;
- artists/releases can be backfilled repeatedly;
- search can return grouped top result, artists, tracks, releases;
- release pages have enough data for the future UI.

### Milestone 2: Playback Foundation

Goal:

- make player state, queue, and listening feedback durable.

Includes:

- Phase 3 complete;
- first settings split if needed by event/player behavior;
- basic API tests for queue/session/event semantics.

Exit criteria:

- player can create/resume session;
- queue can be replaced/refilled/reordered;
- events are captured as raw history;
- preference counters can be updated and recomputed;
- queue click does not count as skip.

### Milestone 3: Web Shell And Basic Dashboard

Goal:

- create the API-backed web shape: dashboard, search, release page, artist page,
  bottom player, expanded player.

Includes:

- Phase 4 complete enough for core navigation;
- Phase 5 simple shelves.

Exit criteria:

- dashboard shows Flow entry placeholder and simple shelves;
- search opens reactive results page;
- release and artist pages match the visual spec structure;
- player stays persistent across pages.

### Milestone 4: Source Continuation

Goal:

- any playable source can continue when it ends.

Includes:

- Phase 6 Autoplay From Any Source.

Exit criteria:

- autoplay can continue album, track, search result, manual queue, and later
  playlist/Flow sources;
- visible queue refills to target;
- scoring is source-first with light personal bias;
- advanced settings expose candidate pool, visible buffer, source/personal
  weight, and chip/tuning behavior.

### Milestone 5: Personalized Collections

Goal:

- add finite personalized generated mixes and release recommendations.

Includes:

- Phase 7 generated mixes;
- Phase 8 release/album recommendation aggregates.

Exit criteria:

- dashboard can show 8 generated mixes;
- each generated mix can materialize as a 100-track playlist;
- albums/release recommendations use release-level evidence instead of naive
  average-only scoring;
- score explanations are available in debug mode.

### Milestone 6: Flow

Goal:

- build the main universal personal listening entry point.

Includes:

- Phase 9 Flow Engine.

Exit criteria:

- dashboard has one attractive Flow entry;
- Flow starts a playback session and fills 5 visible tracks;
- candidate pool/reranker uses long-term taste plus short-term session behavior;
- likes/skips/completions update track/session state;
- repeated early skips can switch or downweight current region;
- debug settings show regions, candidates, score components, and quality metrics.

### Milestone 7: Better Audio Representation

Goal:

- improve recommendation representation and support model comparison.

Includes:

- Phase 10 segment embeddings;
- MAEST experiment;
- selectable analysis/model storage.

Exit criteria:

- current Discogs-EffNet models can store segment embeddings;
- global and segment strategies can be indexed/evaluated separately;
- MAEST can be run as an additional model family without replacing current
  models;
- model selection remains visible in analysis/settings UI.

## Implementation Order By Slice

### 1. Library Normalization

1. Add schema tables for artists, aliases, releases, release tracks, track
   artists, release artists, and external IDs.
2. Add store methods for normalized entity upsert and lookup.
3. Backfill entities from existing tracks.
4. Add Navidrome/provider-aware release identity where raw metadata allows it.
5. Add path-aware fallback identity for local releases.
6. Preserve full artist credit and parsed artists with confidence.
7. Add tests for repeated backfill, artist splitting, release identity, unknown
   type, and compatibility with existing track APIs.

Do not:

- remove legacy track string fields yet;
- infer release type from track count in the product.

### 2. Entity APIs

1. Add `/api/v1` router scaffolding.
2. Add shared response models for artwork, playable entity, release summary,
   artist summary, and track row.
3. Add `/api/v1/search` with grouped results.
4. Add artist detail, discography, top tracks, similar artists.
5. Add release detail, tracks, related discography, recommendations placeholder.
6. Add tests for empty/missing entities and stable response shapes.

Do not:

- make frontend depend on SQLite internal columns;
- mix semantic search into text search until the base contract is stable.

### 3. Playback Sessions And Events

1. Add generic playback session, queue item, playback event, and preference
   aggregate tables.
2. Add session create/read/update endpoints.
3. Add queue read/update endpoints scoped by session.
4. Add event ingest endpoint.
5. Add event interpretation helpers.
6. Add synchronous small-counter updates while preserving raw event source of
   truth.
7. Add tests for queue click, early skip, late skip, completion, like, dislike,
   replay, and recomputation.

Do not:

- create Flow-only session/event tables;
- treat clicking a queued track as negative feedback.

### 4. Core Web Shell

1. Build app shell with left navigation and persistent bottom player.
2. Add dashboard route/skeleton.
3. Add reactive search results page.
4. Add release page matching the visual spec.
5. Add artist page matching the visual spec.
6. Add expanded player: large cover left, queue/autoplay panel right.
7. Split settings into tabs.
8. Add loading, empty, and error states.

Do not:

- create track detail pages;
- build playlist management yet;
- add genre/mood shelves before reliable tags/features exist.

### 5. Dashboard Simple Shelves

1. Add explicit `added_at` for tracks/releases.
2. Add dashboard API envelope.
3. Add Recently Added shelf.
4. Add Listen Again shelf from positive listening history.
5. Add Long Time No Listen shelf from old positive history minus recent plays.
6. Add dashboard shelf tests.

Do not:

- block simple shelves on Flow or release recommendations.

### 6. Autoplay From Any Source

1. Add source context builder for track, release, search result, manual queue,
   generated mix, and later playlist/Flow.
2. Add candidate generation around source vectors.
3. Add source-first reranking with default `80/20` source/personal weight.
4. Add visible queue refill behavior with target `5`.
5. Add preference chips/settings.
6. Add debug score breakdown.
7. Add tests per source type.

Do not:

- make autoplay behave like Flow;
- over-personalize source continuation.

### 7. Generated Mixes

1. Add generated mix schema and item schema.
2. Build taste-region seed selection from strong positive history.
3. Build initial similarity-threshold region builder.
4. Generate 8 dashboard mixes.
5. Generate around 100 tracks per mix.
6. Add cross-mix diversity and deduplication.
7. Add save-as-playlist/export hook.
8. Add diagnostics for region coverage and mix overlap.

Do not:

- require genre tags;
- drop small regions by default in a personal library.

### 8. Release/Album Recommendations

1. Add release aggregate job.
2. Store release centroid/medoid vectors outside the main metadata DB or in
   aggregate vector storage.
3. Store lightweight release aggregate summaries in the main DB.
4. Combine centroid fit, best-track evidence, region coverage, novelty,
   familiarity, and release health.
5. Add Albums For You shelf.
6. Add recommended albums section on release pages.
7. Add debug explanation fields.

Do not:

- recommend albums by naive average embedding only;
- treat recent play as the main relevance signal for a source-specific album
  recommendation.

### 9. Flow Engine

1. Add Flow profile and region storage.
2. Build region builder from positive signals and current embedding model.
3. Preserve small regions.
4. Add candidate pool and reranker.
5. Add Flow start/refill endpoints on generic playback sessions.
6. Fill visible queue to 5.
7. Add feedback loop for track-level/session penalties.
8. Add repeated-skip region switch/downweight behavior.
9. Add advanced settings and diagnostics.
10. Add quality metrics.

Default starting values:

- region similarity threshold: `0.72`, tuneable sweep `0.65-0.85`;
- candidate pool: `1000`;
- visible queue: `5`;
- exploration ratio: `0.10`;
- familiar/discovery ratio: `70/30`;
- long-term/session weighting: `70/30`, drifting toward `55/45`;
- early skip: before `30s` or before `25%`;
- completion: `90%`;
- region switch: `2` early skips in same active region within last `5` Flow
  plays.

Do not:

- expose regions as separate main dashboard cards;
- penalize an entire region from one skip;
- use one averaged liked-track embedding as the Flow engine.

### 10. Segment Embeddings And MAEST

1. Generalize analysis jobs by model and output strategy.
2. Add segment embedding storage for existing Discogs-EffNet models.
3. Store per-segment vectors separately from main metadata/user state.
4. Add index metadata for global vs segment strategies.
5. Add segment-aware retrieval/reranking experiment.
6. Add MAEST as an additional selectable model family.
7. Add GPU worker path for real extraction.
8. Add tests that do not require Essentia/model files unless marked integration.

Do not:

- replace existing Discogs models with MAEST;
- store large segment/MAEST vectors in the main application DB by default.

## Dependency Rules

- Phase 2 depends on Phase 1 entity IDs.
- Phase 3 can start after Phase 1 schema exists, but richer display needs Phase
  2 response models.
- Phase 4 should use `/api/v1` contracts, not direct DB calls.
- Phase 5 depends on Phase 3 for listening-history shelves.
- Phase 6 depends on Phase 3 queue behavior.
- Phase 7 depends on positive user signals from Phase 3 and embeddings/indexes.
- Phase 8 depends on Phase 1 releases and existing embeddings.
- Phase 9 depends on Phases 3, 6, and reusable region/candidate logic from Phase
  7.
- Phase 10 can begin after analysis/vector storage abstractions are ready, but
  should not block Phases 1-9.

## Test Gates

Every implementation slice should include at least one test gate:

- schema/backfill tests for data model changes;
- API response tests for new `/api/v1` endpoints;
- queue/event interpretation tests for playback behavior;
- recommender scoring tests for autoplay, mixes, album recommendations, and
  Flow;
- non-model unit tests for analysis plumbing;
- integration smoke tests only for real model extraction.

Before advancing between milestones:

1. Run unit tests.
2. Run compile/syntax check.
3. Verify current scan/analyze/index workflow still starts.
4. Verify current prototype UI still loads if it was not intentionally replaced.
5. Verify `/api/v1` contracts are documented or covered by response-model tests.

## First Development PR

Start with:

1. Phase 1 Slice 1: add normalized schema tables.
2. Phase 1 Slice 2: add store upsert/lookup helpers.
3. Phase 1 Slice 3: add repeatable backfill from existing track metadata.

This is the right first PR because every later product feature needs stable
artist/release IDs, and it can be implemented without touching recommender
quality, player state, or expensive model extraction.
