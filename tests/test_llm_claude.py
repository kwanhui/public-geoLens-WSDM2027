"""The Claude adapter against the bounded anthropic SDK.

These pin the keyword set `Messages.create` accepts and the shape of the
response the adapter reads, building a real `anthropic.types.Message` so they
fail if either changes inside the version range `pyproject.toml` allows. No
network call is made.
"""

from __future__ import annotations

import inspect

import anthropic
import pytest
from anthropic.types import Message, TextBlock, Usage

from geolens.engines.base import GeolocateInput
from geolens.engines.llm_claude import ClaudeClassifierEngine
from geolens.pricing import PRICES

CITIES = ["Singapore", "Tokyo", "Jakarta"]


def _reply(text: str) -> Message:
    return Message(
        id="msg_test",
        model="claude-haiku-4-5-20251001",
        role="assistant",
        type="message",
        content=[TextBlock(type="text", text=text)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=1200, output_tokens=60),
    )


class _FakeMessages:
    def __init__(self, reply: Message) -> None:
        self.reply = reply
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.reply


class _FakeClient:
    def __init__(self, reply: Message) -> None:
        self.messages = _FakeMessages(reply)


@pytest.fixture
def engine(monkeypatch):
    def _install(text: str) -> tuple[ClaudeClassifierEngine, _FakeClient]:
        client = _FakeClient(_reply(text))
        monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **kw: client)
        return ClaudeClassifierEngine(stub=False, cities=CITIES), client

    return _install


def test_create_accepts_only_the_arguments_the_adapter_sends() -> None:
    params = inspect.signature(anthropic.Anthropic(api_key="test").messages.create).parameters
    for name in ("model", "max_tokens", "messages"):
        assert name in params
    # Removed by the 1.x SDK; passing it is what broke the Space in August.
    assert "temperature" not in params


def test_prediction_is_parsed_from_the_response(engine) -> None:
    eng, client = engine('{"top_k": [["Tokyo", 0.8], ["Singapore", 0.15]]}')
    pred = eng.predict(GeolocateInput(post="Shibuya crossing at midnight"), k=3)

    assert pred.city == "Tokyo"
    assert pred.top_k == [("Tokyo", 0.8), ("Singapore", 0.15)]
    assert pred.mode == "real"
    # The list price for Claude Haiku 4.5, read from geolens.pricing rather
    # than restated here, so one table is the source of every rate.
    in_rate, out_rate, _, _ = PRICES["claude-haiku-4-5-20251001"]
    assert pred.cost_usd == pytest.approx(1200 * in_rate + 60 * out_rate)
    assert pred.cost_is_estimated is True
    assert set(client.messages.kwargs) == {"model", "max_tokens", "messages"}


def test_fenced_json_is_still_parsed(engine) -> None:
    eng, _ = engine('```json\n{"top_k": [["Jakarta", 0.7]]}\n```')
    assert eng.predict(GeolocateInput(post="Banjir di Jakarta"), k=3).city == "Jakarta"


def test_a_prefixed_reply_is_still_parsed(engine) -> None:
    """The live Space recorded a JSONDecodeError on a reply of this shape."""
    eng, _ = engine('Here is the JSON you asked for:\n{"top_k": [["Tokyo", 0.6]]}\nHope that helps.')
    assert eng.predict(GeolocateInput(post="Shibuya"), k=3).city == "Tokyo"


def test_cities_outside_the_catalogue_are_dropped(engine) -> None:
    eng, _ = engine('{"top_k": [["Atlantis", 0.9], ["Singapore", 0.4]]}')
    pred = eng.predict(GeolocateInput(post="kaya toast at Changi"), k=3)
    assert pred.city == "Singapore"


def test_an_unusable_reply_is_recorded_as_a_failed_call(engine) -> None:
    """A reply that cannot be read must not become a placeholder city."""
    eng, _ = engine("not json at all")
    pred = eng.predict(GeolocateInput(post="anything"), k=3)

    assert pred.mode == "failed"
    assert pred.failed is True
    assert pred.city == ""
    assert pred.top_k == []
    assert pred.confidence == 0.0
    assert pred.error_class == "ValueError"


def test_a_reply_naming_no_catalogue_city_is_a_failed_call(engine) -> None:
    eng, _ = engine('{"top_k": [["Atlantis", 0.9]]}')
    pred = eng.predict(GeolocateInput(post="anything"), k=3)

    assert pred.failed is True
    assert pred.error_class == "NoCatalogueCity"
