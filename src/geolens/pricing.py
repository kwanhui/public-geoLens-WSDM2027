"""The per-token list prices `cost_usd` is estimated from, and when they were read.

Nothing here reads a bill: every cost the workbench reports is a token count
multiplied by a listed price. The table is recorded in every run manifest with
each entry's price, the date it was checked and the page it was read from. A
model the table does not list reports no cost rather than another model's rates.

The committed results under `eval/results/` were computed at the Claude Haiku
3.5 rate, which is 0.8 of the 4.5 list price the table now carries. They are
not edited, because they record what the run computed;
`scripts/recompute_table.py` applies the 1.25 correction.
"""

from __future__ import annotations

from typing import Any

# The date every rate below was read from its provider's published price
# list. Move it, and the entry's own `checked`, whenever a rate changes.
PRICE_TABLE_RECORDED = "2026-09-19"

OPENAI_PRICING_PAGE = "https://openai.com/api/pricing/"
ANTHROPIC_PRICING_PAGE = "https://www.anthropic.com/pricing"

# model id -> (USD per input token, USD per output token, date checked, source).
# The rates are the providers' list prices per million tokens, divided by a
# million: 0.15e-6 is USD 0.15 per million input tokens.
PRICES: dict[str, tuple[float, float, str, str]] = {
    # USD 0.15 / MTok input, USD 0.60 / MTok output.
    "gpt-4o-mini": (0.15e-6, 0.60e-6, "2026-09-19", OPENAI_PRICING_PAGE),
    # USD 2.50 / MTok input, USD 10.00 / MTok output.
    "gpt-4o": (2.50e-6, 10.00e-6, "2026-09-19", OPENAI_PRICING_PAGE),
    # USD 1.00 / MTok input, USD 5.00 / MTok output. This entry read 0.80 and
    # 4.00 until 2026-09-19, which is the Claude Haiku 3.5 price.
    "claude-haiku-4-5-20251001": (1.00e-6, 5.00e-6, "2026-09-19", ANTHROPIC_PRICING_PAGE),
    # USD 3.00 / MTok input, USD 15.00 / MTok output.
    "claude-sonnet-4-6": (3.00e-6, 15.00e-6, "2026-09-19", ANTHROPIC_PRICING_PAGE),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated cost of one call, or None when the model is not in the table.

    A call whose price is unknown reports no cost rather than another
    model's, which would be a confident wrong number.
    """
    entry = PRICES.get(model)
    if entry is None:
        return None
    return input_tokens * entry[0] + output_tokens * entry[1]


def price_table() -> dict[str, Any]:
    """The table as the run manifest records it."""
    return {
        "recorded_on": PRICE_TABLE_RECORDED,
        "note": (
            "cost_usd is estimated from these list prices, not read from a "
            "bill; a model absent from this table reports no cost"
        ),
        "usd_per_token": {
            model: {
                "input": entry[0],
                "output": entry[1],
                "checked": entry[2],
                "source": entry[3],
            }
            for model, entry in sorted(PRICES.items())
        },
    }
