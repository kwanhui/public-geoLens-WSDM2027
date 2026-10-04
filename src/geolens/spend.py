"""A process-wide ceiling on estimated spend.

The figures it compares are token counts multiplied by the list prices in
`geolens.pricing`, never a bill, so this is a ceiling on the estimate. When the
estimate for the last hour or the last day reaches the ceiling, the engines
that call a paid model are not called and the local engines answer alone.

Both ceilings are deployment settings in US dollars and both are off by
default. The Dockerfile that builds the hosted image sets them.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass

HOUR_SECONDS = 3600.0
DAY_SECONDS = 86400.0

PER_HOUR_ENV = "GEOLENS_MAX_USD_PER_HOUR"
PER_DAY_ENV = "GEOLENS_MAX_USD_PER_DAY"


def _float_env(name: str) -> float:
    try:
        return max(0.0, float(os.getenv(name, "0")))
    except ValueError:
        return 0.0


def max_usd_per_hour() -> float:
    """Estimated spend allowed in one rolling hour. 0, the default, is no limit."""
    return _float_env(PER_HOUR_ENV)


def max_usd_per_day() -> float:
    """Estimated spend allowed in one rolling day. 0, the default, is no limit."""
    return _float_env(PER_DAY_ENV)


@dataclass(frozen=True)
class SpendState:
    """What the ceiling is, what has been spent against it, and whether it bites."""

    estimated_usd_last_hour: float
    estimated_usd_last_day: float
    max_usd_per_hour: float
    max_usd_per_day: float
    ceiling_reached: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "estimated_usd_last_hour": round(self.estimated_usd_last_hour, 6),
            "estimated_usd_last_day": round(self.estimated_usd_last_day, 6),
            "max_usd_per_hour": self.max_usd_per_hour,
            "max_usd_per_day": self.max_usd_per_day,
            "ceiling_reached": self.ceiling_reached,
            "reason": self.reason,
        }


class SpendLedger:
    """Estimated spend over the last hour and the last day, for one process."""

    def __init__(self) -> None:
        self._entries: deque[tuple[float, float]] = deque()
        self._lock = threading.Lock()

    def record(self, usd: float, now: float | None = None) -> None:
        """Add one run's estimated cost to the ledger."""
        if usd <= 0:
            return
        stamp = time.time() if now is None else now
        with self._lock:
            self._entries.append((stamp, usd))
            self._trim(stamp)

    def _trim(self, now: float) -> None:
        cutoff = now - DAY_SECONDS
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.popleft()

    def totals(self, now: float | None = None) -> tuple[float, float]:
        """(last hour, last day) estimated spend in US dollars."""
        stamp = time.time() if now is None else now
        with self._lock:
            self._trim(stamp)
            hour = sum(u for t, u in self._entries if t >= stamp - HOUR_SECONDS)
            day = sum(u for _, u in self._entries)
        return hour, day

    def state(self, now: float | None = None) -> SpendState:
        """The ceiling, the totals against it, and the reason when it bites."""
        hour, day = self.totals(now)
        hour_cap = max_usd_per_hour()
        day_cap = max_usd_per_day()
        reason = ""
        if hour_cap > 0 and hour >= hour_cap:
            reason = (
                f"the estimated spend for the last hour, USD {hour:.2f}, has "
                f"reached this instance's hourly ceiling of USD {hour_cap:.2f}"
            )
        elif day_cap > 0 and day >= day_cap:
            reason = (
                f"the estimated spend for the last day, USD {day:.2f}, has "
                f"reached this instance's daily ceiling of USD {day_cap:.2f}"
            )
        return SpendState(
            estimated_usd_last_hour=hour,
            estimated_usd_last_day=day,
            max_usd_per_hour=hour_cap,
            max_usd_per_day=day_cap,
            ceiling_reached=bool(reason),
            reason=reason,
        )
