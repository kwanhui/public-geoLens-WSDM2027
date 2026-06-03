"""Common interface every engine adapter implements.

The three engines (ContrastGeo, FewUser, RetrieveZero) share an inference
surface in their respective project repos: a `Locator` nn.Module whose
`forward(..., inference=True)` returns logits over a fixed city catalogue.
This module hides those mechanics behind a single `Engine.predict()` call so
the triangulator and UI can treat them uniformly.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


Granularity = Literal["post", "user"]


@dataclass(frozen=True)
class Prediction:
    """One engine's verdict on a single input."""

    city: str
    confidence: float
    top_k: list[tuple[str, float]] = field(default_factory=list)
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    note: str = ""
    abstain: bool = False  # True when the engine found no usable location signal
    evidence: str = ""  # short human-readable basis (e.g. the matched toponym)

    @property
    def mode(self) -> str:
        """"real" if this came from a live model, "stub" if from the offline
        fallback. Derived from the note prefix so provenance can be surfaced
        in the UI and recorded in the run manifest."""
        return "stub" if self.note.strip().lower().startswith("stub") else "real"


@dataclass
class GeolocateInput:
    """Input bundle passed to engines. Fields are optional; an engine reads
    only what its granularity needs."""

    post: str | None = None
    user_handle: str | None = None
    user_posts: list[str] | None = None
    user_profile: dict | None = None


class Engine(ABC):
    """Abstract base for all three engine adapters."""

    name: str
    granularity: Granularity

    def __init__(self, *, stub: bool | None = None) -> None:
        if stub is None:
            stub = os.getenv("GEOLENS_STUB_MODE", "0") == "1"
        self.stub = stub

    @abstractmethod
    def predict(self, payload: GeolocateInput, k: int = 5) -> Prediction:
        """Return this engine's top prediction (with top-k for triangulation)."""

    @property
    def supports_user(self) -> bool:
        return self.granularity == "user"

    @property
    def supports_post(self) -> bool:
        return self.granularity == "post"
