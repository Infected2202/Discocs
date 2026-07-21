# Discocs DJ implementation plan

**Status:** Phases 0-5 and Phase 6 Groups 1-2 implemented; Phase 6 Group 3 remains pending explicit approval and browser quality validation
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

- DJ crossfader endpoints, centre-unity overlap and input clamping;
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
- confirm that a complete browser-local track buffer can be transferred to the
  worklet without adding a backend live-audio path.

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

**Implementation:** complete and Jenkins verified. Deck A now owns
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

**Implementation:** foundation verified in build #268; mixer correction pass in
progress after real UI validation exposed incorrect defaults and curve semantics.

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
- implement channel trim/EQ/filter/fader, a centre-unity DJ crossfader
  (`A=100%, B=100%` at centre), explicit near-peak master protection and
  pre-crossfader deck meters;
- implement handover reconciliation without `load()` on incoming;
- implement global Next as fast protected handover;
- preserve single-deck behaviour when no incoming deck is ready.

### Required tests

- SQLite/API idempotent handover and cross-session rejection;
- starting incoming does not advance queue;
- handover advances exactly once and does not reload incoming;
- outgoing cleanup occurs only after safe retirement;
- mixer neutral/end/centre mappings, including visually centred unity EQ and
  crossfader defaults;
- explicit protection coefficients so browser compressor defaults cannot alter
  ordinary single-deck playback;
- unavailable incoming falls back without breaking program playback;
- profile changes apply only to later loads during an active mix.

### Gate

Two real locally buffered tracks can overlap and switch program role while the server queue reconciles exactly once.

## 5. Phase 3 — expanded DJ control surface

**Implementation:** visual foundation verified in build #272; interaction
and mixer correction deployed and verified in build #305. The accepted
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
- isolate live physical-deck clock projections so time/readouts update without
  rerendering the complete DJ workspace;
- define one app-level track-drag payload and physical-deck drop contract shared
  by queue and playlist sources; while a playlist drag is active, a compact
  physical-deck dock keeps the source, the existing reorder targets and the
  eligible free deck visible at the same time;
- a successful deck drop opens the workspace, makes that track the canonical
  next queue occurrence and prepares the free physical deck; it must not change
  the program role, start transport or overwrite the on-air deck;
- omit unimplemented Auto DJ controls rather than exposing non-functional placeholders.

### Tests

- open/close does not call load/play/pause/seek;
- UI state is mutually exclusive with Expanded Player;
- controls target the correct physical deck after roles alternate;
- compact player projects program deck after handover;
- keyboard/focus/Escape behaviour;
- pointer controls clamp, commit and expose accessible labels;
- dragging a track onto a physical deck positions it next and prepares that
  deck without changing program role, advancing the current queue item or
  starting transport;
- phone layout keeps the compact player and does not render an unusable dense surface.

## 6. Phase 4 — waveform artifact and renderer

**Implementation:** artifact/backend complete; renderer interaction correction
deployed and verified in build #305. The frozen v1
foundation artifact is published atomically for local and Navidrome-backed
tracks, exposed through authenticated manifest/payload/status endpoints,
and rendered in both DJ deck waveform regions from one shared decoded payload.
Artifact availability never blocks manual audio transport.

### Backend

- add `app/store/timeline.py` and schema initialization;
- add artifact encoder, atomic publisher and safe cleanup;
- add manifest, payload and batch-status endpoints;
- expose combined audio-feature/timeline readiness and rebuild in `/admin`;
- integrate existing worker acquisition for local/Navidrome-backed tracks;
- implement path + mtime + file-size invalidation.

### Frontend

- add manifest/payload client and TypeScript decoder;
- share decoded arrays between overview/detailed views;
- connect Pixi renderer to a live physical-deck clock rather than a static
  transport anchor;
- keep the detailed playhead fixed at centre and render empty tape before zero
  and after track duration;
- support click seek plus captured mouse/touch tape drag on detailed waveforms,
  and captured cursor drag on overview waveforms;
- expose loading/missing/stale/failed states without blocking manual audio.
- poll only already queued/running jobs so the DJ surface remains a read-only
  consumer and never triggers analysis itself.

### Gate

The complete waveform vertical slice works for real queue tracks before adding
the rest of timeline analysis: time and waveform advance during playback, the
detailed playhead remains centred at track edges, and both detailed and overview
surfaces support continuous mouse/touch dragging.

## 7. Phase 5 — full timeline analysis

**Implementation:** corrected production scope deployed; the explicit production
backfill is running. `audio_features_v2` is one durable local/remote worker task that
publishes scalar features and `timeline_foundation_v2` together. The timeline
adds waveform data, beat timestamps, global rhythm confidence, coverage and
interval-derived local tempo without a second analysis job. The DJ renders a
beat grid and never starts analysis. Unvalidated downbeat/onset/loudness/
structure additions remain deferred as described below.

- keep extraction offline and start it only through the existing explicit
  `/admin` audio-feature job; the DJ client remains a read-only artifact consumer;
- refactor the existing `RhythmExtractor2013` boundary so its already computed
  beat timestamps, confidence and intervals are no longer discarded;
- store global BPM/confidence as `audio_features_v2` scalar rows and encode beat
  events plus interval-derived local tempo into the timeline projection of the
  same worker result;
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
intervals. Existing tracks therefore require one explicit admin audio-feature
v2 rebuild after the Phase 5 extractor is introduced.

Integration tests requiring real Essentia are marked `@pytest.mark.integration`; ordinary unit tests use injected small fixtures/fakes.

## 8. Phase 6 — Signalsmith tempo and beat sync

Phase 6 is delivered in three ordered groups.

### Group 1 — production Signalsmith source

**Implementation:** complete; two complete browser-local deck buffers and the
native fallback are in production.

- retain the existing two-complete-`Blob` deck lifecycle; do not add a PCM
  streaming endpoint or another live backend audio dependency;
- decode each loaded physical deck completely in the browser and transfer its
  channel buffers to the Signalsmith worklet;
- promote the Phase 0 adapter into `StretchDeckSource`;
- select it only when browser capability and timeline prerequisites are ready;
- schedule rate/seek/loop with reported latency compensation;
- expose native playback-rate mode as degraded, not completed sync;
- release the compressed payload, decoded/worklet buffers and source references
  when a physical deck is retired or replaced.

Gate: two fully buffered real tracks can be loaded into the physical decks,
played, sought, looped and tempo-adjusted without a network dependency after
preparation; unsupported or failed Signalsmith initialization preserves native
playback.

### Group 2 — beat sync

**Implementation:** complete. The accepted interaction model follows Traktor:
an editable master clock owns tempo while no deck is master; AUTO promotes the
first eligible playing deck and transfers ownership when it stops; either deck
can be selected explicitly with its MASTER button; an engaged SYNC deck is a
tempo/phase follower and its pitch fader is locked. Disengaging SYNC preserves
the currently matched tempo.

- implement beat-timeline mapping and master/follower ownership;
- implement sync engagement/disengagement and initial beat-phase alignment;
- recover correctly around seek, loop, tempo changes and handover.

Gate: two real tracks align by tempo and beat phase and preserve explicit deck
roles through transport operations.

### Group 3 — correction, product state and quality gate

- implement measured drift detection and correction;
- expose Signalsmith/native capability, sync state and degraded reasons in the
  DJ workspace;
- define measurable drift and continuity thresholds from Phase 0 evidence;
- validate supported browsers and record the production results.

Gate: supported browsers pass the defined quality, drift and continuity checks;
future automatic transitions remain blocked until this gate passes.

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

Run Phase 6 Group 3 only after explicit approval: measure drift and continuity,
add bounded correction, expose final capability/degraded diagnostics and record
the supported-browser quality gate. Energy/structure remain deferred until
their payload contracts and actual UI consumers are designed.

Primary external references reviewed for the spike:

- [PixiJS v8 Application lifecycle](https://pixijs.com/8.x/guides/components/application)
- [PixiJS v8 ticker](https://pixijs.com/8.x/guides/components/ticker)
- [Web Audio media element sources](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/createMediaElementSource)
- [Signalsmith Stretch official Web Audio release](https://github.com/Signalsmith-Audio/signalsmith-stretch/tree/main/web/release)
- [Traktor Pro 4 manual: Tempo Master, AUTO, Master Clock and BeatSync](https://www.native-instruments.com/fileadmin/ni_media/downloads/manuals/traktor/Traktor-Pro-4-Manual-English-170724.pdf)
