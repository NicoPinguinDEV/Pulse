"""Pure channel-rename throttling helpers; intentionally has no Discord dependency."""
from __future__ import annotations

# Discord allows only two name changes per channel within ten minutes. Renaming at
# most once every five minutes stays inside that bucket even across event bursts.
CHANNEL_RENAME_MIN_INTERVAL_SECONDS = 300.0


def rename_wait_seconds(
    last_rename_at: float | None,
    now: float,
    min_interval: float = CHANNEL_RENAME_MIN_INTERVAL_SECONDS,
) -> float:
    """Return how long a rename must still wait; 0 means it may run now."""
    if last_rename_at is None:
        return 0.0
    elapsed = float(now) - float(last_rename_at)
    if elapsed < 0:
        # A clock jump must never block renames forever.
        return 0.0
    return max(0.0, float(min_interval) - elapsed)
