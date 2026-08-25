# discocs

Local music recommendation MVP for a personal audio collection.

Pipeline:

```text
music folder -> scan files -> decode audio -> Discogs-EffNet embedding
-> normalize / aggregate -> save embeddings -> build HNSW cosine index
-> REST API / browser UI similar tracks
```

The default recommendation model is `discogs_multi_embeddings-effnet-bs64-1.pb`
through Essentia's `TensorflowPredictEffnetDiscogs`.

The app also has optional analysis packs:

- Discogs-EffNet classification heads for tags, genres, moods, instruments, and
  related model outputs.
- MuQ-MuLan audio embeddings for a separate audio-to-audio similarity space.
- Audio features for BPM, key/scale, loudness, and dynamics.
- Lost-file tracking for scanned tracks whose audio files disappeared.
- Per-track analysis inspector in the web UI.

See [docs/analysis-pipeline.md](docs/analysis-pipeline.md) and
[docs/operations.md](docs/operations.md) for details. Navidrome Instant Mix
integration is documented in [docs/navidrome-plugin.md](docs/navidrome-plugin.md).
Original track and streaming collection downloads are documented in
[docs/music-downloads.md](docs/music-downloads.md). Radio seeded by audio that
is not in the library is documented in
[docs/external-audio.md](docs/external-audio.md).

## Quick Start

Use a Linux server for real embedding extraction.

Create and install the environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,essentia]"
```

For MuQ-MuLan inference on the same worker, install the optional PyTorch/MuQ
dependencies too:

```bash
python -m pip install -e ".[dev,essentia,muq]"
```

Put the default model file here:

```text
models/discogs_multi_embeddings-effnet-bs64-1.pb
```

Download Discogs-EffNet model files from the Essentia model catalog:

```text
https://essentia.upf.edu/models/feature-extractors/discogs-effnet/
```

For the Discogs-EffNet head pack, use the UI `Download head models` job or the
fallback downloader:

```bash
python scripts/download_head_models.py --out-dir models
```

For MuQ-MuLan, use the CLI downloader. It calls Hugging Face through `muq` and
stores the model cache under `models/muq` by default:

```bash
recs download-models --pack muq-mulan
```

You can verify a single file before queueing the whole library:

```bash
recs embedding-smoke --model muq_mulan --path /path/to/track.flac
```

Run the API/UI:

```bash
./run_app.sh
```

Open:

```text
http://localhost:8711
```

If the script is not executable yet:

```bash
chmod +x run_app.sh
./run_app.sh
```

## Analyze Runtime

The web/API analyze job defaults to the best measured local preset:

```text
workers=4
tf_threads=4
```

This maps to:

```text
TF_NUM_INTRAOP_THREADS=4
TF_NUM_INTEROP_THREADS=1
OMP_NUM_THREADS=4
```

You can override these in the UI under Settings before starting Analyze, or by
posting JSON to `/jobs/analyze`:

```bash
curl -X POST http://localhost:8711/jobs/analyze \
  -H "Content-Type: application/json" \
  -d '{"model":"discogs_multi","limit":500,"workers":4,"tf_threads":4,"execution_mode":"both"}'
```

Analyze execution modes:

```text
local   - only the server runs embedding inference
remote  - server creates durable tasks; HTTP workers claim and process them
both    - server and HTTP workers can claim tasks
```

Remote worker setup:

1. Start the server with `./run_app.sh` and open `http://SERVER_IP:8711`.
2. In Settings, set `Analyze execution` to `Remote only` or `Local + remote`.
3. Fill `Server URL for worker` with the URL reachable from the GPU machine.
4. Tune claim/in-flight/download/submit/lease values or keep the defaults.
5. Copy the generated `Worker command`, or set the same values in `run_worker.bat`.
6. On the worker machine, install the environment and run `run_worker.bat`.
7. Start `Analyze missing` in the web UI and watch Jobs / Workers.

Example Windows worker launch:

```bat
set DISCOCS_WORKER_SERVER=http://192.168.1.41:8711
set DISCOCS_WORKER_ID=gpu-4090-1
set DISCOCS_WORKER_CLAIM_BATCH_SIZE=32
set DISCOCS_WORKER_MAX_INFLIGHT_TASKS=128
set DISCOCS_WORKER_DOWNLOAD_CONCURRENCY=8
run_worker.bat
```

Docker GPU worker launch:

Set `DISCOCS_WORKER_SERVER` in the root `.env` (next to
`docker-compose.worker.yml` — Compose reads it automatically for variable
substitution) to the server URL reachable from the GPU machine, e.g.:

```text
DISCOCS_WORKER_SERVER=http://192.168.1.41:8711
```

Build and run 5 worker instances:

```bash
docker compose -f docker-compose.worker.yml build
docker compose -f docker-compose.worker.yml up -d --scale worker=5
```

Rebuild after every `app/` change that touches worker code (e.g. `app/cli.py`)
— a stale image keeps calling old API paths against a moved/renamed backend
route and fails with 404s that look like a server outage.

The worker image installs the heavy Essentia and MuQ dependencies in a cached
Docker layer. After the first build, normal `app/` code changes should only
rebuild the small editable-install layer.

With `--embedding-backend auto`, a broken direct-TensorFlow batch falls back to
the Essentia backend per task (see `fallback_to_essentia_embedding` in
`app/cli.py`). The fallback embedder is built once per model and reused for
every task in the failed batch — building a fresh one per task used to spam
worker logs with Essentia's `No network created, or last created network has
been deleted` warning on every throwaway TensorFlow graph.

## Basic Workflow

From the browser UI:

1. Set the music path visible to the app.
2. Click `Scan`.
3. Click `Analyze missing`.
4. Click `Build index`.
5. Search a track and click `Seed` to show recommendations.

Optional analysis:

1. Click `Download head models`.
2. Click `Analyze Discogs-EffNet heads`.
3. Click `Analyze audio features`.
4. Open a track card and click the tablet icon to inspect stored analysis data.

The same operations are also available through the CLI:

```bash
source .venv/bin/activate
recs scan /path/to/music
recs analyze --limit 500
recs download-models --pack discogs-effnet-heads
recs analyze-heads --limit 20
recs analyze-audio-features --limit 20
recs download-models --pack muq-mulan
recs analyze --model muq_mulan --limit 20
recs build-index --model muq_mulan
recs build-index
recs similar --track-id 1 --k 30
```

Note: the optimized multi-process `workers/tf_threads` path is currently used by
the API/UI analyze job. The CLI analyze command is still the simple sequential
path.

## Web UI

The browser UI includes:

- Dashboard pipeline controls and counters.
- Head model readiness table under the `Head models` details panel.
- Library and Browse track lists.
- Lost files page with check, selection, pagination, and remove actions.
- Recommendations and evaluation workflow.
- Track analysis modal opened from the tablet icon on a track card.

Do not open a live SQLite database through a network share while the server is
running. Create a snapshot first; see [docs/operations.md](docs/operations.md).

## Configuration

Defaults used by `run_app.sh`:

```text
DISCOCS_DB_PATH=data/app.db
DISCOCS_DATA_DIR=data
DISCOCS_MODEL_DIR=models
DISCOCS_INDEX_DIR=data
DISCOCS_DEFAULT_MODEL=discogs_multi
DISCOCS_AUDIO_LOADER=ffmpeg
DISCOCS_HOST=0.0.0.0
DISCOCS_PORT=8711
```

Override any value before launching:

```bash
DISCOCS_PORT=9000 DISCOCS_DB_PATH=data/test.db ./run_app.sh
```

Supported model aliases:

```text
discogs_multi -> discogs_multi_embeddings-effnet-bs64-1.pb
discogs_track -> discogs_track_embeddings-effnet-bs64-1.pb
discogs_release -> discogs_release_embeddings-effnet-bs64-1.pb
discogs_label -> discogs_label_embeddings-effnet-bs64-1.pb
muq_mulan -> OpenMuQ/MuQ-MuLan-large cached in models/muq
```

MuQ-MuLan settings:

```text
DISCOCS_MUQ_MODEL_NAME=OpenMuQ/MuQ-MuLan-large
DISCOCS_MUQ_CACHE_DIR=models/muq
DISCOCS_MUQ_DEVICE=auto
```

Runtime artifacts are intentionally ignored by git:

```text
data/
models/*.pb
models/*.json
models/*.onnx
models/muq/
eval/results/
```

## Benchmarks

Run the embedding benchmark matrix on `probe_tracks.txt`:

```bash
python scripts/benchmark_matrix.py \
  --audio-list probe_tracks.txt \
  --model discogs_multi \
  --loader ffmpeg \
  --limit 48 \
  --warmup 2 \
  --out-dir eval/results/matrix
```

Run only selected presets:

```bash
python scripts/benchmark_matrix.py \
  --audio-list probe_tracks.txt \
  --model discogs_multi \
  --loader ffmpeg \
  --limit 48 \
  --warmup 2 \
  --out-dir eval/results/matrix-selected \
  --preset workers4-tf4 \
  --preset workers6-tf3
```

Read the summary:

```bash
cat eval/results/matrix/matrix-summary.txt
```

## Docker Fallback

Build:

```bash
docker build -t discocs .
```

Run API:

```bash
docker run --rm \
  -p 8711:8711 \
  -v "$PWD/data:/app/data" \
  -v "$PWD/models:/app/models" \
  -v "/path/to/music:/music:ro" \
  discocs
```
