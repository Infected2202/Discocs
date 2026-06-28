You are working in the Discocs repository.



This is a bootstrap and environment audit run.



Read first:



\* AGENTS.md

\* README.md

\* pyproject.toml

\* plans/master-implementation-order.md

\* plans/implementation-roadmap.md



Goals:



1\. Inspect the local development environment.

2\. Determine how to install and run the project on this machine.

3\. Run the lightweight baseline checks that are safe for local development.

4\. Check whether Navidrome connectivity can be tested from the current environment.

5\. Produce a clear bootstrap report.



Important constraints:



\* Do not implement Milestone 1 yet.

\* Do not modify product logic.

\* Do not refactor app code.

\* Do not commit anything.

\* Do not store credentials in git-tracked files.

\* Do not print secrets into logs or reports.

\* If credentials are needed, look for environment variables or an untracked local env file, but do not invent credentials.

\* Heavy optional dependencies must remain optional for ordinary unit tests.

\* If a command fails because the local environment is incomplete, diagnose it and propose the minimal fix.



Allowed changes:



\* You may create or update files under .codex/

\* You may create temporary local-only notes under .codex/

\* You may update .gitignore only if needed to prevent local secret files, caches, or Codex working files from being committed.



Suggested checks:



\* git status

\* python --version

\* python -m pip --version

\* python -m pip install -e ".\[dev]"

\* python -m pytest

\* python -m compileall app tests



Navidrome:



\* Inspect project documentation and code to determine expected Navidrome configuration variables.

\* If Navidrome host and credentials are available through environment variables or local config, perform only a safe connectivity check.

\* Do not mutate Navidrome data.

\* Do not run destructive actions.

\* Do not save credentials into the repository.



Output:

Create .codex/bootstrap-report.md with:



\* environment summary

\* dependency status

\* baseline test status

\* compile status

\* Navidrome connectivity status

\* detected risks

\* exact next command or prompt recommended for Milestone 1 implementation



Final response:

Summarize the report and tell the user whether the repository is ready for the Milestone 1 conductor run.

