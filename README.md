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
- Audio features for BPM, key/scale, loudness, and dynamics.
- Lost-file tracking for scanned tracks whose audio files disappeared.
- Per-track analysis inspector in the web UI.

See [docs/analysis-pipeline.md](docs/analysis-pipeline.md) and
[docs/operations.md](docs/operations.md) for details.

## Quick Start

Use a Linux server for real embedding extraction.

Create and install the environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,essentia]"
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
  -d '{"model":"discogs_multi","limit":500,"workers":4,"tf_threads":4}'
```

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
discogs_label -> discogs_label_embeddings-effnet-bs64-1.pb
```

Runtime artifacts are intentionally ignored by git:

```text
data/
models/*.pb
models/*.json
models/*.onnx
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
