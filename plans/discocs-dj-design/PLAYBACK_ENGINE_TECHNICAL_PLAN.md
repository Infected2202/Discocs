# PlaybackEngine technical plan

**Status:** implementation contract draft
**Applies to:** Phases 0-3, 6 and 8
**Replaces:** direct use of `ui/src/engine/AudioEngine.ts` by player-facing code

## 1. Objective

Create one browser-local playback runtime for ordinary listening, manual two-deck mixing and future Auto DJ. The migration must preserve the current player before any second-deck feature becomes visible.

The runtime is a long-lived singleton outside React. React and Zustand issue commands and consume snapshots; they do not own audio nodes, clocks or scheduled automation.

## 2. Accepted decisions

- There is one `PlaybackEngine`, one `AudioContext`, two stable physical decks and one mixer/master graph.
- Compact playback is a projection of `programDeck`; it is not a third source.
- The initial source implementation uses `HTMLAudioElement` connected through `MediaElementAudioSourceNode`.
- A `DeckSource` boundary permits later replacement by the official Signalsmith WASM/AudioWorklet buffered source.
- The Web Audio graph is used for ordinary playback from Phase 1 onward, with neutral mixer values.
- Audio timing and automation use `AudioContext.currentTime`; JavaScript timers are not an audio clock.
- React never subscribes to a 60 Hz playhead stream. Pixi reads the engine clock on its own ticker; React receives bounded snapshots for text and controls.
- Server state is canonical for the playback session, queue and program queue item. Deck/mixer state is browser-local.
- Handover is explicit and idempotent. Loading, starting or crossfading an incoming deck does not advance the queue.

## 3. Proposed module boundaries

```text
ui/src/engine/playback/
  PlaybackEngine.ts          public commands, subscriptions and lifecycle
  types.ts                   commands, snapshots, capability and errors
  DeckRuntime.ts             one physical deck and source replacement
  sources/
    DeckSource.ts            source interface
    HtmlMediaDeckSource.ts   Phase 1 implementation
    StretchDeckSource.ts     Phase 6 Signalsmith implementation
  MixerGraph.ts              deck strips, crossfader and master graph
  AutomationScheduler.ts     Phase 8 action batches and cancellation
  curves.ts                  pure gain/EQ/filter/crossfader mappings
  clock.ts                   audio-time/playhead conversion helpers
```

`ui/src/engine/AudioEngine.ts` remains temporarily as a compatibility adapter or is reduced to re-exporting the Phase 1 facade. It is removed only after all callers and tests use `PlaybackEngine`.

Zustand responsibilities remain separate:

- `playerStore` coordinates server session/queue operations and exposes the compact-player facade;
- a small UI store owns whether Expanded Player or the DJ control surface is open;
- neither store duplicates audio-node state;
- engine snapshots are copied into Zustand only where ordinary React rendering needs them.

## 4. Runtime identity and state

```ts
type DeckId = "A" | "B"
type CommandOrigin = "manual" | "automation" | "system"
type DeckRole = "program" | "incoming" | "outgoing" | "prepared" | "free"
type PreparationState = "empty" | "loading" | "ready" | "streaming" | "unavailable"
type TransportState = "idle" | "loading" | "paused" | "playing" | "ended" | "error"
type AutoDjState = "disabled" | "idle" | "preparing" | "scheduled" | "transitioning" | "suspended" | "error"
```

The engine snapshot contains:

```ts
interface PlaybackEngineSnapshot {
  revision: number
  contextState: AudioContextState | "uninitialized"
  programDeck: DeckId | null
  decks: Record<DeckId, DeckSnapshot>
  mixer: MixerSnapshot
  automation: AutomationSnapshot
  capabilities: PlaybackCapabilities
  error: EngineError | null
}
```

Each `DeckSnapshot` contains stable track/queue identity, role, preparation and transport state, duration, playhead anchor, buffered ranges, tempo/pitch, cue/loop, mixer values, meter value and ownership metadata. A playhead anchor is `{mediaSeconds, audioTime, rate}`; consumers derive the current position instead of requiring per-frame state writes.

Snapshots are immutable values with a monotonically increasing revision. Discrete changes publish immediately. Textual playhead updates are capped at 4 Hz. Meter snapshots are capped at 20 Hz and only published while a visible consumer requests them.

## 5. Public command surface

Commands use physical deck ids and return structured results or typed failures.

```ts
interface PlaybackEngine {
  ensureReady(): Promise<PlaybackCapabilities>
  getSnapshot(): PlaybackEngineSnapshot
  subscribe(listener: () => void): () => void

  load(deck: DeckId, source: TrackSource, options?: LoadOptions): Promise<void>
  unload(deck: DeckId, command?: CommandMeta): Promise<void>
  play(deck: DeckId, when?: number, command?: CommandMeta): Promise<void>
  pause(deck: DeckId, when?: number, command?: CommandMeta): void
  seek(deck: DeckId, seconds: number, command?: CommandMeta): Promise<void>
  setCue(deck: DeckId, seconds: number, command?: CommandMeta): void
  cue(deck: DeckId, command?: CommandMeta): Promise<void>
  setLoop(deck: DeckId, loop: LoopState, command?: CommandMeta): void
  setTempo(deck: DeckId, ratio: number, command?: CommandMeta): void

  setTrim(deck: DeckId, normalized: number, command?: CommandMeta): void
  setEq(deck: DeckId, band: EqBand, normalized: number, command?: CommandMeta): void
  setFilter(deck: DeckId, normalized: number, command?: CommandMeta): void
  setChannelFader(deck: DeckId, normalized: number, command?: CommandMeta): void
  setCrossfader(normalized: number, command?: CommandMeta): void
  setMasterGain(normalized: number, command?: CommandMeta): void

  handover(request: HandoverRequest): Promise<HandoverResult>
  schedule(batch: AutomationBatch): string
  cancelAutomation(request: CancelAutomationRequest): void
  takeOver(): void
  destroy(): Promise<void>
}
```

`CommandMeta` includes `origin`, optional `transitionId` and optional idempotency/client command id. UI components call commands; they never set snapshot state directly.

## 6. Deck source boundary

```ts
interface DeckSource {
  readonly kind: "media-element" | "signalsmith"
  readonly output: AudioNode
  load(source: TrackSource, signal: AbortSignal): Promise<SourceMetadata>
  play(when?: number): Promise<void>
  pause(when?: number): void
  seek(seconds: number): Promise<void>
  setRate(ratio: number, when?: number): void
  setLoop(loop: LoopState): void
  getClockAnchor(): PlayheadAnchor
  getBufferedRanges(): BufferedRange[]
  release(): Promise<void>
}
```

### Phase 1 media-element implementation

- Create a new `HTMLAudioElement` and its `MediaElementAudioSourceNode` together for each load generation.
- Connect the source node to the persistent deck strip before playback; never route ordinary playback directly to device output.
- Use the existing same-origin `/api/v1/tracks/{id}/audio?profile=...` URL and existing Blob prefetch behaviour.
- On replacement, make old events inert, disconnect the source, pause it, clear `src`, call `load()`, revoke owned object URLs and release references.
- Use `playbackRate` with `preservesPitch` when available as the temporary tempo path.
- Abort stale fetch/load generations; a late completion can never replace a newer deck source.

### Phase 6 Signalsmith implementation

The official Web release provides a WASM/AudioWorklet `AudioNode`, scheduled input/rate/loop changes, dynamically appended sample buffers, buffer dropping and latency reporting. The spike must verify bundling of worklet/WASM assets and whether the app can feed decoded chunks without whole-track PCM residency.

The Signalsmith source must implement the same interface and compensate its reported latency when scheduling handover, cue, loop and sync operations.

## 7. Audio graph

Each persistent deck strip is:

```text
DeckSource
→ input trim GainNode
→ low-shelf BiquadFilterNode
→ mid peaking BiquadFilterNode
→ high-shelf BiquadFilterNode
→ DJ filter stage
→ bounded insert effect stage
→ channel GainNode
→ crossfader GainNode
→ shared mix bus
```

Master:

```text
mix bus
→ master GainNode
→ protection DynamicsCompressorNode
→ master analyser/meter
→ AudioContext.destination
```

Decisions for the foundation:

- Crossfader uses an equal-power curve: `gainA = cos((x + 1) * pi / 4)`, `gainB = sin((x + 1) * pi / 4)` for `x` in `[-1, 1]`.
- Parameter changes use short `AudioParam` ramps to avoid zipper noise and clicks.
- Initial EQ is a bounded three-filter approximation. A true isolator is a later DSP improvement and must not be labelled as full kill unless measured.
- The bipolar DJ filter has a neutral centre, low-pass movement to the left and high-pass movement to the right.
- `DynamicsCompressorNode` is labelled master protection, not true-peak limiting.
- Meter taps never sit in the critical control path and may reduce their update rate when the surface is hidden.

Exact coefficients and ramp durations are pure configuration covered by curve/graph tests and listening validation in Phase 0/2.

## 8. Initialization and browser lifecycle

- Constructing the singleton does not create or resume an `AudioContext`.
- The first user-initiated Play or explicit engine activation calls `ensureReady()` and resumes the context.
- A suspended context is resumed on the next user command; the UI exposes a recoverable capability state.
- `visibilitychange` does not pause playback. Position persistence continues through the existing `pagehide`/visibility path.
- Engine destruction is used only for logout, public/private player boundary changes or application teardown, not when the expanded surface closes.
- Media Session metadata and handlers always represent `programDeck`.

## 9. Preparation and memory lifecycle

- At most the program/outgoing and prepared/incoming complete compressed payloads are retained normally.
- The current payload becomes eligible for release only after successful handover and outgoing completion/cancellation policy.
- Whole-track PCM decode is forbidden in the Phase 1-5 steady state.
- Auto DJ requires `ready`; manual operation may use `streaming`.
- Loading carries an `AbortSignal` and generation id.
- Profile changes apply only to future loads while two decks are active. They never invalidate one side of an audible transition.
- Object URLs, fetch controllers, media elements, source nodes and Pixi/engine subscriptions have explicit release tests.

## 10. Queue and handover integration

The current backend `jump` operation combines canonical queue movement with a `queue_click` event, and the current client then reloads audio. DJ handover needs a separate operation.

Add a queue operation conceptually equivalent to:

```json
{
  "operation": "handover",
  "queue_item_id": "incoming-item",
  "client_handover_id": "uuid"
}
```

Backend requirements:

- validate that the item belongs to the active session;
- atomically mark it current and update session current ids;
- record a handover event with the client id;
- return the normal playback envelope;
- return the same successful result for a repeated client handover id;
- never require the browser to reload or restart the incoming deck.

Client order:

1. verify incoming deck identity and readiness;
2. commit/schedule the audible handover in the engine;
3. submit the idempotent server handover;
4. immediately project incoming as program locally;
5. reconcile the returned envelope;
6. retry a failed canonical update without replaying the audio action.

The server failure state remains visible until reconciled. It does not reverse an already audible transition.

## 11. Automation ownership

- Manual gain/EQ/filter/effect commands cancel only the target automation lane until transition end.
- Manual crossfader, tempo, transport, seek or loop commands suspend the transition batch.
- Take Over cancels every future action, preserves current parameter values and reports `suspended`.
- Disabling Auto DJ cancels a not-yet-audible batch; an audible transition completes safely unless Take Over is used.
- Every scheduled action declares target, start/end audio time, interpolation, origin and transition id.
- AudioParam automation is cancelled with a hold-at-current-value strategy where supported, with a tested equivalent fallback.

## 12. Capability model and fallback

Capabilities are detected, not inferred from user agent strings:

- Web Audio and `MediaElementAudioSourceNode`;
- AudioWorklet;
- WebAssembly;
- pitch-preserving media playback;
- Pixi renderer availability;
- optional MIDI later.

Levels:

- `ordinary`: one-deck playback available;
- `manualMix`: two media-element decks and mixer available;
- `tempoSync`: validated Signalsmith path and timeline beats available;
- `autoDj`: tempoSync plus fully prepared incoming deck and automation scheduler.

Loss of an advanced capability disables only the dependent level and provides a concrete reason.

## 13. Testing contract

No local self-check commands are run; tests are authored and executed by Jenkins per repository policy.

### Pure unit tests

- crossfader, gain, EQ and filter mappings;
- deck-role transitions and idempotent handover reducer;
- playhead anchor calculations;
- automation lane cancellation and takeover;
- capability-to-feature-level resolution.

### Engine tests with Web Audio/media fakes

- Phase 1 preserves current load/play/pause/seek/buffer/error callbacks;
- stale load completion cannot replace a newer generation;
- exactly one source reaches destination per deck;
- neutral graph does not alter compact-player state semantics;
- closing expanded UI does not destroy or pause the engine;
- object URLs and old media elements are released;
- Deck B preparation does not change program queue identity;
- handover changes program identity without another `load()` call.

### Backend tests

- handover validation, atomic state update and event creation;
- repeated `client_handover_id` is idempotent;
- cross-user/session queue items are rejected;
- current queue serialization remains compatible.

### Browser checks in CI or a dedicated controlled stage

- audible continuity cannot be asserted in jsdom; browser instrumentation checks context state, source continuity, scheduled timestamps and rendered controls;
- manual listening and latency measurements remain Phase 0/6 acceptance evidence, not substitutes for regression tests.

## 14. Phase 0 spike exit criteria

- A playing media element routes through a persistent neutral graph with no reload when the second strip is created.
- Two sources overlap and equal-power crossfading schedules successfully.
- Old media/source resources are demonstrably released across repeated loads.
- Signalsmith assets load under the production Vite/static-serving model; schedule, seek, loop, rate, latency and buffer dropping are exercised.
- The source interface above can represent both implementations without UI-specific branches.
- Any browser limitation is recorded as a capability fallback, not hidden in component code.
