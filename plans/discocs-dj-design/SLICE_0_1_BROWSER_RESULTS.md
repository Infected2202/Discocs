# Slice 0.1 browser capability results

**Date:** 2026-07-20
**Scope:** Web Audio graph and media-source lifecycle

## Automated contract evidence

The Slice 0.1 test suite constructs a shared fake `AudioContext` and verifies:

- both persistent strips terminate in one mix/master/protection graph;
- attaching Deck B leaves Deck A's source/transport identity connected;
- equal-power endpoints and centre, input clamping and 15 ms audio-time ramp
  mapping;
- all scheduled ramp log records contain start/end audio timestamps and the
  context state;
- a stale load generation is released and cannot attach;
- replacement aborts the superseded load signal and releases the old media
  source before attaching the current generation;
- release disconnects the node, pauses and clears/reloads the media element,
  and revokes an owned object URL exactly once.

These checks are deterministic and run in the Jenkins UI test stage. They
prove resource ownership and graph topology, not audible continuity.

## Controlled browser attempt

The Codex in-app Chromium browser was available, but its security policy
blocked navigation to the self-contained `data:` probe page required to
instantiate and inspect a disposable Web Audio graph. The blank-page
evaluation sandbox did not expose browser globals. No policy bypass or local
development server was used.

Consequently, this environment did not produce a trustworthy browser version,
context-resume transition, or audible continuity measurement. Jenkins remains
the authoritative automated gate; a future controlled browser stage should
record user agent/version, initial/resumed context state, the two source-node
context identities, and observed ramp timestamps. Audible continuity still
requires the manual listening check already called out by the technical plan.

## Decision

The technical design is confirmed without a file-level contract change.
Phase 1 is unblocked at the module boundary. Its rollout must retain capability
detection and recoverable context-resume errors, and must not claim the manual
audibility check as completed until a controlled browser run records it.
