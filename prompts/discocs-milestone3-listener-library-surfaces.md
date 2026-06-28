You are the lead implementation agent for the Discocs repository.



Current context:

Milestone 1 Stable Library Graph is implemented.

Milestone 2 Playback Foundation is implemented.

The backend now has stable artists, releases, search, playback sessions, queues, raw events, and preference counters.



Read first:



\* AGENTS.md

\* README.md

\* pyproject.toml

\* .codex/implementation-ledger.md

\* .codex/milestone1-fixup-report.md

\* .codex/milestone2-ledger.md

\* .codex/milestone2-report.md

\* plans/master-implementation-order.md

\* plans/implementation-roadmap.md

\* plans/data-model-overview.md

\* any dashboard, artist page, release page, library UI, or listener-facing surface plan files in plans/



Goal:

Implement Milestone 3: Listener Library Surfaces.



Scope:

Build the first user-facing surfaces on top of the stable Milestone 1 and Milestone 2 APIs.



Required capabilities:



1\. Search UI uses /api/v1/search where appropriate.

2\. Artist surface:



&#x20;  \* artist identity/header;

&#x20;  \* discography;

&#x20;  \* top tracks if available;

&#x20;  \* links to releases/tracks.

3\. Release surface:



&#x20;  \* release identity/header;

&#x20;  \* release artists;

&#x20;  \* track list;

&#x20;  \* basic playback entry points if existing player APIs allow it.

4\. Dashboard foundation:



&#x20;  \* recently played or listen-again style sections if playback history exists;

&#x20;  \* library entry points for artists/releases/search;

&#x20;  \* do not require autoplay or generated mixes.

5\. Preserve existing prototype navigation and old track/similar workflows.

6\. Add tests for backend/API/UI helper behavior where the repository already has test patterns.

7\. Add lightweight documentation/reporting under .codex/.



Out of scope:



\* Do not implement autoplay generation logic.

\* Do not implement generated mixes.

\* Do not implement Flow.

\* Do not implement MAEST.

\* Do not redesign the whole frontend.

\* Do not mutate Navidrome data.

\* Do not require live Navidrome for ordinary tests.

\* Do not require Essentia, model files, or heavy optional dependencies for ordinary tests.



Compatibility requirements:



\* Preserve existing track APIs, scan/analyze/index/similar behavior.

\* Preserve Milestone 1 /api/v1 artist/release/search APIs.

\* Preserve Milestone 2 /api/v1/playback APIs.

\* Existing tests must remain green.



Working mode:

Proceed in small, reviewable slices.

Do not ask the user whether to start the next slice inside Milestone 3.

Continue automatically while the next step is within Milestone 3, tests are passing or fixable, and no product decision is blocked.



Before editing:



1\. Inspect current frontend/static/templates/API structure.

2\. Read relevant plan files.

3\. Create .codex/milestone3-ledger.md with:



&#x20;  \* baseline status

&#x20;  \* planned slices

&#x20;  \* risk notes

&#x20;  \* test commands

&#x20;  \* current progress



Suggested slice order:



1\. Baseline audit and plan extraction.

2\. Identify current UI structure and routing.

3\. Add or adapt frontend API client helpers for /api/v1/search, artists, releases, and playback where needed.

4\. Implement artist surface.

5\. Implement release surface.

6\. Implement dashboard foundation using available library and playback data.

7\. Preserve old prototype pages and navigation.

8\. Add focused tests.

9\. Final report.



Quality gates:

Run:



\* python -m pytest --basetemp .pytest-tmp

\* python -m compileall app tests



If the app has frontend-specific build/test commands, discover and run only the lightweight ones that are already documented and safe.



Output:

Create or update:



\* .codex/milestone3-ledger.md

\* .codex/milestone3-report.md



Commit:

Create focused commits after green meaningful slices.



Final response:

When finished or stopped, report:



\* what was implemented

\* commits created

\* tests run and results

\* remaining risks

\* whether manual UI testing can start



