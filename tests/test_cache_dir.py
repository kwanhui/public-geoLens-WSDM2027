from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from geolens.paths import ONBOARDED_CITIES, cache_root, cache_subdir
from geolens.places import place_id

# Onboards one city, then reports every onboarded city the process can see and
# the cache-key salt RetrieveZero derives from them.
PROBE = """
import json
import sys

from geolens.engines.retrievezero import _describe_fn_cache_id
from geolens.onboarding import onboard_city
from geolens.paths import ONBOARDED_CITIES, cache_subdir

onboard_city(sys.argv[1])
visible = sorted(p.stem for p in cache_subdir(ONBOARDED_CITIES).glob("*.json"))
print(json.dumps({"visible": visible, "salt": _describe_fn_cache_id()}))
"""


def _run_probe(city: str, home: Path, cache_dir: Path) -> dict:
    env = dict(os.environ, HOME=str(home), GEOLENS_CACHE_DIR=str(cache_dir))
    # Keep the wizard on its offline template so the probe makes no API call.
    env.pop("OPENAI_API_KEY", None)
    proc = subprocess.run(
        [sys.executable, "-c", PROBE, city],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return json.loads(proc.stdout)


def test_cache_root_follows_the_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path / "somewhere"))
    assert cache_root() == tmp_path / "somewhere"
    assert cache_subdir(ONBOARDED_CITIES) == tmp_path / "somewhere" / ONBOARDED_CITIES

    monkeypatch.delenv("GEOLENS_CACHE_DIR")
    assert cache_root().name == ".geolens"


def test_two_cache_dirs_do_not_share_onboarded_cities(tmp_path) -> None:
    # A shared home, so a process that ignored GEOLENS_CACHE_DIR would land in
    # the same ~/.geolens as the other one and see its city.
    home = tmp_path / "home"
    home.mkdir()

    first = _run_probe("Tengah Plantation Crescent", home, tmp_path / "instance-a")
    second = _run_probe("Pekanbaru", home, tmp_path / "instance-b")

    assert first["visible"] == [place_id("Tengah Plantation Crescent")]
    assert second["visible"] == [place_id("Pekanbaru")]
    # The embedding cache is keyed on this salt, so a shared salt would let one
    # instance serve the other's city descriptions out of cache.
    assert first["salt"] != second["salt"]
