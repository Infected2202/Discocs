# Implementation Roadmap

## Purpose

Turn the product/design notes into an implementation-ready sequence.

This document is not a reduced MVP plan. It is a dependency-aware roadmap for
building the final product without painting the backend into a corner.

The future web UI may be built on another stack, so backend work should expose
stable API surfaces rather than coupling new behavior to the current HTML UI.

Execution map:

- [Master Implementation Order](master-implementation-order.md)

## Current Codebase Audit

Current strengths:

- FastAPI app already exists.
- SQLite store with durable schema initialization.
- Track-centric catalog exists in `tracks`.
- Embeddings are stored as normalized `float32` vectors.
- HNSW index build/load/query exists.
- Similar track and mix recommendation primitives exist.
- Navidrome catalog sync and external track mapping exist.
- Navidrome starred/liked integration exists.
- Audio features and Discogs head predictions exist.
- Worker/job infrastructure exists for analysis.
- Track audio and cover endpoints exist.
- Basic settings/jobs/status endpoints exist.
- Existing tests cover API/store/recommender/scanner areas.

Current limitations for the target product:

- Data model is still track-centric.
- Artists are text fields, not normalized entities.
- Albums/releases are text fields, not normalized entities.
- There is no release type model: album, EP, single, compilation, featured-in.
- There is no first-party playback/session/event model.
- There is no generic queue model.
- There are no dashboard shelf APIs.
- There are no generated mix entities.
- There are no release/album aggregates.
- Search is track-oriented and not entity-oriented.
- Embeddings are global/pooled only, not segment-level.
- Heavy vector storage is still in the main DB.
- Current UI is a prototype, not the final web shell.

## High-Level Build Order

Recommended order:

1. Normalize library entities.
2. Add API-first entity endpoints.
3. Build playback session, queue, and event model.
4. Build future web shell/player/search/entity pages against APIs.
5. Add dashboard simple shelves.
6. Add autoplay from any source.
7. Add generated mixes.
8. Add release/album recommendation aggregates.
9. Add Flow engine.
10. Add segment embeddings and MAEST experiments.

Reason:

- Entity normalization unlocks artist/album pages and better search.
- Playback events unlock listen-again, long-time-no-listen, autoplay quality,
  Flow quality, and album/mix evaluation.
- Dashboard shelves need stable APIs and entities.
- Flow should be built after events and queue behavior exist.
- Segment embeddings/MAEST are important, but they are expensive and should plug
  into already stable analysis/storage APIs.

## Phase 1: Library Entity Normalization

Goal:

Create explicit artists and releases without breaking the current track-centric
app.

Detailed spec:

- [Phase 1 Spec: Library Entity Normalization](phase-1-library-normalization-spec.md)

Add tables:

- `artists`
- `artist_aliases`
- `releases`
- `release_tracks`
- `track_artists`
- `release_artists`
- generic `external_ids` or staged successor to `external_tracks`

Implementation approach:

- Keep existing `tracks.artist`, `tracks.album`, `tracks.title` columns for
  compatibility.
- Add normalized tables alongside them.
- Backfill normalized artists/releases from existing track metadata and
  Navidrome raw JSON.
- Use stable internal IDs.
- Preserve uncertainty: unknown release type should remain unknown.

Obvious solution:

- Use `release` internally, not only `album`.

Decision: release identity strategy.

Options:

1. Simple identity: normalized `(album_artist, album_title, year)`.
2. Path-aware identity: include folder/path grouping as a signal.
3. Provider-aware identity: prefer Navidrome album IDs if available, fallback to
   metadata/path.

Chosen default:

- Use provider-aware + path-aware fallback.

Why:

- Metadata alone can merge unrelated releases with same title.
- Folder/path helps local libraries.
- Navidrome raw metadata may already contain better album IDs or paths.

Decision: artist splitting.

Options:

1. Keep full artist string as one artist.
2. Split common separators into multiple artists.
3. Store full credit plus parsed artists with confidence.

Chosen default:

- Store full credit and parsed artists with confidence.

Why:

- Electronic music credits often include collaborations/remixers.
- Aggressive splitting can create bad artist pages.
- Full credit preserves display correctness.

Deliverables:

- schema additions;
- backfill job/command;
- store methods for artist/release lookup;
- tests for normalization edge cases;
- compatibility tests proving existing track APIs still work.

## Phase 2: Entity APIs

Goal:

Expose stable backend APIs for future web clients.

Detailed spec:

- [Phase 2 Spec: Entity APIs](phase-2-entity-apis-spec.md)

Add endpoints:

- `GET /api/v1/search`
- `GET /api/v1/artists/{id}`
- `GET /api/v1/artists/{id}/discography`
- `GET /api/v1/artists/{id}/top-tracks`
- `GET /api/v1/artists/{id}/similar`
- `GET /api/v1/releases/{id}`
- `GET /api/v1/releases/{id}/tracks`
- `GET /api/v1/releases/{id}/related-discography`
- `GET /api/v1/releases/{id}/recommendations`

Implementation approach:

- Add `/api/v1/...` routes without removing existing prototype routes.
- Response shapes should be UI-friendly:
  - entity core data;
  - display fields;
  - cover/artwork URLs;
  - actions available;
  - optional debug/reason fields in advanced mode.

Search:

- Start with text search over normalized entities and tracks.
- Return grouped results: top result, artists, tracks, releases, playlists later.
- Keep semantic/text embedding search separate or optional initially.

Decision: API route namespace.

Options:

1. Replace existing routes gradually.
2. Add `/api/v1`.
3. Add `/api` now and version later.

Chosen default:

- Add `/api/v1`.

Why:

- Future web on another stack benefits from stable versioned contracts.
- Existing prototype routes can remain untouched.

Deliverables:

- Pydantic response models;
- API tests for artist/release/search;
- OpenAPI validation through FastAPI;
- docs/spec examples in the plan or generated OpenAPI.

## Phase 3: Playback Sessions, Queue, Events

Goal:

Make the player a first-class backend concept.

Detailed spec:

- [Phase 3 Spec: Playback Sessions, Queue, Events](phase-3-playback-sessions-queue-events-spec.md)

Add tables:

- `playback_sessions`
- `queue_items`
- `playback_events`
- `user_track_preferences`
- `user_release_preferences`
- `user_artist_preferences`

Add endpoints:

- `POST /api/v1/playback/sessions`
- `GET /api/v1/playback/sessions/{id}`
- `PATCH /api/v1/playback/sessions/{id}`
- `POST /api/v1/playback/events`
- `GET /api/v1/playback/sessions/{id}/queue`
- `PATCH /api/v1/playback/sessions/{id}/queue`

Event model:

- `track_started`
- `progress`
- `play_threshold_reached`
- `completed`
- `skipped`
- `queue_click`
- `liked`
- `disliked`
- `replayed`
- `removed_from_queue`
- `saved_to_playlist`
- `autoplay_toggled`
- `preference_changed`

Important behavior:

- Queue click is navigation, not negative feedback.
- Early skip is strong track-level negative.
- Late skip is weak/neutral.
- Completion is weak positive.
- Like/save/replay are strong positive.
- Region-level penalties require patterns, not a single skip.

Decision: event aggregation timing.

Options:

1. Update aggregate preference tables synchronously on every event.
2. Store raw events only, aggregate periodically.
3. Hybrid: store raw events and update small counters synchronously.

Chosen default:

- Hybrid.

Why:

- UI needs fresh state.
- Raw events remain source of truth.
- Counters can be recomputed if interpretation changes.

Deliverables:

- event schema and validation;
- queue behavior tests;
- preference aggregate update tests;
- event interpretation helpers.

## Phase 4: Web Shell And Core Pages

Goal:

Build the future web UI shell against API-first backend contracts.

Detailed spec:

- [Phase 4 Spec: Web Shell And Core Pages](phase-4-web-shell-core-pages-spec.md)

Pages/components:

- App shell.
- Left sidebar.
- Dashboard skeleton.
- Search page.
- Album/release page.
- Artist page.
- Bottom player.
- Expanded player/queue.
- Settings page with tabs.

Implementation notes:

- Current prototype UI can coexist, but final web should be structured as real
  pages/components.
- If another stack will be used later, backend/API work remains useful.
- Frontend should not depend on internal SQLite shapes.

Decision: frontend stack timing.

Options:

1. Keep enhancing inline FastAPI HTML for now.
2. Build a new frontend stack now.
3. First stabilize `/api/v1`, then build new frontend.

Chosen default:

- Stabilize `/api/v1` first, then build the new frontend.

Why:

- Prevents UI decisions from forcing backend shortcuts.
- Current prototype remains available for operations.

Deliverables:

- API-backed search page;
- API-backed release page;
- API-backed artist page;
- persistent player using playback session APIs;
- visual spec conformance pass.

## Phase 5: Dashboard Simple Shelves

Goal:

Add dashboard shelves that do not require full recommender complexity.

Detailed spec:

- [Phase 5 Spec: Dashboard Simple Shelves](phase-5-dashboard-simple-shelves-spec.md)

Shelves:

- Recently Added.
- Listen Again.
- Long Time No Listen.

Dependencies:

- normalized releases/artists for display;
- playback events for Listen Again and Long Time No Listen;
- scan/add timestamps for Recently Added.

Implementation:

- `GET /api/v1/dashboard`
- `GET /api/v1/dashboard/shelves/{key}`
- simple shelves query live;
- expensive shelves can cache later.

Decision: added timestamp source.

Options:

1. Use track `created_at`.
2. Use scan state/import timestamp.
3. Add explicit `added_at` to tracks/releases.

Chosen default:

- Add explicit `added_at`.

Why:

- `created_at` is database-row creation time, not always library-add time.
- Release-level "recently added" needs aggregate added time.

Deliverables:

- dashboard API shape;
- Recently Added shelf;
- Listen Again shelf;
- Long Time No Listen shelf;
- shelf tests.

## Phase 6: Autoplay From Any Source

Goal:

Continue albums, tracks, playlists, search results, manual queue, and Flow with
source-aware generated tracks.

Detailed spec:

- [Phase 6 Spec: Autoplay From Any Source](phase-6-autoplay-from-any-source-spec.md)

Dependencies:

- playback sessions and queue;
- entity APIs;
- HNSW recommendation primitives;
- release aggregates for album autoplay later.

Implementation:

- session source context builder;
- candidate generation from source vectors;
- source-first scoring;
- light personal taste bias;
- queue refill behavior;
- preference chips as scoring settings.

Decision: source-vs-personal default weight.

Options:

1. Strong source, weak personal.
2. Balanced.
3. Strong personal.

Chosen default:

- Strong source, weak personal. Initial default is `80/20`.

Why:

- Autoplay's contract is "continue this source".
- Flow is the personal stream.

Deliverables:

- autoplay session generation;
- queue refill to visible buffer;
- scoring breakdown;
- skip/like event influence in session;
- tests for source-type behavior.

## Phase 7: Generated Mixes

Goal:

Generate finite 100-track personalized playlists from taste regions/subregions.

Detailed spec:

- [Phase 7 Spec: Generated Mixes](phase-7-generated-mixes-spec.md)

Dependencies:

- user preferences/events;
- normalized entities for display;
- HNSW indexes;
- generated mix tables.

Implementation:

- taste region builder;
- anchor selection with diversity;
- 100-track mix generator;
- cross-mix deduplication;
- save generated mix as playlist.

Decision: region algorithm.

Options:

1. Similarity-threshold clustering.
2. K-means.
3. HDBSCAN or another density method.

Chosen default:

- Start with similarity-threshold clustering, keep storage generic.

Why:

- Inspectable and easy to tune for a personal library.
- Can be replaced later without changing generated mix API.

Deliverables:

- `generated_mixes` and items;
- mix generation API;
- dashboard Mixes For You shelf;
- quality diagnostics.

## Phase 8: Release/Album Recommendations

Goal:

Support Albums For You and album recommendations on release pages.

Detailed spec:

- [Phase 8 Spec: Release/Album Recommendations](phase-8-release-album-recommendations-spec.md)

Dependencies:

- normalized releases;
- release aggregates;
- playback/user stats;
- track embeddings.

Implementation:

- release centroid and medoid;
- best-track evidence;
- region coverage;
- user release stats;
- evidence-based album score.

Decision: release centroid storage.

Options:

1. Store centroid in main DB.
2. Store centroid in embeddings DB with other vectors.
3. Compute on demand.

Chosen default:

- Store in embeddings DB or aggregate vector storage, keep summary in main DB.

Why:

- Vectors can grow heavy.
- Main DB should remain metadata/user state focused.

Deliverables:

- release aggregate job;
- release recommendation API;
- Albums For You shelf;
- release page recommended albums section.

## Phase 9: Flow Engine

Goal:

Build the universal personal listening button.

Detailed spec:

- [Phase 9 Spec: Flow Engine](phase-9-flow-engine-spec.md)

Dependencies:

- playback sessions/events;
- taste regions;
- generated candidate pools;
- queue refill behavior;
- preferences and skip interpretation.

Implementation:

- Flow profile;
- taste regions;
- candidate pool;
- reranker;
- session state;
- visible queue buffer;
- feedback loop.

Decision: Flow region exposure.

Options:

1. Completely hidden.
2. Debug-only.
3. User-visible settings/tuning.

Chosen default:

- Debug-only plus advanced settings.

Why:

- Main Flow UX should be one button.
- The owner needs power-user controls and diagnostics.

Deliverables:

- `POST /api/v1/flow/start`;
- `POST /api/v1/flow/refill`;
- Flow card/dashboard integration;
- event-driven reranking;
- quality metrics.

## Phase 10: Segment Embeddings And MAEST

Goal:

Improve representation quality and add heavy model experiments.

Detailed spec:

- [Phase 10 Spec: Segment Embeddings And MAEST](phase-10-segment-embeddings-maest-spec.md)

Dependencies:

- stable analysis job framework;
- separate vector storage;
- HNSW/index metadata per model/strategy.

Implementation:

- segment embedding extraction for current Discogs-EffNet models;
- `track_embedding_segments`;
- index metadata for global vs segment strategies;
- MAEST model integration later in separate DB.

Decision: segment strategy.

Options:

1. Fixed 30-second segments.
2. Intro/middle/outro only.
3. Hybrid fixed + summary segments.

Chosen default:

- Hybrid eventually, fixed 30-second segments first.

Why:

- Fixed segments are easiest to implement and evaluate.
- Intro/middle/outro summaries can be derived later.

Deliverables:

- parameterized `analyze-embeddings` job;
- segment storage;
- optional segment reranking;
- MAEST experiment plan.

## Cross-Cutting Work

Testing:

- schema migration/backfill tests;
- API response tests;
- playback event interpretation tests;
- recommender scoring tests;
- UI contract tests where possible.

Settings:

- central runtime settings model;
- settings API;
- separate sections for Flow, Autoplay, Mixes, Albums, Player, Storage.

Observability:

- log generation IDs;
- store score breakdowns;
- expose advanced debug mode;
- export diagnostics.

Compatibility:

- keep existing operational endpoints during transition;
- avoid breaking current scan/analyze/index workflow;
- do not require Essentia for unrelated tests.

## Immediate Next Step

Start implementation from the master order:

1. Phase 1 Slice 1: normalized artist/release schema.
2. Phase 1 Slice 2: store upsert/lookup helpers.
3. Phase 1 Slice 3: repeatable backfill from existing track metadata.

Do this before dashboard, Flow, autoplay, generated mixes, or MAEST work. Stable
artist/release IDs are the base layer for all later pages and recommendations.
