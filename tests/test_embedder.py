import numpy as np
import pytest

from app.embedder import pool_and_normalize


def test_pool_and_normalize_means_patches():
    vector = pool_and_normalize(np.array([[3.0, 0.0], [0.0, 3.0]], dtype=np.float32))

    assert vector.dtype == np.float32
    assert np.allclose(np.linalg.norm(vector), 1.0)
    assert np.allclose(vector, np.array([0.70710677, 0.70710677], dtype=np.float32))


def test_pool_and_normalize_rejects_zero_vector():
    with pytest.raises(ValueError, match="zero norm"):
        pool_and_normalize(np.zeros((2, 3), dtype=np.float32))
