"""FewUser adapter: user-level few-shot geolocation.

Two modes (see `contrastgeo.py` for the broader picture):
- **Stub**: deterministic placeholder.
- **Real**: frozen-encoder cosine-similarity using sup-simcse-roberta-large
  (the encoder the FewUser paper uses). User signal is built by joining the
  user's recent posts with newline separators, which is the simplest way to
  approximate FewUser's user-level aggregation without the trained user
  encoder head.
"""

from __future__ import annotations

import logging

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._stubs import stub_predict
from geolens.engines.base import Engine, GeolocateInput, Prediction

logger = logging.getLogger(__name__)

ENCODER = "princeton-nlp/sup-simcse-roberta-large"


def _describe_city(name: str) -> str:
    """FewUser uses a slightly richer template: 'a user from <city>'."""
    return f"a social media user from {name}"


class FewUserEngine(Engine):
    name = "fewuser"
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
        if payload.user_posts:
            return "\n".join(payload.user_posts)
        if payload.post:
            return payload.post
        return None

    def predict(self, payload: GeolocateInput, k: int = 5) -> Prediction:
        if self.stub:
            return stub_predict(self.name, payload, k, sleep_ms=80.0, note="stub: FewUser")

        text = self._query_text(payload)
        if not text:
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note="stub: FewUser (no input)")

        try:
            from geolens.engines._encoder import encoder_similarity_predict

            return encoder_similarity_predict(
                engine_name=self.name,
                encoder=self.encoder,
                query_text=text,
                cities=self.cities,
                describe_city=_describe_city,
                description_fn_id="user-from",
                k=k,
            )
        except ImportError as e:
            logger.warning("torch/transformers not installed (%s); falling back to stub.", e)
            self.stub = True
            return stub_predict(self.name, payload, k, sleep_ms=80.0, note="stub: FewUser (no torch)")
