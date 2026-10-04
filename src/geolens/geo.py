"""Great-circle distance utilities for spatial evaluation.

Geolocation work reports distance error alongside label accuracy, so a
near-miss (Petaling Jaya for Kuala Lumpur, ~12 km) is not penalised like a
far-miss (London for Singapore, ~10,000 km). The reference metrics are
median and mean great-circle error and Acc@161 km (accuracy within 100 miles),
following Eisenstein et al. (2010) and Han et al. (2014).
"""

from __future__ import annotations

import math

# 100 miles in km, the conventional Acc@Xkm threshold in the geolocation
# literature (Han et al. 2014; Wing & Baldridge 2014).
ACC_KM_THRESHOLD = 161.0

EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))
