#!/usr/bin/env python3
"""Bundle the newest scenario check as a recorded run the page can render.

``docs/scenario-checks/<date>.json`` holds what every engine answered for each
scenario on a real instance. This turns the newest one into
``ui/static/scenarios/records/<id>.json``, shaped like the responses the
single-query view already renders, so ``?replay=<id>`` shows that run with no
API call.

A record carries each engine's place, confidence, mode and abstention. The
consensus vote is counted here from those places; the distance, the flag and
both fused predictions are copied as the instance reported them.

Run it after committing a new check, and commit the files it writes:

    python3 scripts/bundle_scenario_records.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from geolens.engine_reference import engine_reference
from geolens.engines._coords import coords_for
from geolens.engines.registry import (
    ENGINE_SPECS,
    build_engines,
    engine_metadata,
    local_engine_names,
)
from geolens.flag_reference import flag_reference
from geolens.geo import ACC_KM_THRESHOLD
from geolens.ui.limits import MAX_POST_CHARS, MAX_TIMELINE_CHARS

REPO = Path(__file__).resolve().parents[1]
CHECKS = REPO / "docs/scenario-checks"
SCENARIOS = REPO / "src/geolens/ui/static/scenarios"
RECORDS = SCENARIOS / "records"

GRANULARITY = {spec.key: spec.granularity for spec in ENGINE_SPECS}


def newest_check(directory: Path = CHECKS) -> Path:
    """The most recent scenario check, by file name."""
    files = sorted(p for p in directory.glob("*.json"))
    if not files:
        raise SystemExit(f"no scenario check in {directory}")
    return files[-1]


def instance_view() -> dict[str, Any]:
    """The settings the page reads, frozen into the bundle."""
    built, _ = build_engines()
    return {
        "engines": engine_metadata(built),
        "local_engines": local_engine_names(built),
        "require_region": False,
        "max_post_chars": MAX_POST_CHARS,
        "max_timeline_chars": MAX_TIMELINE_CHARS,
        "flag_radius_km_default": ACC_KM_THRESHOLD,
        "flag_reference": flag_reference(),
        "engine_reference": engine_reference(),
        "limits": {},
        "spend": {"ceiling_reached": False, "reason": ""},
    }


def _votes(engines: dict[str, Any], level: str, consensus: str) -> tuple[int, int]:
    """How many of the level's answering engines put `consensus` first."""
    answered = [
        p for name, p in engines.items()
        if GRANULARITY.get(name) == level and not p.get("abstain") and p.get("city")
    ]
    return sum(1 for p in answered if p["city"] == consensus), len(answered)


def _prediction(name: str, recorded: dict[str, Any]) -> dict[str, Any]:
    city = recorded.get("city") or ""
    confidence = float(recorded.get("confidence") or 0.0)
    abstain = bool(recorded.get("abstain"))
    return {
        "city": city,
        "confidence": confidence,
        "top_k": [] if abstain or not city else [[city, confidence]],
        "latency_ms": None,
        "mode": recorded.get("mode", ""),
        "abstain": abstain,
        "evidence": "",
        "note": "",
        "failed": False,
        "error_class": "",
        "outcome": "",
        "skipped": False,
        "reason": "",
    }


def _step_result(entry: dict[str, Any]) -> dict[str, Any]:
    """One recorded query, shaped like a /geolocate response."""
    engines = entry.get("engines") or {}
    not_run = entry.get("engines_not_run") or {}
    per_engine: dict[str, Any] = {}
    for name, recorded in engines.items():
        prediction = _prediction(name, recorded)
        if name in not_run:
            prediction.update(skipped=True, reason=not_run[name], top_k=[])
        per_engine[name] = prediction

    fusion = entry.get("fusion") or {}
    ensembles = {
        level: {
            "consensus_city": e["city"],
            "consensus_confidence": e["confidence"],
            "method": e["method"],
            "contributing_engines": e["engines"],
            "best_single_engine": "",
            "best_single_city": "",
            "differs_from_best_single": False,
        }
        for level, e in fusion.items()
    }

    flag = entry.get("flag") or {}
    post_city, user_city = flag.get("post_consensus", ""), flag.get("user_consensus", "")
    post_votes, post_answered = _votes(per_engine, "post", post_city)
    user_votes, user_answered = _votes(per_engine, "user", user_city)
    triangulation = {
        "disagreement_flag": bool(flag.get("raised")),
        "disagreement_km": flag.get("km"),
        "disagreement_score": 0.0,
        "post_consensus_city": post_city,
        "user_consensus_city": user_city,
        "post_consensus_votes": post_votes,
        "post_consensus_answered": post_answered,
        "user_consensus_votes": user_votes,
        "user_consensus_answered": user_answered,
        "notes": flag.get("notes", []),
    }

    method = next(iter(fusion.values()), {}).get("method", "weighted")
    manifest = {
        "k": 5,
        "ensemble_method": method,
        "flag_radius_km": ACC_KM_THRESHOLD,
        "engines": {name: {} for name in per_engine},
        "selected_engines": list(per_engine),
    }
    return {
        "per_engine": per_engine,
        "ensembles": ensembles,
        "triangulation": triangulation,
        "manifest": manifest,
    }


def _coordinates(result: dict[str, Any], extra: dict[str, list[float]]) -> dict[str, list[float]]:
    names = {p["city"] for p in result["per_engine"].values() if p.get("city")}
    names |= {e["consensus_city"] for e in result["ensembles"].values()}
    names |= {result["triangulation"]["post_consensus_city"],
              result["triangulation"]["user_consensus_city"]}
    table: dict[str, list[float]] = {}
    for name in sorted(n for n in names if n):
        if name in extra:
            table[name] = extra[name]
            continue
        found = coords_for(name)
        if found:
            table[name] = [round(found[0], 4), round(found[1], 4)]
    return table


def _draft_stage(city: str, drafted: dict[str, Any]) -> str:
    """The drafted profile and the warnings the panel raised on it."""
    warnings = [str(w) for w in (drafted.get("warnings") or [])]
    source = drafted.get("source") or "the drafting model"
    head = f"The drafted profile for {city} comes back from {source}."
    if not warnings:
        return f"{head} The panel raises no warning."
    return f"{head} The panel warns: {'; '.join(warnings)}."


def _edits_stage(city: str, edits: dict[str, Any]) -> str:
    """What the operator changed before saving."""
    parts = []
    for field in ("aliases", "landmarks"):
        values = edits.get(field) or []
        if values:
            parts.append(f"{field} {', '.join(str(v) for v in values)}")
    if edits.get("lat") is not None and edits.get("lon") is not None:
        parts.append(f"centroid {edits['lat']}, {edits['lon']}")
    if not parts:
        return f"The operator saves the draft for {city} as it came back."
    return f"The operator sets {'; '.join(parts)}, and saves {city}."


def bundle(check: dict[str, Any], record: dict[str, Any], source: str) -> dict[str, Any]:
    """One scenario's recorded run, ready for the page.

    The step names are the ones the live preset prints, so a recorded run and
    a live one read the same.
    """
    scenario_id = record["id"]
    shipped = json.loads((SCENARIOS / f"{scenario_id}.json").read_text())

    edited = record.get("after_operator_edits") or {}
    drafted = record.get("onboarded") or {}
    onboarded = edited or drafted
    extra: dict[str, list[float]] = {}
    city = record.get("onboard_city")
    if city and onboarded.get("lat") is not None and onboarded.get("lon") is not None:
        extra[city] = [onboarded["lat"], onboarded["lon"]]

    steps = []
    before = record.get("before_onboarding")
    if before and "engines" in before:
        result = _step_result(before)
        steps.append({
            "name": f"{city} before onboarding",
            # The caption and the note behind its icon, as the live preset
            # splits them.
            "stage": f"Before onboarding: {city} is not a candidate.",
            "stage_note": "No engine can return it.",
            "post": before.get("post"),
            "user_posts": before.get("user_posts"),
            "result": result,
            "place_coordinates": _coordinates(result, {}),
        })
    # The draft and the operator's edits are steps of the live preset, and the
    # record carries both, so a recorded run walks through them too. Neither
    # runs the engines, so the answer above stays on screen.
    if city and drafted:
        steps.append({
            "name": "the drafted profile and its warnings",
            "stage": _draft_stage(city, drafted),
        })
    if city and edited:
        steps.append({
            "name": "the operator's edits",
            "stage": _edits_stage(city, shipped.get("operator_edits") or {}),
        })
    total_posts = len(record.get("posts") or [])
    shipped_posts = shipped.get("posts") or []
    default_task = shipped.get("task", "verify")
    for index, entry in enumerate(record.get("posts") or [], start=1):
        if "engines" not in entry:
            continue
        result = _step_result(entry)
        # A scenario whose posts name their own step, caption and tab is
        # walked by the recorded run exactly as the live preset walks it.
        shipped_post = shipped_posts[index - 1] if index <= len(shipped_posts) else {}
        stage = (f"Post {index} of {total_posts}" if total_posts > 1
                 else "The recorded query")
        if city:
            stage = f"After onboarding: {stage.lower()}, with {city} a candidate."
        else:
            stage = f"{stage}."
        if shipped_post.get("stage"):
            stage = shipped_post["stage"]
            if (shipped_post.get("stage_flagged")
                    and result["triangulation"]["disagreement_flag"]):
                stage = shipped_post["stage_flagged"]
        steps.append({
            "name": shipped_post.get("step")
            or (f"post {index} of {total_posts}" if total_posts > 1 else "the query"),
            "stage": stage,
            "task": shipped_post.get("task") or default_task,
            "post": entry.get("post"),
            "user_posts": entry.get("user_posts"),
            "result": result,
            "place_coordinates": _coordinates(result, extra),
        })

    return {
        "scenario_id": scenario_id,
        "title": record["title"],
        "source_file": source,
        "recorded_at": record.get("checked_at") or check.get("generated_at", ""),
        "base_url": check.get("base_url", ""),
        "commit": (check.get("tool") or {}).get("commit", ""),
        "scenario": {
            "id": scenario_id,
            "title": shipped["title"],
            "subtitle": shipped["subtitle"],
            # Which tab the recorded run opens, as the live tile does.
            "task": shipped.get("task", "verify"),
            "headline": shipped["headline"],
            "lang": shipped.get("lang", "en"),
            "engine_focus": shipped.get("engine_focus", ""),
            "onboard_city": shipped.get("onboard_city"),
            "comparison_default": bool(shipped.get("comparison_default")),
        },
        "instance": instance_view(),
        "steps": steps,
    }


def write_bundles(out_dir: Path = RECORDS, check_path: Path | None = None) -> list[Path]:
    path = check_path or newest_check()
    check = json.loads(path.read_text())
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for record in check["scenarios"]:
        data = bundle(check, record, path.name)
        target = out_dir / f"{record['id']}.json"
        target.write_text(json.dumps(data, indent=2) + "\n")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", default=None, help="a scenario check to bundle (default: newest)")
    ap.add_argument("--out-dir", default=str(RECORDS))
    args = ap.parse_args(argv)
    written = write_bundles(Path(args.out_dir),
                            Path(args.check) if args.check else None)
    for path in written:
        print(f"wrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
