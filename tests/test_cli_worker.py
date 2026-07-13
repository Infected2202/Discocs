"""Safety-net tests for the internal logic of `app.cli.worker()`.

`worker()` is a single ~680-line command built from closures over shared
mutable state. The closures are not reachable directly, so these tests drive
them through the public `recs worker --once` CLI entrypoint with every I/O
boundary (HTTP calls, audio download, model inference) monkeypatched. This is
the safety net that any structural refactor of `worker()` must keep green.
"""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
from typer.testing import CliRunner
from urllib.error import HTTPError

from app import cli as cli_module
from app.cli import cli
from app.config import Settings
from app.store import Store


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "app.db",
        model_dir=tmp_path / "models",
        index_dir=tmp_path,
    )


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "app.db")
    store.init()
    return store


def _task_payload(task_id: str, model_name: str, *, file_size: int = 100, mtime: int = 1) -> dict:
    return {
        "task_id": task_id,
        "track_id": 1,
        "model_name": model_name,
        "audio_url": f"/files/{task_id}",
        "path": f"{task_id}.flac",
        "file_size": file_size,
        "mtime": mtime,
    }


class FakeEffnetEmbedder:
    """Stand-in for app.embedder.DiscogsEffnetEmbedder that needs no TensorFlow/Essentia."""

    # Reset per test via `monkeypatch.setattr(FakeEffnetEmbedder, "instances", [])` -
    # lets tests assert how many (and which backend) instances got constructed.
    instances: list["FakeEffnetEmbedder"] = []

    def __init__(
        self,
        settings: Settings,
        model_name: str,
        batch_size: int | None = None,
        backend: str | None = None,
        *,
        vector: np.ndarray | None = None,
        direct_patches: np.ndarray | None = None,
        direct_patches_error: Exception | None = None,
        direct_model_output_dim: int = 8,
        direct_model_predict_error: Exception | None = None,
    ) -> None:
        self.settings = settings
        self.model_name = model_name
        self.batch_size = batch_size
        self.backend = backend
        self.model_path = settings.model_dir / f"{model_name}.pb"
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_path.touch(exist_ok=True)
        self._vector = np.array([1.0, 0.0], dtype=np.float32) if vector is None else vector
        self._direct_patches = (
            np.zeros((2, 4, 4), dtype=np.float32) if direct_patches is None else direct_patches
        )
        self._direct_patches_error = direct_patches_error
        self._direct_model_output_dim = direct_model_output_dim
        self._direct_model_predict_error = direct_model_predict_error
        type(self).instances.append(self)

    def extract_track_vector(self, path: Path) -> np.ndarray:
        return self._vector

    def extract_direct_patches(self, path: Path) -> np.ndarray:
        if self._direct_patches_error is not None:
            raise self._direct_patches_error
        return self._direct_patches

    def direct_model(self) -> "_FakeDirectModel":
        return _FakeDirectModel(self._direct_model_output_dim, predict_error=self._direct_model_predict_error)


class _FakeDirectModel:
    def __init__(self, output_dim: int, *, predict_error: Exception | None = None) -> None:
        self.output_dim = output_dim
        self.predict_error = predict_error

    def predict_patches_unpadded(self, patches: np.ndarray) -> np.ndarray:
        if self.predict_error is not None:
            raise self.predict_error
        return np.ones((len(patches), self.output_dim), dtype=np.float32)


def _make_post_json(claim_sequence: list[list[dict]]):
    calls: list[tuple[str, dict]] = []
    claim_iter = iter(claim_sequence)

    def fake_post_json(server: str, path: str, payload: dict, *, timeout: float = 60) -> dict:
        # submit_worker_buffers clears its result/failure lists in place right after
        # calling post_json, so the recorded payload must be a snapshot, not a reference.
        calls.append((path, copy.deepcopy(payload)))
        if path == "/api/v1/workers/register":
            return {}
        if path == "/api/v1/workers/claim":
            tasks = next(claim_iter, [])
            return {"tasks": tasks}
        if path == "/api/v1/workers/results":
            accepted = (
                [r["task_id"] for r in payload["results"]]
                + [r["task_id"] for r in payload["feature_results"]]
                + [r["task_id"] for r in payload["head_results"]]
            )
            return {"accepted": accepted, "rejected": []}
        if path == "/api/v1/workers/failures":
            return {"failed": [f["task_id"] for f in payload["failures"]]}
        if path == "/api/v1/workers/heartbeat":
            return {}
        raise AssertionError(f"unexpected worker endpoint: {path}")

    return fake_post_json, calls


def _make_task_state(active_map: dict[str, bool | None] | None):
    active_map = active_map or {}

    def fake_task_is_active(server: str, worker_id: str, task_id: str) -> bool:
        state = active_map.get(task_id, True)
        # `None` models a task whose state check itself 404s (task gone entirely) -
        # that must also read as "not active" so callers route into close_inactive_task.
        return bool(state) if state is not None else False

    def fake_worker_task_state(server: str, worker_id: str, task_id: str) -> dict:
        state = active_map.get(task_id, True)
        if state is None:
            raise HTTPError(server, 404, "not found", None, None)  # type: ignore[arg-type]
        return {"active": bool(state), "status": "done", "job_status": "done"}

    return fake_task_is_active, fake_worker_task_state


def run_worker(
    monkeypatch,
    tmp_path: Path,
    *,
    models: list[str],
    claim_sequence: list[list[dict]],
    embedding_backend: str = "essentia",
    active_map: dict[str, bool | None] | None = None,
    embedder_kwargs: dict | None = None,
    fake_embedder_is_effnet: bool = False,
    extra_args: list[str] | None = None,
):
    settings = _settings(tmp_path)
    store = _store(tmp_path)
    embedder_kwargs = embedder_kwargs or {}

    monkeypatch.setattr(cli_module, "get_store_and_settings", lambda: (store, settings))
    monkeypatch.setattr(cli_module, "register_worker_with_retry", lambda *a, **k: None)
    monkeypatch.setattr(cli_module, "resolve_cpu_workers", lambda cpu_workers: (1, "test"))
    monkeypatch.setattr(
        cli_module,
        "download_task_audio",
        lambda server, audio_url, target: target.write_bytes(b"fake-audio"),
    )

    fake_active, fake_state = _make_task_state(active_map)
    monkeypatch.setattr(cli_module, "task_is_active", fake_active)
    monkeypatch.setattr(cli_module, "worker_task_state", fake_state)

    fake_post_json, calls = _make_post_json(claim_sequence)
    monkeypatch.setattr(cli_module, "post_json", fake_post_json)

    if fake_embedder_is_effnet:
        # process_ready_embedding_batches / essentia-fallback both do
        # `isinstance(embedder, DiscogsEffnetEmbedder)` checks against this name.
        monkeypatch.setattr(cli_module, "DiscogsEffnetEmbedder", FakeEffnetEmbedder)

    monkeypatch.setattr(
        cli_module,
        "create_track_embedder",
        lambda settings, model_name, *, batch_size=None, backend=None: FakeEffnetEmbedder(
            settings, model_name, batch_size=batch_size, backend=backend, **embedder_kwargs
        ),
    )

    args = ["worker", "--once", "--embedding-backend", embedding_backend]
    for model in models:
        args += ["--models", model]
    args += extra_args or []

    result = CliRunner().invoke(cli, args)
    return result, calls


def test_claim_more_and_flush_round_trip(tmp_path, monkeypatch):
    """Claimed task flows through the simple (non-direct-pipeline) path and is submitted."""
    result, calls = run_worker(
        monkeypatch,
        tmp_path,
        models=["discogs_multi"],
        claim_sequence=[[_task_payload("t1", "discogs_multi")]],
        embedding_backend="essentia",  # keeps direct_embedding_pipeline off
    )

    assert result.exit_code == 0, result.output
    result_calls = [payload for path, payload in calls if path == "/api/v1/workers/results"]
    assert any(
        r["task_id"] == "t1" for call in result_calls for r in call["results"]
    ), calls
    assert not any(path == "/api/v1/workers/failures" for path, _ in calls)


def test_close_inactive_task_via_active_false(tmp_path, monkeypatch):
    """process_ready_head_batches asks task_is_active and closes via close_inactive_task."""
    result, calls = run_worker(
        monkeypatch,
        tmp_path,
        models=["discogs-effnet-heads"],
        claim_sequence=[[_task_payload("t1", "discogs-effnet-heads")]],
        active_map={"t1": False},
    )

    assert result.exit_code == 0, result.output
    assert "closed inactive task_id=t1" in result.output
    assert not any(
        r.get("task_id") == "t1"
        for path, payload in calls
        if path == "/api/v1/workers/results"
        for r in payload["head_results"]
    )
    assert not any(
        f.get("task_id") == "t1" for path, payload in calls if path == "/api/v1/workers/failures" for f in payload["failures"]
    )


def test_close_inactive_task_via_404(tmp_path, monkeypatch):
    """close_inactive_task also closes the task when the state check itself 404s."""
    result, calls = run_worker(
        monkeypatch,
        tmp_path,
        models=["discogs-effnet-heads"],
        claim_sequence=[[_task_payload("t1", "discogs-effnet-heads")]],
        active_map={"t1": None},
    )

    assert result.exit_code == 0, result.output
    assert "closed missing task_id=t1" in result.output
    assert not any(
        r.get("task_id") == "t1"
        for path, payload in calls
        if path == "/api/v1/workers/results"
        for r in payload["head_results"]
    )
    assert not any(
        f.get("task_id") == "t1" for path, payload in calls if path == "/api/v1/workers/failures" for f in payload["failures"]
    )


def test_essentia_fallback_recovers_from_direct_backend_failure(tmp_path, monkeypatch):
    """embedding_backend=auto: a broken direct-TF path falls back to the essentia backend."""
    result, calls = run_worker(
        monkeypatch,
        tmp_path,
        models=["discogs_multi"],
        claim_sequence=[[_task_payload("t1", "discogs_multi")]],
        embedding_backend="auto",
        fake_embedder_is_effnet=True,
        embedder_kwargs={"direct_patches_error": RuntimeError("direct backend unavailable")},
    )

    assert result.exit_code == 0, result.output
    assert "backend=essentia-fallback" in result.output
    result_calls = [payload for path, payload in calls if path == "/api/v1/workers/results"]
    assert any(r["task_id"] == "t1" for call in result_calls for r in call["results"]), calls
    assert not any(path == "/api/v1/workers/failures" for path, _ in calls)


def test_worker_failure_recorded_without_fallback_when_backend_pinned(tmp_path, monkeypatch):
    """embedding_backend=tensorflow: no essentia fallback, failure goes through append_worker_failure."""
    result, calls = run_worker(
        monkeypatch,
        tmp_path,
        models=["discogs_multi"],
        claim_sequence=[[_task_payload("t1", "discogs_multi")]],
        embedding_backend="tensorflow",
        fake_embedder_is_effnet=True,
        embedder_kwargs={"direct_patches_error": RuntimeError("direct backend unavailable")},
    )

    assert result.exit_code == 0, result.output
    failure_calls = [payload for path, payload in calls if path == "/api/v1/workers/failures"]
    assert failure_calls, calls
    [failure] = failure_calls[0]["failures"]
    assert failure["task_id"] == "t1"
    assert failure["error_type"] == "RuntimeError"
    assert failure["stage"] == "worker"
    assert failure["retryable"] is True


def test_process_ready_embedding_batches_accumulates_until_target_patches(tmp_path, monkeypatch):
    """Two tasks' patches are buffered and processed together once target_patches is reached."""
    result, calls = run_worker(
        monkeypatch,
        tmp_path,
        models=["discogs_multi"],
        claim_sequence=[
            [
                _task_payload("t1", "discogs_multi"),
                _task_payload("t2", "discogs_multi"),
            ]
        ],
        embedding_backend="auto",
        fake_embedder_is_effnet=True,
        embedder_kwargs={"direct_patches": np.zeros((2, 4, 4), dtype=np.float32)},
        extra_args=["--gpu-batch-size", "4", "--ready-batches", "1"],
    )

    assert result.exit_code == 0, result.output
    assert "gpu batch model=discogs_multi tasks=2 patches=4 padded=0" in result.output
    result_calls = [payload for path, payload in calls if path == "/api/v1/workers/results"]
    submitted_ids = {r["task_id"] for call in result_calls for r in call["results"]}
    assert submitted_ids == {"t1", "t2"}
    assert not any(path == "/api/v1/workers/failures" for path, _ in calls)


def test_essentia_fallback_reuses_one_embedder_for_whole_failed_gpu_batch(tmp_path, monkeypatch):
    """A whole-batch GPU predict failure falls two tasks back to essentia individually.

    Before the fix, `fallback_to_essentia_embedding` built a brand-new
    `DiscogsEffnetEmbedder` (and therefore a brand-new Essentia TensorFlow graph)
    per failed task. On a broken GPU batch that meant one fresh graph per task in
    the batch, torn down immediately after - which is what was flooding worker
    logs with Essentia's "no network created" warning. The fallback embedder must
    be built once per model and reused for every task in the batch.
    """
    monkeypatch.setattr(FakeEffnetEmbedder, "instances", [])
    result, calls = run_worker(
        monkeypatch,
        tmp_path,
        models=["discogs_multi"],
        claim_sequence=[
            [
                _task_payload("t1", "discogs_multi"),
                _task_payload("t2", "discogs_multi"),
            ]
        ],
        embedding_backend="auto",
        fake_embedder_is_effnet=True,
        embedder_kwargs={
            "direct_patches": np.zeros((2, 4, 4), dtype=np.float32),
            "direct_model_predict_error": RuntimeError("gpu batch broke"),
        },
        extra_args=["--gpu-batch-size", "4", "--ready-batches", "1"],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("backend=essentia-fallback") == 2
    result_calls = [payload for path, payload in calls if path == "/api/v1/workers/results"]
    submitted_ids = {r["task_id"] for call in result_calls for r in call["results"]}
    assert submitted_ids == {"t1", "t2"}
    assert not any(path == "/api/v1/workers/failures" for path, _ in calls)

    essentia_instances = [inst for inst in FakeEffnetEmbedder.instances if inst.backend == "essentia"]
    assert len(essentia_instances) == 1, "expected a single reused essentia fallback embedder"
