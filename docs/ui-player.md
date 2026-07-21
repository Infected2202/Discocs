# Player UI

The DJ surface renders detailed and overview waveform artifacts with PixiJS.
The renderer loads Pixi's static CSP compatibility implementations before
initialization, so the production policy does not need to allow `unsafe-eval`.
Both views share decoded typed arrays, follow the authoritative deck playhead,
and seek the physical deck selected by the pointer. Detailed waveforms behave
as a tape under a fixed centre playhead, including empty space before zero and
after duration. Played audio is darkened to the left of the playhead, while
uniform beat lines remain above the waveform and history overlay; bar/downbeat
emphasis is intentionally deferred until the analyzer produces validated bar
indices. Hover controls select an 8, 16, 30 or 60 second detailed window and
reset to the 16 second default. Overview cursors and detailed tape both use
captured pointer drag for mouse, pen and touch. Missing or stale analysis
is non-blocking; see [`timeline-waveforms.md`](timeline-waveforms.md).

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
context. Starting a playlist drag leaves playlist reordering available and
reveals a compact A/B deck dock above the player; the on-air deck is locked.
Dropping on the free deck opens the DJ workspace, moves or adds that track to
the next canonical queue position and prepares it without starting playback or
changing the program role. The full deck panels use the same drop payload.

The primary player UI lives in `ui/src/components/player/`.

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
After that signal, `playerStore` fetches the next queue item as a `Blob`.
A completed Blob is consumed through a local `blob:` URL at transition time;
an unfinished or stale prefetch is aborted and playback falls back immediately
to the normal `/api/v1/tracks/{id}/audio` URL.

The browser retains at most the forced complete active Blob and one upcoming Blob.
Object URLs are revoked after use, on profile/source changes, and on logout.
This is intentionally an in-memory transition buffer, not offline storage.

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
