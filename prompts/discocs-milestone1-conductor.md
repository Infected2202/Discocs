You are the lead implementation agent for the Discocs repository.



Read first:



\* AGENTS.md

\* README.md

\* pyproject.toml

\* plans/master-implementation-order.md

\* plans/implementation-roadmap.md

\* plans/data-model-overview.md

\* plans/phase-1-library-normalization-spec.md

\* plans/phase-2-entity-apis-spec.md

\* .codex/bootstrap-report.md, if it exists



Goal:

Implement Milestone 1: Stable Library Graph.



Scope:



\* Implement Phase 1 library normalization.

\* Implement the minimal Phase 2 stable entity API surface needed for artists, releases, discography, release tracks, and search.

\* Keep the existing prototype behavior working.

\* Keep Navidrome-related behavior working if it is already implemented.



Out of scope:



\* Do not implement dashboard UI.

\* Do not implement autoplay.

\* Do not implement generated mixes.

\* Do not implement Flow.

\* Do not implement MAEST.

\* Do not do broad frontend redesign.

\* Do not remove legacy track fields unless a plan explicitly requires it.



Source of truth:

The plans directory is the product/spec source of truth.

The current codebase is the implementation source of truth.

If the plan and code disagree, preserve working behavior and document the decision in .codex/implementation-ledger.md.



Working mode:

Proceed in small, reviewable slices.

Do not ask the user whether to start Phase 1, continue to Phase 2, or proceed to the next slice.

Continue automatically while the next step is within Milestone 1, tests are passing or fixable, and no product decision is blocked.



Subagent policy:

Use subagents only for read-only analysis and review.

Only the main agent may edit files.

Use read-only subagents for:



\* current schema/store behavior

\* metadata/scanner/Navidrome behavior

\* existing tests and compatibility gates

\* final diff review



Environment policy:

You may fix local environment issues needed for development.

You may install normal project development dependencies.

Do not install or require heavy optional dependencies for ordinary unit tests unless the repository already requires them.

Do not store credentials in git-tracked files.

Do not print secrets into logs or reports.

Do not perform destructive external actions.



Navidrome policy:

If Navidrome configuration and credentials are available through environment variables or an untracked local config file, you may run safe read-only connectivity checks.

Do not mutate Navidrome data.

Do not rely on Navidrome being available for ordinary unit tests.

If Navidrome is unavailable, document it but continue with local implementation and tests.



Before editing:



1\. Inspect the repository structure.

2\. Read the required plan files.

3\. Inspect current store/schema, scanner, metadata, Navidrome integration, API routes, and tests.

4\. Create or update .codex/implementation-ledger.md with:



&#x20;  \* baseline status

&#x20;  \* planned slices

&#x20;  \* risk notes

&#x20;  \* test commands

&#x20;  \* current progress



Suggested slice order:



1\. Baseline audit.

2\. Metadata field support required by Phase 1.

3\. Normalized schema/tables.

4\. Normalization helpers.

5\. Store upsert/lookup helpers.

6\. Idempotent backfill/migration path.

7\. Scanner/Navidrome compatibility.

8\. Query methods for artists/releases/search.

9\. Minimal /api/v1 entity routes:



&#x20;  \* GET /api/v1/search

&#x20;  \* GET /api/v1/artists/{id}

&#x20;  \* GET /api/v1/artists/{id}/discography

&#x20;  \* GET /api/v1/releases/{id}

&#x20;  \* GET /api/v1/releases/{id}/tracks

10\. Regression cleanup and documentation notes.



Quality gates after every slice:

Run:



\* python -m pytest

\* python -m compileall app tests



If tests fail:



\* Fix the failure before continuing.

\* If the failure is caused by missing optional heavy dependencies, preserve the existing project rule that normal unit tests must not require heavy analysis dependencies.

\* Document any unavoidable limitation in .codex/implementation-ledger.md.



Commit policy:

After every green slice, create a git commit with a clear message.

Do not create one huge commit for the entire milestone.



Stop conditions:

Stop only if:



\* Milestone 1 is complete.

\* A required product decision is genuinely ambiguous and cannot be resolved from the plans.

\* The repository is in a risky state that cannot be repaired safely.

\* A command requires credentials, external services, or destructive actions that were not explicitly allowed.



Final response:

When finished or stopped, report:



\* what was implemented

\* commits created

\* tests run and their result

\* Navidrome checks run, if any

\* remaining risks

\* exact next recommended milestone prompt

