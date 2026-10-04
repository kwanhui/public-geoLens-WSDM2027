"""LLM-as-classifier adapter using Anthropic Claude Haiku 4.5.

Same prompt and contract as the OpenAI variant in `llm_classifier.py`. Without
`ANTHROPIC_API_KEY`, or when the SDK is missing, the adapter returns a
placeholder.
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

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
API_KEY_ENV = "ANTHROPIC_API_KEY"

# What the call below actually sends. Anthropic's 1.x SDK removed the
# sampling parameters from Messages.create, so none is sent and the run
# manifest says so rather than naming a temperature.
MAX_TOKENS = 400
SAMPLING_PARAMETERS: dict[str, object] = {
    "max_tokens": MAX_TOKENS,
    "temperature": None,
    "note": (
        "no sampling parameter is sent: the anthropic 1.x Messages API rejects "
        "temperature, top_p and top_k, so the model's default sampling applies"
    ),
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
        f"Post text: {query_text}\n\n"
        "Respond with only the JSON object, no prose."
    )


class ClaudeClassifierEngine(Engine):
    name = "llm_claude_haiku"
    granularity = "post"
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
            from anthropic import Anthropic
            from anthropic.types import TextBlock
        except ImportError as e:
            logger.warning("anthropic not installed (%s); falling back to a placeholder.", e)
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (no anthropic)")

        start = time.perf_counter()
        try:
            client = Anthropic()
            # anthropic SDK 1.x removed the sampling parameters (temperature/top_p/
            # top_k) from Messages.create, and current models reject them API-side;
            # the classifier therefore runs at the model's default sampling.
            resp = client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": _build_prompt(text, self.cities, k)}],
            )
        except Exception as e:  # noqa: BLE001
            elapsed = (time.perf_counter() - start) * 1000
            if not os.getenv(API_KEY_ENV):
                # An instance with no key is not configured for live inference,
                # which is the keyless placeholder mode, not a failure.
                return stub_predict(
                    self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (no api key)"
                )
            logger.warning("Claude classifier (%s) call failed: %s", self.model, e)
            return failed_prediction(
                self.name, error_class=type(e).__name__, latency_ms=elapsed
            )

        try:
            texts = [b.text for b in resp.content if isinstance(b, TextBlock)]
            data = extract_json_object(texts[0] if texts else "")
            raw_top_k = data.get("top_k", [])
            estimated = estimate_cost(
                self.model, resp.usage.input_tokens, resp.usage.output_tokens
            )
        except Exception as e:  # noqa: BLE001
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning("Claude classifier (%s) reply could not be read: %s", self.model, e)
            return failed_prediction(
                self.name, error_class=type(e).__name__, latency_ms=elapsed
            )

        latency_ms = (time.perf_counter() - start) * 1000

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
