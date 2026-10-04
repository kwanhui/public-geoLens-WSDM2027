"""Cold-start city onboarding wizard.

Given a place name with no labelled posts, ask an LLM for the Modular
Retrieval (MoR) fields RetrieveZero expects: aliases, landmarks, foods, slang.
The operator edits the profile in the UI and adds the place to the active
catalogue without retraining anything.

Profiles are cached under <cache root>/onboarded_cities/<place_id>.json, where
the cache root is `$GEOLENS_CACHE_DIR` or `~/.geolens` (see `geolens.paths`).
The file is keyed on the place identifier and carries the display name inside,
so two places whose names differ only in script or punctuation keep separate
profiles. Re-onboarding returns the cached profile for free, unless the caller
gives a region hint the cached profile was not drafted under. A hint goes into
the prompt, is stored on the profile, and is checked against the drafted
coordinate by `profile_warnings`; `geolens.onboarding.regions` has a worked
example of why. The provider is OpenAI when `OPENAI_API_KEY` is set, otherwise
a deterministic template-based stub.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from geolens.onboarding.regions import region_mismatch
from geolens.paths import ONBOARDED_CITIES, cache_subdir
from geolens.places import name_key, place_id

logger = logging.getLogger(__name__)


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
    # Country or region the operator named when onboarding, free text and
    # possibly empty. Kept on the profile so a later check or a reader can
    # see what the draft was asked for.
    region: str = ""
    source: str = "stub"  # "openai" | "stub" | "edited"

    def coords(self) -> tuple[float, float] | None:
        """(lat, lon) if both are set, else None."""
        if self.lat is None or self.lon is None:
            return None
        return (self.lat, self.lon)


def _cache_path(name: str) -> Path:
    """The one file a place's profile lives in, named after its identifier.

    The identifier is derived from the normalised name, so two places whose
    names differ in script or punctuation cannot share a file and return each
    other's profile.
    """
    return cache_subdir(ONBOARDED_CITIES, create=True) / f"{place_id(name)}.json"


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


def _stub_profile(name: str, region: str = "") -> CityProfile:
    """Offline placeholder. Names the region hint but invents nothing from it.

    The stub cannot know where the place is, so it leaves the centroid empty
    whether or not a hint was given, and the missing-centroid warning fires as
    it always has.
    """
    where = f"{name}, {region}" if region.strip() else name
    return CityProfile(
        name=name,
        aliases=[name],
        landmarks=[f"{name} Central Station", f"{name} Park"],
        foods=["local breakfast", "street food"],
        slang=[],
        notes=(
            f"Placeholder profile for {where}. No drafting model was available, "
            "so every field below is a placeholder: edit them before relying on "
            "the profile."
        ),
        region=region.strip(),
        source="stub",
    )


def _openai_profile(name: str, region: str = "", model: str = "gpt-4o-mini") -> CityProfile:
    """Call OpenAI to fill the MoR fields, within the region hint if one is given."""
    try:
        from openai import OpenAI
    except ImportError as e:
        logger.warning("openai package not installed (%s); using stub profile.", e)
        return _stub_profile(name, region)

    client = OpenAI()
    hint = region.strip()
    where = f'the city/place "{name}" in {hint}' if hint else f'the city/place "{name}"'
    constraint = (
        f" The place is in {hint}, so do not describe a similarly named place "
        "elsewhere. Fill in what you know and leave a list empty rather than "
        "inventing entries. If you know only the wider area the place belongs "
        "to (its town, district or regency), give that area's coordinate and "
        "say in \"notes\" that the coordinate is approximate."
        if hint
        else ""
    )
    prompt = (
        f"Return a JSON object describing {where}.{constraint} Fields: "
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
        return _stub_profile(name, region)

    return CityProfile(
        name=name,
        aliases=data.get("aliases", []),
        landmarks=data.get("landmarks", []),
        foods=data.get("foods", []),
        slang=data.get("slang", []),
        notes=data.get("notes", ""),
        lat=_as_float(data.get("lat")),
        lon=_as_float(data.get("lon")),
        region=hint,
        source="openai",
    )


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def onboard_city(name: str, *, region: str = "", force_refresh: bool = False) -> CityProfile:
    """Return a CityProfile for `name`. Cached on disk; LLM-generated when first seen.

    A cached profile is reused only when it was drafted under the same region
    hint. Giving a hint the cached draft did not have is a request for a
    different answer, so it redrafts.
    """

    hint = region.strip()
    if not force_refresh:
        cached = _load_cached(name)
        if cached is not None and cached.region.strip() == hint:
            return cached

    profile = (
        _openai_profile(name, hint) if os.getenv("OPENAI_API_KEY") else _stub_profile(name, hint)
    )
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
        region=profile.region.strip(),
        source="edited",
    )
    _save_cached(edited)
    return edited


def forget_profile(name: str) -> bool:
    """Delete a cached onboarded profile. True if a file was there to delete.

    Only ever touches the one file under the onboarded-cities cache that
    `_cache_path` names, so nothing outside the cache root can be reached
    through the city name.
    """
    path = _cache_path(name)
    if not path.exists():
        return False
    path.unlink()
    return True


def stored_profile_keys() -> dict[Path, str | None]:
    """Every stored profile file, mapped to the place key it holds.

    The value is None for a file that cannot be read as a profile or whose
    name does not match the place inside it, which is what a file written
    under an older key looks like.
    """
    directory = cache_subdir(ONBOARDED_CITIES)
    out: dict[Path, str | None] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            name = str(data["name"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            out[path] = None
            continue
        out[path] = name_key(name) if path.name == f"{place_id(name)}.json" else None
    return out


def discard_profiles(keep: set[str]) -> list[str]:
    """Delete every stored profile whose place key is not in `keep`.

    Returns the paths removed. This is what makes an expiry a deletion: the
    registry holds the places that are still live, and anything else on disk
    is left over from a restart or an older key.
    """
    removed: list[str] = []
    for path, key in stored_profile_keys().items():
        if key is not None and key in keep:
            continue
        try:
            path.unlink()
        except OSError as e:  # noqa: PERF203
            logger.warning("Could not remove stale profile %s: %s", path, e)
            continue
        removed.append(str(path))
    return removed


def profile_warnings(profile: CityProfile) -> list[str]:
    """Human-readable warnings about an onboarded profile, so the operator knows
    what to fix before adding the city to the catalogue.

    The LLM wizard can hallucinate or omit fields for exactly the obscure cities
    it is meant to cover, so the UI shows these next to the editable cards.
    """
    w: list[str] = []
    if profile.source == "stub":
        w.append(
            "drafted by the offline placeholder, not a model: every field below is a "
            "placeholder, edit them before use"
        )
    if not profile.region.strip():
        w.append(
            "no country or region hint: a bare place name is often ambiguous and the "
            "drafting model resolves it without saying so, as a draft for Cambridge "
            "did by merging the England and Massachusetts cities"
        )
    if not profile.aliases:
        w.append(
            "no aliases: the gazetteer will match only the full name, so a post that "
            "writes the place informally will not match"
        )
    if not profile.landmarks:
        w.append(
            "no landmarks: RetrieveZero builds its passage from the profile and the "
            "gazetteer matches landmarks, so neither has anything local to match"
        )
    coords = profile.coords()
    if coords is None:
        w.append(
            "no centroid: the place will not pin on the map or enter the distance metrics"
        )
    elif not (-90.0 <= coords[0] <= 90.0 and -180.0 <= coords[1] <= 180.0):
        w.append(f"centroid out of range ({coords[0]}, {coords[1]}): correct the coordinate")
    else:
        mismatch = region_mismatch(profile.region, coords[0], coords[1])
        if mismatch is not None:
            w.append(mismatch)
    return w


def cached_profile(name: str) -> CityProfile | None:
    """The stored profile for a place, or None when it has never been drafted."""
    return _load_cached(name)


def onboarded_coords(name: str) -> tuple[float, float] | None:
    """(lat, lon) for an onboarded city, or None if not onboarded / no coordinate."""
    profile = _load_cached(name)
    return profile.coords() if profile is not None else None
