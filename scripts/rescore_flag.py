#!/usr/bin/env python3
"""Rescore the cross-task verification flag on both candidate row sets.

The flag can only fire on a row carrying both a post and a user timeline, since
it compares the post-level consensus against the user-level one. Two row sets
have been used:

* **user-timeline rows (n=400)**: the `disagree` and `userhome` buckets, where
  the comparison is defined. This is the set `eval/README.md` prescribes.
* **catalogued rows (n=737)**: every row whose ground truth is in the
  catalogue, adding the 337 post-only `intl` and `hard-sem` rows. Given no
  timeline the user-level engines fall back to the post text, so a post-only row
  is not silent by construction and the wider set is not appropriate for
  reporting the flag.

Both sets exclude the 150 `ooc` rows, whose ground truth is outside the
catalogue. The two result files come from two separate runs of the scorer, so
their positives differ by a couple of rows.

This script reads only the committed result JSONs under `eval/results/` and the
committed tweet-ID manifest. Precision and recall are recomputed from the counts
and given 95% Wilson intervals.

Run:
    python3 scripts/rescore_flag.py
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from geolens.batch.metrics import BannerMetrics
from geolens.stats import wilson_interval

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "eval" / "results"
ID_MANIFEST = REPO / "eval" / "wnut2016_id_manifest.csv"

OOC_BUCKET = "ooc"
USER_TIMELINE_BUCKETS = ("disagree", "userhome")

ROW_SETS = {
    "user_timeline_400": {
        "source": "wnut2016_banner.json",
        "buckets": USER_TIMELINE_BUCKETS,
        "rule": (
            "rows that carry a user timeline: the disagree and userhome buckets, "
            "which are the rows where a post-level and a user-level consensus both exist"
        ),
    },
    "catalogued_737": {
        "source": "wnut2016.json",
        "buckets": ("intl", "hard-sem", "disagree", "userhome"),
        "rule": (
            "every row whose ground-truth city is in the catalogue: the whole 887-row "
            "run minus the 150 ooc rows, so the 337 post-only rows are scored as well"
        ),
    },
}


def _banner_from_result(path: Path) -> tuple[BannerMetrics, str]:
    """Rebuild the banner confusion table recorded in a committed result file.

    Returns the table and the run's timestamp. Runs made before `BannerMetrics`
    carried `true_negative` leave it to be recovered from the labelled and
    positive counts.
    """
    result = json.loads(path.read_text())
    stored = result["banner"]
    banner = BannerMetrics(**{k: v for k, v in stored.items() if k in BannerMetrics.__annotations__})
    if not banner.true_negative:
        n_negative = banner.n_labelled - banner.n_positive
        banner.true_negative = n_negative - banner.false_positive
    return banner, result["manifest"]["generated_at"]


def _bucket_counts() -> Counter[str]:
    with ID_MANIFEST.open(newline="", encoding="utf-8") as fh:
        return Counter(row["bucket"] for row in csv.DictReader(fh))


def _rate(numerator: int, denominator: int) -> tuple[float, tuple[float, float]]:
    if denominator <= 0:
        return 0.0, (0.0, 0.0)
    return numerator / denominator, wilson_interval(numerator, denominator)


def rescore(name: str, banner: BannerMetrics, expected_rows: int, generated_at: str = "") -> dict:
    spec = ROW_SETS[name]
    tp, fp, fn, tn = (
        banner.true_positive,
        banner.false_positive,
        banner.false_negative,
        banner.true_negative,
    )
    precision, precision_ci = _rate(tp, tp + fp)
    recall, recall_ci = _rate(tp, tp + fn)
    return {
        "row_set": name,
        "selection_rule": spec["rule"],
        "buckets": list(spec["buckets"]),
        "source_file": spec["source"],
        "source_generated_at": generated_at,
        "n_rows": banner.n_labelled,
        "n_rows_from_id_manifest": expected_rows,
        "n_positive": banner.n_positive,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": precision,
        "precision_ci95": list(precision_ci),
        "recall": recall,
        "recall_ci95": list(recall_ci),
    }


def _print(entry: dict) -> None:
    print(f"{entry['row_set']}  (n = {entry['n_rows']}, positives = {entry['n_positive']})")
    print(f"  rule      : {entry['selection_rule']}")
    print(f"  buckets   : {', '.join(entry['buckets'])}  [{entry['n_rows_from_id_manifest']} rows in the ID manifest]")
    print(f"  source    : eval/results/{entry['source_file']}  (run of {entry['source_generated_at']})")
    print(f"  TP {entry['true_positive']:4d}   FP {entry['false_positive']:4d}"
          f"   FN {entry['false_negative']:4d}   TN {entry['true_negative']:4d}")
    lo, hi = entry["precision_ci95"]
    print(f"  precision : {entry['precision']:.4f}  [{lo:.4f}, {hi:.4f}]")
    lo, hi = entry["recall_ci95"]
    print(f"  recall    : {entry['recall']:.4f}  [{lo:.4f}, {hi:.4f}]")
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-dir", default=str(RESULTS))
    ap.add_argument("--out", default=str(RESULTS / "wnut2016_flag_rescore.json"))
    args = ap.parse_args(argv)

    results_dir = Path(args.results_dir)
    counts = _bucket_counts()

    entries = []
    for name, spec in ROW_SETS.items():
        banner, generated_at = _banner_from_result(results_dir / str(spec["source"]))
        expected = sum(counts[b] for b in spec["buckets"])
        entry = rescore(name, banner, expected, generated_at)
        if entry["n_rows"] != expected:
            raise SystemExit(
                f"{name}: {spec['source']} scored {entry['n_rows']} rows but the ID manifest "
                f"has {expected} in buckets {list(spec['buckets'])}"
            )
        entries.append(entry)
        _print(entry)

    payload = {
        "note": (
            "Recomputed from the committed WNUT-2016 result files; no engine was run "
            "and no API was called."
        ),
        "excluded_bucket": {OOC_BUCKET: counts[OOC_BUCKET]},
        "row_sets": entries,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
