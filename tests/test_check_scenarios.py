"""scripts/check_scenarios.py, driven against the app in process."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from geolens.ui.server import create_app

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "src/geolens/ui/static/scenarios"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "check_scenarios", ROOT / "scripts/check_scenarios.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_scenarios = _load_script()


@pytest.fixture
def call(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as client:
        def _call(method: str, path: str, payload: dict[str, Any] | None) -> Any:
            return client.request(method, path, json=payload).json()

        yield _call


def test_a_cold_start_scenario_records_every_step(call) -> None:
    scenario = json.loads((SCENARIOS / "estate-management.json").read_text())
    record = check_scenarios.check_scenario(call, scenario)

    assert record["onboard_city"] == "Bidadari Estate"
    assert record["region"] == "Singapore"
    assert record["reset"]["catalogue_status"] in {"removed", "not_present"}
    assert record["before_onboarding"]["engines"]
    assert record["onboarded"]["catalogue_status"] == "added"
    assert record["onboarded"]["region"] == "Singapore"
    assert len(record["posts"]) == len(scenario["posts"])


def test_the_operator_edits_come_from_the_scenario_file(call) -> None:
    scenario = json.loads((SCENARIOS / "estate-management.json").read_text())
    record = check_scenarios.check_scenario(call, scenario)

    after = record["after_operator_edits"]
    assert after["aliases"] == scenario["operator_edits"]["aliases"]
    assert after["landmarks"] == scenario["operator_edits"]["landmarks"]
    assert after["lat"] == scenario["operator_edits"]["lat"]
    assert after["source"] == "edited"
    # The edited centroid is in the country the hint names, so the region
    # warning must be gone.
    assert not any("outside" in w for w in after["warnings"])


def _both_levels(record: dict[str, Any]) -> dict[str, Any]:
    """The entry that carries a post and a timeline, so both levels answered."""
    for query in record["posts"]:
        if query.get("post") and query.get("user_posts"):
            return query
    raise AssertionError("no entry sends both a post and a timeline")


def test_each_query_records_every_engine_and_the_flag_state(call) -> None:
    scenario = json.loads((SCENARIOS / "osint-credibility.json").read_text())
    record = check_scenarios.check_scenario(call, scenario)

    assert "reset" not in record  # nothing to onboard in this scenario
    query = _both_levels(record)
    # A superset, not the literal nine: an instance that registers another
    # engine must still pass, which is the point of the one roster.
    assert set(query["engines"]) >= {
        "contrastgeo", "fewuser", "retrievezero",
        "gazetteer_post", "gazetteer_user",
        "gpt4o_mini_post", "gpt4o_mini_user",
        "claude_haiku_post", "claude_haiku_user",
    }
    for engine in query["engines"].values():
        assert engine["mode"] in {"real", "stub"}
        assert isinstance(engine["abstain"], bool)
    assert set(query["fusion"]) == {"post", "user"}
    assert set(query["flag"]) == {"raised", "post_consensus", "user_consensus", "km", "notes"}


def test_the_report_names_the_commit_it_was_run_from(call, tmp_path) -> None:
    report = check_scenarios.run(call, "http://testserver", only="crisis-response")
    assert report["base_url"] == "http://testserver"
    assert len(report["tool"]["commit"]) == 40
    assert len(report["scenarios"]) == 1
    assert report["scenarios"][0]["id"] == "crisis-response"


def test_every_shipped_scenario_can_be_checked(call) -> None:
    for scenario in check_scenarios.load_scenarios():
        record = check_scenarios.check_scenario(call, scenario)
        assert record["posts"], scenario["id"]
        assert all("error" not in q for q in record["posts"]), scenario["id"]


def test_a_cold_start_scenario_records_the_state_before_onboarding(call) -> None:
    """Like the preset, the check runs the first post before onboarding."""
    scenario = json.loads((SCENARIOS / "crisis-response.json").read_text())
    record = check_scenarios.check_scenario(call, scenario)

    before = record["before_onboarding"]
    assert before["engines"]
    assert scenario["onboard_city"] not in {e["city"] for e in before["engines"].values()}


def test_the_crisis_scenario_sends_a_timeline_with_every_post() -> None:
    """Without one the user-level engines are not run, and the scenario
    reports user-level results."""
    scenario = json.loads((SCENARIOS / "crisis-response.json").read_text())
    posts = scenario["posts"]
    for item in posts:
        assert item["user_posts"], item["post"]
        assert item["post"] not in item["user_posts"]
        assert len(item["user_posts"]) == len(posts) - 1


def test_the_estate_scenario_stays_post_only(call) -> None:
    scenario = json.loads((SCENARIOS / "estate-management.json").read_text())
    assert all(item["user_posts"] is None for item in scenario["posts"])

    record = check_scenarios.check_scenario(call, scenario)
    for query in record["posts"]:
        assert set(query["fusion"]) == {"post"}
        assert query["engines_not_run"]
        assert query["flag"]["raised"] is False


def test_the_viral_post_scenario_walks_the_three_tabs() -> None:
    """The post alone, the account alone, then the two together."""
    scenario = json.loads((SCENARIOS / "osint-credibility.json").read_text())
    posts = scenario["posts"]
    assert [item["task"] for item in posts] == ["post", "user", "verify"]
    assert [item["step"] for item in posts] == [
        "the post on its own",
        "the account's recent posts",
        "the post against its account",
    ]
    # Each step sends only what its tab shows, and the last sends both.
    assert posts[0]["post"] and posts[0]["user_posts"] is None
    assert posts[1]["post"] is None and posts[1]["user_posts"]
    assert posts[2]["post"] == posts[0]["post"]
    assert posts[2]["user_posts"] == posts[1]["user_posts"]
    # Only a step that can raise the flag carries a caption that names one.
    assert "stage_flagged" not in posts[0] and "stage_flagged" not in posts[1]
    assert "flag" in posts[2]["stage_flagged"]
    assert "flag" not in posts[2]["stage"]


def test_a_step_that_sends_one_level_records_only_that_level(call) -> None:
    """A post-less or timeline-less entry is still checked end to end."""
    scenario = json.loads((SCENARIOS / "osint-credibility.json").read_text())
    record = check_scenarios.check_scenario(call, scenario)

    assert len(record["posts"]) == 3
    post_only, user_only, both = record["posts"]
    assert set(post_only["fusion"]) == {"post"}
    assert post_only["user_posts"] is None
    assert set(user_only["fusion"]) == {"user"}
    assert user_only["post"] is None
    assert set(both["fusion"]) == {"post", "user"}
    # A level that was never asked is named as not run rather than left out.
    assert post_only["engines_not_run"] and user_only["engines_not_run"]
    assert not both["engines_not_run"]


def test_scenario_files_do_not_mention_a_partner() -> None:
    for path in sorted(SCENARIOS.glob("*.json")):
        text = path.read_text().lower()
        assert "partner" not in text, path.name
