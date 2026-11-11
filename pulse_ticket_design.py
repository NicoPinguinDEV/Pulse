"""Pure helpers for Pulse ticket panels, welcome messages and ticket automation.

No Discord dependency, so the rules can be tested without a bot connection.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pulse_paths

PANEL_IMAGE_DIR = pulse_paths.data_file("uploads", "ticket_panels")  # Phase 13: data directory
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
    # Images of the welcome message inside a new ticket (same options as the panel).
    "welcome_thumbnail_file": "",
    "welcome_banner_file": "",
    "welcome_thumbnail_url": "",
    "welcome_banner_url": "",
    "welcome_use_avatar": True,
}

IMAGE_KINDS = ("thumbnail", "banner", "welcome_thumbnail", "welcome_banner")

AUTOMATION_SLA_KEYS = ("unclaimed_ping_minutes", "staff_reply_hours", "user_reminder_hours", "escalation_minutes")
CATEGORY_KEY_RE = re.compile(r"^[a-z0-9_-]{1,80}$")


AUTOMATION_DEFAULTS = {
    "unclaimed_ping_minutes": 30,     # 0 = aus: zuständige Rollen erinnern, wenn niemand übernimmt
    "staff_reply_hours": 12,          # 0 = aus: Bearbeiter erinnern, wenn der Nutzer auf Antwort wartet
    "user_reminder_hours": 24,        # 0 = aus: Nutzer erinnern, wenn das Team auf ihn wartet
    "auto_close_hours": 0,            # 0 = aus: nach der Nutzer-Erinnerung automatisch schließen
    "escalation_minutes": 0,          # 0 = aus: Führungsebene informieren, wenn eine Team-Erinnerung folgenlos bleibt
}

REOPEN_DEFAULTS = {
    "window_days": 0,                 # 0 = unbegrenzt (bisheriges Verhalten)
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
    for kind in IMAGE_KINDS:
        panel[f"{kind}_url"] = _clean_url(panel.get(f"{kind}_url"))
        name = str(panel.get(f"{kind}_file") or "")
        panel[f"{kind}_file"] = name if re.fullmatch(r"[A-Za-z0-9_-]{1,80}\.(png|jpg|gif|webp)", name) else ""
    if panel.get("component_style") not in ("select", "buttons"):
        panel["component_style"] = "select"
    panel["placeholder"] = str(panel.get("placeholder") or PANEL_DEFAULTS["placeholder"])[:150]
    keys = panel.get("category_keys")
    panel["category_keys"] = [str(k) for k in keys][:25] if isinstance(keys, list) else []
    for key in ("use_server_icon", "show_load", "ping_roles", "welcome_use_avatar"):
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
    raw = raw if isinstance(raw, dict) else {}
    settings = dict(AUTOMATION_DEFAULTS)
    for key, default in AUTOMATION_DEFAULTS.items():
        try:
            value = int(raw.get(key, default))
        except (TypeError, ValueError):
            value = default
        settings[key] = max(0, min(value, 24 * 14 if key.endswith("hours") else 24 * 60))

    # Category values are optional overrides. Missing keys inherit the global value;
    # an explicit zero disables that reminder only for the selected category.
    category_sla = {}
    raw_category_sla = raw.get("category_sla")
    if isinstance(raw_category_sla, dict):
        for category, values in list(raw_category_sla.items())[:100]:
            category = str(category or "").strip().lower()
            if not CATEGORY_KEY_RE.fullmatch(category) or not isinstance(values, dict):
                continue
            overrides = {}
            for key in AUTOMATION_SLA_KEYS:
                if key not in values:
                    continue
                try:
                    value = int(values[key])
                except (TypeError, ValueError):
                    continue
                maximum = 24 * 14 if key.endswith("hours") else 24 * 60
                overrides[key] = max(0, min(value, maximum))
            if overrides:
                category_sla[category] = overrides
    settings["category_sla"] = category_sla
    return settings


def category_automation_settings(category: str, settings: dict | None) -> dict:
    """Resolve per-category SLA overrides while preserving all global automation values."""
    resolved = dict(settings or AUTOMATION_DEFAULTS)
    category_key = str(category or "").strip().lower()
    overrides = (resolved.get("category_sla") or {}).get(category_key, {})
    if isinstance(overrides, dict):
        for key in AUTOMATION_SLA_KEYS:
            if key in overrides:
                resolved[key] = overrides[key]
    return resolved


def automation_enabled(settings: dict | None) -> bool:
    """True when at least one global rule or category-specific reminder is active."""
    settings = settings or {}
    if any(int(settings.get(key, 0) or 0) > 0 for key in AUTOMATION_DEFAULTS):
        return True
    category_sla = settings.get("category_sla")
    return bool(
        isinstance(category_sla, dict)
        and any(
            isinstance(values, dict) and any(int(values.get(key, 0) or 0) > 0 for key in AUTOMATION_SLA_KEYS)
            for values in category_sla.values()
        )
    )


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


def ticket_activity(ticket: dict) -> tuple[datetime | None, datetime | None, datetime | None]:
    """(wait start, last creator message, last team message) for the current opening of a ticket.

    A reopened ticket starts a new waiting period: its original opening time and messages from
    before the reopening must not make it look overdue the moment it is opened again.
    """
    opened = _parse(ticket.get("opened_at"))
    last_user = _parse(ticket.get("last_user_message_at"))
    last_staff = _parse(ticket.get("last_staff_message_at"))
    reopened = _parse(load_state(ticket.get("automation_state")).get("reopened_at"))
    if reopened:
        if not opened or reopened > opened:
            opened = reopened
        if last_user and last_user < reopened:
            last_user = None
        if last_staff and last_staff < reopened:
            last_staff = None
    return opened, last_user, last_staff


def escalation_state(ticket: dict) -> str:
    """'unclaimed' / 'staff' while an escalation for the current situation is on record, else ''."""
    if ticket.get("status") == "closed":
        return ""
    state = load_state(ticket.get("automation_state"))
    _opened, last_user, last_staff = ticket_activity(ticket)
    if not ticket.get("claimed_by_id"):
        return "unclaimed" if state.get("unclaimed_escalated") else ""
    if last_user and (not last_staff or last_user > last_staff):
        if state.get("staff_escalated_for") == last_user.isoformat():
            return "staff"
    return ""


def reopen_settings(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    try:
        days = int(raw.get("window_days", REOPEN_DEFAULTS["window_days"]))
    except (TypeError, ValueError):
        days = REOPEN_DEFAULTS["window_days"]
    return {"window_days": max(0, min(days, 365))}


def reopen_allowed(ticket: dict, settings: dict | None, now: datetime, *, is_admin: bool = False) -> tuple[bool, str]:
    """Whether a closed ticket may be reopened now. Administrators may override an expired window."""
    if ticket.get("status") != "closed":
        return False, "Nur geschlossene Tickets können wieder geöffnet werden."
    days = reopen_settings(settings)["window_days"]
    if not days or is_admin:
        return True, ""
    closed = _parse(ticket.get("closed_at"))
    if closed is None:
        # Without a closing time the window cannot be proven to be open: deny by default.
        return False, "Für dieses Ticket ist kein Schließzeitpunkt gespeichert. Nur Administratoren können es wieder öffnen."
    if now - closed > timedelta(days=days):
        return False, f"Die Frist von {days} Tagen für das Wiederöffnen ist abgelaufen. Nur Administratoren können es noch öffnen."
    return True, ""


def automation_actions(ticket: dict, settings: dict, now: datetime) -> list[str]:
    """Decide which reminders are due for one open ticket; each fires once per situation."""
    if ticket.get("status") == "closed":
        return []
    settings = category_automation_settings(ticket.get("category"), settings)
    state = load_state(ticket.get("automation_state"))
    opened, last_user, last_staff = ticket_activity(ticket)
    escalation = settings.get("escalation_minutes", 0)
    actions = []

    minutes = settings.get("unclaimed_ping_minutes", 0)
    if minutes and not ticket.get("claimed_by_id") and opened and not state.get("unclaimed_ping"):
        if now - opened >= timedelta(minutes=minutes):
            actions.append("remind_unclaimed")
    # Second stage: the responsible roles were pinged, yet nobody took the ticket.
    pinged_at = _parse(state.get("unclaimed_ping"))
    if (escalation and not ticket.get("claimed_by_id") and pinged_at and not state.get("unclaimed_escalated")
            and now - pinged_at >= timedelta(minutes=escalation)):
        actions.append("escalate_unclaimed")

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
        # Second stage: the assignee was reminded for exactly this message and still did not answer.
        # States written before escalations existed carry no reminder time and are never escalated.
        reminded_at = _parse(state.get("staff_reminded_at"))
        if (escalation and state.get("staff_reminder_for") == marker and reminded_at
                and state.get("staff_escalated_for") != marker
                and now - reminded_at >= timedelta(minutes=escalation)):
            actions.append("escalate_staff")
    return actions


def panel_signature(payload: dict) -> str:
    """Stable fingerprint of what a panel message shows, to skip needless Discord edits."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


LOAD_INTRO = (
    "Hier siehst du die aktuelle Auslastung unserer Tickets. Bei höherer Auslastung kann die "
    "Bearbeitung etwas länger dauern – ein Ticket kannst du trotzdem jederzeit eröffnen."
)


def load_indicator(current: int, maximum: int) -> str:
    """Traffic light for one category: green < 50 %, yellow < 80 %, red from 80 % (or full)."""
    if not maximum:
        return "🟢"
    percent = current * 100 // maximum
    return "🟢" if percent < 50 else "🟡" if percent < 80 else "🔴"


def load_line(name: str, emoji: str, current: int, maximum: int) -> str:
    """'- 🟢 🎫 **Support:** Verfügbar `3/5` (60 %)' – the format known from GalaxyBot panels."""
    current = max(0, int(current or 0))
    maximum = max(0, int(maximum or 0))
    label = f"{emoji} **{name}:**".strip()
    if not maximum:
        return f"- {load_indicator(current, maximum)} {label} `{current}` offen"
    percent = min(100, current * 100 // maximum)
    state = "Ausgelastet" if current >= maximum else "Verfügbar"
    return f"- {load_indicator(current, maximum)} {label} {state} `{current}/{maximum}` ({percent} %)"


def category_block(entries, *, show_load: bool, list_categories: bool = True) -> str:
    """Text block below the panel description. entries: (name, emoji, current, maximum)."""
    entries = list(entries)
    if not entries:
        return ""
    if show_load:
        lines = [load_line(*entry) for entry in entries]
        return "### 📊 Ticket-Auslastung\n" + LOAD_INTRO + "\n\n" + "\n".join(lines)
    if list_categories:
        return "### 📂 Verfügbare Kategorien\n" + "\n".join(f"- {emoji} **{name}**" for name, emoji, _, _ in entries)
    return ""


# --------------------------------------------------------------------------- channel names
_BOLD_UPPER, _BOLD_LOWER, _BOLD_DIGIT = 0x1D400, 0x1D41A, 0x1D7CE
CHANNEL_NAME_LIMIT = 100  # Discord limit; bold letters count double (UTF-16), so we measure that way


def fancy_bold(text: str) -> str:
    """'Partnerschaft' → '𝐏𝐚𝐫𝐭𝐧𝐞𝐫𝐬𝐜𝐡𝐚𝐟𝐭'. Umlauts/ß have no bold form and stay readable."""
    out = []
    for char in str(text or ""):
        if "A" <= char <= "Z":
            out.append(chr(_BOLD_UPPER + ord(char) - ord("A")))
        elif "a" <= char <= "z":
            out.append(chr(_BOLD_LOWER + ord(char) - ord("a")))
        elif "0" <= char <= "9":
            out.append(chr(_BOLD_DIGIT + ord(char) - ord("0")))
        else:
            out.append(char)
    return "".join(out)


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def channel_emoji(emoji: str) -> str:
    """Unicode emoji only – custom Discord emojis (<:name:id>) cannot be used in channel names."""
    value = str(emoji or "").strip()
    if not value or "<" in value or ":" in value or len(value) > 8 or re.search(r"[A-Za-z0-9]", value):
        return "🎫"
    return value


def channel_label(label: str) -> str:
    """Category label for the channel: letters, digits, umlauts; spaces become dashes."""
    value = re.sub(r"[^0-9A-Za-zÄÖÜäöüß \-]+", "", str(label or "")).strip()
    value = re.sub(r"[\s\-]+", "-", value).strip("-")
    return value[:40] or "Support"


def channel_user(name: str) -> str:
    value = re.sub(r"[^a-z0-9_]+", "", str(name or "").lower())
    return value[:32] or "user"


def ticket_channel_name(emoji: str, label: str, username: str, suffix: str = "") -> str:
    """'『🤝』𝐏𝐚𝐫𝐭𝐧𝐞𝐫𝐬𝐜𝐡𝐚𝐟𝐭-𝐓𝐢𝐜𝐤𝐞𝐭-maxim' – fits Discord's 100-character limit."""
    clean_label = channel_label(label)
    words = clean_label.split("-")
    if words and words[-1].casefold() != "ticket":
        words.append("Ticket")  # "Highteam Ticket" must not become "…-Ticket-Ticket"
    head = f"『{channel_emoji(emoji)}』"
    user = channel_user(username)
    suffix = re.sub(r"[^a-z0-9_-]+", "", str(suffix or "").lower())[:12]

    def build(label_words, user_part):
        return f"{head}{fancy_bold('-'.join(label_words))}-{user_part}{suffix}"

    name = build(words, user)
    while _utf16_len(name) > CHANNEL_NAME_LIMIT and len(user) > 6:
        user = user[:-1]
        name = build(words, user)
    while _utf16_len(name) > CHANNEL_NAME_LIMIT and len("-".join(words)) > 6:
        joined = "-".join(words)[:-1].rstrip("-")
        words = joined.split("-")
        name = build(words, user)
    return name
