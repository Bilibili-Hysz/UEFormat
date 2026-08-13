"""Pure-Python helpers for batch-applying UEFormat morph deltas."""

from __future__ import annotations

import numpy as np


def morph_coordinates(base_coordinates, deltas) -> np.ndarray:
    """Return base vertex coordinates with all morph deltas accumulated.

    ``numpy.add.at`` is intentional: UEFormat may contain multiple deltas for
    the same vertex, and advanced-index ``+=`` would not reliably accumulate
    repeated indices.
    """
    base = np.asarray(base_coordinates, dtype=np.float32)
    if base.size % 3:
        raise ValueError("base vertex coordinates must contain XYZ triples")
    coordinates = base.reshape((-1, 3)).copy()
    deltas = tuple(deltas)
    if not deltas:
        return coordinates.reshape(-1)

    indices = np.asarray([getattr(delta, "vertex_index") for delta in deltas])
    if indices.ndim != 1 or not np.issubdtype(indices.dtype, np.integer):
        raise ValueError("morph vertex indices must be integers")
    if np.any(indices < 0) or np.any(indices >= len(coordinates)):
        raise ValueError("morph vertex index is outside the base mesh")

    offsets = np.asarray([getattr(delta, "position") for delta in deltas], dtype=np.float32)
    if offsets.shape != (len(deltas), 3):
        raise ValueError("morph positions must contain XYZ triples")
    np.add.at(coordinates, indices.astype(np.intp, copy=False), offsets)
    return coordinates.reshape(-1)
