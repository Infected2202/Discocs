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
background through the OS media pipeline. Auto-DJ / full mixing in the background
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
- A `visibilitychange` → foreground reconcile: if the store believes playback is
  `playing` but the element is actually paused, it resumes it, or drives
  `handleTrackEnded` when the current track ended in the background without the
  auto-advance having fired.
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

## Кнопка Shuffle в заголовке коллекции

Заголовки коллекций (артист, релиз, плейлист, лайки) предлагают Shuffle рядом
с Play. Ей нужен `setShuffle(true)`, а **не** `toggleShuffle()`.

`playSource` переносит `shuffle_enabled` из предыдущей сессии
(`playerStore.ts`, создание сессии), поэтому при уже включённом шафле
последующий toggle выключает его — и кнопка «Перемешать» запускает коллекцию в
обычном порядке. `setShuffle` приводит состояние к заданному и ничего не делает,
если оно уже такое. `toggleShuffle` остался для переключателя в самом плеере и
теперь выражен через `setShuffle`.

Эндпоинты `/playlists/{id}/play` и `/playlists/likes/play` всегда создают
сессию с `mode="linear"`, в отличие от `playSource`, — но кнопка не должна
зависеть от этой детали backend'а.

Порядок важен: перемешивать можно только после того, как новая очередь
применена. И только если старт вообще удался — иначе загруженной остаётся
предыдущая сессия, и шафл уехал бы на неё.

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
  queue item, `AudioEngine.resumeAtSeconds` seeks there — deferred until
  `loadedmetadata`, because an immediate `currentTime` write is dropped while
  duration is still unknown.

`PlasmaFBM` destroys its WebGL context while the document is hidden and builds
a fresh canvas when it becomes visible. This cannot prohibit mobile browsers
from discarding a tab, but releases GPU memory that otherwise makes the tab a
more likely discard candidate.

Autoplay is intentionally not resumed — browsers block `play()` without a
user gesture after a reload.

## Browser audio prefetch

`AudioEngine` uses `preload="auto"` for immediate playback, then explicitly
fetches the active response into a complete in-memory `Blob`. This closes the
mobile-browser gap where native buffering stops around 90%. When the Blob is
ready, playback switches to its local `blob:` URL at the same timestamp; only
then is the active track reported as 100% available and next-track prefetch is
allowed. If native `TimeRanges` reach 100% first, the redundant fetch is
aborted.
The seek bar renders every browser `TimeRanges` segment separately, so a gap
created by an unbuffered seek is not shown as downloaded. Dragging uses Pointer
Events and pointer capture, giving mouse, touch, and pen the same commit path;
`pointercancel` never seeks to a bogus fallback position. The commit uses the
last pointerdown/pointermove value rather than `pointerup.clientX`, because
mobile pointer capture can report a zero release coordinate. If media metadata
is temporarily unavailable during a source swap, fractional seek is deferred
until `loadedmetadata` instead of being discarded.
Before falling back to the slow path below, `seek()` first checks the native
element's own `buffered` `TimeRanges`: if the target second is already covered
(within a small tolerance), it writes `currentTime` directly and returns. This
is just a pointer move over bytes the browser already holds — no new network
request — so none of the transcoding unreliability applies. Computing that
target second needs a duration, and a chunked transcoded stream (no
`Content-Length`, see above) can leave `el.duration` unresolved — `NaN` or
`Infinity` — for the entire download, since the browser has no signal that
the resource is bounded. `load()` accepts the track's real duration from API
metadata as a fallback (`activeKnownDuration`) precisely so this fast path
isn't dead code for exactly the profile that needs it most. This covers the
common case (scrubbing near the current position, rewinding into already-
played audio) instantly instead of waiting on the full-file path.
The raw upstream stream is not reliably seekable while it is still being
transcoded — writing `currentTime` directly on it can be silently accepted and
then reset to zero, audible as the track restarting from the beginning. So
while the first network-backed track is being promoted to its complete local
Blob, seeking *past the already-buffered range* never writes to the network
element at all: it pauses playback, records the requested fraction (and
whether playback should resume) as a pending seek, and forces the active-track
cache fetch to start immediately if it hasn't already. The position is applied
— and playback resumed, if it was playing — only once the Blob swap's
`loadedmetadata` fires on the new, always-locally-seekable element.
`playerStore.seekBuffering` reflects this pending state; the seek bar renders a
soft pulsing dot at the seek target while it is `true`. A failed active-track
cache fetch is retried once before surfacing `onError` and clearing the
pending seek, so a single flaky request cannot leave playback stuck paused
indefinitely. In ordinary mode this Blob replacement remains a plain `<audio>`
element and is routed into Web Audio only when the DJ engine is already
active.
`playerStore.seek()`'s optimistic `currentTime` write is preceded by
`throttledSetTime.cancel()`: without it, a trailing throttled `timeupdate`
already scheduled from just before the seek (leading+trailing throttle, up to
~250ms late) could still fire afterwards and snap the seek bar back to the
stale pre-seek position.
After that signal, `playerStore` fetches the next queue item as a `Blob`
(also retried once on failure). A completed Blob is consumed through a local
`blob:` URL at transition time; an unfinished or stale prefetch is aborted and
playback falls back immediately to the normal `/api/v1/tracks/{id}/audio` URL.
`playerStore.nextTrackBuffer` mirrors this next-track prefetch state
(`null` when nothing is in flight/ready); the seek bar renders a second dot
pinned to its right edge — pulsing while the next track is still buffering,
static once it is fully ready — since the current track's own buffered range
is always full width by the time next-track prefetch is even allowed to start.

The browser retains at most the forced complete active Blob and one upcoming Blob.
Object URLs are revoked after use, on profile/source changes, and on logout.
This is intentionally an in-memory transition buffer, not offline storage.

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
