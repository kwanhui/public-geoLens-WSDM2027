"""The flag rescore reads the committed results and reports both row sets."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("rescore_flag", REPO / "scripts" / "rescore_flag.py")
assert _spec is not None and _spec.loader is not None
rescore_flag = importlib.util.module_from_spec(_spec)
sys.modules["rescore_flag"] = rescore_flag
_spec.loader.exec_module(rescore_flag)


def test_row_sets_partition_the_committed_manifest() -> None:
    counts = rescore_flag._bucket_counts()
    assert sum(counts.values()) == 887
    assert sum(counts[b] for b in rescore_flag.ROW_SETS["user_timeline_400"]["buckets"]) == 400
    assert sum(counts[b] for b in rescore_flag.ROW_SETS["catalogued_737"]["buckets"]) == 737
    assert counts[rescore_flag.OOC_BUCKET] == 150


def test_confusion_table_closes() -> None:
    for spec in rescore_flag.ROW_SETS.values():
        banner, _ = rescore_flag._banner_from_result(rescore_flag.RESULTS / spec["source"])
        total = (
            banner.true_positive
            + banner.false_positive
            + banner.false_negative
            + banner.true_negative
        )
        assert total == banner.n_labelled
        assert banner.true_positive + banner.false_negative == banner.n_positive


def test_user_timeline_set_is_the_one_the_eval_readme_prescribes() -> None:
    banner, _ = rescore_flag._banner_from_result(rescore_flag.RESULTS / "wnut2016_banner.json")
    entry = rescore_flag.rescore("user_timeline_400", banner, 400)
    assert entry["n_rows"] == 400
    assert (entry["true_positive"], entry["false_positive"]) == (155, 119)
    assert (entry["false_negative"], entry["true_negative"]) == (45, 81)
    assert round(entry["precision"], 2) == 0.57
    assert round(entry["recall"], 2) == 0.78
    low, high = entry["precision_ci95"]
    assert low < entry["precision"] < high


def test_main_writes_both_row_sets(tmp_path) -> None:
    out = tmp_path / "rescore.json"
    assert rescore_flag.main(["--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert [e["row_set"] for e in payload["row_sets"]] == ["user_timeline_400", "catalogued_737"]
