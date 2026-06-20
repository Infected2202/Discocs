# Phase 10 Spec: Segment Embeddings And MAEST

## Purpose

Improve representation quality and create a path for heavier model experiments.

Phase 10 covers:

- segment embeddings for current Discogs-EffNet models;
- full embedding storage strategy;
- index metadata per model/strategy;
- MAEST as an additional model family in a separate database;
- evaluation before using heavier models in production ranking.

This is not a replacement for the existing Discogs-EffNet embeddings. MAEST is
an additional signal.

## Current Direction

Decisions already made:

- keep full embeddings, not only pooled/global averages;
- add segment storage for current models;
- MAEST is useful and should live in a separate database;
- use the strongest publicly available MAEST checkpoint available at
  implementation time, preferring the largest/best-performing supervised music
  representation checkpoint over cheaper variants;
- compute heavy models on GPU workers;
- do not optimize for storage size; assume enough disk space and design storage
  for completeness/inspection;
- MERT can remain a later experiment;
- MuLan/CLAP-like text/audio models are more relevant for future prompt
  playlist/search work, not core Flow now;
- Discogs-EffNet global embedding path already exists and should be reused.

## Dependencies

Requires:

- stable analysis job framework;
- model registry/settings;
- vector storage separation;
- HNSW index metadata;
- existing track embedding pipeline.

Useful:

- release aggregates;
- Flow/autoplay evaluation metrics;
- dashboard diagnostics.

Does not require:

- immediate Flow integration;
- prompt search;
- genre/mood shelves.

## Storage Strategy

Main DB:

- metadata;
- model/index status;
- analysis job state;
- summary fields.

Embeddings DB:

- current Discogs-EffNet global embeddings;
- Discogs-EffNet segment embeddings;
- release/album aggregate embeddings.

MAEST DB:

- MAEST track embeddings;
- MAEST segment/global variants;
- MAEST index metadata.

Reason:

- MAEST is heavy;
- embeddings can grow quickly;
- main DB should remain fast and manageable.

## Schema

### `embedding_models`

Fields:

- `model_key TEXT PRIMARY KEY`
- `family TEXT NOT NULL`
- `name TEXT NOT NULL`
- `version TEXT`
- `dimension INTEGER`
- `supports_segments INTEGER NOT NULL`
- `settings_json TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Examples:

- `discogs_multi_effnet_global`;
- `discogs_multi_effnet_segments`;
- `discogs_track_effnet_global`;
- `maest_global`.

### `track_embedding_segments`

Fields:

- `track_id INTEGER NOT NULL`
- `model_key TEXT NOT NULL`
- `segment_index INTEGER NOT NULL`
- `start_seconds REAL NOT NULL`
- `duration_seconds REAL NOT NULL`
- `embedding_ref TEXT NOT NULL`
- `norm REAL`
- `created_at TEXT NOT NULL`

Constraints:

- primary key `(track_id, model_key, segment_index)`.

Embedding vector itself can live in embeddings DB/blob storage referenced by
`embedding_ref`.

### `embedding_indexes`

Fields:

- `index_key TEXT PRIMARY KEY`
- `model_key TEXT NOT NULL`
- `strategy TEXT NOT NULL`
- `path TEXT NOT NULL`
- `space TEXT NOT NULL DEFAULT 'cosine'`
- `dimension INTEGER NOT NULL`
- `item_count INTEGER NOT NULL`
- `settings_json TEXT`
- `built_at TEXT NOT NULL`

Strategies:

- `global_track`;
- `segment_track`;
- `release_centroid`;
- `maest_global`;
- `maest_segment`.

## Segment Strategy

Start:

- fixed 30-second segments.

Later:

- hybrid fixed + summary segments;
- intro/middle/outro summaries;
- salient segment selection.

Why fixed first:

- easiest to implement;
- easiest to test;
- good enough to compare whether segment-aware reranking helps.

Segment extraction:

- decode audio once per track;
- extract embeddings per segment;
- L2 normalize each segment;
- store segment metadata and vector ref;
- store/keep global embedding as separate model/strategy.

## Segment Usage

Do not replace global embeddings immediately.

Use segments for:

- reranking near candidates;
- detecting tracks with multiple moods/parts;
- better release aggregates;
- optional Flow/autoplay continuity later.

Initial usage:

- global HNSW retrieves candidates;
- segment-aware reranker compares best segment or segment summary;
- store debug score.

Avoid:

- building product behavior solely on segments before evaluation.

## MAEST

MAEST should be added as:

- additional model family;
- separate database;
- separate analysis job;
- separate HNSW index;
- selectable model in the UI/API, the same way other embedding models can be
  selected.

Default implementation choice:

- choose the best/largest public MAEST checkpoint available during
  implementation;
- run on GPU workers;
- store global and segment-capable outputs if the checkpoint supports them;
- expose MAEST indexes beside existing Discogs-EffNet indexes;
- do not remove existing Discogs-EffNet models.

Evaluation is still useful, but it should not block adding MAEST as a selectable
model. It should determine which model becomes the recommended default for each
feature, not whether the model can exist.

## Analysis Jobs

Preferred job model:

- parameterized embedding analysis job;
- choose model family/key;
- choose strategy: global, segments, both;
- preserve resume behavior;
- skip existing embeddings for same model/strategy;
- invalidate on file change.

Example:

```text
recs analyze-embeddings --model discogs_multi --strategy segments
recs analyze-embeddings --model maest --strategy global
```

Dashboard/operational UI can still show separate cards per model for clarity,
but backend job logic should be shared.

## APIs / Diagnostics

Potential endpoints:

- `GET /api/v1/embeddings/models`;
- `GET /api/v1/embeddings/indexes`;
- `POST /api/v1/jobs/analyze-embeddings`;
- `POST /api/v1/jobs/build-embedding-index`;
- `GET /api/v1/tracks/{id}/embeddings/segments` in debug mode.

Debug views:

- model coverage;
- segment counts;
- missing embeddings;
- index status;
- vector dimensions;
- storage size estimates;
- sample nearest neighbors by model.

## Evaluation

Evaluation should compare:

- current Discogs-EffNet global;
- Discogs-EffNet segment rerank;
- MAEST global;
- MAEST segment if feasible.

Metrics:

- offline nearest-neighbor sanity checks;
- playlist/release coherence;
- Flow skip/completion/like rate;
- album recommendation acceptance;
- runtime cost;
- indexing time.

Do not hide model differences behind one opaque "best" choice. Keep comparison
outputs inspectable and let the UI select the active model/index for feature
experiments.

## Testing Plan

Unit tests:

- segment boundaries;
- segment metadata;
- L2 normalization;
- vector round trip;
- resume/skip existing segments;
- changed-file invalidation.

Job tests:

- parameterized job creates expected rows;
- model key isolation;
- global and segment outputs coexist;
- index metadata recorded.

Integration smoke tests:

- real model extraction optional and marked heavy;
- tests without Essentia/MAEST dependencies still pass.

## PR Slices

### Slice 1: Model Registry And Index Metadata

Goal:

- make model/strategy/index state explicit.

Includes:

- `embedding_models`;
- `embedding_indexes`;
- model settings;
- index metadata helpers.

### Slice 2: Discogs-EffNet Segment Storage

Goal:

- store fixed segment embeddings for current models.

Includes:

- `track_embedding_segments`;
- segment extraction path;
- vector refs;
- resume/invalidation behavior.

### Slice 3: Parameterized Embedding Jobs

Goal:

- unify global/segment/model analysis.

Includes:

- `analyze-embeddings` job;
- model selection;
- strategy selection;
- worker/task compatibility;
- operational status.

### Slice 4: Segment-Aware Index/Rerank Experiment

Goal:

- evaluate whether segments improve recommendations.

Includes:

- optional segment index or segment reranker;
- debug score output;
- offline comparison reports.

### Slice 5: MAEST Experiment

Goal:

- add MAEST as separate heavy model family.

Includes:

- separate database;
- analysis job;
- index metadata;
- evaluation report;
- no default promotion until proven.

Recommended grouping:

- PR 1: Slice 1.
- PR 2: Slice 2.
- PR 3: Slice 3.
- PR 4: Slice 4.
- PR 5: Slice 5.

## Open Decisions

No blocking decisions.

Decisions:

- MAEST runtime target: GPU workers.
- Storage budget: do not constrain design for storage economy.
- Checkpoint policy: use the strongest public MAEST checkpoint available at
  implementation time.
- Product behavior: model/index choice remains selectable in UI/settings.

Recommended default:

- implement Discogs-EffNet fixed 30-second segments first;
- keep MAEST separate and experimental.
