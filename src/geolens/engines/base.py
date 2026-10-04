"""Common interface every engine adapter implements.

An adapter wraps one method, whether a frozen encoder, a string match or a
prompted model, behind a single `Engine.predict()` call.
`geolens.engines.registry` holds the roster this build ships, and
`docs/adding-an-engine.md` is the walkthrough for adding one.
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
    # Whether `cost_usd` was estimated from a listed price. False both for an
    # engine that calls no model and for a model whose price is not listed,
    # which reports no cost rather than another model's rates.
    cost_is_estimated: bool = False
    note: str = ""
    abstain: bool = False  # True when the engine found no usable location signal
    evidence: str = ""  # short human-readable basis (e.g. the matched toponym)
    # A call that was configured for live inference and did not return a usable
    # answer. It carries no city and is excluded from fusion, from the
    # per-level consensus and from the verification flag.
    failed: bool = False
    error_class: str = ""  # exception or failure class, e.g. "JSONDecodeError"
    # A call that returned but said nothing the workbench can use, as opposed
    # to one that raised. ``no_catalogue_place`` is the only value: the model
    # answered and named nothing in the closed catalogue.
    outcome: str = ""
    # The engine was not run at all, because the input carries nothing it can
    # read (a user-level engine with no timeline). Excluded in the same way.
    skipped: bool = False
    reason: str = ""  # why it was not run, in plain words

    @property
    def mode(self) -> str:
        """Provenance of this one result, recorded in the run manifest.

        ``real`` for live model output, ``stub`` for the keyless placeholder,
        ``failed`` for a live call that raised, could not be parsed, or named
        nothing in the catalogue, and ``skipped`` for an engine that was never
        called. Read ``outcome`` to tell a content outcome from an error.
        """
        if self.skipped:
            return "skipped"
        if self.failed:
            return "failed"
        return "stub" if self.note.strip().lower().startswith("stub") else "real"

    @property
    def usable(self) -> bool:
        """Whether this result may enter fusion, consensus and the flag."""
        return not (self.failed or self.skipped)


def failed_prediction(
    engine_name: str,
    *,
    error_class: str,
    detail: str = "",
    latency_ms: float = 0.0,
) -> Prediction:
    """An explicit error result for an engine whose live call did not succeed.

    No city, no top-k and no confidence, so a caller that reads the fields
    rather than the flag cannot mistake it for a low-confidence answer.
    """
    suffix = f" ({detail})" if detail else ""
    return Prediction(
        city="",
        confidence=0.0,
        top_k=[],
        latency_ms=latency_ms,
        cost_usd=0.0,
        note=f"call failed: {engine_name} ({error_class}){suffix}",
        failed=True,
        error_class=error_class,
    )


NO_CATALOGUE_PLACE = "no_catalogue_place"
NO_CATALOGUE_PLACE_NOTE = "reply named no catalogue place"


def no_catalogue_place_prediction(
    engine_name: str, *, latency_ms: float = 0.0
) -> Prediction:
    """A live reply that named nothing in the closed catalogue.

    The call succeeded, so this is a content outcome rather than an error,
    and it says so. It carries no place and is excluded from the fusion, the
    per-level consensus and the verification flag, exactly as a failed call is.
    """
    return Prediction(
        city="",
        confidence=0.0,
        top_k=[],
        latency_ms=latency_ms,
        cost_usd=0.0,
        note=f"{NO_CATALOGUE_PLACE_NOTE}: {engine_name}",
        failed=True,
        error_class="NoCatalogueCity",
        outcome=NO_CATALOGUE_PLACE,
    )


def skipped_prediction(engine_name: str, *, reason: str) -> Prediction:
    """A result for an engine that was never called, with the reason why."""
    return Prediction(
        city="",
        confidence=0.0,
        top_k=[],
        note=f"not run: {engine_name} ({reason})",
        skipped=True,
        reason=reason,
    )


@dataclass
class GeolocateInput:
    """Input bundle passed to engines. Fields are optional; an engine reads
    only what its granularity needs."""

    post: str | None = None
    user_handle: str | None = None
    user_posts: list[str] | None = None


class Engine(ABC):
    """Abstract base for all engine adapters."""

    name: str
    granularity: Granularity
    # What this engine sends to a model, for the run manifest. Empty for an
    # engine that calls no model; an adapter sets it from the constants its
    # own call uses, so the manifest cannot name a setting that is not sent.
    sampling_parameters: dict[str, object] = {}
    # Whether a call sends the input text off this server. The page offers a
    # preset that runs only the engines for which this is False, for text that
    # may not be sent to a third party, and GET /instance reports it.
    calls_a_third_party: bool = False
    # Whether this engine needs an API key or a downloaded model. Placeholder
    # mode exists so that a keyless clone still runs. An engine that needs no
    # credentials should serve its real answer in that mode, so that a
    # keyless visitor receives at least one real prediction.
    needs_credentials: bool = True

    def __init__(self, *, stub: bool | None = None) -> None:
        if stub is None:
            placeholder_mode = os.getenv("GEOLENS_STUB_MODE", "0") == "1"
            if not self.needs_credentials:
                # A test can still force the placeholder, which is what the
                # deterministic-output tests want.
                placeholder_mode = os.getenv("GEOLENS_STUB_RULE_BASED", "0") == "1"
            stub = placeholder_mode
        self.stub = stub

    @abstractmethod
    def predict(self, payload: GeolocateInput, k: int = 5) -> Prediction:
        """Return this engine's top prediction (with top-k for triangulation)."""
