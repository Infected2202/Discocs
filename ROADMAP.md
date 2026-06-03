# ROADMAP.md

## Immediate Goal

Get from an empty catalog to the first believable similar-track result:

```text
inspect-folder -> scan -> extract-one -> analyze small batch -> build-index -> similar
```

This is the main MVP proof point. Everything else should support evaluating
whether Discogs-EffNet embeddings produce useful recommendations for this music
library.

## 1. First Similar Track

Purpose: prove the full pipeline works on real files.

Tasks:

- Fix Docker/Linux access to the music folder.
- Run `recs inspect-folder /music` and confirm `supported_audio_files > 0`.
- Run `recs scan /music`.
- Run `recs extract-one /music/...opus` on one known file.
- Run `recs analyze --limit 20`.
- Run `recs build-index`.
- Run `recs similar --track-id ID --k 20`.

Done when:

- SQLite contains scanned tracks.
- At least 20 embeddings are stored.
- HNSW index is built.
- CLI returns plausible similar tracks.

## 2. Batch Analyze Observability

Purpose: make large-library analysis debuggable and resumable.

Tasks:

- Print per-track runtime.
- Print counters: done, failed, skipped, remaining.
- Show rough ETA after the first few tracks.
- Persist failed analysis attempts with track id, path, model, error, timestamp.
- Add `recs failed` to list failed tracks.
- Add `recs retry-failed` for failed embeddings.

Done when:

- A long `analyze` run can be interrupted and resumed.
- Bad files are visible without reading terminal scrollback.
- Re-running failed items does not require manual SQL.

## 3. Better CLI Discovery

Purpose: make manual testing fast without opening the web UI.

Tasks:

- Add `recs search QUERY`.
- Add `recs tracks --limit N`.
- Add `recs similar --query QUERY --k N`.
- Print track ids consistently in search and similar output.
- Support `--include-path` for debugging duplicate files.

Done when:

- A seed track can be found and queried from CLI by artist/title text.
- CLI testing does not require knowing track ids in advance.

## 4. Extraction Robustness

Purpose: handle real-world messy audio libraries.

Tasks:

- Skip tracks shorter than a configurable minimum duration.
- Add `recs analyze --force` to recompute existing embeddings.
- Add `recs analyze --since-id ID` or a batch range option.
- Keep Essentia imports lazy.
- Continue on decode/model errors and record failure details.

Done when:

- One broken file cannot stop an analysis run.
- Recomputing a selected subset is straightforward.

## 5. Web Test UI Improvements

Purpose: turn the current minimal page into a useful evaluation surface.

Tasks:

- Show stats: tracks, embeddings, index status.
- Show track id, artist, title, album, duration, and path.
- Add controls for `k`, `max_per_artist`, and `exclude_same_album`.
- Add a copy-path button.
- Persist `good / okay / bad` feedback.
- Add a simple feedback summary by model.

Done when:

- Similarity quality can be evaluated comfortably from the browser.
- Feedback can be used to compare models.

## 6. A/B Discogs-EffNet Models

Purpose: find the best embedding model for this collection.

Candidate models:

- `discogs_multi`
- `discogs_track`
- `discogs_label`

Tasks:

- Store and analyze embeddings for each model alias.
- Build a separate HNSW index per model.
- Query the same seed tracks across models.
- Compare feedback scores by model.
- Add export of evaluation results to Markdown or CSV.

Done when:

- There is enough feedback to choose the default model with evidence.

## 7. Docker and Linux Ops Polish

Purpose: reduce setup friction.

Tasks:

- Add `.env.example` with `MUSIC_PATH`, `DATA_PATH`, and `MODEL_PATH`.
- Document Linux mount examples for local folders and network shares.
- Add compose examples for mounted local folders and network shares.
- Add `docker compose run recs recs ...` examples.

Done when:

- A fresh Linux server setup can run scan/analyze without rediscovering mount details.

## 8. Playlist Mode

Purpose: move from similar tracks to useful listening sessions.

Tasks:

- Add `recs playlist --seed-id ID --length N`.
- Add artist caps and diversity controls.
- Add optional multi-seed recommendations.
- Add M3U export.
- Later add BPM/energy continuity if metadata becomes available.

Done when:

- The system can generate a listenable playlist, not just nearest neighbors.

## Deferred

Do not prioritize these until recommendation quality is validated:

- Navidrome integration
- PostgreSQL
- Redis or background worker infrastructure
- FAISS
- GPU or ONNX migration
- Auth and multi-user support
