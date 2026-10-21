"""U-10: one chronological timeline for a team member's file.

Collects warnings (+ revocations), shifts, tickets, tasks, central events (promotions, trainings, voice
cases … from pulse_db.events) and notes. E-3 is applied here, not only in the page: without permission
warnings and notes are simply not part of the result. Pure functions – the page passes the data in.
"""
from __future__ import annotations

import re
from datetime import datetime
from html import escape
from urllib.parse import urlencode

from pulse_time import parse_timestamp

KINDS = {"warn": ("⚠", "Warnungen"), "shift": ("⏱", "Schichten"), "ticket": ("🎫", "Tickets"), "task": ("✅", "Aufgaben"),
         "event": ("↯", "Ereignisse"), "note": ("📝", "Notizen")}
LIMIT = 60
_NOTE_DATE = re.compile(r"^\[(\d{2}\.\d{2}\.\d{4})\]\s*")


def _at(value) -> datetime | None:
    return parse_timestamp(value) if value else None


def _item(kind, at, title, detail="", url=""):
    return {"kind": kind, "at": at, "title": str(title or ""), "detail": str(detail or ""), "url": url}


def build(*, warns=(), shifts=(), tickets=(), tasks=(), events=(), notes=(), show_warns=False, show_notes=False,
          user_id="") -> list[dict]:
    items = []
    if show_warns:
        for w in warns:
            items.append(_item("warn", _at(w.get("date")), f"Verwarnung: {w.get('reason') or '–'}", f"von {w.get('by') or 'Team'}"))
            if w.get("revoked_at"):
                items.append(_item("warn", _at(w.get("revoked_at")), "Verwarnung zurückgezogen",
                                   f"{w.get('revoked_by') or 'Team'} · {w.get('revoked_reason') or 'ohne Grund'}"))
    for s in shifts:
        minutes = int(s.get("duration_seconds") or 0) // 60
        items.append(_item("shift", _at(s.get("ended_at_iso") or s.get("started_at_iso") or s.get("date")),
                           f"Schicht · {minutes // 60} h {minutes % 60:02d} min", "automatisch beendet" if s.get("auto_closed") else ""))
    for t in tickets:
        role = "übernommen" if str(t.get("claimed_by_id")) == str(user_id) else "eröffnet"
        items.append(_item("ticket", _at(t.get("closed_at") or t.get("opened_at")), f"Ticket {t.get('id')} {role}",
                           f"{t.get('category') or ''} · {t.get('status') or ''}", f"/ticket/{t.get('id')}"))
    for t in tasks:
        items.append(_item("task", _at(t.get("updated_at") or t.get("created_at")), f"Aufgabe: {t.get('title') or '–'}",
                           t.get("status") or "", "/tasks"))
    for ev in events:
        items.append(_item("event", _at(ev.get("created_at")), str(ev.get("event_type") or "Ereignis").replace("_", " "),
                           ev.get("actor_name") or "System"))
    if show_notes:
        for n in notes:
            text = str(n)
            match = _NOTE_DATE.match(text)
            note = _item("note", _at(match.group(1)) if match else None, "Notiz", text[match.end():] if match else text)
            note["date_only"] = True  # notes only carry a day, no time
            items.append(note)
    dated = sorted((i for i in items if i["at"]), key=lambda i: i["at"], reverse=True)
    return dated + [i for i in items if not i["at"]]  # undated (very old) entries at the end


def render(items: list[dict], path: str, selected: str = "", time_tag=None) -> str:
    """Filter chips + list. `time_tag` is webserver.time_tag (relative time with exact tooltip)."""
    counts = {k: sum(1 for i in items if i["kind"] == k) for k in KINDS}
    selected = selected if selected in KINDS else ""
    chips = [f'<a href="{escape(path)}#zeitleiste" class="pulse-pill {"good" if not selected else ""}">Alles · {len(items)}</a>']
    chips += [f'<a href="{escape(path)}?{urlencode({"tl": k})}#zeitleiste" class="pulse-pill {"good" if k == selected else ""}">'
              f'{icon} {label} · {counts[k]}</a>' for k, (icon, label) in KINDS.items() if counts[k]]
    shown = [i for i in items if not selected or i["kind"] == selected]
    rows = []
    for i in shown[:LIMIT]:
        if not i["at"]:
            when = "ohne Datum"
        elif i.get("date_only"):
            when = i["at"].strftime("%d.%m.%Y")
        else:
            when = time_tag(i["at"].isoformat()) if time_tag else i["at"].strftime("%d.%m.%Y %H:%M")
        title = escape(i["title"])
        title = f'<a href="{escape(i["url"])}" class="hover:underline">{title}</a>' if i["url"] else title
        rows.append(f'<li class="pulse-row"><div class="pulse-avatar" aria-hidden="true">{KINDS[i["kind"]][0]}</div><div class="pulse-row-main">'
                    f'<div class="pulse-row-title">{title}</div><div class="pulse-row-meta">{when}'
                    f'{" · " + escape(i["detail"]) if i["detail"] else ""}</div></div></li>')
    more = f'<div class="pulse-row-meta mt-2">… und {len(shown) - LIMIT} ältere Einträge</div>' if len(shown) > LIMIT else ""
    body = f'<ol class="space-y-2" style="list-style:none;padding:0">{"".join(rows)}</ol>{more}' if rows else \
        '<div class="pulse-empty">Noch nichts in der Zeitleiste.</div>'
    return f'<nav class="flex flex-wrap gap-2 mb-3" aria-label="Zeitleiste filtern">{"".join(chips)}</nav>{body}'
