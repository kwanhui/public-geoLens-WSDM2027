"""LLM-as-classifier adapter.

Zero-shot prompted classification: given a post (or a user's recent posts),
ask an LLM to pick one city from the catalogue. A common baseline these days,
and often competitive with fine-tuned methods on out-of-distribution inputs.

Default uses OpenAI gpt-4o-mini for cost (≈$0.0001 per query at typical post
length). Falls back to stub if no `OPENAI_API_KEY` is set, or if the API
errors out.
"""

from __future__ import annotations

import json
import logging
import time

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._stubs import stub_predict
from geolens.engines.base import Engine, GeolocateInput, Prediction

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"


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
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (no input)")

        try:
            from openai import OpenAI
        except ImportError as e:
            logger.warning("openai not installed (%s); falling back to stub.", e)
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (no openai)")

        start = time.perf_counter()
        client = OpenAI()
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": _build_prompt(text, self.cities, k)}],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=300,
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            raw_top_k = data.get("top_k", [])
            usage = resp.usage
            cost_usd = _estimate_cost(self.model, usage.prompt_tokens, usage.completion_tokens)
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM classifier (%s) failed: %s", self.model, e)
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (api error)")

        latency_ms = (time.perf_counter() - start) * 1000

        # Validate that the LLM picked from our city list.
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
                top_k.append((city_canonical, float(conf)))
            except (TypeError, ValueError):
                continue

        if not top_k:
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (parse error)")

        return Prediction(
            city=top_k[0][0],
            confidence=top_k[0][1],
            top_k=top_k,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            note=f"real:{self.name} ({self.model})",
        )


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Rough cost estimator for OpenAI models. Updated 2026."""
    # gpt-4o-mini: $0.15 / 1M input, $0.60 / 1M output
    rates = {
        "gpt-4o-mini": (0.15e-6, 0.60e-6),
        "gpt-4o": (2.50e-6, 10.00e-6),
    }
    in_rate, out_rate = rates.get(model, (0.15e-6, 0.60e-6))
    return prompt_tokens * in_rate + completion_tokens * out_rate
