"""One error shape for every endpoint.

Every error, raised by the application or by the request parser, leaves through
`install_error_handlers` as

    {"error": {"code": ..., "message": ..., "field": ..., "details": [...]},
     "detail": "<the message again>"}

so a client can write one branch that reads all of them. `detail` is a plain
string, which is what the page reads; `error.code` is the stable
machine-readable name and `error.field` names the offending request field where
there is one. Echoed values are serialised defensively, because `nan` is not
valid JSON, and truncated to a short prefix.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# How much of a rejected value is echoed back. Long enough to recognise what
# was sent, short enough that a refusal is never the biggest response.
MAX_ECHO_CHARS = 120

CODES: dict[int, str] = {
    400: "bad_request",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "unprocessable_entity",
    429: "rate_limited",
    500: "internal_error",
}


class ApiError(HTTPException):
    """An HTTPException that also carries the machine-readable code and field."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        field: str | None = None,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code or CODES.get(status_code, "error")
        self.field = field
        self.details = details or []


def safe_echo(value: Any) -> Any:
    """A value that is always JSON-serialisable, truncated to a short prefix.

    `nan` and the infinities are not valid JSON, and a rejected request may
    carry anything at all, so nothing here trusts the value it was handed.
    """
    try:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else repr(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            text = value
        elif isinstance(value, (list, tuple, dict, set)):
            text = f"<{type(value).__name__} of {len(value)}>"
        else:
            text = repr(value)
    except Exception:  # noqa: BLE001
        return "<unreadable>"
    if len(text) > MAX_ECHO_CHARS:
        return text[:MAX_ECHO_CHARS] + f"... ({len(text)} characters)"
    return text


def envelope(
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    field: str | None = None,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "code": code or CODES.get(status_code, "error"),
            "message": message,
            "field": field,
            "details": details or [],
        },
        # The same message as a plain string, which is what the page reads.
        "detail": message,
    }
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def _field_path(location: tuple[Any, ...]) -> str | None:
    """`("body", "inputs", 0, "post")` -> `inputs[0].post`."""
    parts = [p for p in location if p not in ("body", "query", "path", "header")]
    if not parts:
        return None
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out = f"{out}.{part}" if out else str(part)
    return out or None


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    try:
        raw = exc.errors()
    except Exception:  # noqa: BLE001
        return [{"field": None, "message": "the request body could not be read"}]
    for err in raw:
        entry: dict[str, Any] = {
            "field": _field_path(tuple(err.get("loc", ()))),
            "message": str(err.get("msg", "invalid value")),
            "type": str(err.get("type", "")),
        }
        if "input" in err:
            entry["input"] = safe_echo(err["input"])
        details.append(entry)
    return details


def install_error_handlers(app: FastAPI) -> None:
    """Route every error, raised or unhandled, through the one envelope."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        code = getattr(exc, "code", None) or CODES.get(exc.status_code, "error")
        return envelope(
            exc.status_code,
            message,
            code=code,
            field=getattr(exc, "field", None),
            details=getattr(exc, "details", None),
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = _validation_details(exc)
        first = details[0] if details else {}
        field = first.get("field")
        message = first.get("message", "the request could not be read")
        if field:
            message = f"{field}: {message}"
        if len(details) > 1:
            message += f" ({len(details)} fields were rejected)"
        return envelope(422, message, code="validation_error", field=field, details=details)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # A 500 is never returned as plain text, so a client that reads the
        # envelope everywhere else does not have to parse a stack trace here.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return envelope(
            500,
            "The instance could not complete the request. "
            f"The failure class was {type(exc).__name__}.",
            code="internal_error",
        )
