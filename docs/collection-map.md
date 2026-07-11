# Collection Map / Embedding Atlas

An interactive 2D map of the library's embedding space, for **diagnostics and
exploration** — inspecting the collection, understanding similarity regions,
debugging recommendations, and eyeballing generated mixes and taste regions.

The map is a **viewing surface only**. It never feeds recommendation ranking,
the HNSW index, or the original high-dimensional similarity math. Coordinates
are a lossy 2D projection; "near on the map" means near *in the projection*,
while "similar" (the neighbors panel) means near *in the original embedding
space*, computed by the recommender over HNSW/cosine.

Lives in the old admin (`app/ui.html`, `:8711/admin` → **Atlas** section) per
the project UI rule — it is a diagnostic tool, not part of the React app.

## Data model

Two tables (created in `app/store/base.py`, mixin `app/store/map_atlas.py`).
A *projection* is a persisted snapshot of 2D coordinates for one embedding
model; multiple projections per model are allowed.

`map_projections` — one row per build:

- `id` (uuid text PK), `model_name`, `name` (profile key, e.g. `umap_local`).
- `method` (`umap` | `pca`), `metric` (`cosine` for UMAP), `params_json`.
- `source_embedding_count` (embeddings seen at build), `projected_count`
  (points written), `skipped_count` (missing/lost tracks dropped),
  `embedding_dim`, `version`.
- `status` (`pending` | `running` | `ready` | `failed`), `diagnostics_json`
  (runtime, params, counts), `created_at`, `completed_at`.

`map_projection_points` — coordinates per track:

- `projection_id` (FK → `map_projections`, cascade), `track_id` (FK → `tracks`,
  cascade), `x`, `y` (`float32`). PK `(projection_id, track_id)`.

### Staleness

A projection is flagged `stale` when its `source_embedding_count` no longer
matches the live `count_embeddings(model_name)` — the same drift check the
recommender uses for the HNSW index. The admin surfaces the flag; nothing
auto-rebuilds.

## Projection service

`app/projection.py` — `build_projection(store, *, model_name, profile, force,
projector_factory, progress)`:

1. Loads `(track_ids, vectors)` via `Store.load_projection_source(model_name)`,
   which joins `embeddings` to `tracks` and **excludes `missing_at IS NOT NULL`
   tracks**, so lost files never land on the map.
2. Runs the injected projector → 2D coords; validates the `(n, 2)` shape.
3. Persists points (`replace_map_projection_points`) and metadata/diagnostics,
   marking the row `ready` (or `failed` on empty source / projector error).

Profiles (`PROJECTION_PROFILES`): `umap_local` (default, tight neighborhoods),
`umap_global` (broad layout), `pca` (linear baseline). UMAP uses `metric=cosine`
(embeddings are already L2-normalized).

The projector is **mockable**: `build_projection` takes a `projector_factory`,
so unit tests inject a deterministic fake and never import UMAP. The default
factory (`default_projector_factory`) lazy-imports `umap` / `sklearn` only when
a real build runs.

### Where it runs, and the runtime dependency

The build is dispatched as a **backend `BackgroundTask`** (same pattern as the
flow-profile and release-aggregate jobs), not through the per-track analysis
worker queue — a whole-model projection isn't a per-track task. The job wrapper
is `_build_map_projection_job` in `app/analysis_jobs.py`, tracked via the
progress-job mechanism (`create_progress_job` / `update_progress_job`).

The real reduction needs `umap-learn` in the **backend** image (the `[map]`
optional extra: `umap-learn`, `scikit-learn`). Tests use the fake projector, so
CI (test/sonar) does not need it. `scikit-learn` is already pinned transitively
in `uv.lock`; `umap-learn` is not, and the backend image installs with `uv sync
--frozen`, so it cannot be selected via `--extra map` without regenerating the
lock. Instead, `deploy/backend/Dockerfile` installs it as a **separate layer on
top of the frozen venv**:

```dockerfile
uv pip install --python /app/.venv/bin/python "umap-learn>=0.5" "numpy<2"
```

The `numpy<2` constraint keeps umap's transitive deps from dragging in numpy
2.x, which would break `essentia-tensorflow`. The backend image build (CI
Build&Push, plus the Trivy scan of that image) exercises this install, so a
broken dependency layer fails the pipeline rather than silently shipping. The
worker image does **not** need umap — the projection build runs in the backend
process, never the per-track worker queue.

## Entry points

### CLI (`app/cli.py`)

```bash
recs build-map --model discogs_multi --profile umap_local [--force]
recs list-maps [--model discogs_multi]     # shows the stale flag
```

### API (`app/api/map.py`, prefix `/api/map`)

The router is namespaced under `/api/map` rather than `/api/v1` (see the
exception note in `docs/architecture.md#api-routing`); it is still under the
nginx `^/(api|admin|health)` backend prefix, so there is no SPA collision.

```text
GET  /api/map/projections?model=            # list (+ stale flag)
POST /api/map/projections                   # enqueue a build (model, profile, force)
GET  /api/map/projections/{id}              # one projection
GET  /api/map/projections/{id}/points       # bulk points as parallel typed arrays
GET  /api/map/projections/{id}/labels       # bulk artist/title per point (hover tooltips)
GET  /api/map/dimensions?projection=        # available color/filter dimensions
GET  /api/map/projections/{id}/color/{dim}  # per-point values, aligned to points order
GET  /api/map/tracks/{id}?projection=       # inspection (coords, region/mix, predictions)
GET  /api/map/tracks/{id}/neighbors?model=&k=   # REAL HNSW neighbors (via Recommender)
GET  /api/map/mixes?projection=             # generated-mix overlay membership
GET  /api/map/regions?projection=           # taste-region overlay membership
```

Points ship as `{track_ids: [...], x: [...], y: [...]}` — compact parallel
arrays, not one JSON object per point. Color values come back aligned to the
same track-id order so the client can recolor without re-fetching coordinates.

Color dimensions are the metadata fields (`artist` / `release` / `genre` /
`year`) plus `region` / `mix` when present, and **`genre_discogs400`** when the
Discogs-EffNet 400-genre classifier head has been run on any track. For the
classifier dimension each value is `{genre, style, score}` (the rank-1
prediction, e.g. `{"genre":"Electronic","style":"Electronic---House",
"score":0.81}`) or `null` for tracks the head never saw — the store's
`top_prediction_by_track(model, track_ids)` pulls all rank-1 rows in one query.
The client colors by the full **style**, but keeps each style in its genre's
color family (hue/lightness are spread around a fixed per-genre base color), so
Electronic stays blue-ish and Rock red-ish while sub-styles differ; brightness
also encodes classifier confidence, and unclassified points stay gray. This is
the **default** color dimension when the classifier has run.

The `/neighbors` endpoint delegates to `Recommender(...).similar(seed)` — real
HNSW/cosine similarity over the original embeddings, never the map's x/y.

## Admin UI (`app/ui.html`, `#atlas`)

- deck.gl `ScatterplotLayer` on an `OrthographicView`, loaded from CDN; one
  WebGL layer fed from the `/points` typed arrays (no per-point DOM nodes).
- Hovering a point shows **`artist — title`** (from `/labels`, fetched once per
  projection and keyed by track id), with the Discogs400 **style** on a second
  line (e.g. `Electronic · House`) so the genre reads without checking the
  legend. Falls back to `#id` when a label is missing.
- Controls: projection selector (with stale flag), a Build panel (POST + job
  polling), color-by (`artist` / `release` / `genre` / `year` / `region` /
  `mix`, plus **Genre (Discogs400)** when classified — the default: style hue
  within a per-genre color family, brightness = classifier confidence) with a
  legend, and an overlay highlighter for taste regions / mixes.
- Click a point to inspect: metadata, map coords, region/mix membership, top
  Discogs tags, and the track's **real HNSW neighbors** — highlighted on the
  map and labeled as coming from the original embedding space, not map
  distance. Missing-index / neighbor errors degrade to a message.

## Tests

- `tests/test_map_atlas_store.py` — store round-trip, ordering, cascade, lost-
  track exclusion, bad-shape rejection.
- `tests/test_projection.py` — `build_projection` with an injected fake
  projector (no UMAP): persistence, skipped/missing counts, empty-source and
  bad-shape → `failed`, `force` rebuild, profile metadata.
- `tests/test_map_api.py` — TestClient round-trip over a tmp DB: list / points /
  build / dimensions / color / inspection / neighbors (HNSW delegation) /
  overlays, plus error paths (unknown projection 404, unknown profile 400,
  unknown dimension 400, missing index 503, missing track 404).

The admin JS has no unit harness (consistent with the rest of `app/ui.html`)
and was verified live in the browser against a seeded demo DB.

## Out of scope (v1)

Changing recommendation ranking; using 2D coordinates as recommendation input;
3D; real-time projection; t-SNE / HDBSCAN; density/tile rendering;
trustworthiness / neighbor-overlap diagnostics. See
`plans/embedding-atlas-collection-map-spec.md` for deferred phases.
