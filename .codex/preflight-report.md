# Discocs Preflight Dependency Report

## Scope

Small dependency metadata fix only. No Milestone 1 implementation, product code
refactor, or test changes were made.

## Inputs Read

- `.codex/bootstrap-report.md`
- `pyproject.toml`
- `tests/test_api.py`

## Finding

`tests/test_api.py` imports `fastapi.testclient.TestClient`. With the currently
resolved FastAPI/Starlette stack, Starlette's test client requires `httpx2`.
The previous `dev` extra installed `pytest` but did not install `httpx2`, so a
clean `python -m pip install -e ".[dev]"` could still fail during test
collection.

## Change

Added `httpx2>=2.4` to `[project.optional-dependencies].dev` in
`pyproject.toml`.

Heavy optional dependencies remain optional:

- `essentia-tensorflow` stays under the Linux-only `essentia` extra.
- `muq`, `torch`, and `torchaudio` stay under the `muq` extra.

## Verification

Used a fresh audit virtualenv at `%TEMP%\discocs-preflight-venv`.

Commands:

```powershell
%TEMP%\discocs-preflight-venv\Scripts\python.exe -m pip install -e ".[dev]"
%TEMP%\discocs-preflight-venv\Scripts\python.exe -m pytest
%TEMP%\discocs-preflight-venv\Scripts\python.exe -m compileall app tests
```

Results:

- Clean `.[dev]` install succeeded and installed `httpx2` from project metadata.
- `pytest`: `188 passed, 4 warnings`
- `compileall`: passed

Warnings were existing FastAPI `on_event` deprecation warnings and are not
blocking for this preflight fix.
