"""Small, operation-neutral HTTP failure and backoff policy."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from random import uniform
from typing import Mapping

TRANSIENT_HTTP_STATUSES = frozenset({
    HTTPStatus.TOO_MANY_REQUESTS,
    HTTPStatus.INTERNAL_SERVER_ERROR,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
})


def is_transient_status(status_code: int | None) -> bool:
    """Classify responses; callers decide whether replaying an operation is safe."""
    return status_code in TRANSIENT_HTTP_STATUSES


def retry_after_seconds(headers: Mapping[str, str] | None) -> float | None:
    """Read Retry-After seconds or HTTP-date, ignoring invalid values."""
    value = next(
        (v for k, v in (headers or {}).items() if k.lower() == "retry-after"), None
    )
    if value is None:
        return None
    try:
        seconds = float(value)
        if seconds >= 0 and seconds != float("inf"):
            return seconds
    except ValueError:
        pass
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def retry_delay(
    attempt: int, *, headers: Mapping[str, str] | None = None, max_delay: float = 32.0
) -> float:
    """Respect a server delay or use bounded exponential backoff with jitter."""
    server_delay = retry_after_seconds(headers)
    if server_delay is not None:
        return server_delay
    return uniform(0, min(max_delay, 2 ** max(0, attempt)))
