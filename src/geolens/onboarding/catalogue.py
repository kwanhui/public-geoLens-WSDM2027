"""Adding an onboarded place to the live catalogue, and taking it back out.

The server hands one mutable place list to every engine, so appending to that
list is what makes a newly onboarded place selectable everywhere at once. The
response reports whether the place was already a candidate, since drafting a
profile for one is legitimate but is not a cold start.

Because the catalogue is shared, an onboarded place expires after a time to
live, the number held at once is capped with the oldest evicted first, and
removing a place also discards its drafted profile. A built-in place never
leaves the catalogue and never occupies an onboarded slot; a profile drafted or
edited for one is tracked separately as an overlay, expires on the same clock
and is discarded on its own. `reconcile` removes every stored profile the
registry does not hold, so a restart cannot leave an expired one live on disk.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field

from geolens.engines._cities import DEFAULT_CITIES
from geolens.onboarding.wizard import discard_profiles, forget_profile
from geolens.places import name_key, normalise_text

ADDED = "added"
ALREADY_PRESENT = "already_present"
REMOVED = "removed"
NOT_PRESENT = "not_present"
BUILT_IN_RESTORED = "built_in_restored"
EXPIRED = "expired"
EVICTED = "evicted"

NOTES = {
    ADDED: "new to the catalogue, and now a candidate for every engine",
    ALREADY_PRESENT: "already a catalogue city, so only its profile was drafted or updated",
    REMOVED: "removed from the catalogue; its onboarded profile was discarded",
    NOT_PRESENT: "not onboarded on this instance, so there was nothing to remove",
    BUILT_IN_RESTORED: (
        "a built-in catalogue city, so it stays in the catalogue; any onboarded "
        "profile overlay was discarded and its built-in profile is back"
    ),
    EXPIRED: "onboarded, then expired and removed from the catalogue",
    EVICTED: "removed to stay within the cap on onboarded places",
}

DEFAULT_TTL_MINUTES = 60.0
DEFAULT_MAX_ONBOARDED = 20

EDIT_TOKEN_BYTES = 24


def ttl_minutes() -> float:
    """How long an onboarded place stays in the shared catalogue. 0 disables expiry."""
    try:
        return max(0.0, float(os.getenv("GEOLENS_ONBOARD_TTL_MINUTES", DEFAULT_TTL_MINUTES)))
    except ValueError:
        return DEFAULT_TTL_MINUTES


def max_onboarded() -> int:
    """How many onboarded places the shared catalogue holds at once. 0 disables the cap."""
    try:
        return max(0, int(os.getenv("GEOLENS_MAX_ONBOARDED", DEFAULT_MAX_ONBOARDED)))
    except ValueError:
        return DEFAULT_MAX_ONBOARDED


@dataclass
class CatalogueChange:
    """What a register or reset call did to the live catalogue."""

    city: str
    status: str
    catalogue_size: int
    profile_removed: bool = False
    # Onboarding state at the moment of the call, so the interface can say how
    # long the place will last and how full the shared catalogue is.
    onboarded_count: int = 0
    onboarded_cap: int = 0
    ttl_minutes: float = 0.0
    expires_in_minutes: float | None = None
    evicted: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        return NOTES[self.status]

    def as_dict(self) -> dict:
        return {
            "catalogue_status": self.status,
            "catalogue_note": self.note,
            "catalogue_size": self.catalogue_size,
            "profile_removed": self.profile_removed,
            "onboarded_count": self.onboarded_count,
            "onboarded_cap": self.onboarded_cap,
            "onboarding_ttl_minutes": self.ttl_minutes,
            "expires_in_minutes": self.expires_in_minutes,
            "evicted": self.evicted,
        }


def is_default_city(name: str) -> bool:
    target = name_key(name)
    return any(name_key(c) == target for c in DEFAULT_CITIES)


def find_in_catalogue(name: str, catalogue: list[str]) -> str | None:
    """The catalogue entry that is the same place as `name`, or None.

    Matching is on the normalised, case-folded key, so a decomposed spelling
    of an accented name finds the composed entry rather than adding a second
    place beside it.
    """
    target = name_key(name)
    return next((c for c in catalogue if name_key(c) == target), None)


def missing_built_in(catalogue: list[str]) -> list[str]:
    """Which built-in places `catalogue` does not hold, in catalogue order."""
    present = {name_key(c) for c in catalogue}
    return [c for c in DEFAULT_CITIES if name_key(c) not in present]


class OnboardingRegistry:
    """The onboarded places held in one shared catalogue list, with their ages.

    The catalogue list is the same object every engine holds, so it is
    mutated in place rather than replaced. Onboarded places are counted
    against the cap; a profile overlay on a built-in place is tracked
    separately, expires on the same clock and occupies no slot.
    """

    def __init__(self, catalogue: list[str]) -> None:
        self.catalogue = catalogue
        self._added_at: dict[str, float] = {}
        self._overlay_at: dict[str, float] = {}
        self._names: dict[str, str] = {}  # place key -> name as onboarded
        self._tokens: dict[str, str] = {}  # place key -> edit token

    # ----- state ---------------------------------------------------------

    @property
    def count(self) -> int:
        return len(self._added_at)

    def names(self) -> list[str]:
        """Onboarded place names, oldest first."""
        ordered = sorted(self._added_at.items(), key=lambda kv: kv[1])
        return [self._names[key] for key, _ in ordered]

    def overlay_names(self) -> list[str]:
        """Built-in places carrying a profile overlay, oldest first."""
        ordered = sorted(self._overlay_at.items(), key=lambda kv: kv[1])
        return [self._names[key] for key, _ in ordered]

    def added_at(self, name: str) -> float | None:
        key = name_key(name)
        if key in self._added_at:
            return self._added_at[key]
        return self._overlay_at.get(key)

    def expires_in_minutes(self, name: str, now: float | None = None) -> float | None:
        """Minutes left before `name` expires, or None when it never will."""
        ttl = ttl_minutes()
        added = self.added_at(name)
        if ttl <= 0 or added is None:
            return None
        now = time.time() if now is None else now
        return max(0.0, ttl - (now - added) / 60.0)

    def status(self, now: float | None = None) -> dict:
        """What the interface prints under the onboarding form."""
        return {
            "onboarded_count": self.count,
            "onboarded_cap": max_onboarded(),
            "onboarding_ttl_minutes": ttl_minutes(),
            "onboarded": [
                {"name": n, "expires_in_minutes": self.expires_in_minutes(n, now)}
                for n in self.names()
            ],
            "catalogue_size": len(self.catalogue),
        }

    def missing_built_in(self) -> list[str]:
        """The built-in places the live catalogue does not hold."""
        return missing_built_in(self.catalogue)

    # ----- edit tokens ---------------------------------------------------

    def issue_token(self, name: str) -> str:
        """Mint and store the token that may later edit or remove `name`."""
        token = secrets.token_urlsafe(EDIT_TOKEN_BYTES)
        self._tokens[name_key(name)] = token
        return token

    def token_for(self, name: str) -> str | None:
        """The stored edit token for `name`, or None when none was issued."""
        return self._tokens.get(name_key(name))

    def token_matches(self, name: str, token: str | None) -> bool:
        """Whether `token` is the token issued for `name`."""
        stored = self.token_for(name)
        if stored is None or not token:
            return False
        return secrets.compare_digest(stored, token)

    # ----- mutation ------------------------------------------------------

    def expire(self, now: float | None = None) -> list[str]:
        """Drop every onboarded place and overlay past its time to live.

        Returns the names dropped.
        """
        ttl = ttl_minutes()
        if ttl <= 0:
            return []
        now = time.time() if now is None else now
        cutoff = now - ttl * 60.0
        stale = [key for key, added in self._added_at.items() if added <= cutoff]
        stale_overlays = [key for key, added in self._overlay_at.items() if added <= cutoff]
        dropped = [self._drop_onboarded(key) for key in stale]
        dropped += [self._drop_overlay(key) for key in stale_overlays]
        return dropped

    def _forget(self, key: str) -> str:
        name = self._names.pop(key, key)
        self._tokens.pop(key, None)
        return name

    def _drop_onboarded(self, key: str) -> str:
        """Remove an onboarded place from the catalogue and discard its profile."""
        self._added_at.pop(key, None)
        name = self._forget(key)
        present = find_in_catalogue(name, self.catalogue)
        if present is not None and not is_default_city(present):
            self.catalogue.remove(present)
        forget_profile(name)
        return name

    def _drop_overlay(self, key: str) -> str:
        """Discard a built-in place's profile overlay and leave the place alone."""
        self._overlay_at.pop(key, None)
        name = self._forget(key)
        forget_profile(name)
        return name

    def _evict_to_cap(self) -> list[str]:
        cap = max_onboarded()
        if cap <= 0:
            return []
        evicted: list[str] = []
        while self.count > cap:
            oldest = min(self._added_at.items(), key=lambda kv: kv[1])[0]
            evicted.append(self._drop_onboarded(oldest))
        return evicted

    def reconcile(self) -> list[str]:
        """Delete every stored profile this registry does not hold.

        Run at startup and on a timer: the registry lives in memory, so a
        restart would otherwise leave an expired place's profile on disk and
        an overlay live on a built-in place with nothing tracking it.
        """
        keep = set(self._added_at) | set(self._overlay_at)
        return discard_profiles(keep)

    def _change(self, city: str, status: str, **kwargs: object) -> CatalogueChange:
        return CatalogueChange(
            city=city,
            status=status,
            catalogue_size=len(self.catalogue),
            onboarded_count=self.count,
            onboarded_cap=max_onboarded(),
            ttl_minutes=ttl_minutes(),
            expires_in_minutes=self.expires_in_minutes(city),
            **kwargs,  # type: ignore[arg-type]
        )

    def register(self, name: str, now: float | None = None) -> CatalogueChange:
        """Add an onboarded place to the live catalogue if it is not already there.

        Mutates the catalogue in place, which is what makes the place visible
        to the engines holding a reference to it. A place that is already
        onboarded has its clock restarted, because the profile just changed.
        A built-in place is recorded as an overlay: it is already in the
        catalogue and takes no onboarded slot.
        """
        self.expire(now)
        trimmed = normalise_text(name)
        if not trimmed:
            return self._change(name, NOT_PRESENT)

        key = name_key(trimmed)
        stamp = time.time() if now is None else now
        self._names[key] = trimmed

        if is_default_city(trimmed):
            self._overlay_at[key] = stamp
            return self._change(trimmed, ALREADY_PRESENT)

        present = find_in_catalogue(trimmed, self.catalogue)
        self._added_at[key] = stamp
        if present is not None:
            evicted = self._evict_to_cap()
            return self._change(trimmed, ALREADY_PRESENT, evicted=evicted)

        self.catalogue.append(trimmed)
        evicted = self._evict_to_cap()
        status = ADDED if find_in_catalogue(trimmed, self.catalogue) else EVICTED
        return self._change(trimmed, status, evicted=evicted)

    def reset(self, name: str, now: float | None = None) -> CatalogueChange:
        """Undo an onboarding: drop the place from the catalogue and its profile.

        For a built-in city this discards the profile overlay and leaves the
        city where it is, which restores the pristine built-in behaviour.
        Safe to call for a place that was never onboarded, which is the common
        case when a scenario is loaded first.
        """
        self.expire(now)
        trimmed = normalise_text(name)
        key = name_key(trimmed)

        if is_default_city(trimmed):
            profile_removed = forget_profile(trimmed)
            self._overlay_at.pop(key, None)
            self._forget(key)
            return self._change(trimmed, BUILT_IN_RESTORED, profile_removed=profile_removed)

        present = find_in_catalogue(trimmed, self.catalogue)
        if present is not None:
            self.catalogue.remove(present)
        self._added_at.pop(key, None)
        self._overlay_at.pop(key, None)
        self._forget(key)
        profile_removed = forget_profile(trimmed)
        status = REMOVED if (present is not None or profile_removed) else NOT_PRESENT
        return self._change(trimmed, status, profile_removed=profile_removed)
