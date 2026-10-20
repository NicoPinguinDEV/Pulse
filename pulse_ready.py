"""P-08: `on_ready` fires again after every gateway reconnect – heavy start work must not repeat each time.

`first_ready(bot, key)` is True exactly once per process. With `min_interval` it is True again only
after that many seconds (useful for reconciliation that should catch up after a long outage).
"""
from __future__ import annotations

import time


def first_ready(bot, key: str, min_interval: float | None = None) -> bool:
    runs = getattr(bot, "_pulse_ready_runs", None)
    if runs is None:
        runs = {}
        try:
            setattr(bot, "_pulse_ready_runs", runs)
        except AttributeError:
            return True
    now = time.monotonic()
    last = runs.get(key)
    if last is not None and (min_interval is None or now - last < min_interval):
        return False
    runs[key] = now
    return True
