"""BOT-10: application communication – DM templates, interview appointment with calendar entry and reminder.

The built-in texts are exactly the messages Pulse sent before, so nothing changes until an admin edits a
template (Bewerbungen → Einstellungen). New: a receipt DM, an interview invitation with date and a reminder
shortly before the interview (sent by the existing automation loop).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

TEMPLATES_KEY = "application_dm_templates"
REMIND_BEFORE = timedelta(hours=1)
DEFAULTS = {
    "received": "📨 Deine Bewerbung bei **{server}** ist eingegangen (Nr. {id}). Wir melden uns, sobald sie geprüft wurde.",
    "interview": ("📋 Deine Bewerbung bei **{server}** wurde für die nächste Stufe freigegeben. "
                  "Du hast die Bewerberolle **{rolle}** erhalten. "
                  "Vor einer Einstellung muss verpflichtend ein Bewerbungsgespräch stattfinden. "
                  "Du bist damit noch nicht als Teammitglied eingestellt."),
    "scheduled": "🗓️ Dein Bewerbungsgespräch bei **{server}** ist am **{termin}** (deutsche Zeit). Bitte sei pünktlich im Warteraum.",
    "reminder": "⏰ Erinnerung: Dein Bewerbungsgespräch bei **{server}** beginnt um **{termin}**.",
    "hired": ("🎉 Nach deinem Bewerbungsgespräch wurde deine Bewerbung bei **{server}** final angenommen. "
              "Du wurdest als **{rolle}** in das Team aufgenommen; die Bewerberolle wurde entfernt."),
    "rejected": "Deine Bewerbung bei **{server}** wurde leider abgelehnt. Danke für dein Interesse.",
    "rejected_after_interview": "Deine Bewerbung bei **{server}** wurde nach dem Bewerbungsgespräch leider abgelehnt.",
}
LABELS = {"received": "Eingang", "interview": "Freigabe zum Gespräch", "scheduled": "Gesprächstermin",
          "reminder": "Erinnerung vor dem Gespräch", "hired": "Angenommen", "rejected": "Abgelehnt",
          "rejected_after_interview": "Abgelehnt nach Gespräch"}
PLACEHOLDERS = "{server}, {id}, {rolle}, {termin}"


def load() -> dict:
    import pulse_db
    stored = pulse_db.get_setting(TEMPLATES_KEY, {}) or {}
    return {k: (str(stored.get(k)).strip() if isinstance(stored, dict) and str(stored.get(k) or "").strip() else v)
            for k, v in DEFAULTS.items()}


def save(values: dict) -> dict:
    """Only changed texts are stored; an emptied field falls back to the built-in text."""
    import pulse_db
    data = {k: str(v).strip()[:1800] for k, v in values.items() if k in DEFAULTS and str(v or "").strip() and str(v).strip() != DEFAULTS[k]}
    pulse_db.set_setting(TEMPLATES_KEY, data)
    return load()


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def render(key: str, **values) -> str:
    """Template with placeholders filled; unknown placeholders stay visible instead of failing.
    Only plain {name} is replaced – no format specs such as {server:>99999} (review fix: memory)."""
    known = {k: str(v) for k, v in values.items()}
    return _PLACEHOLDER.sub(lambda m: known.get(m.group(1), m.group(0)), load()[key])[:1900]


def parse_slot(value: str, tz) -> datetime:
    """'2026-10-24T18:30' from a datetime-local field in German time → aware datetime; must be in the future."""
    try:
        when = datetime.fromisoformat(str(value)).replace(tzinfo=tz)
    except (TypeError, ValueError):
        raise ValueError("Bitte Datum und Uhrzeit des Gesprächs angeben.") from None
    if when <= datetime.now(timezone.utc):
        raise ValueError("Der Gesprächstermin muss in der Zukunft liegen.")
    return when


def due_reminders(apps: dict, now: datetime) -> list[str]:
    """IDs of applications whose interview starts within REMIND_BEFORE and that were not reminded yet."""
    due = []
    for app_id, item in (apps or {}).items():
        if not isinstance(item, dict) or item.get("status") != "interview" or item.get("interview_reminded"):
            continue
        try:
            when = datetime.fromisoformat(str(item.get("interview_at")))
        except (TypeError, ValueError):
            continue
        if now <= when <= now + REMIND_BEFORE:
            due.append(app_id)
    return due
