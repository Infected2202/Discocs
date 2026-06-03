# Analysis Pipeline

This document describes the analysis features added on top of the original
recommendation MVP.

## Recommendation Embeddings

The recommendation path is unchanged:

```text
audio -> Discogs-EffNet embedding model -> mean pooled track vector
-> normalized float32 vector -> SQLite embeddings -> HNSW cosine index
```

Default recommendation model:

```text
discogs_multi -> models/discogs_multi_embeddings-effnet-bs64-1.pb
```

The web/API analyze job supports `workers` and `tf_threads`. The CLI analyze
path remains sequential and is useful for simple server-side runs.

## Discogs-EffNet Head Pack

The head-pack path decodes a track once, computes transient Discogs-EffNet patch
embeddings once, then applies every enabled compatible classification head:

```text
audio
  -> discogs-effnet-bs64-1.pb
  -> patch embeddings, kept in memory only
  -> enabled *-discogs-effnet heads
  -> full score vectors + top labels in SQLite
```

Patch embeddings are not stored. This keeps the database smaller while still
allowing a single pass to produce many descriptors.

Main commands:

```bash
recs download-models --pack discogs-effnet-heads
recs analyze-heads --limit 20
python scripts/download_head_models.py --out-dir models
```

Main API/UI actions:

```text
POST /jobs/download-head-models
POST /jobs/analyze-heads
GET  /models/head-pack
GET  /tracks/{track_id}/analysis
```

## Stored Head Data

Full score vectors are stored in `track_model_outputs`:

```text
track_id
model_name
dim
dtype
aggregation
scores_blob
created_at
```

`scores_blob` is a raw `float32` vector. For example, a 400-dimensional head
uses `400 * 4 = 1600` bytes.

Top labels are stored in `track_predictions`:

```text
track_id
model_name
label
score
rank
created_at
```

This normalized layout makes cleanup easy. To remove a model later:

```sql
delete from track_model_outputs where model_name = 'mood_sad';
delete from track_predictions where model_name = 'mood_sad';
```

Disabling a head in the registry stops future analysis from producing it.
Deleting stored rows removes old results. Recommendation embeddings are not
affected.

## Audio Features

Audio features are a separate pack and separate UI action. They do not use the
Discogs-EffNet classification heads.

Current extractor:

```text
audio_features_v1
```

Current feature families:

- `RhythmExtractor2013`: BPM and confidence.
- `KeyExtractor`: key, scale, and key strength.
- `LoudnessEBUR128`: integrated LUFS and loudness range.
- `DynamicComplexity`: dynamic complexity and dynamic loudness.

Main command:

```bash
recs analyze-audio-features --limit 20
```

Main API/UI action:

```text
POST /jobs/analyze-audio-features
```

Stored data lives in `track_features`:

```text
track_id
feature_name
value
text_value
unit
confidence
extractor
created_at
```

## Track Analysis Inspector

Every track card has a tablet-icon button. It opens a modal backed by:

```text
GET /tracks/{track_id}/analysis
```

The response includes:

- track metadata;
- all stored head outputs for the track;
- full score vectors;
- top predictions;
- audio features.

The inspector is intended for quick quality checks while experimenting with
model packs. A broader explorer page can be added later.

## Lost Files

Scanned tracks can become missing when files are deleted or paths change. The
app tracks this with `tracks.missing_at` instead of immediately deleting rows.

Current behavior:

- missing tracks are excluded from new analysis queues;
- missing tracks remain visible in the `Lost files` page;
- the page supports check, selection, pagination, remove selected, and remove
  all;
- removing lost records cascades embeddings, predictions, model outputs,
  features, and feedback through SQLite foreign keys.

Main API/UI actions:

```text
POST   /jobs/check-missing-files
GET    /lost-files
DELETE /lost-files
```
