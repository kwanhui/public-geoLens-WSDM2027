#!/usr/bin/env python3
"""Run every shipped scenario against a GeoLens instance and record what happened.

For each scenario the script resets any earlier onboarding, queries once before
onboarding, onboards with the scenario's region hint, applies the operator edits
the scenario file carries, queries every post, and writes what each engine said
together with the verification flag.

The scenario files under ``ui/static/scenarios`` are the single source: the
interface preset, the screencast recorder, ``capture_ui.py`` and this script all
read the same place, the same region hint and the same operator edits.

Output goes to ``docs/scenario-checks/<date>.json``. Run it against a real
instance; a stub-mode run proves the script works but its predictions are
placeholders and must not be filed as evidence.

Usage:
    python3 scripts/check_scenarios.py --base-url https://kwanhui-geo-lens.hf.space
    python3 scripts/check_scenarios.py --base-url http://127.0.0.1:7860 \
        --scenario estate-management --out-dir /tmp/scenario-checks
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCENARIOS = REPO / "src/geolens/ui/static/scenarios"
DEFAULT_OUT = REPO / "docs/scenario-checks"

# (method, path, payload) -> parsed JSON body. The HTTP client is injected so
# the test can drive the app in-process instead of over a socket.
Caller = Callable[[str, str, dict[str, Any] | None], Any]


def urllib_caller(base_url: str, timeout: float = 300.0) -> Caller:
    base = base_url.rstrip("/")

    def call(method: str, path: str, payload: dict[str, Any] | None) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "replace")}

    return call


TOKEN_FILE = Path.home() / ".geolens" / "scenario-check-tokens.json"


def _load_tokens() -> dict[str, str]:
    """Edit tokens from the previous run, so this run can reset what that one onboarded."""
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_tokens(tokens: dict[str, str]) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens))


def tool_commit() -> dict[str, Any]:
    """The commit the checked instance is expected to be running."""
    def git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def load_scenarios(only: str | None = None) -> list[dict[str, Any]]:
    index = json.loads((SCENARIOS / "index.json").read_text())
    ids = [i for i in index["scenarios"] if only is None or i == only]
    if only is not None and not ids:
        raise SystemExit(f"no scenario named {only!r} in {SCENARIOS / 'index.json'}")
    return [json.loads((SCENARIOS / f"{i}.json").read_text()) for i in ids]


def _geolocate(call: Caller, post: str | None, user_posts: list[str] | None) -> dict[str, Any]:
    body = call("POST", "/geolocate", {"post": post, "user_posts": user_posts, "k": 5})
    if "per_engine" not in body:
        return {"post": post, "user_posts": user_posts, "error": body}
    engines = {
        name: {
            "city": p["city"],
            "confidence": round(p["confidence"], 4),
            "mode": p["mode"],
            "abstain": p["abstain"],
            "note": p["note"],
        }
        for name, p in body["per_engine"].items()
    }
    # Which engines were never called, and why. A user-level engine without a
    # timeline is the common case and the record should show it as such rather
    # than leaving the bucket silently absent from `fusion`.
    not_run = {
        name: p.get("reason", "")
        for name, p in body["per_engine"].items()
        if p.get("skipped")
    }
    fusion = {
        bucket: {
            "city": e["consensus_city"],
            "confidence": round(e["consensus_confidence"], 4),
            "method": e["method"],
            "engines": e["contributing_engines"],
        }
        for bucket, e in (body.get("ensembles") or {}).items()
    }
    tri = body.get("triangulation") or {}
    flag = {
        "raised": bool(tri.get("disagreement_flag")),
        "post_consensus": tri.get("post_consensus_city", ""),
        "user_consensus": tri.get("user_consensus_city", ""),
        "km": tri.get("disagreement_km"),
        "notes": tri.get("notes", []),
    }
    return {
        "post": post,
        "user_posts": user_posts,
        "engines": engines,
        "engines_not_run": not_run,
        "fusion": fusion,
        "flag": flag,
    }


def _profile_summary(body: dict[str, Any]) -> dict[str, Any]:
    keys = ("source", "region", "lat", "lon", "aliases", "landmarks",
            "catalogue_status", "catalogue_size")
    out = {k: body.get(k) for k in keys}
    out["warnings"] = body.get("warnings", [])
    return out


def check_scenario(call: Caller, scenario: dict[str, Any]) -> dict[str, Any]:
    """Drive one scenario end to end and return the record for it."""
    city = scenario.get("onboard_city")
    first = scenario["posts"][0]
    record: dict[str, Any] = {
        "id": scenario["id"],
        "title": scenario["title"],
        "onboard_city": city,
        "region": scenario.get("region", ""),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if city:
        tokens = _load_tokens()
        reset_payload = {"city": city}
        if tokens.get(city):
            reset_payload["edit_token"] = tokens[city]
        record["reset"] = call("DELETE", "/onboard", reset_payload)
        if "error" in record["reset"]:
            raise SystemExit(
                f"{scenario['id']}: {city!r} could not be reset, so the check would not "
                f"start from a cold catalogue: {record['reset'].get('detail', '')[:200]}"
            )
        record["before_onboarding"] = _geolocate(call, first.get("post"), first.get("user_posts"))
        drafted = call("POST", "/onboard", {"city": city, "region": scenario.get("region", "")})
        record["onboarded"] = _profile_summary(drafted)

        edits = scenario.get("operator_edits") or {}
        if edits:
            payload = {
                "name": city,
                "region": scenario.get("region", ""),
                "aliases": edits.get("aliases", drafted.get("aliases", [])),
                "landmarks": edits.get("landmarks", drafted.get("landmarks", [])),
                "foods": edits.get("foods", drafted.get("foods", [])),
                "slang": edits.get("slang", drafted.get("slang", [])),
                "notes": edits.get("notes", drafted.get("notes", "")),
                "lat": edits.get("lat", drafted.get("lat")),
                "lon": edits.get("lon", drafted.get("lon")),
            }
            if drafted.get("edit_token"):
                payload["edit_token"] = drafted["edit_token"]
                tokens[city] = drafted["edit_token"]
                _save_tokens(tokens)
            saved = call("PUT", "/onboard", payload)
            if "error" in saved:
                raise SystemExit(
                    f"{scenario['id']}: the operator's edits were refused: {saved.get('detail', saved)}"
                )
            record["after_operator_edits"] = _profile_summary(saved)

    record["posts"] = [
        _geolocate(call, item.get("post"), item.get("user_posts")) for item in scenario["posts"]
    ]
    return record


def run(call: Caller, base_url: str, only: str | None = None) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": base_url,
        "tool": tool_commit(),
        "scenarios": [check_scenario(call, s) for s in load_scenarios(only)],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", required=True, help="a running GeoLens instance")
    ap.add_argument("--scenario", default=None, help="check only this scenario id")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    call = urllib_caller(args.base_url)
    report = run(call, args.base_url, args.scenario)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{date.today().isoformat()}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    stubbed = sum(
        1
        for s in report["scenarios"]
        for q in s["posts"]
        for e in (q.get("engines") or {}).values()
        if e["mode"] == "stub"
    )
    print(f"wrote {out}")
    for s in report["scenarios"]:
        flags = sum(1 for q in s["posts"] if (q.get("flag") or {}).get("raised"))
        print(f"  {s['id']}: {len(s['posts'])} queries, {flags} flagged")
    if stubbed:
        print(
            f"{stubbed} engine results came back in stub mode. This run proves the "
            "script works; it is not evidence about the system's behaviour.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
