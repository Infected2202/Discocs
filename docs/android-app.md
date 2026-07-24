# Android app (Capacitor)

A thin [Capacitor](https://capacitorjs.com/) wrapper around the existing web
UI (`ui/`), shipped as a sideloadable APK. The only reason this exists is to
get a real Android **foreground service** entitlement, so playback survives
being backgrounded — something no installed-PWA/standalone web app gets on
Android. Everything else (rendering, playback, state) is the same web app
running inside a WebView; there is no forked UI or playback code between
browser and app.

## Why hostname-match instead of a configurable backend URL

`ui/capacitor.config.ts` sets:

```ts
server: {
  hostname: new URL(process.env.DISCOCS_PUBLIC_URL ?? "https://localhost").hostname,
  androidScheme: "https",
},
```

This makes the WebView's origin **equal the production origin** — the app
loads `https://<your-domain>/` exactly like a browser tab would, rather than
pointing at an arbitrary/configurable backend URL. This is the documented
Capacitor recipe for "bundled app talking to a remote cookie-authenticated
API".

The payoff: the app needs zero backend changes. The existing session cookie
(`SameSite=Lax`, set by `app/api/auth.py`) is scoped to the production
domain and works unmodified, and the existing same-origin `apiFetch()`
(`credentials: "same-origin"`, `ui/src/api/client.ts`) keeps working exactly
as it does on the web — no CORS configuration, no separate native API base
URL, no `Preferences`-backed "set your server IP" screen. An earlier,
half-wired scaffold (`getBackendUrl`/`setBackendUrl` in `runtimeConfig.ts`)
did exactly that and has been removed — it's unnecessary once hostname-match
is in place, and its `192.168.1.146:8711` default bypassed nginx/the
production domain entirely.

The tradeoff: the production domain is **baked into the APK at build time**
(`DISCOCS_PUBLIC_URL` is a plain `environment{}` value in the root
`Jenkinsfile`, not a runtime setting). Changing the domain means rebuilding
and redistributing the APK. The JS/CSS/HTML bundle itself is not subject to
this limit — see OTA updates below.

## Background playback — shell-level only

`ui/src/lib/nativeInit.ts` starts an Android foreground service once at app
launch (`@capawesome-team/capacitor-android-foreground-service`) and never
explicitly stops it — it dies with the app process, same as any music app's
persistent playback notification. `AndroidManifest.xml` declares the
`FOREGROUND_SERVICE` / `FOREGROUND_SERVICE_MEDIA_PLAYBACK` / `WAKE_LOCK`
permissions and the plugin's service/receiver, with
`android:foregroundServiceType="mediaPlayback"` on the service tag (the
plugin's current `ServiceType` enum only exposes `Location`/`Microphone`, so
the JS call omits `serviceType` and the OS falls back to the manifest's
declared type).

This is the entire native-specific surface. **No playback code changes**:
`ui/src/engine/playback/PlayerPlaybackFacade.ts`, `ui/src/store/playerStore.ts`
and the rest of the `<audio>` + MediaSession implementation described in
`docs/ui-player.md` are completely untouched. The foreground service just
tells Android "don't kill this process" — it does not participate in audio
routing at all. See `docs/ui-player.md`'s "Ordinary mode" section for why a
plain `<audio>` element (not routed through Web Audio) is what actually
keeps playing in the background.

## OTA updates

`@capgo/capacitor-updater` is configured self-hosted (`autoUpdate: false` in
`capacitor.config.ts` — no Capgo cloud account, no automatic background
polling). `nativeInit.ts` does its own minimal check on every launch:

1. `CapacitorUpdater.notifyAppReady()` — must be called every launch or the
   plugin auto-rolls-back to the previous bundle.
2. `fetch("/downloads/update-manifest.json")` — same-origin, no special
   native fetch needed (thanks to hostname-match) — returns
   `{"version": "<git-sha>", "url": "/downloads/discocs-web-<git-sha>.zip"}`.
3. Compare against `CapacitorUpdater.current().bundle.version`. If equal,
   stop.
4. Otherwise `CapacitorUpdater.download({ url, version })` then
   `CapacitorUpdater.set({ id })` — applies immediately and reloads the
   WebView.

Everything is wrapped in try/catch: offline, first launch, or a missing
manifest silently keeps the bundled version, never blocks startup.

The manifest and zip are produced by `deploy/ci/Dockerfile.android`, built on
every push as part of the root `Jenkinsfile`'s `frontend` branch (see
`docs/cicd.md`'s "Android-приложение" section), and published as static files
under `/downloads/` by the frontend nginx image — no dedicated update server,
no auth gate.

## Manual sideload install

The APK is not distributed through the Play Store — install it directly:

1. On the phone, navigate to `https://<your-domain>/downloads/discocs.apk`.
2. Android will warn about installing from an unknown source — enable
   "install unknown apps" for the browser when prompted.
3. Install. Subsequent JS/CSS/HTML updates arrive automatically via OTA
   (above); a new APK download is only needed for native-level changes
   (new permissions, plugin upgrades, etc.).

## Open items

- **No release signing keystore.** `deploy/ci/Dockerfile.android` builds
  `assembleDebug` only — a debug-signed APK, fine for personal sideload but
  not for wider distribution. Add a release keystore as a Jenkins "Secret
  file" credential and switch to `assembleRelease` if that's ever needed.
- iOS/macOS is out of scope — no Apple build runner available; this is an
  Android-only wrapper.
