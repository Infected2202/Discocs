# Player UI

The DJ surface renders detailed and overview waveform artifacts with PixiJS.
The renderer loads Pixi's static CSP compatibility implementations before
initialization, so the production policy does not need to allow `unsafe-eval`.
Both views share decoded typed arrays, follow the authoritative deck playhead,
and seek the physical deck selected by the pointer. Detailed waveforms behave
as a tape under a fixed centre playhead, including empty space before zero and
after duration. The detailed waveform stays fully bright around its fixed
playhead, while the compact whole-track overview darkens played audio to the
left of its moving cursor. Uniform beat lines remain above the detailed
waveform; bar/downbeat emphasis is intentionally deferred until the analyzer
produces validated bar indices. One shared set of hover controls selects an 8,
16, 30 or 60 second window for both detailed deck waveforms and resets both to
the 16 second default. Overview cursors and
detailed tape both use captured pointer drag for mouse, pen and touch. Missing
or stale analysis is non-blocking; see
[`timeline-waveforms.md`](timeline-waveforms.md).
The compact whole-track overview omits the beat grid, preventing dense tracks
from turning the waveform into a barcode; beat-to-transient alignment is
inspected in the detailed view.

The closed DJ surface is not kept behind the page as an off-screen React tree:
it is fully unmounted, including player subscriptions, queue rows, timeline
hooks, canvases and GPU resources. While open, detailed and overview views use
one timeline lifecycle per physical deck. Queued/running analysis polls only
the lightweight status endpoint; manifest and payload are fetched once when the
artifact becomes ready. Each Pixi surface has an explicitly stopped ticker and
renders one frame only for changed input or size. Coarse-pointer devices use
CSS-pixel canvas resolution and disable panel backdrop blur.

While the DJ surface is open, one shared 30 FPS deck clock updates only the
waveform/time leaves; the complete workspace does not subscribe to transport
ticks. A separate 20 FPS analyser clock updates only the three level meters.
Overview waveforms keep their static geometry and redraw only the playhead when
time changes.

Virtual playlist rows and the DJ decks share one application-level drag
context (`DjTrackDragProvider`), but the deck dock and each row's DJ drag
source are gated on `djSurfaceOpen`: rows in non-reorderable lists (liked
tracks, mix/plain playlist views) get no pointer/touch drag listeners at all
while the DJ surface is closed, and the A/B deck dock never mounts unless it
is. This keeps the DJ feature fully isolated until the user opens it via its
own button — dragging can't reveal deck UI or attach touch listeners that
would otherwise interfere with ordinary list scrolling on mobile. Reorderable
lists (playlist edit mode) keep drag listeners for reordering regardless, but
the deck dock still only reveals once the DJ surface is open. Once open,
starting a playlist drag reveals the compact A/B deck dock above the player;
the on-air deck is locked. Dropping on the free deck moves or adds that track
to the next canonical queue position and prepares it without starting
playback or changing the program role. The full deck panels use the same drop
payload.

The primary player UI lives in `ui/src/components/player/`.

## Playback modes: ordinary vs DJ engine (two independent axes)

Playback has two independent axes that must not be conflated:

1. **Panel visibility** — `uiStore.djSurfaceOpen`. Purely presentational. Opening
   or collapsing the DJ panel never starts or stops audio routing. The panel can
   be collapsed while the engine keeps mixing.
2. **DJ engine** — `playerStore.djEngineActive`, backed by
   `PlayerPlaybackFacade.graphActive`. Controls whether audio flows through the
   Web Audio mixer graph.

### Ordinary mode (`graphActive === false`) — default

Playback runs through a plain `<audio>` element that is **never** routed into an
`AudioContext`. `load()` skips `routeProgramElement`, `play()` skips
`ensureReady()`, and `prefetch()` only caches the next track's Blob (no graph
deck). This is deliberate: on mobile (iOS/WebKit, aggressive WebViews such as
Telegram) a `MediaElementAudioSourceNode` stalls together with the suspended
`AudioContext` when the tab is backgrounded, which is what previously froze
playback after one or two tracks. A bare `<audio>` element keeps playing in the
background through the OS media pipeline.

Ordinary mode keeps **one** `<audio>` element for the whole session and only
swaps its `src` between tracks (`load()` → `openSource`), like other web
players. It used to create a fresh element per track (pausing and emptying the
old one) to free Chrome's native buffers; on Android Chrome that tore down the
page's media session on every track change — the media notification vanished
when the second track started, the browser lost its background foreground
service, and Android cut the tab's network: only already-downloaded tracks
(the current one and the prefetched next) played on, then playback stopped.
Swapping the source still releases the old resource (the media element load
algorithm does). The element is not paused before the swap, and a `pause`
event arriving while the element is already playing again is ignored as stale
from the previous source. DJ mode still gives every track its own element:
one routed through `createMediaElementSource` can never leave the graph, and
leaving DJ mode creates a fresh unrouted element.

Auto-DJ / full mixing in the background
is a platform impossibility on mobile and is deferred to a future native app;
the browser only provides ordinary background playback plus manual mixing while
in the foreground.

Background reliability is reinforced by:

- `navigator.mediaSession.playbackState` mirrored from the transport state
  (`loading` is reported as `playing` so the lock screen does not flicker during
  auto-advance), and `setPositionState` updated on every `timeupdate`.
- `handleTrackEnded` fires the `completed` telemetry **fire-and-forget** and
  advances immediately. A backgrounded tab throttles `fetch`; awaiting the POST
  is what used to block the next track from starting. `skipNext`'s own `skipped`
  telemetry is fire-and-forget for the same reason.
- `jumpToQueueItem` (the shared step behind track-ended advance, skip
  next/previous, and autoplay jump) is **optimistic** when the target queue item
  is already known locally: it starts playback immediately from local state and
  syncs the server's queue pointer (`PATCH .../queue` `operation: "jump"`) in the
  background — retried once, logged (not surfaced as an error) on final failure,
  and dropped if a newer jump supersedes it before the response arrives. Only a
  target item not yet known locally falls back to blocking on the PATCH. This
  is what previously stalled every track transition behind a throttled
  background request even though the next track's audio was already available.
  Until that jump is acknowledged, `applyEnvelope` keeps the local pointer on
  the jumped-to item: any envelope fetched in the meantime (typically
  `refreshQueue` after the skip's autoplay refill) still carries the server's
  old pointer and would otherwise flip cover, title and the queue highlight back
  to the previous track for a few seconds. A canonical pointer move — prepared
  handover or the blocking fallback jump — abandons the pending jump and
  cancels its retry, so it can't drag the server pointer back later.
- A `visibilitychange` → foreground reconcile: if the store believes playback is
  `playing` but the element is actually paused, it resumes it, or drives
  `handleTrackEnded` when the current track ended in the background without the
  auto-advance having fired.
- **Dead source recovery.** A phone can put the browser's network to sleep in
  the background while audio keeps playing: the current track and the
  prefetched next Blob play out, then the following track's request never
  reaches the server and its element sits without data (or errors). `play()` on
  that element only resets the shown position. `PlayerPlaybackFacade.sourceFailed`
  reports such a source — a media error, or `readyState` still `HAVE_NOTHING`
  `SOURCE_STALL_MS` (10 s) after the source was opened; never in DJ mode — and
  the store reloads the track (`playTrack` at the position reached, from the
  top if nothing played) instead of resuming the dead element: on return to
  the foreground while playback was wanted (`playing`/`loading`/`error`), and on
  Play while the button shows Play. `playTrack` also switches the shown time and
  duration to the new track at once (0 and the API duration), so a source that
  never gets data no longer leaves the previous track's end under the new
  title. `ArtworkImage` retries an image that failed to load once the page is
  visible again or goes `online`.
- When an autoplay refill fills a queue that had run out (`handleTrackEnded`
  set `playbackState` to `"idle"` because there was no next item yet),
  `scheduleAutoplayRefill` resumes playback into the newly generated item once
  the refill's queue refresh lands — guarded so it only fires if playback is
  still idle, still the same session, and not mid-DJ-mixing.
- `PlayerPlaybackFacade.prefetch()` dedupes on `trackId + profileKey` alone,
  not `queueItemId`. A queue resync (e.g. the background PATCH sync after an
  optimistic `jumpToQueueItem`) can hand the same still-upcoming track a fresh
  `queue_item_id`; treating that as a new prefetch target used to discard an
  already-downloaded or in-flight Blob and refetch the identical audio from
  scratch — doubling network usage and, combined with a flaky proxy hop,
  doubling the odds of hitting a transfer error on the same track.
- `setMediaSession` calls are deduped by `applyMediaSession` (keyed on
  `trackId` + resolved artwork URL). Reassigning
  `navigator.mediaSession.metadata` re-fetches the lock-screen artwork every
  time even when the URL is unchanged, and three independent call sites (DJ
  handover, ordinary track start, session restore) can end up applying the
  same track's metadata back to back.

For the native Android app (Capacitor wrapper, see `docs/android-app.md`),
background survival beyond what a browser tab allows comes from a shell-level
Android foreground service started once at app launch
(`ui/src/lib/nativeInit.ts`). It is not a change to the playback design
described in this section — the same plain `<audio>` element above is what
actually keeps playing; the foreground service only prevents the OS from
killing the app process while backgrounded.

### DJ mode (`graphActive === true`) — activated by explicit gesture

`playerStore.activateDj()` → `PlayerPlaybackFacade.activateDjMode()`. Activation
is a one-time hand-off: the live `<audio>` element is routed into the graph
(`createMediaElementSource`) at its current position, and, when the track is
Signalsmith-eligible, upgraded to a stretch deck source starting at that same
playhead. Any cached prefetch Blob is materialized into the incoming deck
(`ensurePreparedDeckFromCache`). `createMediaElementSource` is irreversible, so
`deactivateDj()` builds a **fresh, unrouted** `<audio>` element at the current
position and calls `runtime.destroy()` to tear the graph down. A brief audible
gap on activate/deactivate is accepted — both are explicit user actions.

While the DJ engine is active, a deck finishing playback stops at its end
position and the transport goes to `paused`: there is **no** auto-advance, since
mixing is manual (`handleTrackEnded` returns early on `djEngineActive`).

The DJ panel shows a single icon-only activate/deactivate button
(`toggle-dj-engine`). While the engine is inactive, the mixer/deck sections are
not rendered at all — their controls require a live `AudioContext` — until the
button is pressed.

## Shuffle: порядок очереди, а не флаг

`mode` и `shuffle_enabled` долго были колонками, которые никто не читал.
`create_playback_session` записывал `mode="shuffle"` и наполнял очередь в
исходном порядке, а PATCH `shuffle_enabled` менял один столбец. В итоге
«Перемешать» запускало первый трек списка по порядку, и единственным видимым
эффектом была подсвеченная иконка в плеере.

Порядок теперь задаётся там, где строится очередь:

- `create_playback_session` перемешивает начальный список при `mode="shuffle"`
  (или `shuffle_enabled=True`). Первый элемент очереди — тот, что начинает
  играть, поэтому перемешанная очередь сама по себе даёт случайный первый трек.
- `queue_items.source_position` хранит индекс элемента в исходном порядке.
  `position` — это порядок воспроизведения, его перемешивание переписывает;
  `source_position` не трогается и нужен, чтобы выключение шафла вернуло список
  на место. У элементов, добавленных позже автоплеем, его нет — они остаются
  в конце в текущем порядке.
- `set_queue_shuffle(session_id, enabled=…)` переупорядочивает живую очередь.
  При включении переставляется только то, что ещё не играло: перемешивать
  историю значило бы переписать уже прослушанное, а сдвиг текущего элемента
  перезапустил бы трек. При выключении восстанавливается `source_position`.
  Индекс `(session_id, position)` уникален, поэтому позиции сначала уводятся
  в отрицательный диапазон, иначе апдейт строка-за-строкой столкнулся бы сам
  с собой.
- PATCH сессии вызывает переупорядочивание только когда флаг **изменился**.

Кнопки в заголовках коллекций (артист, релиз, плейлист, лайки) просят
перемешанную сессию сразу, а не патчат флаг после старта:

- `playSource(type, id, label, undefined, { shuffle: true })` — артист и релиз;
- `POST /api/v1/playlists/{id}/play?shuffle=true` и
  `POST /api/v1/playlists/likes/play?shuffle=true` — плейлист и лайки.

Патч задним числом был неверен вдвойне: он ничего не переупорядочивал, а
`playSource` вдобавок переносит `shuffle_enabled` из предыдущей сессии, так что
`toggleShuffle()` следом мог его и вовсе выключить. `setShuffle` остался для
переключателя в самом плеере, где смена флага и вызывает переупорядочивание.

## Compact player backdrop

When the current track has artwork, the compact player bar renders a decorative
ambient backdrop derived from that image:

- the requested artwork is capped at `320px`, which is sufficient for a heavily
  blurred 76px-high surface;
- a fixed blur, brightness adjustment, dark scrim, and artwork-derived accent
  glow make the artwork clearly visible while darker edges keep controls and
  metadata legible;
- the artwork keeps a slow transform animation while a React Bits-inspired WebGL
  plasma ribbon crosses the bar at `0.2x` speed;
- both ambient effects run while playback is active and freeze when playback is
  paused;
- the next `320px` background artwork is loaded and decoded first, then it
  crossfades with the current background over `960ms` without exposing a
  neutral card-colored frame;
- the compact metadata strip fades to zero before swapping tracks, then fades
  back in; the next cover is loaded and decoded before the fade begins so the
  neutral artwork fallback cannot flash between tracks;
- `prefers-reduced-motion: reduce` disables the decorative movement.

The plasma renderer uses `ogl`, does not react to the pointer, and follows the
current artwork accent, passed directly from palette extraction rather than read
back from transitioning CSS. Theme updates are still pushed explicitly so the
tint refreshes even while playback is paused. For a new artwork image, the
plasma layer stays hidden until the current track accent has been resolved, so
the first frame does not flash the previous track color or a transition-stage
color. After that first resolution, its WebGL canvas remains mounted between
tracks and only the color uniform changes, avoiding a blank frame during track
switches. That readiness check is bound to the exact artwork URL. The UI and
plasma share the same accent transition timing via `--track-accent-transition-*`
variables, so buttons, progress accents, and the plasma tint fade together. It
renders at `0.1x` speed, scale `30`, and 30% opacity. The background artwork
uses 30% opacity (70% transparency).
The full-screen plasma is active only during playback, pauses while the DJ
surface covers it, and uses reduced resolution and a 15 FPS cap on
coarse-pointer devices.

The backdrop is non-interactive and hidden from assistive technology. If artwork
is unavailable, the compact player keeps its normal card background.

## Session restore after a page reload

Mobile browsers silently discard background tabs under memory pressure and
reload the page on return, which used to reset the player. Restore is
two-layered (`ui/src/store/sessionPersistence.ts`):

- the playback **session id** persists in `localStorage`
  (`discocs.sessionId.v1`); `restoreSession` (called once from `AppShell`)
  refetches the queue and reloads the current track. The id is only cleared
  when the server answers 404 — network errors keep it;
- the playback **position** persists as
  `{sessionId, queueItemId, trackId, seconds}`
  (`discocs.playbackPosition.v1`), written throttled (~5s, trailing) from
  `timeupdate` and flushed immediately on `visibilitychange`→hidden /
  `pagehide`. On restore, if the persisted track matches the session's current
  queue item, the track is loaded with that start position
  (`load(..., startPositionSeconds)`) — the same mechanism as any start
  mid-track (see "Streaming, seek and next-track prefetch"): a native seek on
  `loadedmetadata` for raw/Blob sources (an immediate `currentTime` write is
  dropped while duration is still unknown), a `t` stream for a transcode.

`PlasmaFBM` destroys its WebGL context while the document is hidden and builds
a fresh canvas when it becomes visible. This cannot prohibit mobile browsers
from discarding a tab, but releases GPU memory that otherwise makes the tab a
more likely discard candidate.

Autoplay is intentionally not resumed — browsers block `play()` without a
user gesture after a reload.

## Presence reporting ("now playing" for other users)

The logged-in player tells Navidrome what it is doing so other discocs users
can see it (social features, [`docs/social.md`](social.md#присутствие-сейчас-слушает)).
The player itself never calls the API: `startPresenceReporting`
(`ui/src/store/presenceReporter.ts`, started once from `AppShell`, i.e. only
behind `RequireAuth`) subscribes to `playerStore` and feeds snapshots to
`PresenceTracker` (`ui/src/lib/presence.ts`), which turns them into
`POST /api/v1/playback/presence` reports:

| Player change | Report |
|---|---|
| a track starts playing (new queue item, handover, first play after restore) | `starting` (position 0 when the queue pointer moved while the engine still played the old track) |
| pause | `paused` |
| resume | `playing` |
| `seek()` (bumps `playerStore.seekGeneration`) | `playing` (or `paused`) with the seek target, even if the state did not change |
| queue end (`idle`), error, logout | `stopped` |
| `pagehide` | `stopped` via `navigator.sendBeacon` (JSON Blob, same-origin cookie; keepalive `fetch` if the beacon is refused or on native builds) |

`loading` (track load, mid-track buffering) and a transcoded seek reload
waiting for data (`seekBuffering`) are transients and send nothing. Identical
consecutive states are deduplicated (`starting`→`playing` of the same track is
not a change). Reports are fire-and-forget: failures are swallowed, a 403 (no
user behind the session) turns reporting off for the page, nothing is ever
awaited by the player. Logout awaits one `stopped` report *before* revoking the
session. The guest `SharedPlayerPage` has its own `<audio>`, never mounts
`AppShell` and reports nothing. Repeat-one restarts the same track silently
(no new `starting`).

## Streaming, seek and next-track prefetch

The current track plays as a plain progressive stream in a bare `<audio>`
(`preload="auto"`, outside any `AudioContext` — mobile background/lock-screen
playback depends on that, see "Background reliability" above). Its bytes are
fetched exactly once, by the browser; there is no parallel full-track
download of the current track and no swap to a `blob:` source mid-play.

**Seek** (`PlayerPlaybackFacade.seek` / `seekToSeconds`) depends on what the
source can do:

- *Raw profile* (`format=raw`, Navidrome answers `Range` with `206`) and local
  `blob:` sources: `currentTime` is written directly and the browser issues
  the range request itself. No pause, no wait, no fetch from our code. Only a
  still-unknown duration defers the write to `loadedmetadata` (it would be
  dropped otherwise).
- *Transcoded profiles* (`format=mp3&maxBitRate`, `Accept-Ranges: none`, Range
  ignored): a native seek is only used when the target lies inside both
  `el.buffered` and `el.seekable` of the current stream. Chrome reports a
  non-Range transcode with a known length as seekable `[0, 0]`, and writing
  `currentTime` outside `seekable` snaps the playhead to 0 (the old "track
  restarts" bug) — so in practice a transcoded seek is **server-side**: the
  same element is reloaded with `/api/v1/tracks/{id}/audio?…&t=<whole
  seconds>` (Navidrome `timeOffset`, see `docs/architecture.md`). The facade
  keeps `streamOffset`; the reported position is `streamOffset +
  el.currentTime`, the duration is the track's API metadata (not
  `el.duration`, which for an offset stream is the remainder), and buffered
  ranges are shifted by the offset before they reach the seek bar. Seeking
  inside the already buffered part of an offset stream stays native where
  the browser reports it seekable; seeking before the offset reloads again.
  If an offset stream fails (the backend answers `400` for `t` on a track it
  serves from a local file, or an older backend), the plain source is reopened
  once and the position is applied natively.

`playerStore.seekBuffering` is `true` only while such a transcoded reload is
waiting for its first data (cleared on `playing`, or `canplay` when paused);
the seek bar renders a soft pulsing dot at the seek target meanwhile. The
reload's own interruption of a playing element is not reported as a pause.
There is no pause-until-full-download anymore.

**Starting mid-track** uses the same mechanism: `load(url, …, knownDuration,
startPositionSeconds)` (raw/Blob: native seek on `loadedmetadata` before
anything is audible; transcode: `t`). `playerStore.playTrack(id, {
startPositionSeconds })` and `playFromEnvelope(envelope, preferredTrackId, {
startPositionSeconds })` expose it to callers (session restore, the planned
"listen along"); the start position is also what gets persisted as the
playback position. `resumeAtSeconds` remains as the indicator-free seek.

The seek bar renders every browser `TimeRanges` segment separately, so a gap
created by an unbuffered seek is not shown as downloaded. Dragging uses Pointer
Events and pointer capture, giving mouse, touch, and pen the same commit path;
`pointercancel` never seeks to a bogus fallback position. The commit uses the
last pointerdown/pointermove value rather than `pointerup.clientX`, because
mobile pointer capture can report a zero release coordinate.
`playerStore.seek()`'s optimistic `currentTime` write is preceded by
`throttledSetTime.cancel()`: without it, a trailing throttled `timeupdate`
already scheduled from just before the seek (leading+trailing throttle, up to
~250ms late) could still fire afterwards and snap the seek bar back to the
stale pre-seek position.

**Next-track prefetch** starts once the current track's buffering has
*settled* — `onBufferingSettled(trackId, profileKey)`, recorded by
`playerStore` as `bufferSettledSource` — fired once per track by the first of:

1. the buffered range under the playhead reaches the end of the track;
2. a `suspend` event with `networkState === NETWORK_IDLE`,
   `readyState >= HAVE_FUTURE_DATA` and at least `min(60 s, remaining)`
   buffered ahead — the browser decided it has enough (mobile browsers stop
   around 80–90%);
3. safety nets: the buffered end passes 80% of the track, or fewer than 45 s
   remain to play.

All of it is measured on the track's timeline, so an offset stream counts
from its offset. A local Blob (a consumed prefetch, a DJ handover) and a
decoded stretch deck settle immediately and show a full buffer bar; a
network stream keeps showing its real ranges — settling is a scheduling
signal, not "100% downloaded".

`playerStore` then fetches the next queue item as a `Blob` (`fetch` with
`priority: "low"`, retried once on failure). A completed Blob is consumed
through a local `blob:` URL at transition time; an unfinished or stale
prefetch is aborted and playback falls back immediately to the normal
`/api/v1/tracks/{id}/audio` URL. The Blob is what makes the transition
independent of mobile networking (iOS background) and what the DJ engine
seeds its incoming deck from. `playerStore.nextTrackBuffer` mirrors this
next-track prefetch state (`null` when nothing is in flight/ready); the seek
bar renders a second dot pinned to its right edge — pulsing while the next
track is still buffering, static once it is fully ready.

**Tracks ahead** — Settings → Playback has its own "Load tracks ahead"
switch, separate from transcoding and off by default
(`prefetch_ahead_enabled`). Off, only the next track is downloaded ahead —
the standard behaviour. On, the player keeps `prefetch_tracks` (2–5, default
3) upcoming queue tracks downloaded; `prefetchTrackCount()` in
`ui/src/api/settings.ts` turns the two settings into
`playerStore.prefetchTrackCount` (1 while off). Once the next track's Blob is ready, `playerStore`
(`scheduleAheadPrefetch`) hands `PlayerPlaybackFacade.prefetchAhead()` the
following `prefetchTrackCount − 1` items; they are fetched one by one at low
priority into a separate pool (never while the next track itself is still
downloading). When one becomes the next track, `prefetch()` promotes it from
the pool without a refetch; a skip straight to one consumes it directly.
Pooled tracks no longer in the plan are revoked on the next call (`[]` when
the queue has nothing after the next track); starting a track aborts a running
ahead download, which resumes after the next one is ready again. Each track is
a whole file in memory, which is why the setting is capped at 5.

Switched off, the browser retains at most one upcoming Blob (plus the consumed
one that is currently playing). Object URLs are revoked after use, on profile/source
changes, and on logout. This is intentionally an in-memory transition buffer,
not offline storage.

Opening the DJ workspace upgrades each physical deck from its plain routed
`<audio>` element to Signalsmith Stretch, as soon as that deck's track
qualifies (persisted track id plus a valid beat timeline) and the browser
supports AudioWorklet/WASM. The existing complete Blob is decoded in the
browser and transferred to the deck worklet in full; there is no PCM stream
and the backend is not part of playback after preparation. The MASTER deck's
pitch fader controls pitch-preserving tempo over the agreed ±8% range; pitch
stays locked on every non-master deck. A deck that never becomes
Signalsmith-eligible — no persisted track, unsupported AudioWorklet/WASM,
missing beat timeline, or worklet initialization failure — stays on its plain
routed `<audio>` element inside the graph: still playable and mixable, but
`canEngageBeatSync`/`canEngageTempoSync` (see below) report `false` for it and
the deck header shows the degraded reason. There is no dedicated
native-fallback sync path anymore: `HtmlMediaDeckSource`, the `DeckSource`
implementation that used to drive follower alignment off raw
`HTMLAudioElement.playbackRate`, was deleted (R1 of
`plans/discocs-dj-design/SYNC_REWRITE_PLAN.md`) — Signalsmith is now a hard
requirement for both BeatSync and TempoSync. A browser that lacks
AudioWorklet/WASM entirely simply never gets a sync-capable deck; ordinary
single-track playback outside the DJ workspace is completely unaffected,
since it never routes through this code path at all (it plays a bare
`<audio>` element outside any `AudioContext`). Retiring a deck releases its
compressed Blob, decoded/worklet buffer, object URL, and source references.

### Tempo master (AUTO/MASTER clock)

The DJ header contains Traktor-style `AUTO` and `MASTER` clock controls. With
AUTO active, the first deck that starts becomes tempo master; if that deck
stops, ownership moves to the other playing deck or back to the editable
master clock. `canBecomeMaster`/`canBecomeClockMaster` gate the per-deck
MASTER button and the header's clock MASTER button respectively: a deck can
become master only while playing and not already master; the clock can
reclaim MASTER only while both decks are stopped.

AUTO follows the actual transport of both a routed media element and an
upgraded Signalsmith source. With both decks stopped, the standalone clock is
MASTER. Starting the first deck makes it MASTER unconditionally; while either
deck is playing the standalone clock cannot be selected. If both decks play,
MASTER can be moved between them. Stopping the current master transfers
ownership to the other playing deck or returns it to the clock when both have
stopped. Signalsmith and beat timelines are validation requirements for the
SYNC operation itself, not UI prerequisites for pressing SYNC or selecting a
playing deck as MASTER. MASTER/SYNC commands wait for an in-progress
full-track decode.

### BeatSync vs TempoSync

Each loaded deck exposes **two adjacent sync-mode buttons** — `BEAT` and
`TEMPO` — not a mode dropdown, consistent with the rest of the dense
control-surface language:

- **BeatSync** (`BEAT`): permanent tempo *and* phase lock. The follower is
  matched to the master's BPM and beat phase before/while it plays. A
  dedicated 0.25s Signalsmith clock tick continuously measures the
  follower's phase offset from the master and automatically re-aligns it
  once the drift exceeds a small threshold (0.06 beats,
  `BEAT_SYNC_DRIFT_THRESHOLD_BEATS`), no more often than once every 2 seconds
  (`BEAT_SYNC_REALIGN_COOLDOWN_SECONDS`) — bounded auto-correction, not a
  re-seek on every tick.
- **TempoSync** (`TEMPO`): tempo lock only. Phase is allowed to drift by
  design — the reducer never auto-realigns a TempoSync follower. The same
  0.25s clock tick instead just publishes the measured offset into a small
  phase-offset readout, shown only while a deck is an engaged TempoSync
  follower (e.g. `+0.34 beat`). Correction is manual: press-and-hold nudge
  buttons next to the pitch fader (`beginTempoNudge`/`endTempoNudge`) apply a
  live ~2% rate offset while held and snap back to the locked ratio on
  release.

Clicking a mode's button while the deck is already engaged in that mode
disengages SYNC entirely; clicking the *other* mode's button switches the
engaged mode in place. Engagement survives a master switch — an engaged
former master immediately becomes a follower. Starting a previously armed
(paused) deck runs the same alignment path once it starts playing. Master
tempo changes propagate to every engaged follower. Following requires
Signalsmith plus a valid beat timeline and respects the agreed ±8% deck
range. Each deck header displays its resulting BPM and pitch percentage; the
disabled follower pitch fader still moves to the applied ratio.

Both buttons' enabled/disabled state comes directly from the `tempoSync`
reducer's own `canEngageBeatSync`/`canEngageTempoSync` snapshot booleans
(`ui/src/engine/playback/tempoSync.ts`) — the control surface does not
re-derive its own gating expressions. SYNC feasibility is checked
**synchronously at arm-time**: an infeasible request (no track, or a track
that will never produce a beat timeline) is rejected immediately and the
button never lights up. A feasible-but-not-yet-ready request (deck
mid-Signalsmith-upgrade) enters a distinct `"arming"` phase that resolves
itself, with no user-visible flicker, once the pending capability check
reports back — arming SYNC before the async upgrade resolves is a normal,
supported sequence, not a race.

### No toast/banner UI

SYNC/MASTER failures have no dedicated UI surface. Every failure — a
rejected arm attempt, an alignment error, a nudge attempted on a deck that
isn't an engaged TempoSync follower — funnels through one
`reportEngineFailure` helper (`ui/src/engine/playback/reportEngineFailure.ts`)
into `console.error`; DevTools is the only place to observe it directly. The
**only** in-app indicator is the existing per-deck inline status text: the
BEAT/TEMPO button shows its current rejection reason as a `title` tooltip,
and the deck's metadata line shows `preparation · transport · tempoMode` —
driven honestly by the reducer's real state rather than a flag that never
resets.

Production CSP permits only the narrow `wasm-unsafe-eval` script capability
required to compile the packaged Signalsmith WebAssembly module; general
JavaScript `unsafe-eval` remains forbidden.

Seek, loop, and handover/retirement paths re-evaluate phase and ownership.
Bounded BeatSync drift correction (above) already ships; further continuous
drift measurement/statistics and a supported-browser quality gate remain
Phase 6 Group 3 (pending separate approval — see `IMPLEMENTATION_PLAN.md`),
out of scope for this sync rewrite.

The per-user playback settings page can request MP3 transcoding at
96/128/192/256/320 Kbit/s. The profile key is included in the audio URL and
the backend validates it against the saved settings before forwarding
`format`/`maxBitRate` to Navidrome. A quality change cancels buffered audio
from the old profile.

## Flow vs autoplay refill routing

Two refill engines exist: **Flow** (`/api/v1/flow/refill` + `/api/v1/flow/event`)
and **generic autoplay** (`/api/v1/autoplay/refill`).

The routing decision lives in `ui/src/store/flowRefillRouting.ts`:

```
planRefill(session.source_type, eventType)
  → { engine: "flow" | "autoplay", sendEvent: boolean }
```

Rules:

- `source_type === "flow"` → **Flow engine**. Feedback events (`completed`,
  `skipped`, `liked`, `disliked`) are forwarded to `/flow/event` first (to
  accumulate skip/accept signals and possibly switch regions), then
  `/flow/refill` tops up the queue.
- Any other source type (`track`, `release`, `artist`, `playlist`, etc.) →
  **autoplay engine** (generic similarity-radio, unchanged).

**Exiting Flow is automatic.** Starting an Instant Mix, release, or playlist
creates a new session with a different `source_type`. The next
`scheduleAutoplayRefill` call reads the live session from the store, sees a
non-flow type, and routes to the autoplay engine — no explicit cleanup needed.

### Flow refill dedup

`scheduleAutoplayRefill` fires from several event handlers (skip, track-ended,
like/dislike), so overlapping calls are possible for the same session. Two
layers keep a track from being queued twice:

- **Client**: a module-level `refillInFlight` flag in `playerStore.ts` makes
  `scheduleAutoplayRefill` a no-op while a previous call is still in flight.
- **Server**: `app/api/flow.py:_load_session_context` excludes every track
  currently on the queue (any status except `removed`) from the candidate
  pool — not just `played`/`skipped` — and `api_v1_flow_refill` re-reads the
  queue right before `append_queue_items` to drop any candidate that a
  concurrent refill already added.

Neither layer is a hard transactional guarantee (no DB-level unique
constraint); together they close the practical race without adding that
complexity. See `tests/test_flow_refill_dedup.py` for the regression coverage
and `plans/todo.md` for the original bug writeup.
