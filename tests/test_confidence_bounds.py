"""An LLM's self-reported confidence is clamped before it is weighed.

The consensus and the weighted fusion sum confidences across engines, so a
reply of 999 would outvote every other engine.
"""

from __future__ import annotations

import pytest

from geolens.engines._reply_json import parse_confidence
from geolens.engines.base import (
    NO_CATALOGUE_PLACE,
    NO_CATALOGUE_PLACE_NOTE,
    no_catalogue_place_prediction,
)


@pytest.mark.parametrize(
    ("sent", "kept"),
    [(0.0, 0.0), (0.42, 0.42), (1.0, 1.0), (999, 1.0), (-3, 0.0), ("0.7", 0.7)],
)
def test_a_confidence_is_clamped_to_the_unit_interval(sent, kept) -> None:
    assert parse_confidence(sent) == kept


@pytest.mark.parametrize("sent", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_confidence_is_not_a_number(sent) -> None:
    with pytest.raises(ValueError, match="finite"):
        parse_confidence(sent)


@pytest.mark.parametrize("sent", ["high", None, {}])
def test_an_unreadable_confidence_raises(sent) -> None:
    with pytest.raises((TypeError, ValueError)):
        parse_confidence(sent)


def test_a_reply_naming_no_catalogue_place_is_a_content_outcome() -> None:
    pred = no_catalogue_place_prediction("llm_gpt4o_mini", latency_ms=12.0)
    assert pred.outcome == NO_CATALOGUE_PLACE
    assert pred.note.startswith(NO_CATALOGUE_PLACE_NOTE)
    assert "call failed" not in pred.note
    # Unusable, exactly as an error is; see engines/base.py.
    assert pred.usable is False
    assert pred.city == ""
    assert pred.top_k == []
