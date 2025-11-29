from __future__ import annotations

import asyncio
import errno
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar

T = TypeVar("T")

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 2  # initial try + 1 retry
BACKOFF_START = 0.5

_TEMP_ERRNOS = {
    errno.ECONNRESET,
    errno.ECONNREFUSED,
    errno.EHOSTUNREACH,
    errno.ETIMEDOUT,
    errno.ECONNABORTED,
}


@dataclass
class RunOutcome(Generic[T]):
    success: bool
    result: T | None
    attempts: int
    skipped_reason: str | None = None


def _status_code_from(exc: Exception) -> int | None:
    for attr in ("status", "status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value

    response = getattr(exc, "response", None)
    if response is not None:
        for attr in ("status", "status_code"):
            value = getattr(response, attr, None)
            if isinstance(value, int):
                return value

    return None


def _is_rate_limited(exc: Exception) -> bool:
    status = _status_code_from(exc)
    if status == 429:
        return True
    message = str(exc)
    return "429" in message and "rate" in message.lower()


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True

    status = _status_code_from(exc)
    if status is not None and 500 <= status < 600:
        return True

    if isinstance(exc, OSError) and exc.errno in _TEMP_ERRNOS:  # type: ignore[attr-defined]
        return True

    return False


async def run_with_retry(
    func: Callable[[], Awaitable[T]],
    *,
    service: str,
    stage: str,
    warnings: list[str],
    logger: logging.Logger | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> RunOutcome[T]:
    """Execute ``func`` with the common retry/skip policy.

    - Per attempt timeout: 10s by default.
    - 429: skip immediately and warn.
    - Transient/network errors: retry once with exponential backoff (0.5s).
    - Other errors: skip without retry.
    """

    log = logger or logging.getLogger(__name__)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            result = await asyncio.wait_for(func(), timeout=timeout)
            if attempt > 1:
                msg = f"{service} {stage} succeeded after retry #{attempt - 1}"
                warnings.append(msg)
                log.warning(msg)
            return RunOutcome(success=True, result=result, attempts=attempt)
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limited(exc):
                msg = f"{service} {stage} skipped due to rate limit (429): {exc}"
                warnings.append(msg)
                log.warning(msg)
                return RunOutcome(success=False, result=None, attempts=attempt, skipped_reason="rate_limit")

            is_transient = _is_transient(exc)
            has_retry = attempt < MAX_ATTEMPTS and is_transient
            if not has_retry:
                msg = f"{service} {stage} failed after {attempt} attempt(s): {exc}"
                warnings.append(msg)
                log.warning(msg)
                return RunOutcome(success=False, result=None, attempts=attempt, skipped_reason="error")

            delay = BACKOFF_START * (2 ** (attempt - 1))
            msg = f"{service} {stage} transient error on attempt {attempt}: {exc}; retrying in {delay:.1f}s"
            warnings.append(msg)
            log.warning(msg)
            await asyncio.sleep(delay)

    return RunOutcome(success=False, result=None, attempts=MAX_ATTEMPTS, skipped_reason="error")
