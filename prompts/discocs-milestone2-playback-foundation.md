You are the lead implementation agent for the Discocs repository.



Current context:

Milestone 1 Stable Library Graph has been implemented on the current branch history. The normalized artist/release graph exists and has been backfilled successfully on the local database.



Read first:



\* AGENTS.md

\* README.md

\* pyproject.toml

\* .codex/bootstrap-report.md

\* .codex/preflight-report.md

\* .codex/implementation-ledger.md

\* .codex/milestone1-review-report.md

\* .codex/milestone1-fixup-report.md

\* plans/master-implementation-order.md

\* plans/implementation-roadmap.md

\* plans/data-model-overview.md

\* plans/phase-3-playback-sessions-queue-events-spec.md, if it exists

\* any other Phase 3 playback/session/queue/event plan files in plans/



Goal:

Implement Milestone 2: Playback Foundation.



Scope:

Implement Phase 3 playback sessions, queue, playback events, and preference counters.



Required backend capabilities:



1\. Durable playback sessions.

2\. Durable playback queue items.

3\. Raw playback event history.

4\. User preference counters for tracks, releases, and artists.

5\. API routes under /api/v1/playback for:



&#x20;  \* creating sessions

&#x20;  \* reading sessions

&#x20;  \* updating session state

&#x20;  \* reading queue

&#x20;  \* replacing/updating queue

&#x20;  \* appending queue items if the plan requires it

&#x20;  \* recording playback events



Required event semantics:



\* Raw playback\_events are the source of truth.

\* queue\_click is navigation and must not be counted as skip.

\* early skip is stronger negative feedback than late skip.

\* completed/play\_threshold\_reached are positive or weak-positive signals according to the plan.

\* liked, disliked, replayed, saved\_to\_playlist, removed\_from\_queue, autoplay\_toggled, and preference\_changed must be represented if required by the Phase 3 plan.

\* Aggregated preference counters must be recomputable from raw events.

\* Do not destroy raw event history when aggregate logic changes.



Out of scope:



\* Do not implement dashboard UI.

\* Do not implement autoplay generation logic.

\* Do not implement generated mixes.

\* Do not implement Flow.

\* Do not implement MAEST.

\* Do not do broad frontend redesign.

\* Do not change unrelated recommendation algorithms except where required to store playback events/preferences.

\* Do not mutate Navidrome data.



Compatibility requirements:



\* Preserve Milestone 1 normalized artist/release APIs.

\* Preserve existing track APIs, scan/analyze/index/similar behavior.

\* Keep Navidrome behavior working.

\* Keep heavy optional dependencies optional.

\* Ordinary unit tests must not require Essentia, real model files, live Navidrome, or external services.



Working mode:

Proceed in small, reviewable slices.

Do not ask the user whether to start the next slice inside Milestone 2.

Continue automatically while the next step is within Milestone 2, tests are passing or fixable, and no product decision is blocked.



Before editing:



1\. Inspect current schema/store/API/test structure.

2\. Read the Phase 3 plan files.

3\. Create .codex/milestone2-ledger.md with:



&#x20;  \* baseline status

&#x20;  \* planned slices

&#x20;  \* risk notes

&#x20;  \* test commands

&#x20;  \* current progress



Suggested slice order:



1\. Baseline audit and Phase 3 plan extraction.

2\. Playback schema/tables and migrations.

3\. Store helpers for sessions and queue.

4\. Store helpers for raw playback events.

5\. Preference aggregation and recomputation helpers.

6\. /api/v1/playback session routes.

7\. /api/v1/playback queue routes.

8\. /api/v1/playback event route.

9\. Regression tests for event semantics:



&#x20;  \* queue\_click is not skip

&#x20;  \* early skip vs late skip

&#x20;  \* completion/play threshold

&#x20;  \* like/dislike/replay/save/remove

&#x20;  \* recomputation from raw events

10\. Compatibility tests for existing Milestone 1 and legacy behavior.

11\. Final review and documentation notes.



Quality gates after each meaningful slice:

Run:



\* python -m pytest --basetemp .pytest-tmp

\* python -m compileall app tests



If tests fail:



\* Fix the failure before continuing.

\* If the failure is caused by missing optional heavy dependencies, preserve the project rule that normal unit tests must not require heavy dependencies.

\* Document any unavoidable limitation in .codex/milestone2-ledger.md.



Commit policy:

After every green meaningful slice, create a git commit with a clear message.

Do not create one huge commit for the entire milestone unless the implementation is genuinely very small.



Stop conditions:

Stop only if:



\* Milestone 2 is complete.

\* A required product decision is genuinely ambiguous and cannot be resolved from the plans.

\* The repository is in a risky state that cannot be repaired safely.

\* A command requires credentials, external services, destructive actions, or Navidrome mutation that was not explicitly allowed.



Output:

Create or update:



\* .codex/milestone2-ledger.md

\* .codex/milestone2-report.md



Final response:

When finished or stopped, report:



\* what was implemented

\* commits created

\* tests run and results

\* remaining risks

\* whether a review-only hardening run is recommended before manual app testing

