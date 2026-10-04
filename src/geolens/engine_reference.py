"""What each engine scored on WNUT-2016, for the interface to print.

Acc@1 per engine with its 95% Wilson interval and the rows it was measured
over, and for the post-level engines the same split by whether the post named
a catalogue place. The rates are read from a build-time copy of
``eval/results/wnut2016.json``, which ``tests/test_engine_reference.py`` keeps
byte-identical to the committed file. They describe one benchmark on one
catalogue, not this instance's live accuracy, and ``provenance`` says so.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from geolens.stats import wilson_interval

DATA_FILE = Path(__file__).parent / "data" / "wnut2016.json"

EVAL_TAG = "wnut2016-eval-3803853"
PROVENANCE = (
    "WNUT-2016 validation tweets, English, 50-place catalogue, tag "
    f"{EVAL_TAG}."
)

# The two post-level buckets of the committed run, and what each one is.
NAMED_BUCKET = "intl"
UNNAMED_BUCKET = "hard-sem"
SPLIT_LABELS = {
    "named": "the post named a catalogue place",
    "unnamed": "the post named no catalogue place",
}


def _entry(acc: float, n: int, ci: list[float] | tuple[float, float] | None) -> dict[str, Any]:
    """One rate with its interval and the rows behind it."""
    low, high = tuple(ci) if ci else wilson_interval(round(acc * n), n)
    return {"acc_at_1": acc, "acc_at_1_ci": [low, high], "n": n}


@lru_cache(maxsize=1)
def engine_reference() -> dict[str, Any]:
    """Per-engine Acc@1 from the committed WNUT-2016 run.

    ``engines`` is keyed by the registry name every other endpoint uses. Each
    entry carries ``overall``; a post-level engine also carries ``named`` and
    ``unnamed``, the same measurement over the rows whose text did and did not
    name a catalogue place.
    """
    data = json.loads(DATA_FILE.read_text())
    buckets = data["post_level"].get("per_bucket", {})
    post_engines = data["post_level"]["engines"]

    engines: dict[str, Any] = {}
    for level in ("post_level", "user_level"):
        for name, m in data[level]["engines"].items():
            engines[name] = {
                "granularity": "post" if level == "post_level" else "user",
                "overall": _entry(m["acc_at_1"], m["n_evaluated"], m.get("acc_at_1_ci")),
            }

    # The split is measured over post-level rows, so it is reported for the
    # engines that answer at that level.
    for key, bucket in (("named", NAMED_BUCKET), ("unnamed", UNNAMED_BUCKET)):
        entry = buckets.get(bucket)
        if entry is None:
            continue
        n = entry["n_rows"]
        for name, acc in entry["acc_at_1"].items():
            if name not in post_engines:
                continue
            engines[name][key] = _entry(acc, n, None)

    return {
        "tag": EVAL_TAG,
        "provenance": PROVENANCE,
        "split_labels": SPLIT_LABELS,
        "catalogue_size": data["manifest"]["catalogue_size"],
        "engines": engines,
    }
