"""Audit #129-#132, #136, #137, #139: local, rule-based leadership intelligence (no external AI, no decisions).

Pure functions on plain data (tickets, tasks, shift history, applications, members). Every finding names the data
it is based on, the threshold it crossed and a *suggested next step as a link* - Pulse never executes anything from
here. Reports and handover texts are **drafts** for a person to check; nothing is sent automatically.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev

import pulse_teamakte as ta
from pulse_intelligence import ticket_is_overdue

try:  # recruiting statistics (#102) - optional, the application hints are simply left out without it
    import pulse_recruiting_stats as recruiting
except ImportError:  # pragma: no cover
    recruiting = None

SPIKE_MIN = 5            # a day needs at least this many new tickets to count as a spike
SPIKE_SIGMA = 3.0        # ... and must exceed mean + 3 standard deviations of the same weekday baseline
BASELINE_WEEKS = 6
HOURS_DROP = 0.4         # team hours of the last full week at least 40 % below the week before
MIN_PREV_HOURS = 10.0    # ... and the week before needs at least this many hours to be meaningful
UNCLAIMED_HOURS = 6      # open ticket without a claimer for this long
SEVERITY_ORDER = {"hoch": 0, "mittel": 1, "info": 2}
_UTC = timezone.utc


def _clean(data) -> dict:
    """Only dict rows survive (garbage in stored data must never crash a report)."""
    data = data if isinstance(data, dict) else {}
    out = dict(data)
    for key in ("tickets", "tasks", "history"):
        out[key] = [row for row in (data.get(key) or ()) if isinstance(row, dict)]
    out["members"] = list(data.get("members") or ())
    return out


def _opened(ticket):
    return ta._dt(ticket.get("opened_at"))


def ticket_spike(tickets, now: datetime) -> dict | None:
    """Today's new tickets against the same weekday of the last ``BASELINE_WEEKS`` weeks (mean + 3 sigma)."""
    today = now.date()
    counts: dict = {}
    for t in tickets or ():
        opened = _opened(t)
        if opened:
            counts[opened.date()] = counts.get(opened.date(), 0) + 1
    base = [counts.get(today - timedelta(weeks=i), 0) for i in range(1, BASELINE_WEEKS + 1)]
    current = counts.get(today, 0)
    limit = mean(base) + SPIKE_SIGMA * pstdev(base)
    if current >= SPIKE_MIN and current > limit:
        return {"today": current, "baseline": round(mean(base), 1), "limit": round(limit, 1)}
    return None


def findings(data: dict, now: datetime | None = None) -> list[dict]:
    """Explainable hints over tickets, tasks, shifts, workload and applications, most severe first.
    ``data`` keys: ``tickets``, ``tasks``, ``history``, ``members`` [(id, name)], optional ``apps`` (applications.json)."""
    now = now or datetime.now(_UTC)
    data = _clean(data)
    tickets, tasks, history = data["tickets"], data["tasks"], data["history"]
    out = []

    def add(fid, severity, area, title, why, label, url):
        out.append({"id": fid, "severity": severity, "area": area, "title": title, "why": why, "next": {"label": label, "url": url}})
    spike = ticket_spike(tickets, now)
    if spike:
        add("ticket-spike", "hoch", "Tickets", f"Ungewöhnlich viele neue Tickets heute ({spike['today']})",
            f"Üblich für diesen Wochentag: Ø {spike['baseline']}, Schwelle {spike['limit']} (Ø + {SPIKE_SIGMA:g} Standardabweichungen, letzte {BASELINE_WEEKS} Wochen).",
            "Ticketübersicht öffnen", "/tickets")
    open_tickets = [t for t in tickets if str(t.get("status") or "open").lower() != "closed"]
    overdue = [t for t in open_tickets if ticket_is_overdue(t, now)]
    if overdue:
        add("tickets-overdue", "hoch" if len(overdue) >= 5 else "mittel", "Tickets", f"{len(overdue)} Ticket(s) über der Antwortzeit",
            "SLA-Fenster: dringend 30 Min., hoch 4 Std., normal 24 Std. seit Eröffnung.", "Ticketübersicht öffnen", "/tickets")
    unclaimed = [t for t in open_tickets if not t.get("claimed_by_id") and (o := _opened(t)) and (now - o) >= timedelta(hours=UNCLAIMED_HOURS)]
    if unclaimed:
        add("tickets-unclaimed", "mittel", "Tickets", f"{len(unclaimed)} Ticket(s) seit über {UNCLAIMED_HOURS} Std. ohne Bearbeiter",
            "Offen, kein Bearbeiter zugewiesen.", "Tickets verteilen", "/tickets")
    loads = ta.workload(data.get("members") or [], tasks, tickets, now)
    heavy = [r["name"] for r in loads if r["level"] == "überlastet"]
    if heavy:
        add("workload", "mittel", "Team", f"Überlastet: {', '.join(heavy[:5])}",
            f"Gewichtete Last ab {ta.LOAD_CRITICAL:g} Punkten (Aufgaben nach Priorität, Tickets je {ta.TICKET_WEIGHT:g}).", "Teamauslastung ansehen", "/team/planung?ansicht=auslastung")
    shift_flags = ta.shift_anomalies(history, now, days=14)
    if shift_flags:
        add("shifts", "info", "Dienst", f"{len(shift_flags)} auffällige Schicht(en) in 14 Tagen",
            "Regeln: Maximum, Ausreißer je Person, unbestätigt, sehr kurz, viele pro Tag.", "Anomalien prüfen", "/team/planung?ansicht=anomalien")
    rep = ta.report(history, tickets, tasks, now, "week")
    prev, cur = rep["previous"]["hours"], rep["current"]["hours"]
    if prev >= MIN_PREV_HOURS and cur <= prev * (1 - HOURS_DROP):
        add("hours-drop", "mittel", "Dienst", f"Dienstzeit der letzten Woche {cur:g} h (Vorwoche {prev:g} h)",
            f"Rückgang um mindestens {int(HOURS_DROP * 100)} % bei mindestens {MIN_PREV_HOURS:g} h Vorwoche.", "Wochenbericht öffnen", "/team/intelligenz?ansicht=wochenbericht")
    overdue_tasks = [t for t in tasks if str(t.get("status") or "open") not in ("done", "archived", "closed") and (d := ta._dt(t.get("due_at"))) and d < now]
    if len(overdue_tasks) >= 3:
        add("tasks-overdue", "mittel", "Aufgaben", f"{len(overdue_tasks)} überfällige Aufgaben", "Fälligkeitsdatum überschritten, nicht erledigt.", "Aufgaben öffnen", "/tasks")
    apps = data.get("apps")
    if isinstance(apps, dict) and recruiting is not None:
        stale = recruiting.stale_applications(apps, now.replace(tzinfo=None))
        if stale:
            add("apps-stale", "info", "Bewerbungen", f"{len(stale)} Bewerbung(en) warten zu lange",
                f"Ohne Prüfung seit über {recruiting.PENDING_DAYS} Tagen oder ohne Entscheidung {recruiting.INTERVIEW_DAYS} Tage nach dem Gespräch.", "Bewerbungen öffnen", "/applications")
    return sorted(out, key=lambda f: (SEVERITY_ORDER[f["severity"]], f["id"]))


# ----------------------------------------------------------------------------------------- #130/#131 Berichtsentwürfe
def _day_stats(tickets, tasks, history, day_start: datetime) -> dict:
    day_end = day_start + timedelta(days=1)
    opened = sum(1 for t in tickets if (o := _opened(t)) and day_start <= o < day_end)
    closed = sum(1 for t in tickets if str(t.get("status")).lower() == "closed" and (c := ta._dt(t.get("closed_at"))) and day_start <= c < day_end)
    done = sum(1 for t in tasks if t.get("status") == "done" and (u := ta._dt(t.get("updated_at"))) and day_start <= u < day_end)
    seconds, people = 0, set()
    for shift, _st in ta._shifts_of(history, since=day_start, until=day_end):
        credited = int(ta.credited_shift_seconds(shift))
        if credited > 0:
            seconds += credited
            people.add(ta._uid(shift.get("mod_id")))
    return {"opened": opened, "closed": closed, "tasks_done": done, "hours": round(seconds / 3600, 1), "people": len(people)}


def daily_draft(data: dict, now: datetime | None = None) -> dict:
    """Draft for yesterday (UTC day). Returns ``{"title", "lines", "sources"}``; a person reviews before it is used."""
    now = now or datetime.now(_UTC)
    data = _clean(data)
    start = datetime.combine(now.date() - timedelta(days=1), datetime.min.time(), _UTC)
    tickets, tasks, history = data["tickets"], data["tasks"], data["history"]
    y, before = _day_stats(tickets, tasks, history, start), _day_stats(tickets, tasks, history, start - timedelta(days=1))
    open_now = [t for t in tickets if str(t.get("status") or "open").lower() != "closed"]
    found = findings(data, now)
    lines = [f"Tagesbericht {start.date().isoformat()} (Entwurf)",
             f"Tickets: {y['opened']} neu, {y['closed']} geschlossen (Vortag: {before['opened']} / {before['closed']}).",
             f"Dienst: {y['hours']:g} h von {y['people']} Person(en) (Vortag: {before['hours']:g} h).",
             f"Aufgaben erledigt: {y['tasks_done']}.",
             f"Aktuell offen: {len(open_now)} Ticket(s), davon {sum(1 for t in open_now if ticket_is_overdue(t, now))} über der Antwortzeit."]
    lines += [f"Hinweis ({f['severity']}): {f['title']}" for f in found[:5]] or ["Keine auffälligen Hinweise."]
    return {"title": f"Tagesbericht {start.date().isoformat()}", "lines": lines,
            "sources": ["Tickets (opened_at/closed_at)", "Schichtverlauf (angerechnete Dauer)", "Aufgaben (Status, Änderungsdatum)"]}


def weekly_draft(data: dict, now: datetime | None = None) -> dict:
    """Draft for the last full week against the week before, with data sources named."""
    now = now or datetime.now(_UTC)
    data = _clean(data)
    rep = ta.report(data["history"], data["tickets"], data["tasks"], now, "week")
    cur, prev, delta = rep["current"], rep["previous"], rep["delta"]

    def row(label, key, unit=""):
        d = delta[key]
        return f"{label}: {cur[key] if cur[key] is not None else '–'}{unit} (Vorwoche {prev[key] if prev[key] is not None else '–'}{unit}, {'–' if d is None else f'{d:+g}'})"
    found = findings(data, now)
    lines = [f"Wochenbericht {rep['start']} bis {rep['end']} (Entwurf)", row("Dienstzeit", "hours", " h"), row("Schichten", "shifts"),
             row("Aktive Personen", "active"), row("Geschlossene Tickets", "tickets_closed"), row("Erledigte Aufgaben", "tasks_done")]
    lines += [f"Hinweis ({f['severity']}): {f['title']}" for f in found[:6]] or ["Keine auffälligen Hinweise."]
    return {"title": f"Wochenbericht {rep['start']} bis {rep['end']}", "lines": lines, "definitions": rep["definitions"],
            "sources": ["Schichtverlauf", "Tickets (closed_at, Bearbeiter)", "Aufgaben (Status 'done')"]}


# ------------------------------------------------------------------------------------------------ #136 Übergabe
def handover_draft(data: dict, now: datetime | None = None, limit: int = 12) -> dict:
    """Shift handover draft: open tickets (oldest first), open/overdue tasks by priority, current hints. Facts only."""
    now = now or datetime.now(_UTC)
    data = _clean(data)
    open_tickets = sorted((t for t in data["tickets"] if str(t.get("status") or "open").lower() != "closed"),
                          key=lambda t: _opened(t) or now)
    tasks = [t for t in data["tasks"] if str(t.get("status") or "open") not in ("done", "archived", "closed")]
    tasks.sort(key=lambda t: (-ta.PRIORITY_WEIGHT.get(str(t.get("priority") or "normal"), 1.0), str(t.get("due_at") or "9")))
    lines = [f"Schichtübergabe {now.strftime('%d.%m.%Y %H:%M')} UTC (Entwurf)", f"Offene Tickets: {len(open_tickets)}"]
    for t in open_tickets[:limit]:
        age = int((now - (_opened(t) or now)).total_seconds() // 3600)
        mine = t.get("claimed_by_name") or "nicht zugewiesen"
        late = " – über der Antwortzeit" if ticket_is_overdue(t, now) else ""
        lines.append(f"- {t.get('category') or 'Ticket'} von {t.get('user_name') or '?'} · {mine} · seit {age} h · {t.get('priority') or 'normal'}{late}")
    if len(open_tickets) > limit:
        lines.append(f"- … und {len(open_tickets) - limit} weitere")
    lines.append(f"Offene Aufgaben: {len(tasks)}")
    for t in tasks[:limit]:
        due = ta._dt(t.get("due_at"))
        lines.append(f"- {t.get('title') or 'Aufgabe'} · {t.get('assignee_name') or 'nicht zugewiesen'} · {t.get('priority') or 'normal'}"
                     + (f" · fällig {due.strftime('%d.%m.')}{' (überfällig)' if due < now else ''}" if due else ""))
    found = findings(data, now)
    if found:
        lines.append("Hinweise: " + "; ".join(f["title"] for f in found[:4]))
    lines.append("Nächster Schritt: Offene Punkte prüfen, Notizen ergänzen und der nächsten Schicht persönlich übergeben.")
    return {"title": "Schichtübergabe", "lines": lines, "sources": ["Offene Tickets", "Offene Aufgaben", "Aktuelle Hinweise"]}


# ----------------------------------------------------------------------------------------- #132 Führungs-Assistent
INTENTS = (
    ("overdue", ("überfällig", "ueberfaellig", "antwortzeit", "sla")),
    ("unclaimed", ("unbearbeitet", "ohne bearbeiter", "keinen bearbeiter", "kein bearbeiter", "nicht zugewiesen", "unzugewiesen")),
    ("workload", ("überlastet", "ueberlastet", "auslastung", "belastet", "belastung")),
    ("anomalies", ("auffällig", "auffaellig", "anomal", "ungewöhnlich")),
    ("hints", ("hinweis", "warnung", "empfehlung", "was ist los", "wichtig")),
    ("applications", ("bewerbung", "bewerber")),
    ("open", ("offen", "wie viele ticket")),
)
EXAMPLES = ("Welche Tickets sind überfällig?", "Wer ist überlastet?", "Welche Tickets haben keinen Bearbeiter?",
            "Gibt es auffällige Schichten?", "Welche Hinweise gibt es?", "Welche Bewerbungen warten?", "Wie viele Tickets sind offen?")


def _intent(question: str) -> str | None:
    text = re.sub(r"\s+", " ", str(question or "").lower())
    for name, words in INTENTS:
        if any(w in text for w in words):
            return name
    return None


def ask(question: str, data: dict, now: datetime | None = None) -> dict:
    """Answer one of the supported leadership questions from live data; anything else gets an honest "not supported"
    plus the examples that work. ``rows`` are ``(text, url)`` pairs, ``sources`` names where the numbers come from."""
    now = now or datetime.now(_UTC)
    intent = _intent(question)
    data = _clean(data)
    tickets = data["tickets"]
    open_tickets = [t for t in tickets if str(t.get("status") or "open").lower() != "closed"]
    rows: list[tuple[str, str]] = []
    if intent == "overdue":
        late = [t for t in open_tickets if ticket_is_overdue(t, now)]
        rows = [(f"{t.get('category') or 'Ticket'} · {t.get('user_name') or '?'} · {t.get('priority') or 'normal'} · {t.get('claimed_by_name') or 'nicht zugewiesen'}", "/tickets") for t in late[:15]]
        answer, sources = f"{len(late)} offene(s) Ticket(s) sind über der Antwortzeit.", ["Offene Tickets, SLA-Fenster 30 Min./4 Std./24 Std."]
    elif intent == "unclaimed":
        free = [t for t in open_tickets if not t.get("claimed_by_id")]
        rows = [(f"{t.get('category') or 'Ticket'} · {t.get('user_name') or '?'} · {t.get('priority') or 'normal'}", "/tickets") for t in free[:15]]
        answer, sources = f"{len(free)} offene(s) Ticket(s) ohne Bearbeiter.", ["Offene Tickets ohne claimed_by"]
    elif intent == "workload":
        heavy = [r for r in ta.workload(data["members"], data["tasks"], tickets, now) if r["level"] != "ok"]
        rows = [(f"{r['name']} · {r['level']} · {r['points']:g} Punkte ({r['tasks']} Aufgaben, {r['tickets']} Tickets)", f"/member/{r['id']}") for r in heavy[:15]]
        answer, sources = (f"{len(heavy)} Person(en) mit hoher oder kritischer Last." if heavy else "Niemand ist aktuell hoch ausgelastet."), ["Aufgaben und Tickets je Person"]
    elif intent == "anomalies":
        flags = ta.shift_anomalies(data["history"], now, days=30)
        rows = [(f"{f['name']} · {f['at'][:10]} · {f['hours']:g} h · {f['text']}", f"/member/{f['user_id']}") for f in flags[:15]]
        answer, sources = f"{len(flags)} auffällige Schicht(en) in 30 Tagen.", ["Schichtverlauf, Regeln der Dienstzeit-Anomalien"]
    elif intent == "hints":
        found = findings(data, now)
        rows = [(f"{f['title']} – {f['why']}", f["next"]["url"]) for f in found]
        answer, sources = (f"{len(found)} Hinweis(e)." if found else "Keine auffälligen Hinweise."), ["Tickets", "Aufgaben", "Schichten", "Bewerbungen"]
    elif intent == "applications":
        stale = recruiting.stale_applications(data.get("apps") or {}, now.replace(tzinfo=None)) if recruiting else []
        rows = [(f"{s['name']} · {'wartet auf Prüfung' if s['status'] == 'pending' else 'wartet auf Entscheidung'} · {s['days']} Tage", "/applications") for s in stale[:15]]
        answer, sources = f"{len(stale)} Bewerbung(en) warten zu lange.", ["Bewerbungen (Status, Zeitstempel)"]
    elif intent == "open":
        answer, sources = f"{len(open_tickets)} Ticket(s) sind offen, {sum(1 for t in open_tickets if not t.get('claimed_by_id'))} davon ohne Bearbeiter.", ["Offene Tickets"]
    else:
        return {"intent": None, "answer": "Diese Frage kann ich nicht beantworten. Unterstützt werden:", "rows": [(e, "") for e in EXAMPLES], "sources": []}
    return {"intent": intent, "answer": answer, "rows": rows, "sources": sources}
