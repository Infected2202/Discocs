from app.cli import worker_failure_retryable


def test_worker_dependency_failures_are_not_retryable():
    assert not worker_failure_retryable(
        RuntimeError("essentia-tensorflow is required for rhythm extraction")
    )
    assert not worker_failure_retryable(ImportError("No module named 'essentia'"))


def test_worker_runtime_failures_remain_retryable():
    assert worker_failure_retryable(RuntimeError("temporary download failed"))
