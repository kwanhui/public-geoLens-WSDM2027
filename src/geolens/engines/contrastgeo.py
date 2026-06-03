"""ContrastGeo adapter: post-level few-shot geolocation.

Two modes:
- **Stub** (default; set `GEOLENS_STUB_MODE=1` or leave unset): deterministic
  placeholder predictions, zero deps beyond the package itself. Used for
  cold HF Space deploys and CI smoke tests.
- **Real** (set `GEOLENS_STUB_MODE=0` or `GEOLENS_REAL_MODE=1`): frozen-encoder
  cosine-similarity baseline using the same encoder as the published
  ContrastGeo paper (sup-simcse-bert-large-uncased). This is the zero-shot
  baseline that ContrastGeo improves on with prompt-aware contrastive
  fine-tuning. No ContrastGeo checkpoint is published, so this is what runs.
"""

from __future__ import annotations

import logging

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._stubs import stub_predict
from geolens.engines.base import Engine, GeolocateInput, Prediction

logger = logging.getLogger(__name__)

ENCODER = "princeton-nlp/sup-simcse-bert-large-uncased"


def _describe_city(name: str) -> str:
    """ContrastGeo treats cities as plain class labels, no enrichment."""
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
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note="stub: ContrastGeo (no post)")

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
