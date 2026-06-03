"""Tests for the WNUT-2016 -> GeoLens eval adapter.

These exercise the label-mapping and bucketing logic on tiny synthetic records,
so they need neither the real (multi-GB) dataset nor network access.
"""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path

# The adapter lives under eval/ (not an installed package), so load it by path.
_ADAPTER = Path(__file__).resolve().parents[1] / "eval" / "adapters" / "wnut2016_to_geolens.py"
_spec = importlib.util.spec_from_file_location("wnut2016_to_geolens", _ADAPTER)
wnut = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wnut)


# --- pure helpers -----------------------------------------------------------

def test_clean_text_strips_urls_html_and_pipes():
    out = wnut.clean_text('Hello &amp; bye | done http://t.co/abc  end')
    assert "http" not in out
    assert "&amp;" not in out and "&" in out
    assert "|" not in out  # pipe is the user_posts separator
    assert "  " not in out


def test_nearest_city_picks_the_closest_catalogue_entry():
    # Exactly the Bangkok centroid -> Bangkok, distance ~0.
    name, km = wnut.nearest_city(13.7563, 100.5018)
    assert name == "Bangkok"
    assert km < wnut.IN_CAT_KM


def test_nearest_city_far_point_exceeds_radius():
    # Reykjavik is far from every catalogue city.
    _, km = wnut.nearest_city(64.1466, -21.9426)
    assert km > wnut.IN_CAT_KM


def test_has_toponym_detects_catalogue_city_names():
    assert wnut.has_toponym("Stuck in traffic in Singapore again")
    assert not wnut.has_toponym("best kaya toast in town")


def test_slug_to_name():
    assert wnut.slug_to_name("los angeles-ca037-us") == "Los Angeles"
    assert wnut.slug_to_name("rio de janeiro-21-br") == "Rio De Janeiro"


# --- end-to-end bucketing on a synthetic zip --------------------------------

def _make_zip() -> io.BytesIO:
    """Build an in-memory 'Validation Set.zip' with a handful of records."""
    sg = (1.3521, 103.8198)
    tokyo = (35.6762, 139.6503)

    tweet_gold = [
        # explicit toponym + in-catalogue -> intl
        {"tweet_id": "1", "tweet_city": "singapore-00-sg", "tweet_latitude": sg[0], "tweet_longitude": sg[1]},
        # no toponym + in-catalogue -> hard-sem
        {"tweet_id": "2", "tweet_city": "singapore-00-sg", "tweet_latitude": sg[0], "tweet_longitude": sg[1]},
        # far from every catalogue city -> ooc
        {"tweet_id": "3", "tweet_city": "reykjavik-00-is", "tweet_latitude": 64.1466, "tweet_longitude": -21.9426},
    ]
    tweet_text = [
        {"tweet_id": "1", "text": "Lovely evening in Singapore tonight"},
        {"tweet_id": "2", "text": "best kaya toast in town"},
        {"tweet_id": "3", "text": "northern lights are unreal"},
    ]
    # One user whose home is Singapore but who posted once from Tokyo -> disagree.
    user_gold = [
        {"tweet_id": "10", "user_id": "u1", "user_city": "singapore-00-sg",
         "user_city_latitude": sg[0], "user_city_longitude": sg[1],
         "tweet_city": "singapore-00-sg", "tweet_latitude": sg[0], "tweet_longitude": sg[1]},
        {"tweet_id": "11", "user_id": "u1", "user_city": "singapore-00-sg",
         "user_city_latitude": sg[0], "user_city_longitude": sg[1],
         "tweet_city": "tokyo-13-jp", "tweet_latitude": tokyo[0], "tweet_longitude": tokyo[1]},
        # A home-consistent user -> userhome.
        {"tweet_id": "20", "user_id": "u2", "user_city": "singapore-00-sg",
         "user_city_latitude": sg[0], "user_city_longitude": sg[1],
         "tweet_city": "singapore-00-sg", "tweet_latitude": sg[0], "tweet_longitude": sg[1]},
        {"tweet_id": "21", "user_id": "u2", "user_city": "singapore-00-sg",
         "user_city_latitude": sg[0], "user_city_longitude": sg[1],
         "tweet_city": "singapore-00-sg", "tweet_latitude": sg[0], "tweet_longitude": sg[1]},
    ]
    user_text = [
        {"tweet_id": "10", "text": "home in singapore"},
        {"tweet_id": "11", "text": "shibuya crossing is wild"},
        {"tweet_id": "20", "text": "lunch at the hawker centre"},
        {"tweet_id": "21", "text": "mrt is packed today"},
    ]

    def dump(records):
        return "\n".join(json.dumps(r) for r in records).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(wnut.TWEET_GOLD, dump(tweet_gold))
        zf.writestr(wnut.TWEET_TEXT, dump(tweet_text))
        zf.writestr(wnut.USER_GOLD, dump(user_gold))
        zf.writestr(wnut.USER_TEXT, dump(user_text))
    buf.seek(0)
    return buf


def test_tweet_rows_bucketing():
    with zipfile.ZipFile(_make_zip()) as zf:
        buckets = wnut.build_tweet_rows(zf)
    assert len(buckets["intl"]) == 1
    assert buckets["intl"][0]["ground_truth_city"] == "Singapore"
    assert len(buckets["hard-sem"]) == 1
    assert buckets["hard-sem"][0]["ground_truth_city"] == "Singapore"
    # ooc keeps the real (out-of-catalogue) city as ground truth.
    assert len(buckets["ooc"]) == 1
    assert buckets["ooc"][0]["ground_truth_city"] not in wnut.CITY_COORDS


def test_user_rows_disagreement_construction():
    with zipfile.ZipFile(_make_zip()) as zf:
        buckets = wnut.build_user_rows(zf)
    # u1 (Singapore home, one Tokyo post) -> disagree with should_disagree=1.
    assert len(buckets["disagree"]) == 1
    row = buckets["disagree"][0]
    assert row["should_disagree"] == 1
    assert row["ground_truth_city"] == "Tokyo"  # ground truth follows the post's own city
    assert "|" in row["user_posts"] or row["user_posts"]  # home timeline preserved
    # u2 (consistent) -> userhome with should_disagree=0.
    assert len(buckets["userhome"]) == 1
    assert buckets["userhome"][0]["should_disagree"] == 0
    assert buckets["userhome"][0]["ground_truth_city"] == "Singapore"
