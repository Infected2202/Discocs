# Discocs Bootstrap Report

## Environment Summary

- Date: 2026-06-20
- Working directory: `C:\Users\nexus\scripts\discocs`
- Git branch: `codex/milestone-1-stable-library-graph`
- Global `python`: not available on `PATH`
- Global `pip`: not available on `PATH`
- Existing `.venv`: present, but it is a Unix/Linux-style venv (`bin/`, no `Scripts/python.exe`) and is not runnable from this Windows environment.
- Usable audit Python: bundled Codex Python `3.12.13`
- Audit venv used: `%TEMP%\discocs-bootstrap-venv`
- CLI check: `recs --help` works from the audit venv.

## Dependency Status

- Project metadata requires Python `>=3.11,<3.15`; bundled Python `3.12.13` satisfies this.
- Normal development install command from docs: `python -m pip install -e ".[dev]"`
- Install result: succeeded in the temporary audit venv after network approval.
- Heavy optional dependencies were not installed. `essentia-tensorflow` remains optional and was not required for baseline tests.
- Dependency risk found: `python -m pytest` failed immediately after a clean `.[dev]` install because `fastapi.testclient` / `starlette.testclient` requires `httpx2`, but `httpx2` is not declared in `pyproject.toml` dev dependencies.
- Temporary audit-only fix: installed `httpx2` into `%TEMP%\discocs-bootstrap-venv`; tests then ran successfully.
- Minimal project fix proposed before future conductor runs: add the current test client dependency to `[project.optional-dependencies].dev`, likely `httpx2`, or pin FastAPI/Starlette to a version whose test client dependency is already satisfied.

## Baseline Test Status

- Initial command: `%TEMP%\discocs-bootstrap-venv\Scripts\python.exe -m pytest`
- Initial result: failed during collection with `RuntimeError: The starlette.testclient module requires the httpx2 package to be installed.`
- After temporary `httpx2` install: `188 passed, 4 warnings`
- Warnings: FastAPI `on_event` deprecation warnings in `app/main.py`; not blocking.

## Compile Status

- Command: `%TEMP%\discocs-bootstrap-venv\Scripts\python.exe -m compileall app tests`
- Result: passed.

## Navidrome Connectivity Status

- Expected configuration variables from docs/code:
  - `DISCOCS_NAVIDROME_URL`
  - `DISCOCS_NAVIDROME_USER`
  - `DISCOCS_NAVIDROME_PASSWORD`
  - `DISCOCS_NAVIDROME_AUTH_MODE`
  - `DISCOCS_NAVIDROME_TIMEOUT_SECONDS`
  - `DISCOCS_NAVIDROME_DOWNLOAD_MODE`
  - `DISCOCS_NAVIDROME_TEMP_DIR`
- Environment variables matching Discocs/Navidrome were not present in the shell.
- Runtime settings file exists at `data/settings.json`; values were not printed.
- Redacted config probe showed Navidrome URL, user, and password are configured.
- Safe connectivity command: `%TEMP%\discocs-bootstrap-venv\Scripts\recs.exe navidrome-ping`
- First ping attempt was blocked by sandbox socket policy (`WinError 10013`).
- Elevated safe ping result: `navidrome=ok api_version=1.16.1 server_version=0.61.2 (aa84e645)`.
- No Navidrome data mutation commands were run.

## Detected Risks

- Windows-side global Python is missing; use a project-local Windows venv or the bundled Codex Python for local checks.
- The existing `.venv` is not usable on Windows; it appears to be from a Unix/Linux environment.
- `.[dev]` is incomplete for tests with the currently resolved FastAPI/Starlette stack because `httpx2` is missing.
- `.codex` has explicit ACL deny entries for normal sandbox writes. Writing this report may require elevated file access in this environment.
- LAN app URL `http://192.168.1.41:8711/` did not accept a TCP connection from this environment during the audit; the API/UI may not currently be running there.
- Dependency resolution is floating. Future installs may pick newer FastAPI/Starlette/Pydantic/Pytest versions unless pinned or locked.

## Exact Next Command Or Prompt For Milestone 1

Recommended next prompt:

```text
Implement Milestone 1 Phase 1 Slices 1-3 only: add normalized artist/release schema tables, store upsert/lookup helpers, and a repeatable backfill from existing track metadata. Preserve existing track APIs, scan/analyze/index flows, and Navidrome behavior. Add focused tests and run pytest plus compileall.
```

Before running that conductor pass, apply the minimal dev dependency fix so a clean `python -m pip install -e ".[dev]" && python -m pytest` works without manual `httpx2` installation.
