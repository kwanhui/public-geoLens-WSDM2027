"""The recorded runs the page renders with no network.

`scripts/bundle_scenario_records.py` turns the newest scenario check into one
file per scenario under `ui/static/scenarios/records`; these keep the
committed bundles in step with it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "src/geolens/ui/static/scenarios"
RECORDS = SCENARIOS / "records"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "bundle_scenario_records", ROOT / "scripts/bundle_scenario_records.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bundler = _load_script()


def shipped_ids() -> list[str]:
    return list(json.loads((SCENARIOS / "index.json").read_text())["scenarios"])


def test_every_scenario_has_a_bundled_record() -> None:
    for scenario_id in shipped_ids():
        assert (RECORDS / f"{scenario_id}.json").exists(), scenario_id


def test_the_bundles_match_the_newest_check(tmp_path) -> None:
    """Rerunning the bundler over the newest check reproduces the files."""
    written = bundler.write_bundles(tmp_path)
    assert {p.stem for p in written} == set(shipped_ids())
    for path in written:
        fresh = json.loads(path.read_text())
        committed = json.loads((RECORDS / path.name).read_text())
        assert fresh == committed, f"{path.name} is stale; rerun the bundler"


def both_levels(record: dict) -> dict:
    """The step that sends a post and a timeline, so both levels answered."""
    for step in record["steps"]:
        if step.get("post") and step.get("user_posts"):
            return step
    raise AssertionError("no step sends both a post and a timeline")


def test_a_record_carries_what_the_result_view_reads() -> None:
    record = json.loads((RECORDS / "osint-credibility.json").read_text())
    assert record["base_url"] and record["commit"] and record["recorded_at"]
    assert record["instance"]["engines"]
    assert record["instance"]["engine_reference"]["engines"]
    assert record["steps"]
    step = record["steps"][0]
    assert step["post"]
    for name, prediction in step["result"]["per_engine"].items():
        assert set(prediction) >= {"city", "confidence", "top_k", "mode",
                                   "abstain", "skipped", "failed"}, name
    both = both_levels(record)
    assert set(both["result"]["ensembles"]) == {"post", "user"}
    assert both["place_coordinates"]["Singapore"]


def test_the_viral_post_record_walks_the_three_tabs() -> None:
    """The recorded run reads as the live preset does, step for step."""
    record = json.loads((RECORDS / "osint-credibility.json").read_text())
    assert [s["name"] for s in record["steps"]] == [
        "the post on its own",
        "the account's recent posts",
        "the post against its account",
    ]
    assert [s["task"] for s in record["steps"]] == ["post", "user", "verify"]
    assert record["steps"][0]["stage"] == (
        "The post alone: the post-level engines place it.")
    assert record["steps"][1]["stage"] == (
        "The account alone: the user-level engines place it.")
    assert record["steps"][2]["stage"] == (
        "Both together: the two levels disagree, so the flag is raised.")
    # Each step carries only what its tab sends.
    assert record["steps"][0]["user_posts"] is None
    assert record["steps"][1]["post"] is None


def test_the_recorded_vote_counts_the_engines_that_answered() -> None:
    """The vote is counted from the record's own per-engine places."""
    record = json.loads((RECORDS / "osint-credibility.json").read_text())
    triangulation = both_levels(record)["result"]["triangulation"]
    assert triangulation["post_consensus_city"] == "Singapore"
    assert (triangulation["post_consensus_votes"],
            triangulation["post_consensus_answered"]) == (3, 4)
    assert triangulation["user_consensus_city"] == "Tokyo"
    assert (triangulation["user_consensus_votes"],
            triangulation["user_consensus_answered"]) == (4, 4)
    assert triangulation["disagreement_flag"] is True
    assert round(triangulation["disagreement_km"]) == 5311


def test_a_cold_start_record_walks_the_presets_steps() -> None:
    record = json.loads((RECORDS / "estate-management.json").read_text())
    assert record["scenario"]["onboard_city"] == "Bidadari Estate"
    assert record["steps"][0]["stage"].startswith("Before onboarding")
    # The clause the caption drops is carried with it, as the live preset
    # carries it behind the caption's icon.
    assert record["steps"][0]["stage_note"] == "No engine can return it."
    assert [s["name"] for s in record["steps"]] == [
        "Bidadari Estate before onboarding",
        "the drafted profile and its warnings",
        "the operator's edits",
        "post 1 of 3", "post 2 of 3", "post 3 of 3",
    ]
    # The two onboarding steps run no engine, so they carry no result and the
    # answer above them stays on screen.
    assert "result" not in record["steps"][1]
    assert "Bidadari" in record["steps"][2]["stage"]
