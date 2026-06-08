# discocs Instant Mix Navidrome Plugin

Navidrome plugin that replaces the standard Instant Mix similar-track source
with discocs recommendations.

The plugin implements Navidrome's `MetadataAgent` similar-song provider. When
Navidrome requests an Instant Mix for a seed track, the plugin calls discocs,
receives Navidrome song IDs, and returns them to Navidrome.

Full documentation:

```text
docs/navidrome-plugin.md
```

## Build

Requirements:

- Go
- TinyGo
- PowerShell

Build the `.ndp` package:

```powershell
.\build.ps1
```

Output:

```text
dist/discocs.ndp
```

## Install

Copy `dist/discocs.ndp` into Navidrome's plugin directory and enable plugins in
Navidrome:

```text
ND_PLUGINS_ENABLED=true
```

Add `discocs` to Navidrome metadata agents:

```text
ND_AGENTS=discocs,audiomuseai,lastfm,spotify,deezer
```

Enable the plugin in the Navidrome UI and set `discocsUrl` to the base URL that
is reachable from the Navidrome plugin runtime.

## Runtime

The plugin calls:

```text
GET /navidrome/similar?item_id=<navidrome_song_id>&count=<n>&model=<model>
```

Optional debug events are posted to:

```text
POST /navidrome/plugin-event
```

When `debugPluginEvents` is enabled, plugin activity is written by discocs to:

```text
data/logs/navidrome_plugin.log
```
