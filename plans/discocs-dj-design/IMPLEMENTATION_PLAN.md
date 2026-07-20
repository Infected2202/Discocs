# Discocs DJ implementation plan

**Status:** Phase 0 complete; Phases 1-3 implemented and Jenkins verified; Phase 4 implemented, Jenkins verification pending
**Delivery rule:** one complete logical slice includes code, meaningful tests, affected docs, one commit and pushes to both configured remotes; Jenkins is the verification environment.

## 1. Readiness

| Phase | Ready to start | Blocking dependency |
|---|---:|---|
| 0. Contracts and spikes | yes | none |
| 1. One-deck engine migration | after Web Audio spike | Phase 0 engine result |
| 2. Second deck and handover | after Phase 1 CI | stable one-deck compatibility |
| 3. Expanded control surface | after Phase 2 snapshots | real two-deck runtime |
| 4. Waveform artifact/rendering | after format spike | frozen v1 manifest/payload |
| 5. Full timeline analysis | after waveform lifecycle | artifact/job foundation |
| 6. Signalsmith tempo/sync | after beat data and WASM spike | Phases 0 and 5 |
| 7. Settings/diagnostics | incremental after owners exist | corresponding runtime/backend features |
| Future Epic B: Auto DJ | not part of this plan | completed foundation and separate product/design work |

There are currently no unresolved product decisions blocking Phases 0-3. New approval is required only if a spike forces a user-visible compromise.

## 2. Phase 0 — contracts and risk spikes

Phase 0 produces evidence and freezes seams. Spike code is either promoted into tested production modules or removed before the phase commit; abandoned prototypes are not left in the runtime bundle.

### Slice 0.1 — Web Audio graph and source lifecycle

**Implementation:** complete; Jenkins verified in build #256. Browser evidence
and the controlled-environment limitation are recorded in
`SLICE_0_1_BROWSER_RESULTS.md`.

Work:

- add the initial playback type contracts and pure crossfader/parameter curves;
- build a minimal two-strip `MixerGraph` using one shared `AudioContext`;
- prove that a playing Deck A remains connected when Deck B is created and loaded;
- prove replacement releases the previous media element, source node, fetch and object URL;
- measure/context-log scheduled ramp timestamps and state changes;
- record browser capability results in this plan directory.

Tests to author:

- equal-power endpoints and centre;
- clamping and ramp mapping;
- stale source generation cannot attach;
- release is idempotent;
- Deck B construction does not change Deck A transport identity.

Exit:

- the graph/source design in `PLAYBACK_ENGINE_TECHNICAL_PLAN.md` is confirmed or amended;
- Phase 1 file-level tasks are unblocked.

### Slice 0.2 — PixiJS waveform renderer spike

**Implementation:** complete; Jenkins verified in build #257. The renderer contract,
measurements and per-deck application decision are recorded in
`SLICE_0_2_BROWSER_RESULTS.md`.

Work:

- add PixiJS v8 with a locked dependency version;
- asynchronously initialize a renderer in a React-owned container;
- render two synthetic multi-resolution coloured waveforms;
- exercise container resizing, device-pixel ratio, zoom/follow and pointer-to-time conversion;
- stop the private ticker while hidden and destroy all resources on teardown;
- measure frame time and retained memory at representative desktop sizes.

Tests to author:

- level-of-detail selection;
- pointer/time conversion;
- async initialization cancellation;
- hidden/unmounted ticker and resource cleanup.

Exit:

- decide one shared Pixi application versus one application per deck from measurements;
- freeze renderer input contract without requiring backend data yet.

### Slice 0.3 — Signalsmith integration spike

**Implementation:** complete; Jenkins verified in build #262. Package/asset validation,
latency measurements and the `DeckSource` feasibility decision are recorded in
`SLICE_0_3_BROWSER_RESULTS.md`.

Work:

- lock the official Signalsmith Stretch Web package/version and document its license;
- verify Vite production URLs for module, worklet and WASM assets;
- exercise buffer append/drop, start/stop, seek, rate, loop and scheduled changes;
- record reported latency and the scheduling lead time required for clean changes;
- compare default and cheaper configurations on representative hardware;
- confirm how decoded chunks will reach the worklet without retaining whole-track PCM.

Tests to author where deterministic:

- asset URL construction;
- capability/failure classification;
- adapter scheduling and latency compensation using a fake node;
- cleanup and buffer dropping.

Exit:

- `StretchDeckSource` remains feasible behind `DeckSource`, or the plan records the smallest required interface correction;
- failure affects Phase 6 choice, not Phase 1 ordinary playback.

### Slice 0.4 — Timeline v1 fixture spike

**Implementation:** complete; Jenkins verified in build #264. The accepted
layout, fixture sizing and decode/render memory estimates are recorded in
`SLICE_0_4_FORMAT_RESULTS.md`.

Work:

- implement an offline fixture encoder/decoder outside request handling;
- generate short/typical/long synthetic manifests and payloads;
- validate base bucket, pyramid, quantization and browser decoder;
- estimate bytes/minute and decode/render memory;
- freeze v1 descriptor alignment and endianness.

Tests to author:

- deterministic encoding;
- offset/dtype/scale round-trip;
- extrema preservation;
- corrupt length/checksum/version rejection.

Exit:

- `TIMELINE_ANALYSIS_TECHNICAL_PLAN.md` moves from draft to accepted v1 contract.

## 3. Phase 1 — one-deck PlaybackEngine migration

This is the first production vertical slice. It must not introduce a visible DJ feature.

**Implementation:** complete; Jenkins verification pending. Deck A now owns
ordinary playback through the neutral Web Audio graph while the compatibility
facade preserves existing player selectors, persisted keys and UI behavior.

### Backend impact

None expected. Existing track audio, queue and playback event APIs remain unchanged.

### Frontend work

- create `ui/src/engine/playback/` modules from the technical plan;
- route Deck A through the neutral Web Audio graph;
- port load, Blob cache, prefetch, buffer reporting, play/pause/seek, volume, mute, Media Session, errors and cleanup;
- expose a compatibility facade to `playerStore` so component selectors remain stable;
- replace direct `audioEngine` access in `playerStore` and session restore paths;
- retain the current UI and persisted-state keys.

### Required regression tests

- every meaningful current `AudioEngine.test.ts` scenario is retained or migrated;
- `playTrack`, restore, Next/Previous, repeat, prefetch and logout tests fail if the engine call is removed/inverted;
- neutral Web Audio routing and context-resume failures are covered;
- old and new engines cannot both own media output;
- Media Session follows Deck A/program state.

### Documentation

- update player/runtime architecture documentation;
- record supported/degraded capabilities;
- remove references to the retired single-element ownership model.

### Gate

Push once the slice is complete, then inspect Jenkins. A failed pipeline is diagnosed through structured test/stage sources per `AGENTS.md`; no local test/build duplication.

## 4. Phase 2 — Deck B, mixer and canonical handover

**Implementation:** complete; Jenkins verified in build #268.

### Backend work

- extend `PlaybackQueuePatchRequest` with `handover` and `client_handover_id`;
- add idempotency persistence using a playback event/client id or a narrowly scoped handover record;
- update queue/session atomically without creating a second playback session;
- serialize the existing envelope shape unchanged;
- add semantic playback event types without applying incorrect like/skip preference deltas.

### Frontend work

- create the second persistent deck strip;
- turn next-track Blob prefetch into free-deck preparation;
- add deck role reducer, preparation states and resource rotation;
- implement channel trim/EQ/filter/fader, equal-power crossfader, master protection and meters;
- implement handover reconciliation without `load()` on incoming;
- implement global Next as fast protected handover;
- preserve single-deck behaviour when no incoming deck is ready.

### Required tests

- SQLite/API idempotent handover and cross-session rejection;
- starting incoming does not advance queue;
- handover advances exactly once and does not reload incoming;
- outgoing cleanup occurs only after safe retirement;
- mixer neutral/end/centre mappings;
- unavailable incoming falls back without breaking program playback;
- profile changes apply only to later loads during an active mix.

### Gate

Two real locally buffered tracks can overlap and switch program role while the server queue reconciles exactly once.

## 5. Phase 3 — expanded DJ control surface

**Implementation:** complete; Jenkins verified in build #272. The accepted
visual direction keeps the Discocs palette, typography,
backdrop and expanded-player transition while borrowing only the control set,
information hierarchy and placement from the supplied Traktor reference.
Waveforms and overviews are labelled placeholders until Phase 4. The lower
region reuses the current playback queue and its existing actions; no separate
library browser is introduced.

### UI structure

```text
AppShell
├── ordinary routes/content
├── PlayerBar
├── ExpandedPlayer
└── DjControlSurface       fixed full-screen layer, engine remains mounted
    ├── deck waveforms/placeholders
    ├── Deck A controls
    ├── Mixer controls
    ├── Deck B controls
    ├── queue/source context
    └── reserved future Auto DJ integration area
```

The surface follows the current Expanded Player open/close transition and focus model. Expanded Player and DJ surface are mutually exclusive presentations; closing either does not change transport.

### Work

- add separate UI state/actions for opening the DJ surface;
- add entry from PlayerBar/Expanded Player distinct from Auto DJ toggle;
- implement responsive desktop/landscape-tablet layout;
- connect controls to real engine commands/snapshots;
- show physical deck identity, runtime role, preparation/error state and active program deck;
- show a compact active-mix indicator when the surface is closed;
- omit unimplemented Auto DJ controls rather than exposing non-functional placeholders.

### Tests

- open/close does not call load/play/pause/seek;
- UI state is mutually exclusive with Expanded Player;
- controls target the correct physical deck after roles alternate;
- compact player projects program deck after handover;
- keyboard/focus/Escape behaviour;
- pointer controls clamp, commit and expose accessible labels;
- phone layout keeps the compact player and does not render an unusable dense surface.

## 6. Phase 4 — waveform artifact and renderer

**Implementation:** complete; Jenkins verification pending. The frozen v1
foundation artifact is published atomically for local and Navidrome-backed
tracks, exposed through authenticated manifest/payload/status/job endpoints,
and rendered in both DJ deck waveform regions from one shared decoded payload.
Artifact availability never blocks manual audio transport.

### Backend

- add `app/store/timeline.py` and schema initialization;
- add artifact encoder, atomic publisher and safe cleanup;
- add manifest, payload, batch status and waveform analysis job endpoints;
- expose ready/missing counts and explicit waveform job launch in `/admin`;
- integrate existing worker acquisition for local/Navidrome-backed tracks;
- implement path + mtime + file-size invalidation.

### Frontend

- add manifest/payload client and TypeScript decoder;
- share decoded arrays between overview/detailed views;
- connect Pixi renderer to both deck snapshots and pointer seek;
- expose loading/missing/stale/failed states without blocking manual audio.
- poll only already queued/running jobs so the DJ surface remains a read-only
  consumer and never triggers analysis itself.

### Gate

The complete waveform vertical slice works for real queue tracks before adding the rest of timeline analysis.

## 7. Phase 5 — full timeline analysis

**Implementation:** minimal production scope complete; Jenkins verification
pending. `timeline_foundation_v2` adds beat timestamps, global rhythm
confidence, coverage and interval-derived local tempo from the explicit admin
job's single FFmpeg decode. The DJ renders a beat grid and never starts
analysis. Unvalidated downbeat/onset/loudness/structure additions remain
deferred as described below.

- keep timeline extraction offline and start it only through the explicit
  `/admin` job; the DJ client remains a read-only artifact consumer;
- refactor the existing `RhythmExtractor2013` boundary so its already computed
  beat timestamps, confidence and intervals are no longer discarded;
- project global BPM/confidence into the existing `audio_features_v1` rows and
  encode beat events plus interval-derived local tempo into the timeline pack;
- reuse the Phase 4 waveform low/mid/high series instead of computing a second
  set of frequency-energy curves;
- add the beat-grid overlay first and validate it on representative library
  material before making beat-aware transport depend on it;
- treat downbeats/bar indices as a separately validated follow-up because the
  current analyzer does not produce them;
- defer onset, short-term loudness and structural-boundary series until a
  concrete DJ feature consumes them;
- expose admin rebuild/storage diagnostics and keep optional activity/stem
  packs separate.

Existing `audio_features_v1` rows cannot backfill the beat grid: historical
analysis stored only scalar BPM/confidence and discarded beat timestamps and
intervals. Existing tracks therefore require an explicit admin timeline
rebuild after the Phase 5 extractor is introduced.

Integration tests requiring real Essentia are marked `@pytest.mark.integration`; ordinary unit tests use injected small fixtures/fakes.

## 8. Phase 6 — Signalsmith tempo and beat sync

- promote the Phase 0 adapter into `StretchDeckSource`;
- select it only when capability and timeline prerequisites are ready;
- schedule rate/seek/loop with reported latency compensation;
- implement beat-phase mapping, sync engagement/disengagement and drift correction;
- expose native playback-rate mode as degraded, not completed sync;
- define measurable drift and continuity thresholds from Phase 0 evidence.

## 9. Phase 7 — settings and diagnostics

### Private admin (`/admin`)

- feature availability/experimental rollout;
- analysis counts, rebuild and cleanup;
- instance storage limits and operational diagnostics;
- engine/browser compatibility summary where reported by clients.

### Authenticated user settings (`/settings`)

- waveform palette/zoom/follow;
- quantization and default loop sizes;
- preferred latency/quality profile where supported;
- manual layout preferences.

### Runtime/session

- current manual transition state;
- deck roles and preparation state;
- short-lived capability/error state.

Typed request schemas remain `extra="forbid"`; settings ownership and defaults are tested.

## 10. Future Epic B — Auto DJ and automatic mixing

This is explicitly outside the current foundation delivery. Before implementation it receives its own design covering:

- manoeuvre and transition catalogue;
- context, candidate and decision model;
- automation execution and observation;
- ownership, takeover, cancellation and failure recovery;
- evaluation corpus, diagnostics and quality scoring;
- UI explanations and intervention controls.

The only current requirement is architectural compatibility: the future epic must be able to use the same public engine commands and snapshots as manual controls.

## 11. Cross-cutting definition of done

Every implementation slice:

- preserves unrelated dirty worktree changes;
- includes tests that fail if the new behaviour is removed or inverted;
- updates `docs/` when runtime behaviour, API or pipeline changes;
- does not commit generated audio, timeline files, databases, WASM build output not required at runtime, or evaluation results;
- does not run local tests/builds/linters for self-check; Jenkins is authoritative;
- commits and pushes once after the entire logical slice is complete;
- checks Jenkins build/test/stage status and SonarQube through the prescribed structured sources.

## 12. Current next action

Verify Phase 4 in Jenkins, then begin Phase 5 by extending the frozen payload
with beat, tempo, energy and structure observations.

Primary external references reviewed for the spike:

- [PixiJS v8 Application lifecycle](https://pixijs.com/8.x/guides/components/application)
- [PixiJS v8 ticker](https://pixijs.com/8.x/guides/components/ticker)
- [Web Audio media element sources](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/createMediaElementSource)
- [Signalsmith Stretch official Web Audio release](https://github.com/Signalsmith-Audio/signalsmith-stretch/tree/main/web/release)
