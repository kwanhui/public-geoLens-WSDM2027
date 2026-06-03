"""RetrieveZero adapter: zero-shot user geolocation with LLM-retrieved knowledge.

Two modes:
- **Stub**: deterministic placeholder.
- **Real**: frozen-encoder cosine-similarity using intfloat/e5-large (the
  encoder the RetrieveZero paper uses). Cities are described using the
  Modular Retrieval profiles produced by the cold-start onboarding wizard
  (aliases / landmarks / foods), which are the same MoR fields the paper's
  pre-training step generates from an LLM. When a city has no MoR profile
  cached yet, we fall back to its plain name, so RetrieveZero has more to
  work with for any city the user has onboarded.
"""

from __future__ import annotations

import logging

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._stubs import stub_predict
from geolens.engines.base import Engine, GeolocateInput, Prediction
from geolens.onboarding.wizard import _load_cached as _load_cached_profile

logger = logging.getLogger(__name__)

ENCODER = "intfloat/e5-large"


def _describe_city(name: str) -> str:
    """Fold the cached MoR profile (if any) into a passage e5-large can embed."""
    profile = _load_cached_profile(name)
    if profile is None:
        return f"passage: {name}"

    parts: list[str] = [name]
    if profile.aliases:
        parts.append("also known as " + ", ".join(profile.aliases))
    if profile.landmarks:
        parts.append("landmarks include " + ", ".join(profile.landmarks))
    if profile.foods:
        parts.append("local foods include " + ", ".join(profile.foods))
    if profile.notes:
        parts.append(profile.notes)
    return "passage: " + ". ".join(parts)


class RetrieveZeroEngine(Engine):
    name = "retrievezero"
    granularity = "user"

    def __init__(
        self,
        *,
        stub: bool | None = None,
        encoder: str = ENCODER,
        cities: list[str] | None = None,
    ) -> None:
        super().__init__(stub=stub)
        self.encoder = encoder
        self.cities = cities or DEFAULT_CITIES

    def _query_text(self, payload: GeolocateInput) -> str | None:
        # E5-large expects a "query: " prefix for asymmetric retrieval.
        if payload.user_posts:
            return "query: " + "\n".join(payload.user_posts)
        if payload.post:
            return "query: " + payload.post
        return None

    def predict(self, payload: GeolocateInput, k: int = 5) -> Prediction:
        if self.stub:
            return stub_predict(
                self.name, payload, k, sleep_ms=120.0, note="stub: RetrieveZero"
            )

        text = self._query_text(payload)
        if not text:
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note="stub: RetrieveZero (no input)")

        try:
            from geolens.engines._encoder import encoder_similarity_predict

            return encoder_similarity_predict(
                engine_name=self.name,
                encoder=self.encoder,
                query_text=text,
                cities=self.cities,
                describe_city=_describe_city,
                # MoR cache contents may change as users onboard cities, so bust the embedding
                # cache by date so a freshly-onboarded city actually shifts predictions.
                description_fn_id=_describe_fn_cache_id(),
                k=k,
            )
        except ImportError as e:
            logger.warning("torch/transformers not installed (%s); falling back to stub.", e)
            self.stub = True
            return stub_predict(self.name, payload, k, sleep_ms=120.0, note="stub: RetrieveZero (no torch)")


def _describe_fn_cache_id() -> str:
    """Cache key salt that reflects the current set of onboarded cities."""
    from pathlib import Path

    base = Path.home() / ".geolens" / "onboarded_cities"
    if not base.exists():
        return "mor:empty"
    files = sorted(p.name for p in base.glob("*.json"))
    return "mor:" + "+".join(files) if files else "mor:empty"
