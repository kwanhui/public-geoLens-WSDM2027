"""The API-backed engines go out together; the encoders stay in order."""

from __future__ import annotations

import threading
import time

from geolens.dispatch import CONCURRENT_ENGINES, max_concurrency, run_engines
from geolens.engines.base import Engine, GeolocateInput, Prediction

PAYLOAD = GeolocateInput(post="a post", user_posts=["one", "two"])


class _SlowEngine(Engine):
    """Sleeps like a network round trip and records when it ran."""

    def __init__(self, name: str, granularity: str, sleep_s: float, seen: list) -> None:
        super().__init__(stub=True)
        self.name = name
        self.granularity = granularity  # type: ignore[assignment]
        self.sleep_s = sleep_s
        self.seen = seen

    def predict(self, payload: GeolocateInput, k: int = 5) -> Prediction:
        self.seen.append(threading.current_thread().name)
        time.sleep(self.sleep_s)
        return Prediction(city="Singapore", confidence=0.5, top_k=[("Singapore", 0.5)],
                          note="real:test")


def _roster(seen: list) -> dict[str, Engine]:
    return {
        "gpt4o_mini_post": _SlowEngine("llm_gpt4o_mini", "post", 0.15, seen),
        "gpt4o_mini_user": _SlowEngine("llm_gpt4o_mini", "user", 0.15, seen),
        "claude_haiku_post": _SlowEngine("llm_claude_haiku", "post", 0.15, seen),
        "claude_haiku_user": _SlowEngine("llm_claude_haiku", "user", 0.15, seen),
    }


def test_the_api_backed_engines_overlap(monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_MAX_CONCURRENCY", "4")
    seen: list[str] = []
    start = time.perf_counter()
    results = run_engines(_roster(seen), PAYLOAD, k=3)
    elapsed = time.perf_counter() - start

    assert len(results) == 4
    assert all(p.city == "Singapore" for p in results.values())
    # Four 150 ms calls in order take 600 ms; overlapped they take about 150.
    assert elapsed < 0.45, elapsed
    assert len(set(seen)) > 1


def test_one_worker_puts_them_back_in_order(monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_MAX_CONCURRENCY", "1")
    seen: list[str] = []
    start = time.perf_counter()
    run_engines(_roster(seen), PAYLOAD, k=3)

    assert time.perf_counter() - start >= 0.55
    assert len(set(seen)) == 1
    assert max_concurrency() == 1


def test_the_encoders_are_not_pooled() -> None:
    """They share a process-wide model, so a pool buys nothing and risks a race."""
    assert set(CONCURRENT_ENGINES) == {"llm_gpt4o_mini", "llm_claude_haiku"}


def test_the_roster_order_is_preserved(monkeypatch) -> None:
    monkeypatch.setenv("GEOLENS_MAX_CONCURRENCY", "4")
    seen: list[str] = []
    roster = _roster(seen)
    assert list(run_engines(roster, PAYLOAD, k=3)) == list(roster)
