"""RetrieveZero adapter: zero-shot user geolocation with LLM-retrieved knowledge.

Placeholder mode returns a deterministic stand-in. Real mode is frozen-encoder
cosine similarity over `intfloat/e5-large`, the encoder the RetrieveZero paper
uses. A place is described from the Modular Retrieval profile the cold-start
wizard produces, that is, its aliases, landmarks and foods; where a place has
no cached profile the passage is the bare place name. A RetrieveZero
prediction therefore changes for any place the operator has onboarded.
"""

from __future__ import annotations

import logging

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._stubs import stub_predict
from geolens.engines.base import Engine, GeolocateInput, Prediction, failed_prediction
from geolens.onboarding.wizard import _load_cached as _load_cached_profile
from geolens.paths import ONBOARDED_CITIES, cache_subdir

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
            return failed_prediction(
                self.name, error_class="NoInput", detail="no user timeline"
            )

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
    base = cache_subdir(ONBOARDED_CITIES)
    if not base.exists():
        return "mor:empty"
    files = sorted(p.name for p in base.glob("*.json"))
    return "mor:" + "+".join(files) if files else "mor:empty"
