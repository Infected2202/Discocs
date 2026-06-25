# AGENTS.md

## Project Shape

This repo is a Python MVP for local music recommendations:

```text
scan audio files -> read tags -> extract Discogs-EffNet embeddings
-> store vectors in SQLite -> build hnswlib cosine index
-> query similar tracks through CLI / FastAPI
```

Core code lives in `app/`, tests live in `tests/`.

## Setup

Use Python 3.11+.

For normal development and tests:

```bash
python -m pip install -e ".[dev]"
```

For real audio embedding extraction:

```bash
python -m pip install -e ".[dev,essentia]"
```

`essentia-tensorflow` is a heavy/fragile dependency. Use Docker or an environment with Essentia installed for real embedding extraction. Keep imports lazy and do not require Essentia for unit tests that do not actually run model inference.

## Codex Environment

Use this default app URL for browser/API checks and configuration:

```text
http://192.168.1.146:8711/
```

## Runtime Files

Runtime state is intentionally local and ignored by git:

```text
data/app.db
data/index_*_hnsw.bin
models/*.pb
models/*.onnx
eval/results/
```

Do not commit music files, model binaries, generated indexes, SQLite databases, or local evaluation output.

Default model path:

```text
models/discogs_multi_embeddings-effnet-bs64-1.pb
```

## Common Commands

Run tests:

```bash
python -m pytest
```

Check syntax:

```bash
python -m compileall app tests
```

Show CLI:

```bash
recs --help
```

Typical local workflow:

```bash
recs scan /music
recs analyze --limit 500
recs build-index
recs similar --track-id 1 --k 30
uvicorn app.main:app --host 0.0.0.0 --port 8711
```

## Code Guidelines

- Keep MVP changes small and direct. Prefer working CLI behavior before polishing API/UI.
- Keep heavy dependencies optional at import time. `app.embedder` may import Essentia inside methods; top-level imports should remain lightweight.
- Store vectors as normalized `float32` arrays. HNSW uses `space="cosine"`, and UI/API similarity is `1 - distance`.
- If a scanned file changes by `path + mtime + file_size`, invalidate existing embeddings for that track.
- Preserve resume behavior: analyzer should skip tracks that already have embeddings for the selected model.
- Avoid introducing PostgreSQL, Redis, FAISS, GPU assumptions, or queue workers until the MVP proves recommendation quality.

## Tests

Add tests for behavior, not implementation trivia.

Important scenarios:

- SQLite upsert and embedding round trip
- changed-file invalidation
- vector pooling and L2 normalization
- HNSW build/load/query on a tiny catalog
- recommender filtering: remove seed, cap artists, exclude same album
- FastAPI health/search/similar error paths

Tests should pass without real model files and without Essentia installed unless explicitly marked as an integration smoke test.

## Docker

Docker is for running the service with mounted local data and music folders. Update `docker-compose.yml` mounts locally, but do not commit machine-specific music paths unless they are placeholders.

## Product Direction

The first milestone is proving this hypothesis:

```text
Discogs-EffNet embeddings produce useful recommendations for this electronic music library.
```

Prefer features that help evaluate that hypothesis:

- better scan/analyze reliability
- clear similar-track output
- feedback capture
- A/B model comparison for `discogs_multi`, `discogs_track`, and `discogs_label`

Defer playlist generation, Navidrome integration, auth, and production job infrastructure until after recommendation quality is validated.
