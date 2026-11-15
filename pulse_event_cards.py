"""Audit #41: Discord-Terminkarten mit Zu-/Absage-Buttons (Daten und Regeln; die Buttons selbst liegen in ``cogs/pulse_pro.py``).

Eine Karte gehört zu genau einem Kalendertermin (``ultimate_calendar``). Die Antworten werden mit derselben Funktion gespeichert wie im
Dashboard (``pulse_ultimate.set_calendar_rsvp``: nur die eigene Zeile, nur für künftige Termine, nur für Terminarten mit Teilnehmerliste).
Die Karte zeigt Zahlen und eine begrenzte Namensliste; Namen sind Teamdaten und stehen nur auf Karten, die eine Führungskraft
selbst im Team-Kanal veröffentlicht hat.
"""
from __future__ import annotations

import re

import pulse_ultimate as pu

RESPONSES = {"going": ("✅ Ich komme", "success"), "declined": ("❌ Ich kann nicht", "danger"), "remove": ("↩ Zurücknehmen", "secondary")}
TEMPLATE = r"pulse:rsvp:(?P<eid>[A-Za-z0-9_-]{1,60}):(?P<resp>going|declined|remove)"
_EVENT_ID = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")
MAX_NAMES = 12


def custom_id(event_id: str, response: str) -> str:
    if not _EVENT_ID.match(str(event_id or "")) or response not in RESPONSES:
        raise ValueError("Ungültige Karte.")
    return f"pulse:rsvp:{event_id}:{response}"


def get_event(event_id) -> dict | None:
    pu.init_db()
    with pu.cx() as c:
        row = c.execute("SELECT id,title,kind,start_at,end_at,location,description FROM ultimate_calendar WHERE id=?", (str(event_id or ""),)).fetchone()
    return dict(row) if row else None


def answers(event_id) -> dict:
    """``{going: [names], declined: [names], counts}`` - names bounded, counts exact."""
    with pu.cx() as c:
        rows = c.execute("SELECT display_name,response FROM ultimate_calendar_rsvps WHERE event_id=? ORDER BY updated_at", (str(event_id),)).fetchall()
    out: dict = {"going": [], "declined": []}
    for row in rows:
        if row["response"] in out:
            out[row["response"]].append(str(row["display_name"] or "Teammitglied"))
    out["counts"] = {k: len(out[k]) for k in ("going", "declined")}
    out["going"], out["declined"] = out["going"][:MAX_NAMES], out["declined"][:MAX_NAMES]
    return out


def card_fields(event: dict, data: dict) -> dict:
    """Plain data for the embed (the cog turns it into ``discord.Embed``)."""
    start = str(event.get("start_at") or "")[:16].replace("T", " ")
    lines = [f"🗓 {start} UTC"] + ([f"📍 {event['location']}"] if event.get("location") else [])
    more_going = data["counts"]["going"] - len(data["going"])
    names = ", ".join(data["going"]) + (f" … (+{more_going})" if more_going > 0 else "")
    return {"title": str(event.get("title") or "Termin")[:200], "description": ("\n".join(lines) + ("\n\n" + str(event["description"])[:500] if event.get("description") else ""))[:1500],
            "fields": [("✅ Zusagen", f"{data['counts']['going']}" + (f"\n{names}" if names else ""), True), ("❌ Absagen", str(data["counts"]["declined"]), True)]}


def respond(event_id, user_id, display_name, response) -> dict:
    """Store the person's own answer (shared rule). Raises ``pulse_ultimate.CalendarRsvpError`` with the user-facing message."""
    pu.set_calendar_rsvp(event_id, user_id, display_name, response, require_upcoming=True)
    return answers(event_id)
