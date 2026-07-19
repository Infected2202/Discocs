# Player UI

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

`AudioEngine` uses `preload="auto"` for the active track and reports full
buffering only when `TimeRanges` continuously cover the complete duration.
After that signal, `playerStore` fetches the next queue item as a `Blob`.
A completed Blob is consumed through a local `blob:` URL at transition time;
an unfinished or stale prefetch is aborted and playback falls back immediately
to the normal `/api/v1/tracks/{id}/audio` URL.

The browser retains at most the active prepared Blob and one upcoming Blob.
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
