"""Translation of Google API failures into `GoogleIntegrationError`.

DEV-0205. Every call to Drive or Sheets goes through `guard`, so no caller has to know what
googleapiclient raises, and no raw HTTP detail reaches the завуч.
"""

from __future__ import annotations

import socket
from contextlib import contextmanager

from .errors import (
    CATEGORY_NETWORK,
    CATEGORY_UNKNOWN,
    GoogleIntegrationError,
    error_from_http_status,
)


def _http_status(exc: BaseException) -> int | None:
    """Pull the HTTP status out of a googleapiclient error without importing it."""

    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


@contextmanager
def guard(operation: str, **context):
    """Run a Google API call, converting any failure into a `GoogleIntegrationError`."""

    try:
        yield
    except GoogleIntegrationError:
        raise
    except (TimeoutError, socket.timeout) as exc:
        raise GoogleIntegrationError(
            CATEGORY_NETWORK,
            f"Google API call '{operation}' timed out",
            context={**context, "operation": operation},
        ) from exc
    except OSError as exc:
        raise GoogleIntegrationError(
            CATEGORY_NETWORK,
            f"Google API call '{operation}' failed to reach Google: {exc}",
            context={**context, "operation": operation},
        ) from exc
    except Exception as exc:
        status = _http_status(exc)
        if status is not None:
            raise error_from_http_status(status, operation=operation, context=context) from exc
        raise GoogleIntegrationError(
            CATEGORY_UNKNOWN,
            f"Google API call '{operation}' failed: {type(exc).__name__}: {exc}",
            context={**context, "operation": operation},
        ) from exc
