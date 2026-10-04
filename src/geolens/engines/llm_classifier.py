"""LLM-as-classifier adapter.

Zero-shot prompted classification: given a post, or a user's recent posts, ask
an LLM to pick one place from the closed catalogue. The default model is
OpenAI gpt-4o-mini, roughly USD 0.0001 per query at typical post length,
estimated from the listed prices in `geolens.pricing`. Without
`OPENAI_API_KEY`, or when the call raises, the adapter returns a placeholder.
"""

from __future__ import annotations

import logging
import os
import time

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._reply_json import extract_json_object, parse_confidence
from geolens.engines._stubs import stub_predict
from geolens.engines.base import (
    Engine,
    GeolocateInput,
    Prediction,
    failed_prediction,
    no_catalogue_place_prediction,
)
from geolens.pricing import estimate_cost

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
API_KEY_ENV = "OPENAI_API_KEY"

# What the call below actually sends. The run manifest reports these rather
# than restating them, so it cannot claim a setting the request does not
# carry.
TEMPERATURE = 0.0
MAX_TOKENS = 300
RESPONSE_FORMAT = "json_object"
SAMPLING_PARAMETERS: dict[str, object] = {
    "temperature": TEMPERATURE,
    "max_tokens": MAX_TOKENS,
    "response_format": RESPONSE_FORMAT,
}


def _build_prompt(query_text: str, cities: list[str], k: int) -> str:
    cities_str = ", ".join(cities)
    return (
        "You are a geolocation classifier. Given a social media post (or a "
        "user's recent posts), pick the most likely city from this list:\n\n"
        f"{cities_str}\n\n"
        f"Return JSON: {{\"top_k\": [[\"city_name\", confidence_0_to_1], ...]}} "
        f"with up to {k} entries, ordered by confidence descending. Use ONLY "
        "city names from the list above, exactly as written. If no city in the "
        "list seems to fit, still return your best guess from the list.\n\n"
        f"Post text: {query_text}"
    )


class LLMClassifierEngine(Engine):
    name = "llm_gpt4o_mini"
    granularity = "post"  # works for both granularities; assignment is a UI choice
    sampling_parameters = SAMPLING_PARAMETERS
    calls_a_third_party = True

    def __init__(
        self,
        *,
        stub: bool | None = None,
        model: str = DEFAULT_MODEL,
        cities: list[str] | None = None,
        granularity: str = "post",
    ) -> None:
        super().__init__(stub=stub)
        self.model = model
        self.cities = cities or DEFAULT_CITIES
        self.granularity = granularity  # type: ignore[assignment]

    def _query_text(self, payload: GeolocateInput) -> str | None:
        if self.granularity == "post":
            return payload.post or (payload.user_posts[0] if payload.user_posts else None)
        # user-level
        if payload.user_posts:
            return "\n".join(payload.user_posts)
        return payload.post

    def predict(self, payload: GeolocateInput, k: int = 5) -> Prediction:
        if self.stub:
            return stub_predict(self.name, payload, k, sleep_ms=80.0, note=f"stub: {self.name}")

        text = self._query_text(payload)
        if not text:
            return failed_prediction(
                self.name, error_class="NoInput", detail="no text for this granularity"
            )

        try:
            from openai import OpenAI
        except ImportError as e:
            logger.warning("openai not installed (%s); falling back to a placeholder.", e)
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (no openai)")

        start = time.perf_counter()
        try:
            client = OpenAI()
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": _build_prompt(text, self.cities, k)}],
                response_format={"type": "json_object"},
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
            )
        except Exception as e:  # noqa: BLE001
            elapsed = (time.perf_counter() - start) * 1000
            if not os.getenv(API_KEY_ENV):
                return stub_predict(
                    self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (no api key)"
                )
            logger.warning("LLM classifier (%s) call failed: %s", self.model, e)
            return failed_prediction(self.name, error_class=type(e).__name__, latency_ms=elapsed)

        try:
            data = extract_json_object(resp.choices[0].message.content or "")
            raw_top_k = data.get("top_k", [])
            usage = resp.usage
            estimated = (
                estimate_cost(self.model, usage.prompt_tokens, usage.completion_tokens)
                if usage
                else None
            )
        except Exception as e:  # noqa: BLE001
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning("LLM classifier (%s) reply could not be read: %s", self.model, e)
            return failed_prediction(self.name, error_class=type(e).__name__, latency_ms=elapsed)

        latency_ms = (time.perf_counter() - start) * 1000

        # Keep only the places the reply picked from the catalogue, with a
        # confidence inside the range the prompt asked for.
        valid_cities = {c.lower(): c for c in self.cities}
        top_k: list[tuple[str, float]] = []
        for entry in raw_top_k[:k]:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            city_raw, conf = entry[0], entry[1]
            try:
                city_canonical = valid_cities.get(str(city_raw).lower())
                if city_canonical is None:
                    continue
                top_k.append((city_canonical, parse_confidence(conf)))
            except (TypeError, ValueError):
                continue

        if not top_k:
            return no_catalogue_place_prediction(self.name, latency_ms=latency_ms)

        return Prediction(
            city=top_k[0][0],
            confidence=top_k[0][1],
            top_k=top_k,
            latency_ms=latency_ms,
            cost_usd=estimated or 0.0,
            cost_is_estimated=estimated is not None,
            note=f"real:{self.name} ({self.model})",
        )
