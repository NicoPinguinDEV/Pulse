"""Shared in-process rate limiting and circuit breaking for Pulse AI callers.

The support assistant and moderation worker use the same provider/API key. A shared
semaphore and cooldown prevents them from sending overlapping retry storms when the
provider is unhealthy or the API quota is exhausted. API keys are hashed for state
identification and are never logged or retained by this module.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class ProviderState:
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))
    next_request_at: float = 0.0
    paused_until: float = 0.0
    transient_failures: int = 0
    rate_limit_failures: int = 0


_STATES: dict[str, ProviderState] = {}
MAX_PROVIDER_STATES = 16


def _retry_after_seconds(value: str | None) -> int:
    """Parse Retry-After as delta-seconds or an HTTP date, bounded to one hour."""
    if not value:
        return 0
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            seconds = (target - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, min(3600, int(seconds + 0.999)))


def _state_for(api_base: str, api_key: str) -> ProviderState:
    key_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    identity = f"{api_base.rstrip('/')}#{key_fingerprint}"
    state = _STATES.get(identity)
    if state is None:
        state = ProviderState()
        _STATES[identity] = state
    if len(_STATES) > MAX_PROVIDER_STATES:
        now = time.monotonic()
        for old_key, old_state in list(_STATES.items()):
            if old_key == identity:
                continue
            if (
                old_state.paused_until <= now
                and not old_state.semaphore.locked()
                and now - old_state.next_request_at > 3600
            ):
                _STATES.pop(old_key, None)
            if len(_STATES) <= MAX_PROVIDER_STATES:
                break
    return state


def provider_pause_remaining(api_base: str, api_key: str) -> float:
    """Return the shared provider cooldown remaining, or zero when available."""
    if not api_key:
        return 0.0
    state = _state_for(api_base, api_key)
    return max(0.0, state.paused_until - time.monotonic())


@asynccontextmanager
async def provider_request_slot(
    api_base: str,
    api_key: str,
    min_interval: float = 1.0,
) -> AsyncIterator[bool]:
    """Serialize calls for one API key and enforce a shared request start interval.

    The yielded boolean is false during a provider cooldown. Callers should fail
    quickly instead of sleeping and building a backlog of stale moderation work.
    """
    if not api_key:
        yield False
        return
    state = _state_for(api_base, api_key)
    async with state.semaphore:
        now = time.monotonic()
        if state.paused_until > now:
            yield False
            return
        wait = max(0.0, state.next_request_at - now)
        if wait:
            await asyncio.sleep(wait)
        now = time.monotonic()
        if state.paused_until > now:
            yield False
            return
        state.next_request_at = now + max(0.0, min(float(min_interval), 60.0))
        yield True


def record_provider_success(api_base: str, api_key: str) -> None:
    """Clear exponential failure state after a successful provider response."""
    if not api_key:
        return
    state = _state_for(api_base, api_key)
    state.transient_failures = 0
    state.rate_limit_failures = 0
    state.paused_until = 0.0


def record_provider_failure(
    api_base: str,
    api_key: str,
    *,
    status_code: int | None = None,
    retry_after: str | None = None,
    short_rate_limit: bool = False,
) -> int:
    """Open/increase a shared circuit breaker and return the pause in seconds.

    ``short_rate_limit`` marks a 429 that the provider identified as a
    per-minute request limit (not exhausted quota). It starts at a 30s pause
    instead of 5 minutes and still escalates exponentially up to one hour when
    429s keep repeating.
    """
    if not api_key:
        return 0
    state = _state_for(api_base, api_key)
    now = time.monotonic()

    if status_code == 429:
        state.rate_limit_failures = min(state.rate_limit_failures + 1, 8)
        base = 30 if short_rate_limit else 300
        quota_backoff = min(
            3600,
            base * (2 ** min(state.rate_limit_failures - 1, 7 if short_rate_limit else 4)),
        )
        pause = max(quota_backoff, _retry_after_seconds(retry_after))
    elif status_code in (401, 403):
        pause = max(300, _retry_after_seconds(retry_after))
    elif status_code is None or status_code >= 500:
        state.transient_failures = min(state.transient_failures + 1, 8)
        pause = max(
            min(300, 15 * (2 ** min(state.transient_failures - 1, 5))),
            _retry_after_seconds(retry_after),
        )
    else:
        return int(max(0, state.paused_until - now))

    state.paused_until = max(state.paused_until, now + pause)
    return int(max(0, state.paused_until - now + 0.999))


def provider_cooldown_snapshot(api_base: str, api_key: str) -> dict[str, int]:
    """Safe diagnostic data; excludes the API key and its fingerprint."""
    if not api_key:
        return {"paused_seconds": 0}
    state = _state_for(api_base, api_key)
    return {"paused_seconds": int(max(0.0, state.paused_until - time.monotonic()) + 0.999)}
