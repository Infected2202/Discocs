# Player UI

The primary player UI lives in `ui/src/components/player/`.

## Compact player backdrop

When the current track has artwork, the compact player bar renders a decorative
ambient backdrop derived from that image:

- the requested artwork is capped at `320px`, which is sufficient for a heavily
  blurred 76px-high surface;
- a fixed blur, brightness adjustment, dark scrim, and artwork-derived accent
  glow keep controls and metadata legible;
- only transforms and opacity are animated;
- the 16-second ambient movement runs while playback is active and freezes when
  playback is paused;
- artwork changes fade in over the stable card background;
- `prefers-reduced-motion: reduce` disables the decorative movement.

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
