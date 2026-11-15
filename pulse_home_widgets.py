"""Audit #57: zusätzliche Widgets für „Mein Pulse“ aus den neueren Modulen (Bestätigungen, Abstimmungen, Besprechungen).

Jedes Widget zeigt nur Daten, die die Person ohnehin sehen darf, ist begrenzt (höchstens fünf Zeilen) und liefert nichts, wenn die
Quelle fehlschlägt. Auswahl, Reihenfolge und Spalten steuern wie bei den übrigen Widgets die persönlichen Layouts.
"""
from __future__ import annotations


def _rows(items) -> str:
    return "".join(f'<div class="pulse-row"><div class="pulse-row-main">{item}</div></div>' for item in items)


def ack_widget(uid, card, esc):
    import pulse_ack
    pending = pulse_ack.pending_for(uid)
    items = [f'<div class="pulse-row-title">{esc(p["title"])}</div><div class="pulse-row-meta">{esc(p["kind_label"])}'
             f'{" · <b>überfällig</b>" if p["overdue"] else ""}</div>' for p in pending[:5]]
    body = _rows(items) + (f'<div class="text-[11px] text-slate-400 mt-1">+{len(pending) - 5} weitere</div>' if len(pending) > 5 else "") \
        if pending else '<div class="pulse-empty">Nichts zu bestätigen. 🎉</div>'
    return card("✅ Offene Bestätigungen", body, '<a href="/bestaetigungen" class="text-[11px] text-indigo-500">Alle →</a>')


def polls_widget(uid, card, esc):
    import pulse_ultimate as pu
    pu.init_db()
    with pu.cx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT p.id,p.title,p.ends_at FROM ultimate_polls p WHERE p.active=1 AND NOT EXISTS "
            "(SELECT 1 FROM ultimate_poll_votes v WHERE v.poll_id=p.id AND v.user_id=?) ORDER BY p.created_at DESC LIMIT 5", (str(uid),)).fetchall()]
    now = pu.iso()
    rows = [r for r in rows if not r["ends_at"] or str(r["ends_at"]) > now]
    items = [f'<div class="pulse-row-title">{esc(r["title"])}</div><div class="pulse-row-meta">noch keine Stimme von dir'
             f'{" · Ende " + esc(str(r["ends_at"])[:10]) if r["ends_at"] else ""}</div>' for r in rows]
    return card("🗳 Aktive Abstimmungen", _rows(items) if items else '<div class="pulse-empty">Keine offene Abstimmung für dich.</div>',
                '<a href="/ultimate/polls" class="text-[11px] text-indigo-500">Abstimmen →</a>')


def meetings_widget(uid, card, esc):
    import pulse_meetings
    upcoming = [m for m in pulse_meetings.list_meetings(20) if m["status"] in ("planned", "running")][:3]
    label = {"planned": "geplant", "running": "läuft"}
    items = [f'<a class="pulse-row-title" href="/besprechungen/{esc(m["id"])}">{esc(m["title"])}</a><div class="pulse-row-meta">'
             f'{esc(m["scheduled_at"][:16].replace("T", " "))} · {esc(label[m["status"]])}</div>' for m in upcoming]
    return card("📅 Besprechungen", _rows(items) if items else '<div class="pulse-empty">Keine anstehende Besprechung.</div>',
                '<a href="/besprechungen" class="text-[11px] text-indigo-500">Alle →</a>')


WIDGETS = {"ack": ack_widget, "polls": polls_widget, "besprechungen": meetings_widget}


def cards(uid, card, esc) -> dict:
    """``{key: html}`` for every extra widget that could be built (a failing source drops only its own widget)."""
    out = {}
    for key, build in WIDGETS.items():
        try:
            out[key] = build(uid, card, esc)
        except Exception:  # noqa: BLE001
            continue
    return out
