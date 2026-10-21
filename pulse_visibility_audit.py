"""F-02: read-only audit of channels visible to the unverified role.

This module only reads Discord effective permissions. It never changes overwrites, roles,
categories or channel settings. A visible channel is a review hint, not proof of a
misconfiguration: public rules and onboarding channels may be intentional.
"""
from __future__ import annotations


def _positive_ids(values) -> set[int]:
    result = set()
    for value in values or () :
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if parsed > 0:
            result.add(parsed)
    return result


def audit_unverified_visibility(channels, unverified_role, expected_visible_channel_ids=()) -> dict:
    """Return a bounded report; unknown permission checks are surfaced, never treated as hidden."""
    if unverified_role is None:
        return {
            "available": False,
            "reason": "Die konfigurierte Unverifiziert-Rolle existiert nicht oder konnte nicht geladen werden.",
            "rows": [],
            "visible_count": 0,
            "review_count": 0,
            "hidden_count": 0,
            "unknown_count": 0,
        }

    expected = _positive_ids(expected_visible_channel_ids)
    rows = []
    for channel in list(channels or ()):
        try:
            channel_id = int(getattr(channel, "id", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            channel_id = 0
        try:
            permission = channel.permissions_for(unverified_role)
            viewable = bool(getattr(permission, "view_channel"))
        except Exception as exc:
            viewable = None
            error = type(exc).__name__
        else:
            error = ""

        expected_visible = channel_id in expected
        rows.append({
            "channel_id": channel_id,
            "name": str(getattr(channel, "name", "") or "Unbenannter Kanal")[:100],
            "kind": type(channel).__name__[:60],
            "position": _position(channel),
            "viewable": viewable,
            "expected_visible": expected_visible,
            "needs_review": viewable is True and not expected_visible,
            "error": error,
        })

    rows.sort(key=lambda row: (row["position"], row["name"].casefold(), row["channel_id"]))
    return {
        "available": True,
        "reason": "",
        "rows": rows,
        "visible_count": sum(row["viewable"] is True for row in rows),
        "review_count": sum(row["needs_review"] for row in rows),
        "hidden_count": sum(row["viewable"] is False for row in rows),
        "unknown_count": sum(row["viewable"] is None for row in rows),
    }


def _position(channel) -> int:
    try:
        return int(getattr(channel, "position", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
