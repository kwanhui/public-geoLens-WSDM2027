"""Read a JSON object, and a confidence, out of an LLM reply.

Both classifier adapters ask for a bare JSON object and sometimes get a fenced
block, a sentence of preamble or a trailing explanation. `extract_json_object`
raises when it cannot find the object, so the caller records a failed call
rather than guessing. `parse_confidence` holds the self-reported value to the
[0, 1] the prompt asks for and refuses anything that is not a finite number.
"""

from __future__ import annotations

import json
import math
import re

_FENCE = re.compile(r"```[A-Za-z0-9_+-]*\s*\n?(.*?)```", re.DOTALL)


def _first_braced(text: str) -> str | None:
    """The first balanced ``{...}`` span in `text`, ignoring braces in strings."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json_object(text: str) -> dict:
    """Return the JSON object in `text`.

    Tries the whole reply first, then each fenced block, then the first
    balanced brace span. Raises ValueError when none of those parses to an
    object, so the caller can record a failed call rather than a prediction.
    """
    stripped = (text or "").strip()
    candidates: list[str] = []
    if stripped:
        candidates.append(stripped)
    candidates.extend(block.strip() for block in _FENCE.findall(stripped) if block.strip())
    braced = _first_braced(stripped)
    if braced:
        candidates.append(braced)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("the reply contains no JSON object")


def parse_confidence(value: object) -> float:
    """A reply's confidence, clamped to [0, 1].

    Raises ValueError when the value is not a finite number, so the caller
    drops the entry. An unbounded confidence would outweigh every other
    engine in the per-level consensus and in the weighted fusion; a reply
    inside the range the prompt asks for is unchanged.
    """
    conf = float(value)  # type: ignore[arg-type]
    if not math.isfinite(conf):
        raise ValueError(f"confidence {value!r} is not a finite number")
    return min(1.0, max(0.0, conf))
