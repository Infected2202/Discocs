# DJ tempo-master/sync rewrite

**Status:** R1, R2, R3, R5, R6, R7 delivered (R4/R8 pending). R3 shipped the BeatSync/TempoSync button split, honest `phase`-keyed active-glow, and `canEngage*`/`canBecome*` gating. R4's remaining work: mode-aware `alignFollower` branching, phase-offset readout, and pitch-bend nudge controls.
**Supersedes:** `PLAYBACK_ENGINE_TECHNICAL_PLAN.md` and `IMPLEMENTATION_PLAN.md` Phase 6 Group 2 ("beat sync") — the product behavior described there is correct in spirit and is kept; the code backing it is replaced by this plan.
**Applies to:** the tempo-master/SYNC subsystem under `ui/src/engine/playback/`

## 1. Context

The DJ engine's SYNC/MASTER feature has been effectively non-functional through
13 commits over 44 hours (`053ae03` … `3e94e23`, all on branch
`codex/dj-phase4-sonar`), each patching the specific symptom just reported
rather than the underlying design. Three parallel investigations (UI/store
layer, git archaeology + test coverage, deck-source timing internals)
converged on **10 confirmed root causes**, all with file:line citations,
verified twice (once by the investigating agents, once by re-reading the
cited lines directly and by a dedicated planning pass that re-checked every
citation against current code).

The core problem is architectural, not a bug in any one line: tempo-master
and sync state is mutated from **four independent, uncoordinated places**
inside `PlaybackEngine.ts` (explicit command methods, a DOM-event listener, a
`DeckRuntime` state-change callback, and `upgradeDeckSource`'s own inline
mutation), the SYNC button arms itself *before* checking feasibility and
never un-arms on failure (a broken state the pre-rewrite test suite asserted
as *correct*), and the DJ's second deck shares a single mutable "prepared
slot" with the ordinary player's unrelated background next-track cache — so
routine non-DJ activity silently deletes the DJ's armed second deck. The
pre-rewrite test suite could not catch any of this because every layer mocks
the layer below with a rubber-stamp double whose fake `AudioContext.currentTime`
never advances.

Full diagnosis (all 10 root causes, git history, Traktor reference model) was
captured as a design artifact ahead of this plan:
https://claude.ai/code/artifact/948252b2-1647-4a57-8961-4ec4dd3104d7

The existing design intent in `PLAYBACK_ENGINE_TECHNICAL_PLAN.md` and
`IMPLEMENTATION_PLAN.md` (Phase 6 Group 2, previously marked "complete") is
not wrong in spirit — it already describes a Traktor-accurate ownership model
in prose. The gap is that the prose was never enforced as a single owned
state machine in code, so ad hoc patches accumulated against it instead of
the design holding. This plan **supersedes** Phase 6 Group 2's
implementation (the product behavior it describes is correct and is kept;
the code backing it is replaced) and also revises one of its "Accepted
decisions" (§2 below).

**Product decisions already confirmed — not open questions:**

1. **Both sync modes required**, matching Traktor: **BeatSync** (permanent
   tempo+phase lock, auto re-corrects on drift) and **TempoSync** (tempo lock
   only, phase can drift, corrected manually via pitch-bend, needs a
   phase-offset readout).
2. **Delete `HtmlMediaDeckSource` entirely.** Signalsmith becomes a hard
   requirement for DJ mode — no native-fallback degrade. Unsupported browsers
   simply don't get DJ mode; ordinary single-track playback is unaffected
   (it never uses this code path at all — it plays a bare `<audio>` element
   outside any `AudioContext`).
3. **The DJ deck is a fully isolated resource**, structurally separate from
   the ordinary player's next-track prefetch cache. The only allowed
   interaction: at DJ-mode activation, the DJ deck may seed its starting
   position from whatever the background player already has cached, so
   activation doesn't interrupt playback. After that instant, the DJ deck's
   lifecycle is completely independent of ordinary prefetch/queue events.
4. **No toast/banner UI.** Failures funnel to `console.error` (grep/breakpoint
   via DevTools); the existing per-deck inline status text in the DJ panel
   stays as the only in-app indicator, now driven honestly by real state
   instead of a flag that never resets.

## 2. Architecture

Replace the four scattered mutation sites with **one pure reducer + one thin
effect-executing controller** (Elm-architecture split of decision from
side-effect), so there is exactly one place that decides tempo-master/sync
state and exactly one place that turns a decision into a scheduled Web Audio
operation.

```
ui/src/engine/playback/tempoSync.ts            pure: reduceTempoSync(state, event) -> {state, effects}
ui/src/engine/playback/TempoSyncController.ts  thin: dispatches events, executes effects via a host seam
ui/src/engine/playback/PlaybackEngine.ts       implements the host seam (alignFollower), owns DeckRuntime/MixerGraph
```

`reduceTempoSync` never touches audio; it only reacts to **facts** pushed in
(`deck-transport`, `deck-capability`, `phase-offset-observed`, `align-result`)
and **commands** (`toggle-sync`, `set-deck-master`, `set-clock-master`,
`set-auto-master`), and emits **effects** (`align-follower`) that the
controller executes exactly once each, feeding the result back in as another
fact. This is what kills root cause A for real (not relocates it): every
current mutation site — `playDeck`, `pauseDeck`, `unload`,
`setTempoMaster`/`setClockMaster`/`setAutoMaster`, `toggleSync`, the DOM
listener in `watchExternalElement`, `handleDeckRuntimeChange`, and
`upgradeDeckSource`'s inline `if (options.autoplay && ...)` block at
`PlaybackEngine.ts:234-237` — shrinks to "do the DeckRuntime-level work, then
dispatch a fact/command," never mutating master/sync state directly again.
Redundant facts from multiple trigger paths become structurally idempotent
no-ops inside the reducer, so the `explicitTransportCommands` suppression
`Set` (a patch bolted on in commit `e3427b4` to arbitrate between two paths
that shouldn't both exist) is deleted outright rather than kept.

SYNC feasibility is checked **synchronously at arm-time**: an infeasible
request (no track, will-never-have-a-beat-timeline) is rejected immediately
— `enabled` never becomes `true`. A feasible-but-not-yet-ready request (deck
mid-Signalsmith-upgrade) gets a new phase, `"arming"`, distinct from
`"pending"` (known-feasible, alignment in flight) and `"unavailable"`
(terminal failure) — `arming` emits no effect and resolves itself only when a
later `deck-capability` fact confirms readiness. This directly fixes root
cause B (the button that lights up and lies) and turns "arm SYNC before the
async upgrade resolves, it engages once ready" into an explicit, tested state
transition instead of an implicit race.

All UI-facing "can I click this" booleans (`canEngageBeatSync`,
`canEngageTempoSync`, `canBecomeMaster`, `canBecomeClockMaster`) become
**outputs of the same reducer**, so `DjControlSurface.tsx` stops
independently re-deriving its own (drifting, progressively-loosened) gating
expressions — this is the fix for root cause C.

### 2.1 BeatSync vs TempoSync

`alignFollower` (today's `synchronizeFollower`, `PlaybackEngine.ts:673-715`)
branches by mode:

- **BeatSync**: unchanged `setRate → seek → play` behavior. New: the existing
  0.25s Signalsmith clock tick (`StretchDeckSource`'s `setUpdateInterval`)
  also computes the follower's beat-phase offset from the master and
  dispatches `phase-offset-observed`; the reducer re-triggers `align-follower`
  only past a drift threshold + cooldown (bounded correction, not the
  deferred continuous-measurement epic).
- **TempoSync**: only `setRate` ever runs from the alignment path; phase is
  allowed to drift by design. The same clock tick instead just publishes
  `phaseOffsetBeats` into the snapshot for a UI readout. New engine methods
  `beginTempoNudge(deck, direction)` / `endTempoNudge(deck)` implement the
  manual pitch-bend correction (live `setRate` nudge on press, snap back to
  the locked ratio on release), valid only for an engaged TempoSync follower.

UI gets two adjacent sync-mode controls per deck (not a dropdown, consistent
with the existing dense control-surface language), a small phase-offset
readout shown only in TempoSync mode, and two press-and-hold nudge buttons
near the pitch fader.

### 2.2 DJ deck isolation from ordinary prefetch

The actual mechanism behind root cause D: `prepareDjDeck` (the DJ manually
loading a track) and `scheduleNextPrefetch` (ordinary background caching of
the next queue track) **call the same `PlayerPlaybackFacade.prefetch()`
method**, which unconditionally tears down whatever was previously prepared.
There is no dedicated "load a track onto a DJ deck" primitive today.

Fix — split into two structurally separate resources:

- **`prefetch()` becomes fully graph-unaware.** Delete its
  `routeIncomingElement`/graph-deck branch entirely; it only ever populates
  the ordinary blob/objectUrl cache, regardless of DJ-mode state. This alone
  makes it structurally impossible for ordinary prefetch to touch a DJ deck.
- **New dedicated field + methods**: `this.djDeck` (private,
  `PlayerPlaybackFacade`), `prepareDjDeck(trackId, url, profileKey,
  queueItemId)`, `clearDjDeck()` — own fetch, own element, own
  `AbortController`, never touching `prefetched`/`prefetchController`.
- **One explicitly-allowed handoff**: at DJ-mode activation, if the ordinary
  player already has something prefetched, copy it once into a fresh
  `djDeck` (no new network fetch — reuses the already-downloaded blob). From
  that instant on, `djDeck` and `prefetched` never alias again.
- Rename the DJ-only call sites that already only ever touched
  `this.prepared` for DJ purposes (`handoverPrepared`, `confirmHandover`,
  `stretchCandidateForDeck`, `elementForDeck`) — pure rename, not a behavior
  change, since those were never actually the bug.
- `playerStore.ts`'s `prepareDjDeck` action calls the new facade method
  instead of `cancelPrefetch()` + `clearPrefetched()` + `prefetch()`.

**Known, intended behavior change**: today, ordinary background prefetch
passively re-populates a DJ graph deck on *every* subsequent prefetch while
DJ mode is open (existing comment at `PlayerPlaybackFacade.ts:159-162`).
After this fix it only happens once, at DJ-activation instant — matching the
confirmed product decision #3 wording above, but worth confirming isn't a
surprise once it ships.

### 2.3 Timeouts on the Signalsmith pipeline (root cause E)

`DeckRuntime.load()` gets an injectable overall timeout (real default ~15s,
tiny value in tests). `StretchDeckSource.load()`'s worklet-RPC sequence
(`adapter.initialize`/`append`/`node.setUpdateInterval` — currently has zero
`AbortSignal` awareness) gets wrapped in a new small shared `abortRace(promise,
signal, dispose)` helper; a late-resolving node after abort is disposed
(`disconnect()`/`port.close()`) via fire-and-forget rather than leaked. Every
resulting failure (plus the controller's `align-follower` catch path) funnels
through one new `reportEngineFailure(context, error)` → `console.error` call
— root cause G's fix per product decision #4.

`sources/DeckSource.ts` gets TSDoc stating `when`'s contract (an
`AudioContext.currentTime` timestamp; an implementation unable to honor it
must throw, not silently ignore it) — root cause J's fix, cheap insurance now
that `HtmlMediaDeckSource` (the implementation that violated this) is
deleted.

### 2.4 Realistic Web Audio test fake (root cause I)

New `testing/webAudioFakes.ts`: `createFakeAudioContext()` /
`createFakeStretchNode()` replace three near-duplicate hand-rolled mocks
currently scattered across test files. The fake's `currentTime` is a real
counter with an explicit `advance(seconds)`; `schedule`/`start`/`stop` record
the `when` they were given against the fake's *current* time and can flag a
timestamp already in the fake past; `inputTime` advances proportionally once
scheduled `active: true` — so a test can assert a deck's *real projected
position* instead of a mock that resolves instantly regardless of `when`.

The scenario from the original bug report becomes one integration test in
`PlaybackEngine.test.ts`: arm SYNC on deck B while A plays, press Play on B
*before* its Signalsmith upgrade resolves, advance fake time, assert deck B's
audio actually starts and ends up phase-aligned. This must fail against
pre-rewrite code (as a timeout, once the timeout slice exists — before that
it would hang indefinitely) and pass against the rewrite.

## 3. Slices

Each slice ships with its own tests per this repo's rule that new code
without a test isn't done. No local `pytest`/`vitest`/`tsc`/build runs for
self-check — author tests, push once the whole plan is complete, verify via
Jenkins per `AGENTS.md`/`CLAUDE.md`. Because this bug's whole signature is
"passed every mocked test, still broken in a real browser," this feature
specifically also warrants a manual pass in the actual dev server (Play/SYNC
across two real tracks, watching Web Audio state in Chrome DevTools) before
considering the work done — automated coverage alone already proved
insufficient once for this exact code.

**R1 — Pure reducer + confirmed-dead-code deletion** (isolated, ships nothing
into production wiring yet)
- Create `tempoSync.ts` (types + `reduceTempoSync`) and `tempoSync.test.ts`
  (exhaustive: AUTO promotion/handoff, CLOCK gating, MASTER override,
  synchronous SYNC rejection, `arming`→effect-on-capability-fact,
  mode-branching `phase-offset-observed`, idempotent redundant facts).
- Delete `sources/HtmlMediaDeckSource.ts` + its test — confirmed zero other
  references in the repo (`grep -r HtmlMediaDeckSource ui/src` returns only
  the class file and its own test).
- Add TSDoc to `sources/DeckSource.ts`.
- Create `plans/discocs-dj-design/SYNC_REWRITE_PLAN.md` (this document,
  persisted into the repo's existing plan-doc convention so it survives
  session/context boundaries) and flip the Phase 6 Group 2 status lines in
  `PLAYBACK_ENGINE_TECHNICAL_PLAN.md`/`IMPLEMENTATION_PLAN.md` to
  "superseded — see SYNC_REWRITE_PLAN.md."

**R2 — Wire the controller in; delete the four scattered mutation sites**
(depends on R1)
- Create `TempoSyncController.ts`, `reportEngineFailure.ts`.
- `PlaybackEngine.ts`: delete `this.beatSync` field,
  `explicitTransportCommands`, `assignTempoMaster`, `reconcileAutoMaster`,
  the inline mutation in `upgradeDeckSource`; every former mutation site
  becomes "DeckRuntime work, then dispatch"; `getSnapshot()` reads the
  controller.
- `types.ts`: rename `BeatSyncSnapshot` → `TempoSyncSnapshot`, add `"arming"`
  phase.
- Rewrite the relevant `PlaybackEngine.test.ts` section: invert the current
  `enabled: true, phase: "unavailable"` assertion (infeasible SYNC now
  rejects synchronously), add a redundant-facts-produce-one-reassignment
  regression test.

**R3 — Honest gating and honest visuals** (depends on R2)
- `DjControlSurface.tsx`: split SYNC into BeatSync/TempoSync controls, fix
  the button's active-glow to key off `phase` (today it's
  `enabled && isPlaying`, so `"unavailable"` renders identically to
  `"aligned"` while playing — the literal "lights up, does nothing" bug at
  the DOM level), gate Play/SYNC/MASTER off the new `canEngage*`/`canBecome*`
  snapshot booleans.
- `PlayerPlaybackFacade.ts`: `toggleDeckSync(deck, mode)` gains the mode
  param.
- Rewrite `DjControlSurface.test.tsx` sync/master section, add the
  "unavailable-while-playing never gets the active glow" case (currently
  untested — this gap is why 13 commits didn't catch it).

**R4 — Mode-aware alignment + pitch-bend nudge** (depends on R2, R3)
- `PlaybackEngine.ts`: branch `alignFollower` by mode, wire the 0.25s tick to
  `phase-offset-observed`, add `beginTempoNudge`/`endTempoNudge`.
- `PlayerPlaybackFacade.ts`: nudge passthroughs.
- `DjControlSurface.tsx`/`.module.css`: phase-offset readout, nudge buttons.
- Tests: BeatSync re-aligns past threshold+cooldown; TempoSync never
  auto-realigns and only updates the readout; nudge unit tests.

**R5 — DJ deck resource isolation** (needs R2's final snapshot shape for its
own tests; otherwise independent — can be pulled earlier if the second-deck
crash is the most urgent thing to land first)
- `PlayerPlaybackFacade.ts`: delete `prefetch()`'s graph branch, add
  `djDeck`/`prepareDjDeck`/`clearDjDeck`, rename remaining DJ-only
  `prepared` references, strip `consumePrefetched()`'s cross-teardown.
- `playerStore.ts`: `prepareDjDeck` action calls the new facade method.
- Rewrite the `PlayerPlaybackFacade.test.ts` sync-race tests to use
  `prepareDjDeck`; add the direct regression test — background prefetch
  after a DJ deck is manually prepared must not tear it down.
- Non-regression check: `playerStore.background.test.ts` /
  `playerStore.queueActions.test.ts` need no changes — same
  `prefetched`/`prefetchController`/`prefetchTarget` fields, identical
  semantics, minus the DJ side effect that was never part of that feature's
  contract.

**R6 — Realistic Web Audio test fake** (no functional dependency; can run
first to de-risk test infra before touching production code)
- Create `testing/webAudioFakes.ts`.
- Retrofit `PlaybackEngine.test.ts`, `StretchDeckSource.test.ts`,
  `DeckRuntime.test.ts` (and `MixerGraph.test.ts` if it hand-rolls its own)
  to consume the shared fake instead of duplicated inline mocks.

**R7 — Timeouts/cancellation + the named regression test** (depends on R2,
R6)
- `DeckRuntime.ts`: injectable load timeout.
- `StretchDeckSource.ts`: `abortRace` around the worklet-init sequence,
  dispose late-resolving nodes.
- Create `abortRace.ts` + `abortRace.test.ts`.
- `PlaybackEngine.ts`: funnel remaining catch paths through
  `reportEngineFailure`.
- Add the named "SYNC deck B while A plays, Play before upgrade resolves"
  integration test; add timeout-specific unit tests.

**R8 — Final consistency pass + docs**
- Confirm the rewrite/delete/keep test inventory is accurate.
- Rewrite `docs/ui-player.md`'s Sync/Master section (BeatSync/TempoSync,
  phase-meter, pitch-bend) and replace the now-inaccurate "keeps that deck on
  native media playback" fallback sentence with: unsupported browsers simply
  don't get DJ mode.
- Update `PLAYBACK_ENGINE_TECHNICAL_PLAN.md` §9's payload-retention sentence
  to reflect the `prefetched`/`djDeck` split.
- Final status-line pass on both existing plan docs and
  `SYNC_REWRITE_PLAN.md` confirming completion.

### 3.1 Dependency order

```
R1 ─▶ R2 ─▶ R3 ─▶ R4                     (hard chain: each needs the previous slice's shape)
R6 ───────────────▶ R7                   (R7 needs R2's clean failure path + R6's fake)
R5 (needs only R2; otherwise orthogonal — can be pulled earlier for urgency)
R1,R2,R3,R4,R5,R6,R7 ─▶ R8               (docs/final pass last)
```

### 3.2 Existing test disposition

- **Delete**: `sources/HtmlMediaDeckSource.test.ts`.
- **Rewrite substantially**: `PlaybackEngine.test.ts`, `PlayerPlaybackFacade.test.ts`,
  `DjControlSurface.test.tsx`.
- **Add, keep the rest**: `DeckRuntime.test.ts` (+timeout case),
  `StretchDeckSource.test.ts` (+abort-race case), `tempoSync.test.ts` (new).
- **Keep unmodified**: `beatSync.test.ts` (pure primitives still used by
  mode-aware `alignFollower`), `MixerGraph.test.ts`, `capabilities.test.ts`,
  `signalsmith/capabilities.test.ts`, `signalsmith/selection.test.ts`,
  `deckRoles.test.ts`, `playerStore.background.test.ts`,
  `playerStore.queueActions.test.ts`, `playerStore.playFromEnvelope.test.ts`.

## 4. Critical files

- `ui/src/engine/playback/PlaybackEngine.ts` — orchestrator, loses its four
  scattered mutation sites
- `ui/src/engine/playback/tempoSync.ts` (new) — the single reducer
- `ui/src/engine/playback/TempoSyncController.ts` (new) — effect execution
- `ui/src/engine/playback/PlayerPlaybackFacade.ts` — DJ-deck/prefetch split
- `ui/src/store/playerStore.ts` — `prepareDjDeck` action
- `ui/src/components/dj/DjControlSurface.tsx` — gating/visuals, two sync
  modes, nudge controls
- `ui/src/engine/playback/DeckRuntime.ts` — load timeout
- `ui/src/engine/playback/signalsmith/StretchDeckSource.ts` — abort-race
  around worklet init
- `ui/src/engine/playback/sources/HtmlMediaDeckSource.ts` — deleted (R1)
- `ui/src/engine/playback/sources/DeckSource.ts` — documented `when`
  contract (R1)
- `plans/discocs-dj-design/SYNC_REWRITE_PLAN.md` (new) — this plan, persisted
  (R1)

## 5. Verification

- Each slice's own tests (authored per-slice, run by Jenkins — no local
  vitest/tsc self-check per `AGENTS.md`/`CLAUDE.md`).
- R7's named integration test is the direct regression guard for the
  originally reported bug: SYNC armed on deck B while A plays, Play pressed
  before Signalsmith upgrade resolves, deck B genuinely starts and reaches
  `phase: "aligned"`.
- R5's regression test directly guards root cause D: prepare a DJ deck,
  fire the ordinary player's background-prefetch path, assert the DJ deck's
  identity survives.
- After R8, a manual pass in the real dev server (two real tracks, actually
  listening, Chrome DevTools open on the AudioContext/worklet state) — this
  bug's entire signature was "green tests, broken browser," so this is not
  optional polish for this particular feature.
- One commit + push per this repo's convention only once the full plan is
  complete (not per-slice) — `disableConcurrentBuilds()` makes frequent
  pushes costly, and per `AGENTS.md`/`CLAUDE.md` push both `origin` and
  `gitea` remotes together.
