"""What a catalogue name may be, and the identifier every place carries.

A name is pasted verbatim into the instruction part of the prompt both LLM
classifiers send, and it reaches every other visitor of a hosted instance.
"""

from __future__ import annotations

import unicodedata

import pytest
from starlette.testclient import TestClient

from geolens.places import name_key, normalise_text, place_id
from geolens.ui.server import create_app

NFC_CAFEVILLE = unicodedata.normalize("NFC", "Caféville")
NFD_CAFEVILLE = unicodedata.normalize("NFD", "Caféville")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


# ----- names that are not place names ----------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "<script>alert(1)</script>",
        "../../etc/passwd",
        "Sin tang\x1b[31m",
        "Foo/Bar",
        "Bedok (old)",
        "Tokyo; DROP",
        "Washington, D.C.",
    ],
)
def test_a_name_that_is_not_a_place_name_is_refused(client, name) -> None:
    resp = client.post("/onboard", json={"city": name, "region": "Singapore"})
    assert resp.status_code == 422, resp.json()
    assert resp.json()["error"]["field"] == "city"


@pytest.mark.parametrize(
    ("sent", "stored"),
    [("Bedok\x00Extra", "BedokExtra"), ("Sintang\r\nBarat", "Sintang Barat")],
)
def test_a_control_character_in_a_name_is_stripped_not_stored(client, sent, stored) -> None:
    """A control never reaches the prompt, the page or a terminal.

    Tab, newline and carriage return become a space so two words do not run
    together; the rest, NUL and the ANSI escape included, are dropped.
    """
    body = client.post("/onboard", json={"city": sent, "region": "Singapore"}).json()
    assert body["name"] == stored
    names = [p["name"] for p in client.get("/catalogue").json()["places"]]
    assert stored in names
    assert not any(any(ord(ch) < 32 for ch in n) for n in names)


@pytest.mark.parametrize(
    "name",
    ["Sintang", "Ho Chi Minh City", "St. John's", "Washington D.C.", "Jurong-East", NFC_CAFEVILLE],
)
def test_an_ordinary_place_name_is_accepted(client, name) -> None:
    resp = client.post("/onboard", json={"city": name, "region": "Singapore"})
    assert resp.status_code == 200, resp.json()
    assert resp.json()["name"] == name


def test_markup_in_a_profile_field_is_refused(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    for field, value in (
        ("aliases", ["<b>Kota</b>"]),
        ("landmarks", ["<img src=x onerror=1>"]),
        ("foods", ["<script>"]),
        ("slang", ["<i>lah</i>"]),
    ):
        resp = client.put("/onboard", json={"name": "Sintang", field: value})
        assert resp.status_code == 422, field
    resp = client.put("/onboard", json={"name": "Sintang", "notes": "a <script> note"})
    assert resp.status_code == 422


def test_a_control_character_in_a_profile_field_is_stripped(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    body = client.put(
        "/onboard", json={"name": "Sintang", "aliases": ["Kota\x00 Sintang\x1b"]}
    ).json()
    assert body["aliases"] == ["Kota Sintang"]


# ----- one place, one name ----------------------------------------------------

def test_the_two_unicode_spellings_are_one_place(client) -> None:
    first = client.post("/onboard", json={"city": NFC_CAFEVILLE, "region": "Singapore"})
    assert first.status_code == 200
    assert first.json()["catalogue_status"] == "added"

    second = client.post("/onboard", json={"city": NFD_CAFEVILLE, "region": "Singapore"})
    assert second.status_code == 200
    # The decomposed spelling normalises onto the composed one, so it is the
    # same place rather than a second entry.
    assert second.json()["catalogue_status"] == "already_present"
    assert second.json()["name"] == NFC_CAFEVILLE
    assert second.json()["place_id"] == first.json()["place_id"]

    names = [p["name"] for p in client.get("/catalogue").json()["places"]]
    assert names.count(NFC_CAFEVILLE) == 1
    assert NFD_CAFEVILLE not in [n for n in names if n != NFC_CAFEVILLE]


def test_a_different_spelling_of_a_catalogue_place_is_a_conflict(client) -> None:
    resp = client.post("/onboard", json={"city": "singapore", "region": "Singapore"})
    assert resp.status_code == 409
    assert "Singapore" in resp.json()["detail"]


def test_a_put_edits_the_place_under_the_spelling_the_catalogue_holds(client) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    body = client.put("/onboard", json={"name": "SINTANG", "lat": -0.0833, "lon": 111.5}).json()
    assert body["name"] == "Sintang"
    names = [p["name"] for p in client.get("/catalogue").json()["places"]]
    assert names.count("Sintang") == 1
    assert "SINTANG" not in names


# ----- the identifier ---------------------------------------------------------

def test_every_catalogue_place_carries_a_stable_identifier(client) -> None:
    places = client.get("/catalogue").json()["places"]
    ids = [p["place_id"] for p in places]
    assert all(pid.startswith("pl_") for pid in ids)
    assert len(set(ids)) == len(ids)
    # Derived from the name, so it is the same on a second call and on
    # another instance.
    for place in places:
        assert place["place_id"] == place_id(place["name"])


def test_the_identifier_ignores_case_and_unicode_composition() -> None:
    assert place_id(NFD_CAFEVILLE) == place_id(NFC_CAFEVILLE)
    assert place_id("singapore") == place_id("Singapore")
    assert place_id("Singapore") != place_id("Sintang")


def test_a_prediction_carries_the_identifier_of_the_place_it_names(client) -> None:
    body = client.post("/geolocate", json={"post": "Fire at Marina Bay Sands"}).json()
    for pred in body["per_engine"].values():
        if pred["city"]:
            assert pred["place_id"] == place_id(pred["city"])
        else:
            assert pred["place_id"] is None
    for fused in body["ensembles"].values():
        assert fused["consensus_place_id"] == place_id(fused["consensus_city"])


def test_the_places_list_carries_the_identifier_and_the_point(client) -> None:
    body = client.post("/geolocate", json={"post": "Fire at Marina Bay Sands"}).json()
    by_name = {p["name"]: p for p in body["places"]}
    assert by_name
    for name, ref in by_name.items():
        assert ref["place_id"] == place_id(name)
        if name in body["place_coordinates"]:
            assert [ref["lat"], ref["lon"]] == body["place_coordinates"][name]


# ----- the normalisers themselves ---------------------------------------------

def test_normalise_text_collapses_whitespace_and_drops_controls() -> None:
    assert normalise_text("  Kota\t Sintang \x00 ") == "Kota Sintang"
    assert normalise_text("") == ""


def test_name_key_is_what_two_spellings_of_one_place_share() -> None:
    assert name_key(NFD_CAFEVILLE) == name_key(NFC_CAFEVILLE)
    assert name_key("  SINGAPORE ") == name_key("Singapore")
