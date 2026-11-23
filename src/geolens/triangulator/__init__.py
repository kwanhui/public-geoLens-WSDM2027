"""Cross-engine fusion: agreement, disagreement, calibration."""

from geolens.triangulator.consensus import (
    CONSENSUS_RULES,
    TriangulationResult,
    majority_tied,
    triangulate,
)

__all__ = ["CONSENSUS_RULES", "TriangulationResult", "majority_tied", "triangulate"]
