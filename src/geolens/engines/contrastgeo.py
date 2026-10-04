"""ContrastGeo adapter: post-level geolocation over a frozen encoder.

With `GEOLENS_STUB_MODE=1`, or the variable unset, the adapter returns
deterministic stand-in predictions and needs nothing beyond the package, which
is what a cold HF Space deploy runs under. With `GEOLENS_STUB_MODE=0` it runs
frozen-encoder cosine similarity over `sup-simcse-bert-large-uncased`, the
encoder of the published ContrastGeo paper. No trained checkpoint is
published, so the zero-shot baseline is what runs here.
"""

from __future__ import annotations

import logging

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._stubs import stub_predict
from geolens.engines.base import Engine, GeolocateInput, Prediction, failed_prediction

logger = logging.getLogger(__name__)

ENCODER = "princeton-nlp/sup-simcse-bert-large-uncased"


def _describe_city(name: str) -> str:
    """ContrastGeo treats cities as plain class labels, with no enrichment."""
    return name


class ContrastGeoEngine(Engine):
    name = "contrastgeo"
    granularity = "post"

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
        if payload.post:
            return payload.post
        if payload.user_posts:
            return payload.user_posts[0]
        return None

    def predict(self, payload: GeolocateInput, k: int = 5) -> Prediction:
        if self.stub:
            return stub_predict(self.name, payload, k, sleep_ms=60.0, note="stub: ContrastGeo")

        text = self._query_text(payload)
        if not text:
            return failed_prediction(
                self.name, error_class="NoInput", detail="no post text"
            )

        try:
            from geolens.engines._encoder import encoder_similarity_predict

            return encoder_similarity_predict(
                engine_name=self.name,
                encoder=self.encoder,
                query_text=text,
                cities=self.cities,
                describe_city=_describe_city,
                description_fn_id="plain",
                k=k,
            )
        except ImportError as e:
            logger.warning("torch/transformers not installed (%s); falling back to stub.", e)
            self.stub = True
            return stub_predict(self.name, payload, k, sleep_ms=60.0, note="stub: ContrastGeo (no torch)")
