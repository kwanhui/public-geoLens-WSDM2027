"""LLM-as-classifier adapter using Anthropic Claude Haiku 4.5.

Same prompt and contract as the OpenAI variant in `llm_classifier.py`. Having
two different LLM classifiers in the workbench lets the ensemble see whether
two model families agree, which says more than the
agreement of one LLM with itself across temperatures.

Falls back to stub if `ANTHROPIC_API_KEY` is unset or the SDK is missing.
"""

from __future__ import annotations

import json
import logging
import time

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._stubs import stub_predict
from geolens.engines.base import Engine, GeolocateInput, Prediction

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


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
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (no input)")

        try:
            from anthropic import Anthropic
        except ImportError as e:
            logger.warning("anthropic not installed (%s); falling back to stub.", e)
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (no anthropic)")

        start = time.perf_counter()
        client = Anthropic()
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=400,
                temperature=0.0,
                messages=[{"role": "user", "content": _build_prompt(text, self.cities, k)}],
            )
            content = resp.content[0].text if resp.content else "{}"
            # Claude sometimes wraps JSON in fences despite "no prose"; strip them.
            content = content.strip()
            if content.startswith("```"):
                content = content.strip("`")
                if content.lower().startswith("json"):
                    content = content[4:].lstrip()
            data = json.loads(content)
            raw_top_k = data.get("top_k", [])
            cost_usd = _estimate_cost(self.model, resp.usage.input_tokens, resp.usage.output_tokens)
        except Exception as e:  # noqa: BLE001
            logger.warning("Claude classifier (%s) failed: %s", self.model, e)
            return stub_predict(self.name, payload, k, sleep_ms=10.0, note=f"stub: {self.name} (api error)")

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


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Rough cost estimator for Anthropic models (2026 prices)."""
    # claude-haiku-4-5: ~$0.80/MTok input, ~$4.00/MTok output (verify against billing)
    rates = {
        "claude-haiku-4-5-20251001": (0.80e-6, 4.00e-6),
        "claude-sonnet-4-6": (3.00e-6, 15.00e-6),
    }
    in_rate, out_rate = rates.get(model, (0.80e-6, 4.00e-6))
    return input_tokens * in_rate + output_tokens * out_rate
