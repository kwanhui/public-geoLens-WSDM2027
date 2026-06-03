"""Small statistics helpers for honest reporting on small evaluation sets.

The bundled case-study set has ~48 evaluable rows, so point accuracies carry
wide uncertainty. We report Wilson score intervals (better than the normal
approximation at small n and near 0/1) so a reader does not over-read a gap
between two engines that is within sampling noise.
"""

from __future__ import annotations

import math

# z for a 95% two-sided interval.
Z_95 = 1.959963984540054


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion.

    Returns (low, high), each clamped to [0, 1]. For n == 0 returns (0.0, 0.0).
    """
    if n <= 0:
        return 0.0, 0.0
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = phat + z2 / (2 * n)
    half = z * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    low = (centre - half) / denom
    high = (centre + half) / denom
    # Snap the degenerate edges exactly (avoids tiny FP residue like 6e-18,
    # and a perfect/zero score has a 0/1 bound by construction).
    low = 0.0 if successes <= 0 else max(0.0, low)
    high = 1.0 if successes >= n else min(1.0, high)
    return low, high
