"""Helpers for receiving signed GalaxyBot ticket webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Iterable

GALAXYBOT_WEBHOOK_PATH = "/api/galaxybot/webhook"
TICKET_OPEN_LOG_CHANNEL_ID = 1543914075308818503
TICKET_CLAIM_LOG_CHANNEL_ID = 1535025111722098778
TICKET_SOURCE_CHANNEL_IDS = frozenset({
    1511390378752741386,
    1494338404224340179,
    1494397389635719269,
    1488972966774706186,
    1559242374658662602,
    1488699195157254436,
})
SUPPORTED_TICKET_EVENTS = frozenset({"ticket.created", "ticket.claimed"})
SIGNATURE_TOLERANCE_SECONDS = 300


def verify_webhook_signature(
    secret: str,
    timestamp: str,
    signature: str,
    raw_body: bytes,
    *,
    now: float | None = None,
    tolerance_seconds: int = SIGNATURE_TOLERANCE_SECONDS,
) -> bool:
    """Verify GalaxyBot's HMAC-SHA256 signature and reject stale deliveries."""
    if not secret or not timestamp or not signature.startswith("sha256="):
        return False
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    current_time = time.time() if now is None else float(now)
    if abs(current_time - sent_at) > max(0, int(tolerance_seconds)):
        return False
    signed_payload = timestamp.encode("ascii", errors="strict") + b"." + raw_body
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), signed_payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def _key(value: Any) -> str:
    return "".join(char.lower() for char in str(value) if char.isalnum())


def _walk_items(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield _key(key), child
            yield from _walk_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_items(child)


def _as_display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, dict):
        for name in ("displayName", "display_name", "username", "userName", "name", "tag", "id"):
            if value.get(name) not in (None, ""):
                result = _as_display(value[name])
                if result:
                    return result
    return ""


def find_data_value(data: dict[str, Any], aliases: Iterable[str]) -> str:
    """Find a simple value by key, tolerating common API casing/nesting variants."""
    alias_keys = {_key(alias) for alias in aliases}
    for key, value in _walk_items(data):
        if key in alias_keys:
            result = _as_display(value)
            if result:
                return result
    return ""


def collect_numeric_ids(data: Any) -> set[int]:
    """Collect Snowflake-like IDs from nested webhook data without trusting key casing."""
    found: set[int] = set()
    for _, value in _walk_items(data):
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            found.add(value)
        elif isinstance(value, str) and value.isdigit() and len(value) >= 16:
            try:
                found.add(int(value))
            except ValueError:
                pass
    return found


def find_channel_ids(data: dict[str, Any]) -> list[int]:
    """Extract channel-like identifiers for optional Discord-side parent/category checks."""
    wanted = {
        "channelid", "ticketchannelid", "discordchannelid", "parentid",
        "categoryid", "panelchannelid", "sourcechannelid", "ticketchannel",
        "channel", "parentchannelid",
    }
    found: list[int] = []
    for key, value in _walk_items(data):
        if key not in wanted:
            continue
        candidates: list[Any] = [value]
        if isinstance(value, dict):
            candidates = [value.get("id"), value.get("channelId"), value.get("channel_id")]
        for candidate in candidates:
            try:
                channel_id = int(candidate)
            except (TypeError, ValueError):
                continue
            if channel_id > 0 and channel_id not in found:
                found.append(channel_id)
    return found


def ticket_display_fields(data: dict[str, Any]) -> dict[str, str]:
    """Extract human-readable ticket attributes without including the ticket message body."""
    ticket_obj = data.get("ticket") if isinstance(data.get("ticket"), dict) else {}
    ticket_id = find_data_value(data, ("ticketId", "ticketID", "ticket_id", "ticketNumber", "ticketNo", "number"))
    if not ticket_id:
        ticket_id = _as_display(ticket_obj.get("id")) or _as_display(data.get("id"))

    channel_id = find_data_value(data, (
        "ticketChannelId", "ticketChannelID", "ticket_channel_id",
        "discordChannelId", "channelId", "channelID", "channel_id",
    ))
    if not channel_id:
        for key in ("channel", "ticketChannel", "ticket_channel"):
            if isinstance(data.get(key), dict):
                channel_id = _as_display(data[key].get("id"))
                if channel_id:
                    break
    if not channel_id and ticket_obj:
        channel_id = find_data_value(ticket_obj, (
            "ticketChannelId", "ticketChannelID", "channelId", "channelID", "channel_id",
        ))

    category = find_data_value(data, (
        "categoryName", "ticketCategoryName", "category", "ticketCategory",
        "panelName", "ticketType", "type",
    ))
    creator = find_data_value(data, (
        "creatorName", "createdByName", "openerName", "username",
        "userName", "creator", "createdBy", "opener", "user",
    ))
    claimed_by = find_data_value(data, (
        "claimedByName", "claimantName", "assigneeName", "assignedToName",
        "claimedBy", "claimant", "assignee", "assignedTo", "staffName",
    ))
    title = find_data_value(data, ("ticketName", "ticketTitle", "title", "name", "subject"))
    priority = find_data_value(data, ("priority", "priorityName"))
    return {
        "ticket_id": ticket_id[:100],
        "channel_id": channel_id[:32],
        "category": category[:200],
        "creator": creator[:200],
        "claimed_by": claimed_by[:200],
        "title": title[:200],
        "priority": priority[:100],
    }


def make_webhook_event_id(event: dict[str, Any], delivery_id: str) -> str:
    """Stable event identifier for retry de-duplication."""
    return (delivery_id or str(event.get("id") or "")).strip()[:200]
