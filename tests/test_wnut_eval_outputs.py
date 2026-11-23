"""What `eval/adapters/run_wnut_eval.py` writes beside the metrics.

The call counts in the manifest, the per-row CSV, the majority-vote consensus
option for the flag, and the flag radius sweep. Every test builds its rows
from fixed predictions, so no engine runs and no API is called.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

from geolens.batch.runner import BatchRow
from geolens.engines.base import (
    Prediction,
    failed_prediction,
    no_catalogue_place_prediction,
    skipped_prediction,
)
from geolens.triangulator import triangulate

_SCRIPT = Path(__file__).resolve().parents[1] / "eval" / "adapters" / "run_wnut_eval.py"
_spec = importlib.util.spec_from_file_location("run_wnut_eval", _SCRIPT)
assert _spec is not None and _spec.loader is not None
rwe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rwe)

GRAN = {"p1": "post", "p2": "post", "p3": "post", "u1": "user", "u2": "user", "u3": "user"}


def _p(city: str, conf: float = 0.5) -> Prediction:
    return Prediction(city=city, confidence=conf, top_k=[(city, conf)])


ABSTAIN = Prediction(city="", confidence=0.0, top_k=[], abstain=True)


def _row(rid: str, per_engine: dict, *, bucket: str, gt: str = "Singapore",
         gt_user: str | None = None, should: bool | None = None,
         status: str = "ok") -> BatchRow:
    return BatchRow(
        id=rid, status=status, in_catalogue=status != "ooc", ground_truth_city=gt,
        ground_truth_user_city=gt_user, bucket=bucket, should_disagree=should,
        per_engine=per_engine if status == "ok" else {},
        triangulation=triangulate(per_engine, engines=GRAN) if status == "ok" else None,
    )


# --- call counts -------------------------------------------------------------

def test_outcomes_split_attempted_calls_and_count_failed_replies() -> None:
    rows = [
        _row("a", {"p1": _p("Singapore"), "p2": ABSTAIN,
                   "p3": failed_prediction("p3", error_class="JSONDecodeError")}, bucket="intl"),
        _row("b", {"p1": _p("Tokyo"), "p2": _p("Singapore"),
                   "p3": no_catalogue_place_prediction("p3")}, bucket="intl"),
        _row("c", {"p1": skipped_prediction("p1", reason="no post"), "p2": ABSTAIN,
                   "p3": _p("Singapore")}, bucket="intl"),
        _row("d", {}, bucket="ooc", status="ooc"),
    ]
    out = rwe.engine_outcomes(rows, ["p1", "p2", "p3"])

    assert out["p1"] == {"rows": 4, "attempted": 2, "answered": 2, "abstained": 0,
                         "failed": 0, "no_catalogue_place": 0, "skipped": 1,
                         "not_run": 1, "failed_replies": 0, "coverage": 1.0}
    assert out["p2"]["attempted"] == 3
    assert out["p2"]["abstained"] == 2
    assert out["p2"]["coverage"] == round(1 / 3, 4)
    assert out["p3"]["failed"] == 1
    assert out["p3"]["no_catalogue_place"] == 1
    assert out["p3"]["failed_replies"] == 2
    assert out["p3"]["attempted"] == 3


def test_mcnemar_leaves_out_failed_calls_but_counts_abstentions_as_misses() -> None:
    rows = [
        _row("a", {"p1": _p("Singapore"), "p2": failed_prediction("p2", error_class="X")},
             bucket="intl"),
        _row("b", {"p1": _p("Singapore"), "p2": ABSTAIN}, bucket="intl"),
        _row("c", {"p1": _p("Tokyo"), "p2": _p("Singapore")}, bucket="intl"),
    ]
    m = rwe._mcnemar(rows, "p1", "p2")

    assert m["paired_rows"] == 2  # row a is not paired: p2's call failed
    assert m["a_only_correct"] == 1  # row b: the abstention is a miss
    assert m["b_only_correct"] == 1


# --- per-row output ------------------------------------------------------------

def test_per_row_csv_has_one_line_per_row_and_no_text(tmp_path) -> None:
    rows = [
        _row("intl-1", {"p1": _p("Singapore"), "p2": ABSTAIN, "u1": _p("Tokyo")},
             bucket="intl"),
        _row("ooc-1", {}, bucket="ooc", gt="Reykjavik", status="ooc"),
    ]
    header, records = rwe.per_row_records(rows, ["p1", "p2", "u1"], GRAN, radius_km=161.0)
    path = tmp_path / "rows.csv"
    rwe.write_rows_csv(header, records, path)
    with path.open(newline="", encoding="utf-8") as fh:
        got = list(csv.DictReader(fh))

    assert [r["id"] for r in got] == ["intl-1", "ooc-1"]
    first = got[0]
    assert first["section"] == "post_level"
    assert (first["p1_city"], first["p1_outcome"], first["p1_correct"]) == ("Singapore", "answered", "1")
    assert first["p1_error_km"] == "0.0" and first["p1_within_161km"] == "1"
    assert (first["p2_outcome"], first["p2_correct"], first["p2_error_km"]) == ("abstained", "0", "")
    assert first["p2_within_161km"] == "0"  # an abstention is a miss for Acc@161 km
    assert first["weighted_flag"] == "1" and first["majority_flag"] == "1"
    assert got[1]["section"] == "excluded_ooc" and got[1]["p1_outcome"] == "not_run"
    assert not any(h in ("post", "user_posts") for h in header)


# --- majority-vote consensus -------------------------------------------------------

def test_majority_counts_engines_where_weighted_sums_confidence() -> None:
    preds = {"p1": _p("Tokyo", 0.95), "p2": _p("Singapore", 0.3), "p3": _p("Singapore", 0.3)}

    weighted = triangulate(preds, engines=GRAN)
    majority = triangulate(preds, engines=GRAN, consensus="majority")

    assert weighted.post_consensus_city == "Tokyo"
    assert majority.post_consensus_city == "Singapore"


def test_a_majority_tie_falls_back_on_confidence() -> None:
    from geolens.triangulator import majority_tied

    preds = {"p1": _p("Tokyo", 0.9), "p2": _p("Singapore", 0.2)}

    assert triangulate(preds, engines=GRAN, consensus="majority").post_consensus_city == "Tokyo"
    assert majority_tied(preds, GRAN, "post")


def test_an_unknown_consensus_rule_is_refused() -> None:
    with pytest.raises(ValueError):
        triangulate({"p1": _p("Tokyo")}, engines=GRAN, consensus="loudest")


def _flag_rows() -> list[BatchRow]:
    # disagree-1: the post points to Singapore by majority, to Tokyo by weight.
    split = {"p1": _p("Tokyo", 0.95), "p2": _p("Singapore", 0.3), "p3": _p("Singapore", 0.3),
             "u1": _p("Tokyo"), "u2": _p("Tokyo")}
    home = {"p1": _p("Bedok"), "u1": _p("Tampines"), "u2": _p("Tampines")}
    far = {"p1": _p("Bangkok"), "u1": _p("Jakarta"), "u2": _p("Jakarta")}
    return [
        _row("disagree-1", split, bucket="disagree", should=True),
        _row("userhome-1", home, bucket="userhome", should=False),
        _row("userhome-2", far, bucket="userhome", should=False),
        _row("intl-1", {"p1": _p("Tokyo")}, bucket="intl"),  # no label: not a flag row
    ]


def test_flag_metrics_give_every_rate_with_its_denominator() -> None:
    m = rwe.flag_metrics(_flag_rows(), GRAN, consensus="majority", radius_km=161.0)

    assert (m["n_rows"], m["n_positive"], m["n_negative"]) == (3, 1, 2)
    assert (m["true_positive"], m["false_positive"]) == (1, 1)  # Bangkok-Jakarta fires
    assert m["true_positive_rate"]["k"] == 1 and m["true_positive_rate"]["n"] == 1
    assert m["false_positive_rate"] == {"k": 1, "n": 2, "rate": 0.5,
                                        "wilson_95": m["false_positive_rate"]["wilson_95"]}
    assert m["precision"]["n"] == 2
    assert "tied_rows" in m


def test_the_weighted_rule_misses_the_split_row_that_majority_flags() -> None:
    w = rwe.flag_metrics(_flag_rows(), GRAN, consensus="weighted", radius_km=161.0)
    assert w["true_positive"] == 0  # Tokyo against Tokyo: no flag


# --- radius sweep -------------------------------------------------------------------

def test_sweep_covers_both_rules_and_every_radius(tmp_path) -> None:
    sweep = rwe.flag_sweep(_flag_rows(), GRAN, [25.0, 161.0, 5000.0])

    assert [(s["consensus"], s["flag_radius_km"]) for s in sweep] == [
        ("weighted", 25.0), ("weighted", 161.0), ("weighted", 5000.0),
        ("majority", 25.0), ("majority", 161.0), ("majority", 5000.0),
    ]
    by = {(s["consensus"], s["flag_radius_km"]): s for s in sweep}
    # Bedok-Tampines is about 6 km apart: flagged at no radius in the sweep.
    # Bangkok-Jakarta (about 2,300 km) is flagged at 161 km, not at 5,000 km.
    assert by[("majority", 161.0)]["false_positive"] == 1
    assert by[("majority", 5000.0)]["false_positive"] == 0
    path = tmp_path / "sweep.csv"
    rwe.write_sweep_csv(sweep, path)
    lines = path.read_text().splitlines()
    assert lines[0].split(",") == rwe.SWEEP_COLUMNS
    assert len(lines) == 1 + len(sweep)


def test_sweep_radii_are_parsed_and_bounded() -> None:
    import argparse

    assert rwe._radii("50, 161,1000") == [50.0, 161.0, 1000.0]
    with pytest.raises(argparse.ArgumentTypeError):
        rwe._radii("0,161")


def test_the_script_writes_all_three_files_in_placeholder_mode(tmp_path, monkeypatch) -> None:
    """End to end on a four-row CSV with every engine in placeholder mode."""
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    src = tmp_path / "in.csv"
    src.write_text(
        "id,post,user_posts,ground_truth_city,ground_truth_user_city,bucket,should_disagree\n"
        "intl-1,Queue at the Bedok hawker centre,,Bedok,,intl,\n"
        "ooc-1,Lovely day,,Reykjavik,,ooc,\n"
        "disagree-1,Fire at Marina Bay Sands,Ramen in Tokyo|Tokyo again,Singapore,Tokyo,disagree,1\n"
        "userhome-1,Bedok run,Bedok reservoir|Bedok again,Bedok,Bedok,userhome,0\n",
        encoding="utf-8",
    )
    out = tmp_path / "res.json"
    assert rwe.main(["--csv", str(src), "--out", str(out), "--flag-consensus", "majority",
                     "--flag-radius-sweep", "100,161"]) == 0

    res = json.loads(out.read_text())
    man = res["manifest"]
    assert man["flag_consensus"] == "majority"
    assert man["flag_radius_km"] == 161.0
    assert set(man["engine_outcomes"]) == set(man["engines"])
    assert man["engine_outcomes"]["gazetteer_post"]["rows"] == 4
    assert "engine_call_counts" in man and len(man["input_file_sha256"]) == 64
    assert res["flag"]["n_rows"] == 2
    assert [c["consensus"] for c in res["flag_comparison"]] == ["weighted", "majority"]
    assert len(res["flag_sweep"]) == 4
    rows = list(csv.DictReader((tmp_path / "res_rows.csv").open(encoding="utf-8")))
    assert [r["id"] for r in rows] == ["intl-1", "ooc-1", "disagree-1", "userhome-1"]
    assert (tmp_path / "res_flag_sweep.csv").exists()
