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
POST /api/v1/jobs/download-head-models
POST /api/v1/jobs/analyze-heads
GET  /api/v1/models/head-pack
GET  /api/v1/tracks/{track_id}/analysis
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
audio_features_v2
```

Current feature families:

- `RhythmExtractor2013`: BPM and confidence. Input is capped at
  `RHYTHM_MAX_DURATION_SECONDS` (1800s / 30 min, see `app/audio_features.py`) —
  its `OnsetDetectionGlobal` step has a fixed-size internal output buffer and
  raises `output buffer is full` on very long tracks (DJ mixes, podcasts
  mistagged as a single track). BPM is stable early on, so a prefix is enough.
- `KeyExtractor`: key, scale, and key strength.
- `LoudnessEBUR128`: integrated LUFS and loudness range.
- `DynamicComplexity`: dynamic complexity and dynamic loudness.

Main command:

```bash
recs analyze-audio-features --limit 20
```

Main API/UI action:

```text
POST /api/v1/jobs/analyze-audio-features
```

The same durable audio-feature task also publishes `timeline_foundation_v2`
with waveform extrema, frequency-colour buckets, beat timestamps, global
rhythm confidence, coverage and interval-derived local tempo. There is no
separate timeline-analysis job: local and remote audio-feature executors return
both projections from one analysis result. The DJ interface remains read-only
and never starts analysis.

Remote worker logs expose per-track timing without changing the analysis path
or its parameters: download, the existing independent 16 kHz and 44.1 kHz
decodes, rhythm, key, loudness, dynamics, timeline encoding and result
serialization. Result-submit logs include request duration. These measurements
are used for queue tuning; they are not stored as audio features and do not
affect accepted analysis data.

The five-container worker Compose profile keeps at most two CPU analyses
running in each container while allowing four claimed tasks in flight. The
extra tasks remain buffered around the serial download and submit stages, so
stage transitions do not starve the two analysis slots. Downloads and result
submissions remain unbatched (`1`) to avoid increasing backend and storage
bursts.

`audio_features_v1` rows are legacy scalar-only results. They do not satisfy
v2 readiness and are removed per track only after a complete v2 result has
been accepted. Consequently the first v2 deployment intentionally queues all
active tracks for one explicit audio-feature rebuild.

Remote pull workers are stateless with respect to the server catalog: startup
loads runtime/model settings but does not initialize the worker-local SQLite
file. Task identity and accepted results are owned by the backend database.

Scalar data lives in `track_features`; browser-efficient temporal arrays live
in the checksummed timeline artifact:

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
GET /api/v1/tracks/{track_id}/analysis
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
POST   /api/v1/jobs/check-missing-files
GET    /api/v1/lost-files
DELETE /api/v1/lost-files
```
