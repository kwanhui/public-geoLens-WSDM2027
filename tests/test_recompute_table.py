from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "recompute_table", REPO / "scripts" / "recompute_table.py"
)
assert _spec is not None and _spec.loader is not None
recompute_table = importlib.util.module_from_spec(_spec)
sys.modules["recompute_table"] = recompute_table
_spec.loader.exec_module(recompute_table)


PAPER = Path(recompute_table.PAPER_SECTIONS) if recompute_table.PAPER_SECTIONS else None

needs_paper = pytest.mark.skipif(
    PAPER is None or not PAPER.is_dir(),
    reason="set GEOLENS_PAPER_DIR to a copy of the paper's LaTeX sections",
)


@needs_paper
def test_every_number_the_paper_prints_reproduces(capsys) -> None:
    assert recompute_table.main(["--paper-dir", str(PAPER)]) == 0
    out = capsys.readouterr().out
    assert "MISMATCH" not in out
    # The table is read out of the manuscript, so a relabelled row is still
    # checked and an edited cell is compared as it now stands.
    assert "[table: post level, n=337]" in out
    assert "[table: user level, n=200]" in out
    # The flag is checked on the 400-row user-timeline set, not the 737-row
    # set, which prints for reference only.
    assert "[prose: verification flag, 400-row user-timeline set]" in out
    assert "[for reference: not reported in the paper, the 737-row catalogued set]" in out
    assert "[not reported in the paper: fusion's differs-from-best-single rate]" in out
    assert "fusion changes top-1" not in out.lower()
    assert "[stated in the paper, not checkable from committed files]" in out
    assert "161 km separation radius" in out
    assert "orders of magnitude" not in out
    assert "flag false-positive rate 95% Wilson" in out
    assert "paper=0.53 to 0.66" in out
    assert "flag true-positive rate (Section 5 and abstract)" in out
    assert "flag false-positive rate (Section 5 and abstract)" in out
    assert "flag precision at 5% away-from-home prevalence" in out
    assert "paper=0.06" in out
    assert "0.05*TPR / (0.05*TPR + 0.95*FPR)" in out
    assert "out-of-catalogue rows carry no per-engine metric section" in out
    assert "paper=absent" in out and "recomputed=absent" in out
    assert "OSINT scenario check separation (figure caption)" in out
    assert "paper=5,311" in out
    assert "tool commit:" in out
    assert "scenario presets" in out
    assert "authored example file rows" in out
    assert "verification flag candidate causes" in out


@needs_paper
def test_the_checks_the_paper_gained_are_covered(capsys) -> None:
    assert recompute_table.main(["--paper-dir", str(PAPER)]) == 0
    out = capsys.readouterr().out
    for label in (
        "rows sent to the engines",
        "gazetteer user-level Acc@1 matches",
        "gazetteer post-level Acc@161km without four chance hits",
        "gazetteer post-level hits within 161 km without four",
        "majority-class rate, rows naming no place",
        "majority-class rate, user-level rows",
        "Singapore places within 161 km of each other",
        "other city pairs within 161 km",
        "hosted queries per hour",
        "hosted profile saves per hour",
        "hosted bulk jobs per hour",
        "hosted rows per bulk job",
        "hosted onboarded places",
        "hosted expiry of an onboarded place",
    ):
        assert label in out, label
    # Values the paper has stopped printing are skipped, not failed.
    assert "skipped: the paper no longer prints this" in out
    assert "adapter shuffle seed" not in out
    assert "selection cap" not in out


def test_a_missing_manuscript_skips_rather_than_fails(capsys, tmp_path) -> None:
    assert recompute_table.main(["--paper-dir", str(tmp_path / "nowhere")]) == 0
    out = capsys.readouterr().out
    assert "not readable, checks skipped" in out
    assert "MISMATCH" not in out
    assert "skipped: no paper source" in out


def test_no_paper_dir_skips_the_paper_checks(capsys, monkeypatch) -> None:
    monkeypatch.setattr(recompute_table, "PAPER_SECTIONS", None)
    assert recompute_table.main([]) == 0
    out = capsys.readouterr().out
    assert "no --paper-dir given, checks skipped" in out
    assert "MISMATCH" not in out


def test_the_bundled_records_do_not_count_as_scenario_drift() -> None:
    changed = recompute_table._scenario_files_changed_since("HEAD~1")
    assert all("/scenarios/records/" not in name for name in changed), changed


def test_accuracies_round_half_up() -> None:
    assert recompute_table.acc2(0.175) == ".18"
    assert recompute_table.acc2(0.615) == ".62"
    assert recompute_table.acc2(0.3442136) == ".34"
    assert recompute_table.is_rounding_tie(0.175)
    assert not recompute_table.is_rounding_tie(0.3442136)


def test_three_decimal_and_rate_formatting_round_half_up() -> None:
    # Per-bucket Acc@1 and the run-to-run variation print three decimals with
    # the leading zero dropped.
    assert recompute_table.acc3(0.8978102189781022) == ".898"
    assert recompute_table.acc3(0.585) == ".585"
    assert recompute_table.is_rounding_tie(0.7525, places=3)
    assert not recompute_table.is_rounding_tie(0.7518248175182481, places=3)
    # The flag's rates print two decimals with the leading zero kept.
    assert recompute_table.rate2(155 / 200) == "0.78"
    assert recompute_table.rate2(119 / 200) == "0.60"
    # Cost ratios print one decimal.
    assert recompute_table.ratio1(0.148704 / 0.0225783) == "6.6"
    assert recompute_table.ratio1(0.1293712 / 0.0174852) == "7.4"


def test_units_match_the_table() -> None:
    assert recompute_table.latency(66.77) == "67 ms"
    assert recompute_table.latency(1394.08) == "1.4 s"
    assert recompute_table.cost_per_1000(0.148704, 337) == "0.44"
    assert recompute_table.cost_per_1000(0.0, 337) == "0"


def test_wilson_range_rate2_matches_the_flag_prose() -> None:
    assert recompute_table.wilson_range_rate2(155, 200) == "0.71 to 0.83"
    assert recompute_table.wilson_range_rate2(155, 274) == "0.51 to 0.62"
    # The false-positive rate's Wilson interval: 119 flagged of the 200
    # home-consistent rows.
    assert recompute_table.wilson_range_rate2(119, 200) == "0.53 to 0.66"


def test_precision_at_5pct_prevalence_matches_the_paper_arithmetic() -> None:
    # "By arithmetic from the two rates, the precision would be
    # approximately 0.06 if 5% of the rows were away-from-home posts":
    # 0.05*TPR / (0.05*TPR + 0.95*FPR) over the unrounded recomputed rates.
    tpr = 155 / 200
    fpr = 119 / 200
    adjusted = (0.05 * tpr) / (0.05 * tpr + 0.95 * fpr)
    assert recompute_table.rate2(adjusted) == "0.06"


# ----- the corrected Claude rate ---------------------------------------------

def test_only_the_claude_engines_are_cost_corrected() -> None:
    """The committed results were costed at the Claude Haiku 3.5 rate.

    eval/results/ records what the run computed and is not edited, so the
    correction lives in the script and applies to nothing else.
    """
    module = recompute_table
    assert module.CLAUDE_COST_CORRECTION == 1.25
    for engine in ("claude_haiku_post", "claude_haiku_user"):
        assert module.cost_correction(engine) == 1.25
    for engine in ("gpt4o_mini_post", "gpt4o_mini_user", "contrastgeo", "gazetteer_post"):
        assert module.cost_correction(engine) == 1.0


def test_the_correction_is_the_ratio_of_the_two_claude_rates() -> None:
    from geolens.pricing import PRICES

    listed_in, listed_out, _, _ = PRICES["claude-haiku-4-5-20251001"]
    was_in, was_out = 0.80e-6, 4.00e-6  # what the run was costed at
    module = recompute_table
    assert listed_in / was_in == pytest.approx(module.CLAUDE_COST_CORRECTION)
    assert listed_out / was_out == pytest.approx(module.CLAUDE_COST_CORRECTION)


def test_the_corrected_figures_are_the_ones_the_paper_now_prints() -> None:
    module = recompute_table
    results = json.loads((REPO / "eval" / "results" / "wnut2016.json").read_text())
    for level, label, n, expected_cost, expected_ratio in (
        ("post_level", "post", 337, "0.55", "8.2"),
        ("user_level", "user", 200, "0.81", "9.2"),
    ):
        engines = results[level]["engines"]
        engine = f"claude_haiku_{label}"
        recorded = engines[engine]["total_cost_usd"]
        corrected = module.corrected_cost(engine, recorded)
        assert module.cost_per_1000(corrected, n) == expected_cost
        gpt = engines[f"gpt4o_mini_{label}"]["total_cost_usd"]
        assert module.ratio1(corrected / gpt) == expected_ratio
