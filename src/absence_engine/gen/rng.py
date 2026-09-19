"""Seeded random streams, one named substream per generator.

Naming the streams means adding a source, or changing how one works, cannot
shift the numbers any other source produces. That is what makes "regenerate
and diff" a useful habit rather than a wall of noise.
"""

from __future__ import annotations

import hashlib

import numpy as np


def substream(seed: int, name: str) -> np.random.Generator:
    digest = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def lognormal_from_mean(
    rng: np.random.Generator, mean: float, sigma: float, size: int | tuple[int, ...]
) -> np.ndarray:
    """Lognormal draws whose *arithmetic* mean is ``mean``.

    numpy parameterises by the mean of the underlying normal, which is not what
    anyone means when they write ``mean: 42`` in a config file.
    """
    return rng.lognormal(np.log(mean) - 0.5 * sigma**2, sigma, size)


def beta_around(
    rng: np.random.Generator, mean: float, concentration: float, size: int
) -> np.ndarray:
    mean = float(np.clip(mean, 1e-3, 1 - 1e-3))
    return rng.beta(mean * concentration, (1.0 - mean) * concentration, size)


def choice_from_mix(rng: np.random.Generator, mix: dict[str, float], size: int) -> np.ndarray:
    labels = list(mix)
    weights = np.array([mix[k] for k in labels], dtype=float)
    return rng.choice(labels, size=size, p=weights / weights.sum())
