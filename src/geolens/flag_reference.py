"""What the verification flag measured on WNUT-2016, for the interface to print.

The flag is printed next to the rate at which it was wrong, read from the
rescore of the committed WNUT-2016 results. The packaged file is a build-time
copy of ``eval/results/wnut2016_flag_rescore.json``, taken because the
evaluation directory is not part of the installed package or the deployed
image. ``tests/test_flag_reference.py`` keeps the two byte-identical.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).parent / "data" / "wnut2016_flag_rescore.json"

# The row set the paper reports: every row that carries a user timeline, so
# both a post-level and a user-level consensus exist.
REPORTED_ROW_SET = "user_timeline_400"


@lru_cache(maxsize=1)
def flag_reference() -> dict[str, Any]:
    """The flag's measured behaviour, with the sentence the interface shows.

    ``false_alarm_rate`` is the share of accounts whose post and home agreed
    that were flagged anyway, and ``precision`` the share of raised flags that
    were true disagreements. The set was half disagreements by construction,
    so the precision on real traffic, where disagreements are rarer, is lower.
    """
    data = json.loads(DATA_FILE.read_text())
    row_sets = {r["row_set"]: r for r in data["row_sets"]}
    row = row_sets[REPORTED_ROW_SET]
    negatives = row["false_positive"] + row["true_negative"]
    false_alarm_rate = row["false_positive"] / negatives if negatives else 0.0
    precision = float(row["precision"])
    positives_share = row["n_positive"] / row["n_rows"] if row["n_rows"] else 0.0
    return {
        "row_set": row["row_set"],
        "n_rows": row["n_rows"],
        "n_positive": row["n_positive"],
        "false_alarm_rate": false_alarm_rate,
        "precision": precision,
        "recall": float(row["recall"]),
        "positives_share": positives_share,
        "sentence": (
            f"On WNUT-2016, {false_alarm_rate * 100:.0f}% of accounts whose post and "
            "home agreed were also flagged; treat a flag as a reason to look."
        ),
    }
