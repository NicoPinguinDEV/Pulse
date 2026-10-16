"""Pure policy for detecting suspicious bursts of manual team warnings.

Automatic system warnings are deliberately not part of this policy. Only callers
that process a human-triggered warning should record a warn attempt.
"""
from __future__ import annotations

from typing import Iterable

WARN_WINDOW_SECONDS = 300
BROAD_WARN_WINDOW_SECONDS = 600
SAME_TARGET_WARN_LIMIT = 3
MULTI_TARGET_WARN_LIMIT = 4
MULTI_TARGET_MIN_TARGETS = 3
BROAD_WARN_LIMIT = 6
FIRST_LOCKOUT_SECONDS = 30 * 60
REPEAT_INCIDENT_WINDOW_SECONDS = 24 * 60 * 60
REPEAT_LOCKOUT_SECONDS = 24 * 60 * 60
DUPLICATE_ATTEMPT_SECONDS = 4


def detect_warn_burst(
    recent_events: Iterable[dict],
    target_id: int | str,
    now_ts: float,
) -> dict:
    """Classify a proposed warning using only recent, successful human attempts.

    The proposed warning counts toward the threshold. This function never mutates
    the supplied history and is deterministic for easy unit testing.
    """
    target = str(target_id)
    events = [
        event for event in recent_events
        if isinstance(event, dict)
        and event.get("action") == "warn_attempt"
        and event.get("target_id") is not None
    ]
    recent_5m = [
        event for event in events
        if now_ts - float(event.get("created_at", 0)) < WARN_WINDOW_SECONDS
    ]
    recent_10m = [
        event for event in events
        if now_ts - float(event.get("created_at", 0)) < BROAD_WARN_WINDOW_SECONDS
    ]

    same_target = sum(str(event["target_id"]) == target for event in recent_5m) + 1
    total_5m = len(recent_5m) + 1
    distinct_targets = {str(event["target_id"]) for event in recent_5m}
    distinct_targets.add(target)
    total_10m = len(recent_10m) + 1

    if same_target >= SAME_TARGET_WARN_LIMIT:
        reason = (
            f"{same_target} manuelle Warnversuche gegen dasselbe Teammitglied "
            f"innerhalb von {WARN_WINDOW_SECONDS // 60} Minuten."
        )
        code = "same_target_burst"
    elif total_5m >= MULTI_TARGET_WARN_LIMIT and len(distinct_targets) >= MULTI_TARGET_MIN_TARGETS:
        reason = (
            f"{total_5m} manuelle Warnversuche gegen {len(distinct_targets)} Teammitglieder "
            f"innerhalb von {WARN_WINDOW_SECONDS // 60} Minuten."
        )
        code = "multi_target_burst"
    elif total_10m >= BROAD_WARN_LIMIT:
        reason = (
            f"{total_10m} manuelle Warnversuche innerhalb von "
            f"{BROAD_WARN_WINDOW_SECONDS // 60} Minuten."
        )
        code = "broad_burst"
    else:
        return {
            "triggered": False,
            "code": "",
            "reason": "",
            "same_target_count": same_target,
            "count_5m": total_5m,
            "distinct_targets_5m": len(distinct_targets),
            "count_10m": total_10m,
        }

    return {
        "triggered": True,
        "code": code,
        "reason": reason,
        "same_target_count": same_target,
        "count_5m": total_5m,
        "distinct_targets_5m": len(distinct_targets),
        "count_10m": total_10m,
    }


def next_incident_action(strike_count: int, last_trigger_at: float, now_ts: float) -> str:
    """First incident locks warning powers; a repeat within 24 hours removes team roles."""
    repeated = (
        int(strike_count or 0) >= 1
        and float(last_trigger_at or 0) > 0
        and 0 <= now_ts - float(last_trigger_at) <= REPEAT_INCIDENT_WINDOW_SECONDS
    )
    return "team_kick" if repeated else "lockout"


def lockout_action(attempt_count: int) -> str:
    """One locked high-impact attempt is blocked; repeated attempts cause team removal."""
    return "team_kick" if int(attempt_count or 0) >= 2 else "blocked"
