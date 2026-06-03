"""Cold-start city onboarding wizard.

Given a brand-new city or estate name with no labeled posts, ask an LLM to
generate the Modular Retrieval (MoR) fields RetrieveZero expects: aliases,
landmarks, foods, slang. The operator can then edit the profile in the UI
and add the city to the active catalogue without retraining anything.

Behaviour:
- Profiles are cached on disk under ~/.geolens/onboarded_cities/<slug>.json.
  Re-onboarding the same city returns the cached profile for free.
- LLM provider is OpenAI when `OPENAI_API_KEY` is set, otherwise the wizard
  falls back to a deterministic template-based stub so the demo still runs.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("GEOLENS_CACHE_DIR", str(Path.home() / ".geolens"))) / "onboarded_cities"


@dataclass
class CityProfile:
    name: str
    aliases: list[str] = field(default_factory=list)
    landmarks: list[str] = field(default_factory=list)
    foods: list[str] = field(default_factory=list)
    slang: list[str] = field(default_factory=list)
    notes: str = ""
    lat: float | None = None
    lon: float | None = None
    source: str = "stub"  # "openai" | "stub" | "edited"

    def coords(self) -> tuple[float, float] | None:
        """(lat, lon) if both are set, else None."""
        if self.lat is None or self.lon is None:
            return None
        return (self.lat, self.lon)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{_slug(name)}.json"


def _load_cached(name: str) -> CityProfile | None:
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        return CityProfile(**json.loads(path.read_text()))
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Failed to load cached profile for %s: %s", name, e)
        return None


def _save_cached(profile: CityProfile) -> None:
    _cache_path(profile.name).write_text(json.dumps(asdict(profile), indent=2))


def _stub_profile(name: str) -> CityProfile:
    return CityProfile(
        name=name,
        aliases=[name],
        landmarks=[f"{name} Central Station", f"{name} Park"],
        foods=["local breakfast", "street food"],
        slang=[],
        notes=f"Stub profile for {name}: no LLM available; edit in UI before relying on it.",
        source="stub",
    )


def _openai_profile(name: str, model: str = "gpt-4o-mini") -> CityProfile:
    """Call OpenAI to fill the MoR fields."""
    try:
        from openai import OpenAI
    except ImportError as e:
        logger.warning("openai package not installed (%s); using stub profile.", e)
        return _stub_profile(name)

    client = OpenAI()
    prompt = (
        f'Return a JSON object describing the city/place "{name}". Fields: '
        '"aliases" (list of common alternative names, 0-5 items), '
        '"landmarks" (list of well-known places, 3-7 items), '
        '"foods" (list of dishes / food items associated with the place, 3-7 items), '
        '"slang" (list of local slang or distinctive phrases, 0-5 items), '
        '"lat" (approximate centroid latitude in decimal degrees, number), '
        '"lon" (approximate centroid longitude in decimal degrees, number), '
        '"notes" (one-sentence summary). Return ONLY the JSON object, no prose.'
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=500,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:  # noqa: BLE001
        logger.warning("OpenAI MoR call failed for %s: %s. Using stub.", name, e)
        return _stub_profile(name)

    return CityProfile(
        name=name,
        aliases=data.get("aliases", []),
        landmarks=data.get("landmarks", []),
        foods=data.get("foods", []),
        slang=data.get("slang", []),
        notes=data.get("notes", ""),
        lat=_as_float(data.get("lat")),
        lon=_as_float(data.get("lon")),
        source="openai",
    )


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def onboard_city(name: str, *, force_refresh: bool = False) -> CityProfile:
    """Return a CityProfile for `name`. Cached on disk; LLM-generated when first seen."""

    if not force_refresh:
        cached = _load_cached(name)
        if cached is not None:
            return cached

    if os.getenv("OPENAI_API_KEY"):
        profile = _openai_profile(name)
    else:
        profile = _stub_profile(name)

    _save_cached(profile)
    return profile


def save_profile(profile: CityProfile) -> CityProfile:
    """Persist an edited CityProfile to disk and return it (with source bumped)."""

    edited = CityProfile(
        name=profile.name,
        aliases=list(profile.aliases),
        landmarks=list(profile.landmarks),
        foods=list(profile.foods),
        slang=list(profile.slang),
        notes=profile.notes,
        lat=profile.lat,
        lon=profile.lon,
        source="edited",
    )
    _save_cached(edited)
    return edited


def profile_warnings(profile: CityProfile) -> list[str]:
    """Human-readable warnings about an onboarded profile, so the operator knows
    what to fix before adding the city to the catalogue.

    The LLM wizard can hallucinate or omit fields for exactly the obscure cities
    it is meant to cover, so the UI shows these next to the editable cards.
    """
    w: list[str] = []
    if profile.source == "stub":
        w.append("generated by the offline stub, not an LLM: the fields are placeholders, edit before use")
    if not profile.aliases:
        w.append("no aliases: the gazetteer cannot match this city by name")
    if not profile.landmarks:
        w.append("no landmarks: the retrieval engines have little local signal to match")
    coords = profile.coords()
    if coords is None:
        w.append("no centroid: the city will not pin on the map or enter the distance metrics")
    elif not (-90.0 <= coords[0] <= 90.0 and -180.0 <= coords[1] <= 180.0):
        w.append(f"centroid out of range ({coords[0]}, {coords[1]}): correct the coordinate")
    return w


def onboarded_coords(name: str) -> tuple[float, float] | None:
    """(lat, lon) for an onboarded city, or None if not onboarded / no coordinate."""
    profile = _load_cached(name)
    return profile.coords() if profile is not None else None
