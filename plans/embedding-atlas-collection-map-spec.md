# Embedding Atlas / Collection Map Spec

Standalone task. Not part of the phase-N sequence.

## Purpose

Add an interactive collection map that visualizes the music library in a
reduced 2D embedding space. It is an **exploratory and diagnostic surface** —
it helps inspect the collection, understand similarity regions, debug
recommendations, inspect generated mixes and taste regions, and explore
relationships between tracks, artists, releases, labels, audio features and
user preferences.

The map must **not** replace or feed the existing recommendation logic, the
HNSW index, or the original high-dimensional similarity calculations.
Recommendations, generated mixes and nearest-neighbor logic keep using
original embeddings / HNSW / cosine similarity. The 2D map is an approximation
used for viewing only.

## Current Direction

Decisions already made (do not re-litigate):

- **v1 scope = MVP** matching the acceptance criteria below. Everything wider
  in the "Later phases" section is deferred to follow-up tasks, not the first
  commit.
- **UI lives in the old admin** (`app/ui.html`, `:8711/admin`) as a new
  `<section>` — this is a diagnostic tool, it belongs in the admin per the
  project UI rule. Not the new `ui/src` React app.
- **Rendering: WebGL point cloud via a CDN `<script>`** (deck.gl / regl /
  pixi — pick one at implementation time; deck.gl `ScatterplotLayer` is the
  default candidate because it gives pan/zoom/picking for tens of thousands of
  points out of the box). No SVG-per-point. No new frontend build step; the
  admin stays a no-build page and loads the lib from CDN.
- **Dependencies: do not skimp.** Add `umap-learn` and `scikit-learn`. Heavy
  projection computation runs in the **worker** container (same pattern as
  `analyze`), not inline in the API request. Projection is offline/background,
  never computed on page load.

## Execution Stages (v1)

Each stage is self-contained, tested, and builds on the previous. Strict order:
2 needs 1, 3 needs 2, 4 needs 3.

1. **Data layer** — `map_projections` + `map_projection_points` in `base.py`,
   `MapProjection` dataclass, `MapAtlasStoreMixin` (create/get/list/update/
   delete, points round-trip, count), registered in `Store`. No heavy deps.
   Tests: create/get/list, update, points round-trip + ordering, replace,
   cascade delete, bad-shape rejection. **← done**
2. **Projection service + CLI** — `app/projection.py` with a mockable
   projector (lazy UMAP/PCA), profile registry, `[map]` extra deps, store
   helpers (`find_map_projection`, `load_projection_source` which excludes
   lost tracks), `recs build-map` / `recs list-maps` (with stale flag).
   Missing/lost-track + empty-source + force-rebuild handling. Tests use a fake
   projector (no UMAP import). The background **build job + `POST` endpoint**
   move to stage 3 (wired where they are reachable/testable via TestClient),
   to avoid landing untested plumbing here. **← done**
3. **Map API** — `app/api/map.py` router + background projection build job:
   `POST` build / list / points (typed arrays) / dimensions / track /
   neighbors (HNSW) / mixes / regions. Happy + error paths. Worker image gets
   the `[map]` extra installed here (where the job actually runs).
4. **Admin UI** — `<section id="atlas">` in `app/ui.html`, WebGL via CDN,
   pan/zoom/hover/click, color-by, filters, region/mix overlays, inspection,
   2D-vs-embedding-space UX distinction.
5. **Docs** — `docs/collection-map.md` + `data-model.md`/`architecture.md`.

## Dependencies (what this builds on — already in the codebase)

Requires (all present, verified):

- `embeddings` table + `Store.load_embeddings(model_name) -> (ids, matrix)` —
  the projection input (`app/store/embeddings.py`).
- HNSW index + recommender for the **real** neighbor lookups
  (`app/index.py`, `app/recommender.py`, `space="cosine"`,
  similarity = `1 - distance`, includes a staleness check).
- Taste regions: `flow_profiles` / `flow_regions` / `flow_region_tracks` /
  `flow_region_embeddings` (`app/store/flow.py`) — centroid vectors, medoid
  track, seed/member roles, `summary_json`, `quality_json`.
- Generated mixes: `generated_mixes` / `generated_mix_items`
  (`app/store/mixes.py`).
- Discogs head predictions: `track_predictions` / `track_model_outputs`.
- Audio features (BPM/key/scale/loudness/dynamics): `track_features`.
- Preference/playback signals: `user_*_preferences`, `playback_events`.
- Background progress-job mechanism: `create_progress_job` /
  `update_progress_job` (`app/store/jobs.py`) — reused for the projection job.
- CLI is Click-based (`app/cli.py`, `@cli.command`), `recs` entrypoint.
- Schema is created via `CREATE TABLE IF NOT EXISTS` in `app/store/base.py`
  plus an `ALTER TABLE ADD COLUMN` migration helper.
- Admin UI is `app/ui.html`, sections as `<section id="...">`.

Does not require:

- Any change to ranking / HNSW / cosine similarity.
- PostgreSQL/Redis/FAISS/GPU/task queues (explicitly out per CLAUDE.md).
- The new React UI.

New dependencies to add:

- `umap-learn` — primary projection.
- `scikit-learn` — PCA baseline + projection diagnostics (trustworthiness).
- CDN WebGL scatter lib (deck.gl or equivalent) — frontend, via `<script>`.

Add `umap-learn`/`scikit-learn` as an optional extra (e.g. `[map]`) with
**lazy imports** inside the job/service body (project convention for heavy
deps). The projector must be **mockable** so unit tests run without UMAP:
inject a projector interface; the default implementation lazy-imports UMAP,
tests pass a fake that returns deterministic coordinates.

## Data Model (new tables, in `app/store/base.py`)

Two new tables. A projection is a persisted snapshot of 2D coordinates for one
model; multiple projections per model are allowed.

`map_projections` — one row per projection run:

- `id` (uuid text, pk)
- `model_name` — source embedding model (e.g. `discogs_multi`)
- `name` — human name / profile key (e.g. `umap_local`, `umap_global`, `pca`)
- `method` — `umap` | `pca` (later: `tsne`)
- `params_json` — projection parameters (n_neighbors, min_dist, metric, seed…)
- `metric` — embedding distance metric (`cosine` for UMAP)
- `source_embedding_count` — embeddings seen at build time
- `projected_count` — points actually written
- `skipped_count` — missing/invalid embeddings skipped
- `embedding_dim` — source dimensionality
- `version` — projection schema/format version (int)
- `status` — `pending` | `running` | `ready` | `failed`
- `diagnostics_json` — runtime, trustworthiness, neighbor-overlap@k, etc.
- `created_at`, `completed_at`

`map_projection_points` — coordinates per track for a projection:

- `projection_id` (fk → `map_projections.id`)
- `track_id` (fk → `tracks.id`)
- `x` (float32), `y` (float32)
- PK `(projection_id, track_id)`; index on `projection_id`.

Store methods (new mixin `app/store/map_atlas.py`, added to `Store` in
`app/store/__init__.py` per the architecture rule):

- `create_map_projection(...)`, `update_map_projection_status/diagnostics(...)`
- `get_map_projection(id)`, `list_map_projections(model_name=None)`
- `replace_map_projection_points(projection_id, ids, xy)` — bulk insert
- `load_map_projection_points(projection_id) -> (ids, xy)` — bulk read as
  parallel arrays

### Staleness

Surface a `stale` flag on a projection by comparing
`source_embedding_count` with the current `count_embeddings(model_name)`
(same idea the recommender already uses for the HNSW index). Do not
auto-rebuild; just show it in the admin.

## Projection Service + Worker Job

New module `app/projection.py` (analogous to `app/recommender.py` /
`build_index`):

- `build_projection(store, settings, model_name, profile, *, force, projector,
  progress_cb) -> ProjectionResult`
  - load embeddings via `store.load_embeddings(model_name)`;
  - skip tracks with missing embeddings, and drop `missing_at IS NOT NULL`
    tracks so lost files never land on the map;
  - run the injected `projector` (UMAP/PCA) → 2D coords;
  - compute diagnostics (runtime always; trustworthiness/neighbor overlap when
    feasible);
  - persist points + projection metadata + diagnostics;
  - report progress through the progress-job mechanism.
- Projection profiles (v1): `umap_local` (detail: small `n_neighbors`, small
  `min_dist`), plus the metadata/plumbing so `umap_global` and `pca` slot in
  as later phases without schema change.
- Metric = `cosine` for UMAP (embeddings are already L2-normalized).

The heavy call runs in the worker container. Reuse the existing worker/job
loop; add a projection job kind. Handle: missing embeddings, missing/lost
tracks, empty catalog, `force` rebuild, status/progress, failure with recorded
error.

## Entry Points

### CLI (`app/cli.py`)

- `recs build-map --model discogs_multi --profile umap_local [--force]`
  — generate/rebuild a projection (mirrors `build-index`).
- `recs list-maps [--model discogs_multi]` — list projections + status.

### API (`app/api/`, new `map.py` router)

Read + job endpoints. The API must clearly separate **2D map proximity** from
**real high-dimensional similarity** (neighbors come from HNSW, never from x/y).

- `GET  /api/map/projections?model=` — list projections (+ stale flag).
- `POST /api/map/projections` — enqueue a build job (model, profile, force).
- `GET  /api/map/projections/{id}/points` — bulk points as **parallel typed
  arrays** (ids[], x[], y[]) — compact payload, not one JSON object per point.
- `GET  /api/map/projections/{id}/color/{dimension}` — per-point color/filter
  values for a dimension (lazy, separate from coordinates).
- `GET  /api/map/dimensions?projection=` — available color/filter dimensions.
- `GET  /api/map/tracks/{id}?projection=` — inspection payload (metadata, map
  coords, region/mix membership, top predictions, audio features, preference
  summary) — loaded lazily on click.
- `GET  /api/map/tracks/{id}/neighbors?model=&k=` — **real** HNSW neighbors
  (delegates to the existing recommender).
- `GET  /api/map/mixes?projection=` and `/api/map/regions?projection=` —
  overlay payloads (membership, centroids, representative track).

## UI (admin `app/ui.html`, new `<section id="atlas">`)

- Load the WebGL scatter lib from CDN via `<script>`.
- Controls: model selector, projection selector, searchable
  track/artist/release selection, color-by selector, filter controls.
- Map: WebGL pan/zoom point cloud; hover tooltip; click-to-inspect; selected
  highlight; nearest-neighbor highlight; region/mix overlays.
- Point cloud is one WebGL layer, not per-point DOM nodes. Coordinates loaded
  once as typed arrays; per-track detail fetched lazily on click; color/filter
  dimensions fetched on demand and applied client-side without page reload.
- **Interpretation UX (required):** make explicit that "near on map" = near in
  the 2D projection, while "similar" = near in the original embedding space.
  The neighbor highlight is labeled as coming from real HNSW similarity.

### v1 color modes (MVP subset)

At minimum: artist, release, year, genre, liked/disliked, play/preference
score, generated mix, taste region. (Top Discogs head label and BPM/audio
feature buckets can be v1 if cheap, otherwise a later phase.) For high-
cardinality categoricals (artist/release) use a server-provided or
client-hashed palette with an "other" bucket.

### v1 overlays

Taste region centroids; generated-mix membership; selected track's real HNSW
neighbors; selected region/mix selection.

### Track inspection on click

Title, artist, album/release, year, duration, model/projection, map coords,
real similarity neighbors, region membership, mix membership, top predictions,
audio features, preference/playback summary (each shown when available), and
actions to open the existing track card / analysis inspector / recommendations.

## Generated Mixes & Taste Regions Integration

- **Mixes:** highlight all mix tracks, anchor region, representative track,
  familiar vs discovery split, score summary, top artists/releases; help spot
  when multiple mixes occupy the same spatial area (duplicate-region debug).
- **Regions:** centroid, seed/member tracks, representative track, top
  artists/releases, diagnostics, and mixes derived from the region. Regions
  are inspectable independently of mixes.

## Performance Requirements

- Target: personal library ~tens of thousands of tracks.
- WebGL point cloud (not thousands of SVG nodes).
- Coordinates delivered as compact typed arrays; detail metadata lazy on
  click; filtering responsive without full reload.
- Projection generation stays offline/background (worker), never on page load.

## Diagnostics (persisted per projection)

Required: source embedding count, projected point count, skipped/missing
count, model name, embedding dim, projection params, runtime, projection
version, nearest-neighbor preservation metric when available.
Optional (later): trustworthiness, neighbor overlap@k, region separation
summary, outlier count, density summary.

## Tests (mandatory, per CLAUDE.md — no heavy deps in unit tests)

- Projection persistence + round-trip (`replace_/load_map_projection_points`).
- Missing-embedding and missing/lost-track handling (skipped counts correct;
  lost tracks never appear on the map).
- Projection service with an **injected fake projector** (deterministic
  coords) — no UMAP import in the unit path.
- API: list/build/points/dimensions/tracks/neighbors — happy path + error
  paths (unknown projection id, model with no embeddings, empty catalog).
- Neighbors endpoint delegates to the real recommender/HNSW (not x/y).
- Mark any test that genuinely needs UMAP/sklearn as
  `@pytest.mark.integration`.

## Docs

Update `docs/` (per CLAUDE.md, behavior/API change): a short
`docs/collection-map.md` (or a section in `docs/architecture.md` +
`docs/data-model.md`) covering the new tables, the projection job, the API,
and the 2D-vs-embedding-space distinction.

## Acceptance Criteria (v1 done when)

- A projection can be generated for `discogs_multi`.
- Projection metadata + coordinates are persisted (multiple per model
  supported).
- Admin displays the collection as an interactive WebGL 2D map.
- User can select a track and see metadata plus **real** HNSW neighbors.
- Generated mixes can be highlighted on the map.
- Taste regions can be highlighted on the map.
- At least the basic color/filter modes work.
- The map does not alter existing recommendation behavior.
- Missing/lost tracks are handled safely.
- Works on a library with tens of thousands of tracks without freezing the
  browser under normal use.

## Later Phases (deferred, separate tasks)

- Second UMAP profile (`umap_global`) + PCA baseline projection surfaced in UI.
- `muq_mulan` as an alternative projection space when available.
- Full set of color modes (top Discogs head label, BPM/audio-feature buckets)
  and remaining overlays.
- t-SNE for small sampled diagnostic views.
- HDBSCAN automatic region discovery.
- Datashader-style density rendering / deck.gl tiling for very large libraries.
- Trustworthiness / neighbor-overlap@k / density diagnostics.

## Out of Scope (first version and generally)

Changing recommendation ranking; using 2D coordinates as recommendation input;
3D visualization; real-time projection updates; manual embedding editing;
training/fine-tuning embedding models; replacing generated-mix logic; a
complex tile server unless WebGL proves insufficient.
