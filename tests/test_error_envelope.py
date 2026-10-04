"""One error shape, on every endpoint and every status code."""

from __future__ import annotations

import io
import json

import pytest
from starlette.testclient import TestClient

from geolens.ui.errors import MAX_ECHO_CHARS
from geolens.ui.server import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    with TestClient(create_app()) as c:
        yield c


def _assert_envelope(resp, code: str | None = None) -> dict:
    body = resp.json()
    assert set(body) == {"error", "detail"}
    error = body["error"]
    assert set(error) == {"code", "message", "field", "details"}
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert error["field"] is None or isinstance(error["field"], str)
    assert isinstance(error["details"], list)
    # The plain string the page reads is the same message.
    assert body["detail"] == error["message"]
    if code is not None:
        assert error["code"] == code
    return error


# ----- every status code the server emits -------------------------------------

def test_an_application_422_uses_the_envelope(client) -> None:
    resp = client.post("/geolocate", json={"user_handle": "@someone"})
    assert resp.status_code == 422
    _assert_envelope(resp, "no_input_text")


def test_a_parser_422_uses_the_same_envelope(client) -> None:
    resp = client.post("/geolocate", json={"post": "a post", "k": 0})
    assert resp.status_code == 422
    error = _assert_envelope(resp, "validation_error")
    assert error["field"] == "k"
    assert error["details"][0]["field"] == "k"


def test_a_404_uses_the_envelope(client) -> None:
    resp = client.put("/onboard", json={"name": "Nowhere At All"})
    assert resp.status_code == 404
    _assert_envelope(resp, "place_not_in_catalogue")


def test_a_405_uses_the_envelope(client) -> None:
    resp = client.request("PATCH", "/geolocate", json={"post": "a post"})
    assert resp.status_code == 405
    _assert_envelope(resp, "method_not_allowed")


def test_a_409_uses_the_envelope(client) -> None:
    resp = client.post("/onboard", json={"city": "SINGAPORE", "region": "Singapore"})
    assert resp.status_code == 409
    error = _assert_envelope(resp, "place_already_in_catalogue")
    assert error["details"][0]["existing_name"] == "Singapore"


def test_a_413_uses_the_envelope(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.setenv("MAX_BATCH_BYTES", "40")
    with TestClient(create_app()) as client:
        big = b"id,post\n" + b"\n".join(f"{i},a post".encode() for i in range(20))
        resp = client.post(
            "/batch_predict_csv", files={"file": ("rows.csv", io.BytesIO(big), "text/csv")}
        )
    assert resp.status_code == 413
    _assert_envelope(resp, "upload_too_large")


def test_a_429_uses_the_envelope_and_keeps_its_headers(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GEOLENS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOLENS_STUB_MODE", "1")
    monkeypatch.setenv("MAX_QUERIES_PER_HOUR", "1")
    with TestClient(create_app()) as client:
        client.post("/geolocate", json={"post": "a post"})
        resp = client.post("/geolocate", json={"post": "a post"})
    assert resp.status_code == 429
    _assert_envelope(resp, "rate_limited")
    assert resp.headers["Retry-After"]
    assert resp.headers["X-RateLimit-Remaining-Queries"] == "0"


# ----- non-finite numbers -----------------------------------------------------

@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_radius_is_a_422_not_a_500(client, value) -> None:
    resp = client.post(
        "/geolocate",
        content=json.dumps({"post": "a post", "flag_radius_km": float(value)}).encode(),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    error = _assert_envelope(resp)
    assert error["field"] == "flag_radius_km"


@pytest.mark.parametrize("field", ["lat", "lon"])
def test_a_non_finite_coordinate_is_a_422_not_a_500(client, field) -> None:
    client.post("/onboard", json={"city": "Sintang", "region": "Indonesia"})
    payload = {"name": "Sintang", "lat": 0.0, "lon": 0.0}
    payload[field] = float("nan")
    resp = client.put(
        "/onboard",
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    _assert_envelope(resp)


def test_a_non_finite_radius_on_a_form_is_a_422(client) -> None:
    resp = client.post(
        "/batch_predict_csv",
        files={"file": ("rows.csv", io.BytesIO(b"id,post\n1,a post\n"), "text/csv")},
        data={"flag_radius_km": "NaN"},
    )
    assert resp.status_code == 422
    _assert_envelope(resp)


# ----- how much of a rejected value comes back --------------------------------

def test_a_refusal_is_not_the_biggest_response(client) -> None:
    resp = client.post("/geolocate", json={"post": "x" * 100_000})
    assert resp.status_code == 422
    # The whole body, not just the echoed value: a refusal never carries
    # the rejected post back.
    assert len(resp.content) < 4_000
    echoed = resp.json()["error"]["details"][0].get("input", "")
    assert len(str(echoed)) <= MAX_ECHO_CHARS + 40


# ----- an unknown field is not silently dropped -------------------------------

def test_a_misspelt_parameter_is_refused(client) -> None:
    resp = client.post("/geolocate", json={"post": "a post", "flag_radius": 500})
    assert resp.status_code == 422
    error = _assert_envelope(resp, "validation_error")
    assert "flag_radius" in error["message"]


def test_a_misspelt_batch_parameter_is_refused(client) -> None:
    resp = client.post("/batch_predict", json={
        "inputs": [{"id": "1", "post": "a post"}], "ensemble_methd": "rrf",
    })
    assert resp.status_code == 422


def test_a_misspelt_row_field_is_refused(client) -> None:
    resp = client.post("/batch_predict", json={
        "inputs": [{"id": "1", "post": "a post", "grond_truth_city": "Bedok"}],
    })
    assert resp.status_code == 422


# ----- the document says what the server emits --------------------------------

def test_the_openapi_document_declares_every_status_code(client) -> None:
    spec = client.get("/openapi.json").json()

    geolocate = spec["paths"]["/geolocate"]["post"]["responses"]
    assert {"200", "422", "429", "500"} <= set(geolocate)

    eval_csv = spec["paths"]["/eval_csv"]["post"]["responses"]
    assert {"200", "400", "413", "422", "429", "500"} <= set(eval_csv)

    onboard = spec["paths"]["/onboard"]["put"]["responses"]
    assert {"404", "409"} <= set(onboard)


def test_the_root_is_declared_as_html(client) -> None:
    spec = client.get("/openapi.json").json()
    content = spec["paths"]["/"]["get"]["responses"]["200"]["content"]
    assert "text/html" in content
    assert "application/json" not in content


def test_every_endpoint_carries_an_example(client) -> None:
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in ("GeolocateRequest", "BatchRequest", "OnboardRequest", "SaveProfileRequest"):
        assert schemas[name].get("examples"), name
