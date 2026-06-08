import numpy as np
import pytest

from app.embedder import blend_embeddings


def test_blend_embeddings_averages_then_normalizes():
    first = np.array([1.0, 0.0], dtype=np.float32)
    second = np.array([0.0, 1.0], dtype=np.float32)

    blended = blend_embeddings([first, second])

    assert blended.dtype == np.float32
    assert np.allclose(np.linalg.norm(blended), 1.0)
    assert np.allclose(blended, np.array([0.70710677, 0.70710677], dtype=np.float32))


def test_blend_embeddings_accepts_2d_array():
    vectors = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)

    blended = blend_embeddings(vectors)

    assert np.allclose(blended, np.array([1.0, 0.0], dtype=np.float32))


def test_blend_embeddings_rejects_empty_list():
    with pytest.raises(ValueError, match="No vectors"):
        blend_embeddings([])
