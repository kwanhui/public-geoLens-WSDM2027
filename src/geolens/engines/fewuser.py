"""FewUser adapter: user-level geolocation over a frozen encoder.

Placeholder mode returns a deterministic stand-in; see `contrastgeo.py` for
how the two modes are selected. Real mode is frozen-encoder cosine similarity
over `sup-simcse-roberta-large`, a related encoder and not the published one,
since the FewUser manuscript reports SimCSE-BERT-large as its backbone. The
user signal is the recent posts joined with newlines.
"""

from __future__ import annotations

import logging

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._stubs import stub_predict
from geolens.engines.base import Engine, GeolocateInput, Prediction, failed_prediction

logger = logging.getLogger(__name__)

ENCODER = "princeton-nlp/sup-simcse-roberta-large"


def _describe_city(name: str) -> str:
    """The string FewUser embeds for a place: "a social media user from <name>"."""
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
                description_fn_id="user-from",
                k=k,
            )
        except ImportError as e:
            logger.warning("torch/transformers not installed (%s); falling back to stub.", e)
            self.stub = True
            return stub_predict(self.name, payload, k, sleep_ms=80.0, note="stub: FewUser (no torch)")
