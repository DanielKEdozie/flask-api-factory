"""Error types and helpers for flask-api-factory.

``ApiError`` is the framework's canonical error. Raise it (or call
:func:`abort`) anywhere in a resource, hook, or resolver and the factory's
error handler turns it into a consistent JSON envelope::

    {"message": "...", "code": "...", "errors": {...}}

``code`` and ``errors`` are optional.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


_DEFAULT_MESSAGES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    422: "Invalid data",
    429: "Too many requests",
    500: "Internal server error",
}


class ApiError(Exception):
    """Canonical API error carrying an HTTP status and JSON payload."""

    def __init__(
        self,
        status_code: int = 400,
        message: Optional[str] = None,
        errors: Optional[Dict[str, Any]] = None,
        code: Optional[str] = None,
    ):
        self.status_code = status_code
        self.message = message or _DEFAULT_MESSAGES.get(status_code, "Error")
        self.errors = errors
        self.code = code
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"message": self.message}
        if self.code is not None:
            payload["code"] = self.code
        if self.errors is not None:
            payload["errors"] = self.errors
        return payload


def abort(
    status_code: int,
    message: Optional[str] = None,
    errors: Optional[Dict[str, Any]] = None,
    code: Optional[str] = None,
):
    """Raise an :class:`ApiError`. Mirrors ``flask.abort`` but JSON-native."""
    raise ApiError(status_code, message=message, errors=errors, code=code)
