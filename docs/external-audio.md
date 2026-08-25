# External audio seeds

Radio can start from a track that is not in the library — a link the Telegram
bot downloaded, or an audio file someone sent to the bot. The audio is embedded
on the fly, the vector queries the existing HNSW index, and the results are
ordinary catalog tracks.

## Invariant: the catalog is never modified

External analysis is read-only. It does not write embeddings, tracks, model
outputs, predictions, or index entries, and it does not add the external track
to the library. The query vector exists only in memory, and the uploaded bytes
are deleted before the response is returned.

This is deliberate: external listening must not drift the recommendation index.
`tests/test_external_audio.py` asserts embedding count, track count, and index
file size/mtime are unchanged across a request — those tests fail if the
invariant is ever broken.

The vector cache lives in process memory (an LRU of 64 vectors keyed by the
SHA-256 of the submitted bytes plus the model name), not in `app.db`. A restart
drops it and the next request simply recomputes.

## API

```text
POST /api/v1/similar/by-audio
```

The request body is the raw audio file — no multipart, no form fields. Response:

```json
{
  "source": "external_audio",
  "request_id": "…",
  "model": "discogs_multi",
  "effective_count": 50,
  "min_similarity": null,
  "duration_seconds": 312.4,
  "analyzed_seconds": 312.4,
  "analysis_offset_seconds": 0.0,
  "vector_cached": false,
  "skipped_without_external_id": 0,
  "results": [
    {"item_id": "…", "track_id": 1, "artist": "…", "title": "…", "album": "…",
     "distance": 0.12, "similarity": 0.88}
  ]
}
```

Items use the same shape as `/api/v1/navidrome/similar`, so a client that can
render one can render the other. Results without a Navidrome external id are
dropped and counted in `skipped_without_external_id`: a client cannot play what
it cannot address.

Model, count, `max_per_artist`, `exclude_same_album`, `count_collaboration_artists`
and `min_similarity` come from the shared instant-mix settings. An external seed
therefore produces the same kind of radio as a catalog seed, and there are no
per-request recommendation parameters to keep in sync.

Failures:

| Status | Meaning |
|---|---|
| 400 | Empty body, no audio stream, or audio that will not decode |
| 413 | Body larger than `DISCOCS_EXTERNAL_AUDIO_MAX_MB` |
| 503 | Index missing or stale, model unavailable, or all analysis slots busy |

## Long recordings

A two-hour DJ set has no single "sound", and running the model over all of it
costs minutes of CPU. Above 12 minutes only the middle 10 minutes are analyzed —
intros and outros are the least characteristic part of a mix. The window is cut
to a temporary FLAC file, which keeps the source sample rate so each model still
resamples the way it normally would (16 kHz for Discogs-EffNet, 24 kHz for
MuQ-MuLan).

Shorter audio is embedded whole, exactly like a catalog track, so an ordinary
external track and its catalog twin produce the same vector.

Duration comes from `ffprobe`. If `ffprobe` is unavailable, the whole file is
analyzed and a broken input is reported by the decoder instead.

## Limits and configuration

| Variable | Default | Meaning |
|---|---|---|
| `DISCOCS_EXTERNAL_AUDIO_MAX_MB` | `200` | Request body cap |
| `DISCOCS_EXTERNAL_ANALYSIS_CONCURRENCY` | `2` | Concurrent analyses; further requests wait up to 5 minutes, then get 503 |

A fresh embedder is created per request rather than cached between requests: the
TensorFlow graph load is small next to decoding plus inference, and a
per-request embedder keeps two concurrent analyses from sharing model state.

## Deployment boundary

`/api/v1/similar/by-audio` is denied by the public nginx route, like the other
internal surfaces. The bot reaches the backend directly inside the Docker
network (`DISCOCS_BASE_URL=http://backend:7752`) and authenticates with its
service token, so it is unaffected. `tests/test_deploy_boundary.py` enforces the
deny rule.
