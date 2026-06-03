"""Gazetteer / string-match baseline.

Classical pre-neural geolocation baseline: count substring matches of each
city's name and onboarded aliases in the input text, return top-k by count.
No external dependencies and very fast. Does well when a post names the place
outright; misses indirect cues ("kaya toast" for Singapore), cannot tell
ambiguous names apart (which Manchester?), and has no handling of typos.
"""

from __future__ import annotations

import re
import time

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._stubs import stub_predict
from geolens.engines.base import Engine, GeolocateInput, Prediction
from geolens.onboarding.wizard import _load_cached as _load_cached_profile


def _aliases_for(city: str) -> list[str]:
    """City name + any cached MoR aliases (so onboarded cities benefit)."""
    out = [city]
    profile = _load_cached_profile(city)
    if profile is not None:
        out.extend(a for a in profile.aliases if a and a != city)
    return out


def _count_matches(text: str, terms: list[str]) -> tuple[int, list[str]]:
    """Case-insensitive match count plus the distinct terms that matched.

    Returns (count, matched_terms). Whole-word boundaries are used for
    single-word alphabetic terms; substring matching for multi-word place
    names. The matched terms become the evidence shown in the UI.
    """
    n = 0
    matched: list[str] = []
    lower = text.lower()
    for term in terms:
        t = term.lower().strip()
        if not t:
            continue
        if re.fullmatch(r"[a-z\-' ]+", t) and " " not in t:
            hits = len(re.findall(rf"\b{re.escape(t)}\b", lower))
        else:
            hits = lower.count(t)
        if hits:
            n += hits
            matched.append(term)
    return n, matched


class GazetteerEngine(Engine):
    name = "gazetteer"
    granularity = "post"  # works for either; default to post

    def __init__(
        self,
        *,
        stub: bool | None = None,
        cities: list[str] | None = None,
        granularity: str = "post",
    ) -> None:
        # Gazetteer never needs to "stub" because it has no external deps,
        # but honour stub mode for consistency with other engines.
        super().__init__(stub=stub)
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
            return stub_predict(self.name, payload, k, sleep_ms=5.0, note="stub: gazetteer")

        text = self._query_text(payload)
        if not text:
            return stub_predict(self.name, payload, k, sleep_ms=1.0, note="stub: gazetteer (no input)")

        start = time.perf_counter()
        scores: list[tuple[str, int]] = []
        matched_by_city: dict[str, list[str]] = {}
        for city in self.cities:
            count, matched = _count_matches(text, _aliases_for(city))
            scores.append((city, count))
            matched_by_city[city] = matched
        scores.sort(key=lambda x: x[1], reverse=True)
        latency_ms = (time.perf_counter() - start) * 1000

        total = sum(s for _, s in scores) or 1
        if scores[0][1] == 0:
            # No city name appeared anywhere. Abstain rather than emit a
            # confident-looking uniform guess: the operator should treat this
            # as "no toponym found", not as a real low-confidence prediction.
            top_k = [(scores[i][0], 1.0 / len(scores)) for i in range(min(k, len(scores)))]
            return Prediction(
                city=top_k[0][0],
                confidence=float(top_k[0][1]),
                top_k=[(c, float(p)) for c, p in top_k],
                latency_ms=latency_ms,
                cost_usd=0.0,
                note="real:gazetteer (no toponym in text)",
                abstain=True,
            )

        top_k = [(c, s / total) for c, s in scores[: min(k, len(scores))] if s > 0]
        # Pad if we found fewer than k cities with non-zero matches.
        while len(top_k) < k and len(top_k) < len(scores):
            next_idx = len(top_k)
            top_k.append((scores[next_idx][0], 1.0 / total))

        evidence_terms = matched_by_city.get(top_k[0][0], [])
        evidence = "matched: " + ", ".join(evidence_terms[:3]) if evidence_terms else ""
        return Prediction(
            city=top_k[0][0],
            confidence=float(top_k[0][1]),
            top_k=[(c, float(p)) for c, p in top_k],
            latency_ms=latency_ms,
            cost_usd=0.0,
            note="real:gazetteer",
            evidence=evidence,
        )
