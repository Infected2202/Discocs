# Navidrome Instant Mix Plugin

> **Known breaking change:** the discocs backend moved all REST routes under
> `/api/v1/*` (see `docs/architecture.md`, "API Routing") to stop bare backend
> paths from colliding with the new web UI's client-side routes
> (`/artists/:id`, `/releases/:id`, `/settings`). The plugin (Go, compiled
> into `plugins/navidrome-instant-mix/main.go`) still calls the old bare
> `/navidrome/similar` and `/navidrome/plugin-event` paths and was
> **intentionally not updated** — it is planned for retirement. Until the
> plugin is rebuilt against `/api/v1/navidrome/similar` /
> `/api/v1/navidrome/plugin-event` (or retired), Instant Mix via the Navidrome
> plugin will 404 against a migrated discocs backend.

The discocs Navidrome plugin replaces Navidrome's standard Instant Mix similar
track source with recommendations from the local discocs embedding index.

The plugin package lives in:

```text
plugins/navidrome-instant-mix
```

The built Navidrome package is:

```text
plugins/navidrome-instant-mix/dist/discocs.ndp
```

## What It Does

Navidrome normally builds Instant Mix from its own similar-song logic. This
plugin registers a `MetadataAgent` named `discocs` and provides similar songs for
a seed track through discocs.

Runtime flow:

```text
Navidrome Instant Mix
-> discocs MetadataAgent plugin
-> GET /navidrome/similar on the discocs API
-> discocs recommender / HNSW index
-> Navidrome song IDs
-> Navidrome playlist result
```

The plugin works with Navidrome song IDs. It does not match tracks by tags or
filesystem paths during recommendation. The ID mapping is created by the
discocs Navidrome sync workflow.

## Requirements

Navidrome:

- plugin support enabled;
- `discocs` included in Navidrome's metadata agent list;
- plugin enabled in the Navidrome UI.

discocs:

- Navidrome sync completed;
- embeddings available for the selected model;
- HNSW index built for the selected model;
- API server running and reachable from Navidrome's plugin runtime.

Typical discocs preparation:

```bash
python -m app.cli navidrome-sync
python -m app.cli analyze --model discogs_multi
python -m app.cli build-index --model discogs_multi
uvicorn app.main:app --host 0.0.0.0 --port 8711
```

If the project is installed as console scripts, the equivalent commands are:

```bash
recs navidrome-sync
recs analyze --model discogs_multi
recs build-index --model discogs_multi
uvicorn app.main:app --host 0.0.0.0 --port 8711
```

## Background play-state refresh

`navidrome-sync` is a full catalog sync: it paginates the whole library, imports
new tracks, and (optionally) marks missing ones stale. It is too heavy to run
every minute, so it stays manual / occasional.

For keeping **play history fresh** (the "Recently Played" shelf and Flow's
recently-played exclusion), the API server runs a lightweight delta refresh
inside its maintenance loop. It calls `getAlbumList2?type=recent`, expands those
albums to their songs, and ratchets `play_count` / `last_played_at` for tracks
that already map to Navidrome — it never imports new tracks or marks anything
stale.

Configuration (`NavidromeSettings`, via env or `data/settings.json`):

| Setting | Env var | Default | Meaning |
|---|---|---|---|
| `play_state_refresh_seconds` | `DISCOCS_NAVIDROME_PLAY_STATE_REFRESH_SECONDS` | `60` | Min seconds between refreshes; `0` disables it. |
| `play_state_refresh_albums` | `DISCOCS_NAVIDROME_PLAY_STATE_REFRESH_ALBUMS` | `25` | How many recently played albums to scan each tick. |

The maintenance loop ticks every 15s and throttles the refresh to the configured
interval, so you no longer need to run `navidrome-sync` by hand just to update
play history.

## Build

Build from the plugin directory:

```powershell
cd plugins\navidrome-instant-mix
.\build.ps1
```

The script creates:

```text
dist/discocs.ndp
```

The plugin is compiled with TinyGo for WASI. The package contains:

```text
manifest.json
plugin.wasm
```

## Navidrome Configuration

Enable plugins:

```text
ND_PLUGINS_ENABLED=true
```

Add `discocs` to the metadata agent chain:

```text
ND_AGENTS=discocs,audiomuseai,lastfm,spotify,deezer
```

or in `navidrome.toml`:

```toml
Agents = "discocs,audiomuseai,lastfm,spotify,deezer"
```

Order matters. Put `discocs` first when Instant Mix should prefer discocs
recommendations before other metadata agents.

Useful plugin logging during setup:

```text
ND_PLUGINS_LOGLEVEL=debug
```

For deep Navidrome agent-chain debugging, temporarily use:

```text
ND_LOGLEVEL=trace
ND_PLUGINS_LOGLEVEL=trace
```

Trace logs are noisy and should be turned off after diagnosis.

## Plugin Settings

Configure the plugin in the Navidrome UI.

`discocsUrl`

Base URL for the discocs API as seen from the Navidrome plugin runtime.

Examples:

```text
http://192.168.1.41:8711
http://host.docker.internal:8711
http://discocs:8711
```

Use the URL that is routable from Navidrome, not necessarily the URL that works
from a browser.

`model`

discocs model alias used for recommendations.

Default:

```text
discogs_multi
```

Supported aliases are the same as the discocs API/index configuration, for
example:

```text
discogs_multi
discogs_track
discogs_label
```

`count`

Fallback result count used when Navidrome does not provide a count. Navidrome's
Instant Mix currently requests its own count, commonly `100`, and the plugin
honors that request.

`maxPerArtist`

Maximum number of returned recommendations per artist.

`excludeSameAlbum`

When enabled, discocs excludes tracks from the same album as the seed when
building recommendations.

`timeoutSeconds`

HTTP timeout for plugin calls to discocs.

`debugPluginEvents`

When enabled, the plugin posts diagnostic lifecycle and request events back to
discocs. This is useful while validating the integration.

## discocs API Contract

The plugin calls:

```text
GET /navidrome/similar
```

Query parameters:

```text
item_id=<navidrome_song_id>
count=<requested_count>
model=<model_alias>
max_per_artist=<n>
exclude_same_album=<true|false>
```

Expected response shape:

```json
{
  "item_id": "seed_navidrome_id",
  "model": "discogs_multi",
  "results": [
    {
      "item_id": "recommended_navidrome_id",
      "score": 0.93
    }
  ]
}
```

Only Navidrome song IDs with a known discocs mapping can be returned to
Navidrome.

When `debugPluginEvents` is enabled, the plugin also posts:

```text
POST /navidrome/plugin-event
```

These events are diagnostic only and are not required for recommendations.

## Logs

Navidrome logs plugin lifecycle and handler messages in the main Navidrome log.
Useful lines:

```text
Loaded plugin capabilities="[Lifecycle MetadataAgent]" plugin=discocs
[discocs] plugin initialized ...
[discocs] GetSimilarSongsByTrack ...
```

discocs writes plugin-specific API and debug-event logs to:

```text
data/logs/navidrome_plugin.log
```

Healthy Instant Mix call sequence:

```text
plugin_event event=similar_called ...
plugin_event event=api_request ...
api_request item_id=...
api_completed item_id=... results=...
plugin_event event=api_response status=200 ...
plugin_event event=similar_returned ...
```

If `debugPluginEvents` is disabled, the `plugin_event` lines are absent, but
`api_request` and `api_completed` should still appear when Navidrome calls the
plugin.

## Verification

After installing and enabling the plugin:

1. Open Navidrome.
2. Start Instant Mix from a track that has a discocs embedding.
3. Check `data/logs/navidrome_plugin.log`.
4. Confirm an `api_completed` line with `results` greater than zero.

A successful request looks like:

```text
api_completed item_id=<seed_id> track_id=<discocs_track_id> model=discogs_multi results=100
```

To verify through the Subsonic API, call Navidrome directly:

```bash
curl "http://<navidrome-host>:4533/rest/getSimilarSongs2.view?u=<user>&p=<pass>&v=1.16.1&c=discocs&id=<navidrome_song_id>&count=10&f=json"
```

The result should correspond to discocs recommendations for the same seed.

## Troubleshooting

Plugin is loaded but Instant Mix is unchanged:

- confirm `Loaded plugin capabilities="[Lifecycle MetadataAgent]"`;
- confirm `discocs` is present in `Agents` / `ND_AGENTS`;
- put `discocs` first in the agent list;
- enable `debugPluginEvents` and check for `similar_called`;
- temporarily enable `ND_LOGLEVEL=trace` to inspect Navidrome agent selection.

Plugin calls discocs but returns no recommendations:

- confirm the seed track exists in the Navidrome sync mapping;
- confirm the selected model has an embedding for the seed track;
- rebuild the index for the selected model;
- check `data/logs/navidrome_plugin.log` for `api_completed` and result count.

Plugin request times out:

- confirm `discocsUrl` is reachable from the Navidrome plugin runtime;
- confirm the discocs API is listening on the expected host and port;
- confirm `/health` returns `{"status":"ok"}` from the same runtime context.

## Versioning

When changing plugin behavior, bump `version` in:

```text
plugins/navidrome-instant-mix/manifest.json
```

Then rebuild `dist/discocs.ndp` and reinstall it into Navidrome.
