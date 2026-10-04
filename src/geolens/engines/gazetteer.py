"""Gazetteer baseline: count catalogue names, aliases and landmarks in the text.

Each place scores by how often its name, its cached aliases and, for an
onboarded place, its cached landmarks occur in the input; a landmark counts a
quarter of a name match. The ranked list holds only the places the text named.
It is not padded up to k, and an abstention carries no place.
"""

from __future__ import annotations

import re
import time

from geolens.engines._cities import DEFAULT_CITIES
from geolens.engines._stubs import stub_predict
from geolens.engines.base import Engine, GeolocateInput, Prediction, failed_prediction
from geolens.onboarding.wizard import _load_cached as _load_cached_profile

# A landmark is weaker evidence than the place's own name: a post naming the
# river a town sits on has said something about where it is, but less than a
# post naming the town. Only an onboarded place has landmarks, because a
# built-in place has no profile, so no evaluated number moves.
LANDMARK_WEIGHT = 0.25


def _aliases_for(city: str) -> list[str]:
    """City name + any cached MoR aliases (so onboarded cities benefit)."""
    out = [city]
    profile = _load_cached_profile(city)
    if profile is not None:
        out.extend(a for a in profile.aliases if a and a != city)
    return out


def _landmarks_for(city: str) -> list[str]:
    """Cached landmarks for an onboarded place; empty for a built-in one."""
    profile = _load_cached_profile(city)
    if profile is None:
        return []
    return [m for m in profile.landmarks if m and m.strip()]


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


def _evidence(
    top_k: list[tuple[str, float]],
    matched_by_city: dict[str, list[str]],
    landmarks_by_city: dict[str, list[str]],
) -> str:
    """What the top place matched on, and which other places the text also named.

    A text can name two catalogue places, so the evidence names the terms
    behind the winner and the other places the text also named.
    """
    if not top_k:
        return ""
    first = top_k[0][0]
    parts = []
    names = matched_by_city.get(first, [])
    marks = landmarks_by_city.get(first, [])
    if names:
        parts.append("matched: " + ", ".join(names[:3]))
    if marks:
        parts.append("matched landmark: " + ", ".join(marks[:3]))
    others = [c for c, _ in top_k[1:] if matched_by_city.get(c) or landmarks_by_city.get(c)]
    if others:
        parts.append("the text also names " + ", ".join(others[:3]))
    return "; ".join(parts)


class GazetteerEngine(Engine):
    name = "gazetteer"
    granularity = "post"  # works for either; default to post
    # It needs no key, no model download and no network, so placeholder mode
    # leaves it real and a keyless clone receives one real answer.
    needs_credentials = False

    def __init__(
        self,
        *,
        stub: bool | None = None,
        cities: list[str] | None = None,
        granularity: str = "post",
    ) -> None:
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
            return failed_prediction(
                self.name, error_class="NoInput", detail="no text for this granularity"
            )

        start = time.perf_counter()
        scores: list[tuple[str, float]] = []
        matched_by_city: dict[str, list[str]] = {}
        landmarks_by_city: dict[str, list[str]] = {}
        for city in self.cities:
            count, matched = _count_matches(text, _aliases_for(city))
            hits, marks = _count_matches(text, _landmarks_for(city))
            scores.append((city, count + hits * LANDMARK_WEIGHT))
            matched_by_city[city] = matched
            landmarks_by_city[city] = marks
        scores.sort(key=lambda x: x[1], reverse=True)
        latency_ms = (time.perf_counter() - start) * 1000

        total = sum(s for _, s in scores) or 1
        # Only places the text actually named. Padding the list up to k
        # would put a place that matched nothing into the fused ranking at
        # the same score as the one that did match.
        top_k = [(c, s / total) for c, s in scores[: min(k, len(scores))] if s > 0]

        if not top_k:
            # No city name appeared anywhere. Return no city at all: an
            # abstention that names the first catalogue entry reads as a
            # low-confidence prediction to any client that ignores the flag.
            return Prediction(
                city="",
                confidence=0.0,
                top_k=[],
                latency_ms=latency_ms,
                cost_usd=0.0,
                note="real:gazetteer (no toponym in text)",
                abstain=True,
            )

        evidence = _evidence(top_k, matched_by_city, landmarks_by_city)
        return Prediction(
            city=top_k[0][0],
            confidence=float(top_k[0][1]),
            top_k=[(c, float(p)) for c, p in top_k],
            latency_ms=latency_ms,
            cost_usd=0.0,
            note="real:gazetteer",
            evidence=evidence,
        )
