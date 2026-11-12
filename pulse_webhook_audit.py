"""Safe, structured metadata for Discord webhook audit-log changes.

Only non-secret identity and change fields are retained. Webhook URLs, tokens and credentials
are deliberately excluded from both the persisted event payload and the alert text.
"""
from __future__ import annotations

_ACTION_LABELS = {
    "webhook_create": "Webhook erstellt",
    "webhook_update": "Webhook geändert",
    "webhook_delete": "Webhook gelöscht",
}
_SAFE_CHANGE_FIELDS = ("name", "channel", "channel_id", "type", "application_id")
_MISSING = object()


def _safe_text(value, limit: int = 100) -> str:
    if value is None:
        return ""
    raw = getattr(value, "name", value)
    text = " ".join(str(raw).split()).replace("@", "@\u200b")
    return text[:limit]


def _safe_id(value) -> int | None:
    candidate = getattr(value, "id", value)
    try:
        number = int(candidate)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _reference(value) -> dict | None:
    if value is None:
        return None
    result = {}
    object_id = _safe_id(value)
    name = _safe_text(value)
    if object_id is not None:
        result["id"] = object_id
    if name and not name.isdigit():
        result["name"] = name
    return result or None


def _safe_field(field: str, value):
    if value is _MISSING:
        return _MISSING
    if value is None:
        return None
    if field == "channel":
        return _reference(value)
    if field in {"channel_id", "application_id"}:
        return _safe_id(value)
    if field in {"name", "type"}:
        return _safe_text(value, 100)
    return _MISSING


def audit_entry_metadata(entry, *, guild_id: int, actor_id: int, action: str) -> dict:
    """Serialize only an allowlisted subset of a webhook AuditLogEntry."""
    target = getattr(entry, "target", None)
    target_channel = getattr(target, "channel", None)
    channel_id = _safe_id(getattr(target, "channel_id", None))
    if channel_id is None:
        channel_id = _safe_id(target_channel)
    channel_name = _safe_text(target_channel)
    payload = {
        "schema_version": 1,
        "guild_id": int(guild_id),
        "action": action if action in _ACTION_LABELS else "webhook_update",
        "audit_entry_id": _safe_id(getattr(entry, "id", None)),
        "webhook_id": _safe_id(target),
        "webhook_name": _safe_text(getattr(target, "name", None)),
        "webhook_type": _safe_text(getattr(target, "type", None)),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "actor_id": int(actor_id),
        "changed_fields": {},
    }
    before = getattr(entry, "before", None)
    after = getattr(entry, "after", None)
    changed = {}
    for field in _SAFE_CHANGE_FIELDS:
        old_raw = getattr(before, field, _MISSING) if before is not None else _MISSING
        new_raw = getattr(after, field, _MISSING) if after is not None else _MISSING
        old = _safe_field(field, old_raw)
        new = _safe_field(field, new_raw)
        if old is _MISSING and new is _MISSING:
            continue
        if old != new:
            changed[field] = {
                "before": None if old is _MISSING else old,
                "after": None if new is _MISSING else new,
            }
    payload["changed_fields"] = changed
    return payload


def alert_text(payload: dict) -> str:
    """Build a short alert from sanitized fields only; never read a webhook URL or token."""
    action = _ACTION_LABELS.get(str(payload.get("action") or ""), "Webhook-Änderung")
    name = _safe_text(payload.get("webhook_name")) or "unbekannter Webhook"
    webhook_id = payload.get("webhook_id") or "unbekannt"
    channel = _safe_text(payload.get("channel_name")) or "unbekannter Kanal"
    actor_id = payload.get("actor_id") or "unbekannt"
    details = []
    labels = {"name": "Name geändert", "channel": "Kanal geändert", "channel_id": "Kanal geändert",
              "type": "Typ geändert", "application_id": "Anwendung geändert"}
    for field in payload.get("changed_fields", {}):
        label = labels.get(field)
        if label and label not in details:
            details.append(label)
    suffix = " · " + ", ".join(details) if details else ""
    return (
        f"🪝 {action}: **{name}** (ID {webhook_id}) in **{channel}**, "
        f"ausgelöst durch Nutzer-ID {actor_id}{suffix}."
    )
