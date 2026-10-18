"""Pure helpers for Pulse ticket panels, welcome messages and ticket automation.

No Discord dependency, so the rules can be tested without a bot connection.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

PANEL_IMAGE_DIR = "uploads/ticket_panels"
MAX_PANEL_IMAGE_BYTES = 8 * 1024 * 1024
MAX_QUESTIONS = 5
BUTTON_STYLES = ("primary", "secondary", "success", "danger")

PANEL_DEFAULTS = {
    "name": "",
    "channel_id": 0,
    "title": "Ticket-Support",
    "description": "Wähle unten die passende Kategorie aus. Pulse erstellt anschließend einen privaten Ticket-Kanal.",
    "color": "#5865F2",
    "footer": "Pulse Ticket-System",
    "category_keys": [],
    "thumbnail_file": "",
    "banner_file": "",
    "thumbnail_url": "",
    "banner_url": "",
    "use_server_icon": True,
    "show_load": False,
    "component_style": "select",
    "placeholder": "Wähle die passende Ticket-Kategorie …",
    "welcome_title": "",
    "welcome_message": "",
    "ping_roles": True,
}

AUTOMATION_DEFAULTS = {
    "unclaimed_ping_minutes": 30,     # 0 = aus: zuständige Rollen erinnern, wenn niemand übernimmt
    "staff_reply_hours": 12,          # 0 = aus: Bearbeiter erinnern, wenn der Nutzer auf Antwort wartet
    "user_reminder_hours": 24,        # 0 = aus: Nutzer erinnern, wenn das Team auf ihn wartet
    "auto_close_hours": 0,            # 0 = aus: nach der Nutzer-Erinnerung automatisch schließen
}

PLACEHOLDER_HELP = (
    ("{user}", "Erwähnung des Ticket-Erstellers"),
    ("{user_name}", "Anzeigename des Erstellers"),
    ("{server}", "Servername"),
    ("{category}", "Name der Kategorie"),
    ("{ticket_id}", "Ticket-ID"),
)


def _clean_url(value) -> str:
    value = str(value or "").strip()[:500]
    return value if re.fullmatch(r"https://[^\s\"'<>]+", value) else ""


def _clean_color(value) -> str:
    value = str(value or "").strip()
    return value.upper() if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else "#5865F2"


def normalize_panel(raw: dict | None) -> dict:
    """Return a panel with every builder field present and sanitised (stored data is never trusted)."""
    raw = raw if isinstance(raw, dict) else {}
    panel = dict(PANEL_DEFAULTS)
    panel.update({k: v for k, v in raw.items() if v is not None})
    panel["color"] = _clean_color(panel.get("color"))
    panel["thumbnail_url"] = _clean_url(panel.get("thumbnail_url"))
    panel["banner_url"] = _clean_url(panel.get("banner_url"))
    for key in ("thumbnail_file", "banner_file"):
        name = str(panel.get(key) or "")
        panel[key] = name if re.fullmatch(r"[A-Za-z0-9_-]{1,80}\.(png|jpg|gif|webp)", name) else ""
    if panel.get("component_style") not in ("select", "buttons"):
        panel["component_style"] = "select"
    panel["placeholder"] = str(panel.get("placeholder") or PANEL_DEFAULTS["placeholder"])[:150]
    keys = panel.get("category_keys")
    panel["category_keys"] = [str(k) for k in keys][:25] if isinstance(keys, list) else []
    for key in ("use_server_icon", "show_load", "ping_roles"):
        panel[key] = bool(panel.get(key))
    return panel


def clean_questions(raw) -> list[dict]:
    """Up to five modal questions shown before a ticket opens (Discord modal limit)."""
    questions = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:45]
        if not label:
            continue
        questions.append({
            "label": label,
            "placeholder": str(item.get("placeholder") or "").strip()[:100],
            "required": bool(item.get("required", True)),
            "long": bool(item.get("long", False)),
        })
        if len(questions) >= MAX_QUESTIONS:
            break
    return questions


def render_placeholders(text: str, *, user_mention: str = "", user_name: str = "",
                        server: str = "", category: str = "", ticket_id: str = "") -> str:
    values = {
        "{user}": user_mention, "{user_name}": user_name, "{server}": server,
        "{category}": category, "{ticket_id}": ticket_id,
    }
    result = str(text or "")
    for key, value in values.items():
        result = result.replace(key, str(value))
    return result


def welcome_texts(panel: dict | None, category: dict | None) -> tuple[str, str]:
    """Category text wins, then the panel default, then the Pulse default."""
    panel = panel if isinstance(panel, dict) else {}
    category = category if isinstance(category, dict) else {}
    title = (
        str(category.get("welcome_title") or "").strip()
        or str(panel.get("welcome_title") or "").strip()
        or f"{category.get('emoji') or '🎫'} {category.get('name') or 'Ticket'}"
    )
    message = (
        str(category.get("opening_message") or "").strip()
        or str(panel.get("welcome_message") or "").strip()
        or "Willkommen {user}! Beschreibe dein Anliegen bitte möglichst vollständig. "
           "Ein zuständiges Teammitglied kümmert sich gleich um dich."
    )
    return title[:256], message[:3500]


def detect_image_type(data: bytes) -> str | None:
    """Identify a panel image by its file signature (never trust the filename)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def automation_settings(raw: dict | None) -> dict:
    settings = dict(AUTOMATION_DEFAULTS)
    for key, default in AUTOMATION_DEFAULTS.items():
        try:
            value = int((raw or {}).get(key, default))
        except (TypeError, ValueError):
            value = default
        settings[key] = max(0, min(value, 24 * 14 if key.endswith("hours") else 24 * 60))
    return settings


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_state(raw) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def automation_actions(ticket: dict, settings: dict, now: datetime) -> list[str]:
    """Decide which reminders are due for one open ticket; each fires once per situation."""
    if ticket.get("status") == "closed":
        return []
    state = load_state(ticket.get("automation_state"))
    opened = _parse(ticket.get("opened_at"))
    last_user = _parse(ticket.get("last_user_message_at"))
    last_staff = _parse(ticket.get("last_staff_message_at"))
    actions = []

    minutes = settings.get("unclaimed_ping_minutes", 0)
    if minutes and not ticket.get("claimed_by_id") and opened and not state.get("unclaimed_ping"):
        if now - opened >= timedelta(minutes=minutes):
            actions.append("remind_unclaimed")

    # Team is waiting for the creator (team spoke last).
    if last_staff and (not last_user or last_staff > last_user):
        marker = last_staff.isoformat()
        hours = settings.get("user_reminder_hours", 0)
        if hours and state.get("user_reminder_for") != marker and now - last_staff >= timedelta(hours=hours):
            actions.append("remind_user")
        close_hours = settings.get("auto_close_hours", 0)
        reminded_at = _parse(state.get("user_reminded_at"))
        if (close_hours and state.get("user_reminder_for") == marker and reminded_at
                and now - reminded_at >= timedelta(hours=close_hours)):
            actions.append("auto_close")

    # Creator is waiting for the claimed team member (creator spoke last).
    if ticket.get("claimed_by_id") and last_user and (not last_staff or last_user > last_staff):
        marker = last_user.isoformat()
        hours = settings.get("staff_reply_hours", 0)
        if hours and state.get("staff_reminder_for") != marker and now - last_user >= timedelta(hours=hours):
            actions.append("remind_staff")
    return actions


def panel_signature(payload: dict) -> str:
    """Stable fingerprint of what a panel message shows, to skip needless Discord edits."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
