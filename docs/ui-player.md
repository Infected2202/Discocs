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
