"""Pure, dependency-free safety rules shared by Pulse runtime and regression tests.

Kept in a tiny stdlib-only module to make critical policy decisions testable without
connecting to Discord or loading the full web dashboard.
"""
from __future__ import annotations

from collections.abc import Iterable


# Policy for the current 2-GiB VPS: retain useful headroom for host and Docker.
RAM_LIMIT_MB = 768.0
RAM_WARN_MB = 500.0
RAM_CRITICAL_MB = 620.0
RAM_EMERGENCY_MB = 700.0
RAM_RECOVERY_MB = 450.0
RAM_CRITICAL_RECOVERY_MB = 560.0
RAM_EMERGENCY_RECOVERY_MB = 650.0


def memory_guard_level(
    rss_mb: float | None,
    current_level: int = 0,
    *,
    warn_mb: float = RAM_WARN_MB,
    critical_mb: float = RAM_CRITICAL_MB,
    emergency_mb: float = RAM_EMERGENCY_MB,
    recovery_mb: float = RAM_RECOVERY_MB,
    critical_recovery_mb: float = RAM_CRITICAL_RECOVERY_MB,
    emergency_recovery_mb: float = RAM_EMERGENCY_RECOVERY_MB,
) -> int:
    """Return a RAM guard state with hysteresis to prevent threshold flapping.

    Escalation uses the normal thresholds. Once a warning is active, Pulse only
    fully returns to normal below recovery_mb. Critical/emergency alerts step down
    when RSS passes their respective lower thresholds.
    """
    current_level = max(0, min(3, int(current_level)))
    if rss_mb is None:
        return current_level

    rss = float(rss_mb)
    if rss >= emergency_mb:
        return 3
    if current_level >= 3 and rss >= emergency_recovery_mb:
        return 3
    if rss >= critical_mb:
        return 2
    if current_level >= 2 and rss >= critical_recovery_mb:
        return 2
    if rss >= warn_mb:
        return 1
    if current_level and rss >= recovery_mb:
        return 1
    return 0


def rank_role_ids_after_change(
    current_role_ids: Iterable[int],
    team_role_ids: Iterable[int],
    target_role_id: int,
) -> tuple[int, ...]:
    """Keep unrelated roles while replacing all team rank roles with one target."""
    team_ids = {int(role_id) for role_id in team_role_ids}
    target = int(target_role_id)
    result = [
        int(role_id)
        for role_id in current_role_ids
        if int(role_id) not in team_ids and int(role_id) != target
    ]
    result.append(target)
    return tuple(result)


def approved_action_operation(action_kind: str) -> str | None:
    """Make the difference between server removal and team offboarding explicit."""
    if action_kind == "server-exit":
        return "kick_member"
    if action_kind == "team-exit":
        return "remove_team_roles"
    return None
