"""Audit #173, #174, #177: the BOCHUM RP control room - one live overview for leadership.

Everything is *read-only aggregation* of data that already exists (shifts, tickets, tasks, cases, player reports, calendar,
departments) plus the local rule-based hints of ``pulse_intel``. Nothing is decided or executed here: escalations are
listed with a link, the person responsible acts in the module that owns the record.

``build`` is a pure function over plain data so every figure can be tested; ``collect`` reads the live sources.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pulse_intel as intel
import pulse_teamakte as ta

_UTC = timezone.utc
OPEN_CASE_STATUSES = ("open", "investigating", "waiting")
STALE_REPORT_HOURS = 48
QUEUE_SHOWN = 8


def _on_duty(active_shifts: dict, members: dict, now: datetime) -> list[dict]:
    out = []
    for uid, shift in (active_shifts or {}).items():
        if not isinstance(shift, dict) or shift.get("status") not in ("online", "break"):
            continue
        start = ta._dt(shift.get("started_at_iso"))
        out.append({"id": str(uid), "name": members.get(str(uid)) or str(shift.get("name") or uid), "status": shift["status"],
                    "minutes": int((now - start).total_seconds() // 60) if start else None})
    return sorted(out, key=lambda r: r["name"].casefold())


def _coverage(history, on_duty_count: int, now: datetime, tz=None) -> dict:
    """Typical number of people on duty at this weekday/hour (last 8 weeks) against the people on duty now."""
    matrix = ta.availability_matrix(history, now, weeks=8, tz=tz)
    local = now.astimezone(tz) if tz else now
    typical = matrix["cells"][local.weekday()][local.hour]
    if typical <= 0:
        state = "keine Vergleichsdaten"
    elif on_duty_count + 0.5 < typical:
        state = "unter dem Üblichen"
    elif on_duty_count > typical + 1:
        state = "über dem Üblichen"
    else:
        state = "im üblichen Rahmen"
    return {"typical": round(typical, 1), "now": on_duty_count, "state": state}


def _support(tickets, now: datetime, voice_waiting: int) -> dict:
    open_t = [t for t in tickets if str(t.get("status") or "open").lower() != "closed"]
    waiting = [t for t in open_t if not t.get("claimed_by_id")]
    waiting.sort(key=lambda t: ta._dt(t.get("opened_at")) or now)
    queue = []
    for t in waiting[:QUEUE_SHOWN]:
        opened = ta._dt(t.get("opened_at"))
        queue.append({"id": str(t.get("id")), "who": str(t.get("user_name") or "?"), "category": str(t.get("category") or "Ticket"),
                      "priority": str(t.get("priority") or "normal"), "minutes": int((now - opened).total_seconds() // 60) if opened else None,
                      "overdue": intel.ticket_is_overdue(t, now)})
    return {"open": len(open_t), "unclaimed": len(waiting), "overdue": sum(1 for t in open_t if intel.ticket_is_overdue(t, now)),
            "voice_waiting": int(voice_waiting or 0), "queue": queue}


def _departments(departments, on_duty_ids: set, tasks, tickets, history, now: datetime) -> list[dict]:
    since = now - timedelta(days=30)
    out = []
    for dep in departments or ():
        member_ids = {str(m) for m in dep.get("members", ())}
        seconds = sum(int(ta.credited_shift_seconds(s)) for s, _st in ta._shifts_of(history, since=since) if ta._uid(s.get("mod_id")) in member_ids)
        out.append({"id": str(dep.get("id")), "name": str(dep.get("name")), "leader": str(dep.get("leader_name") or ""),
                    "members": len(member_ids), "on_duty": len(member_ids & on_duty_ids),
                    "open_tasks": sum(1 for t in tasks if ta._uid(t.get("assignee_id")) in member_ids and str(t.get("status") or "open") not in ("done", "archived", "closed")),
                    "open_tickets": sum(1 for t in tickets if ta._uid(t.get("claimed_by_id")) in member_ids and str(t.get("status") or "open").lower() != "closed"),
                    "hours_30d": round(seconds / 3600, 1)})
    return sorted(out, key=lambda d: d["name"].casefold())


def build(data: dict, now: datetime | None = None, tz=None) -> dict:
    """Control-room snapshot. ``data`` keys: ``members`` [(id, name)], ``active_shifts``, ``history``, ``tickets``, ``tasks``,
    ``cases`` (ultimate cases), ``reports`` (player reports), ``events`` (calendar), ``departments``, ``voice_waiting``, ``apps``."""
    now = now or datetime.now(_UTC)
    data = data if isinstance(data, dict) else {}
    cleaned = intel._clean(data)
    members = {str(i): str(n) for i, n in cleaned["members"]}
    tickets, tasks, history = cleaned["tickets"], cleaned["tasks"], cleaned["history"]
    duty = _on_duty(data.get("active_shifts"), members, now)
    tasks_open = [t for t in tasks if str(t.get("status") or "open") not in ("done", "archived", "closed")]
    overdue_tasks = [t for t in tasks_open if (d := ta._dt(t.get("due_at"))) and d < now]
    cases = [c for c in (data.get("cases") or ()) if isinstance(c, dict) and str(c.get("status") or "open") in OPEN_CASE_STATUSES]
    urgent_cases = [c for c in cases if str(c.get("priority") or "normal") in ("urgent", "high")]
    stale_reports = []
    for r in (data.get("reports") or ()):
        if isinstance(r, dict) and str(r.get("status") or "open") != "closed":
            created = ta._dt(r.get("created_at"))
            if created and (now - created) >= timedelta(hours=STALE_REPORT_HOURS):
                stale_reports.append(r)
    findings = [f for f in intel.findings(cleaned | {"apps": data.get("apps")}, now) if f["severity"] in ("hoch", "mittel")][:6]
    horizon = now + timedelta(days=7)
    upcoming = []
    for e in (data.get("events") or ()):
        start = ta._dt(e.get("starts_at")) if isinstance(e, dict) else None
        if start and now <= start < horizon:
            upcoming.append({"title": str(e.get("title") or "Termin")[:100], "at": start.strftime("%Y-%m-%d %H:%M"), "category": str(e.get("category") or "")})
    upcoming.sort(key=lambda e: e["at"])
    return {"at": now.strftime("%Y-%m-%d %H:%M"), "on_duty": duty, "coverage": _coverage(history, len(duty), now, tz),
            "support": _support(tickets, now, data.get("voice_waiting") or 0),
            "workload": [r for r in ta.workload(cleaned["members"], tasks, tickets, now) if r["level"] != "ok"][:5],
            "tasks": {"open": len(tasks_open), "overdue": len(overdue_tasks), "unassigned": sum(1 for t in tasks_open if not t.get("assignee_id"))},
            "escalations": {"findings": findings, "urgent_cases": [{"no": str(c.get("case_no") or c.get("id")), "title": str(c.get("title") or "")[:100],
                                                                      "priority": str(c.get("priority")), "id": str(c.get("id"))} for c in urgent_cases[:6]],
                            "stale_reports": len(stale_reports), "open_cases": len(cases)},
            "upcoming": upcoming[:8],
            "departments": _departments(data.get("departments"), {d["id"] for d in duty}, tasks_open, tickets, history, now)}


def collect(ws, ctx, now: datetime | None = None) -> dict:
    """Read the live sources (bounded queries, no writes). A source that fails contributes nothing instead of breaking the page."""
    import pulse_access
    import pulse_db
    now = now or datetime.now(_UTC)
    guild_id = getattr(getattr(ctx, "guild", None), "id", None)
    data: dict = {"members": [(str(m.id), getattr(m, "display_name", str(m.id))) for m in pulse_access.team_members(ctx.guild, ctx.config)][:300]}
    shifts = ws.load_shifts_raw()
    data["active_shifts"], data["history"] = shifts.get("active_shifts", {}), shifts.get("history", [])
    for key, loader in (("tickets", lambda: pulse_db.list_tickets(limit=2000)),
                        ("tasks", lambda: pulse_db.list_tasks(limit=1000, include_archived=True)),
                        ("events", lambda: pulse_db.list_calendar_events(now.isoformat(), (now + timedelta(days=7)).isoformat(), 100)),
                        ("apps", lambda: ws.load_json(ws.APPS_FILE, {})),
                        ("cases", _cases), ("reports", lambda: _reports(guild_id)), ("departments", _departments_data)):
        try:
            data[key] = loader()
        except Exception:  # noqa: BLE001
            data[key] = [] if key != "apps" else {}
    try:
        from pulse_pro import voice_waiting
        data["voice_waiting"] = voice_waiting(ctx.guild)
    except Exception:  # noqa: BLE001
        data["voice_waiting"] = 0
    return data


def _cases() -> list[dict]:
    import pulse_ultimate
    with pulse_ultimate.cx() as c:
        rows = c.execute("SELECT id,case_no,title,priority,status FROM ultimate_cases WHERE status IN ('open','investigating','waiting') "
                         "ORDER BY updated_at DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


def _reports(guild_id) -> list[dict]:
    import pulse_player_reports
    return pulse_player_reports.list_reports(guild_id, limit=200)


def _departments_data() -> list[dict]:
    import pulse_ultimate
    with pulse_ultimate.cx() as c:
        deps = c.execute("SELECT id,name,leader_id FROM ultimate_departments WHERE active=1 ORDER BY name LIMIT 50").fetchall()
        members = c.execute("SELECT department_id,user_id FROM ultimate_department_members").fetchall()
    by_dep: dict[str, list[str]] = {}
    for row in members:
        by_dep.setdefault(str(row["department_id"]), []).append(str(row["user_id"]))
    return [{"id": d["id"], "name": d["name"], "leader_id": d["leader_id"], "members": by_dep.get(str(d["id"]), [])} for d in deps]


def render(snap: dict, esc) -> str:
    """HTML of the control room (all texts escaped). Auto-reloads every minute while the tab is visible and nothing is typed."""
    def tile(label, value, sub=""):
        return f'<div class="pulse-stat"><div class="label">{esc(label)}</div><div class="value">{esc(value)}</div><div class="text-[11px] text-slate-400">{esc(sub)}</div></div>'

    def card(title, inner, link=""):
        more = f'<a class="text-[11px] text-indigo-500" href="{esc(link)}">Öffnen →</a>' if link else ""
        return f'<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">{title}</div>{more}</div><div class="pulse-card-b">{inner}</div></section>'

    def empty(text):
        return f'<div class="pulse-empty">{esc(text)}</div>'
    sup, cov, esc_ = snap["support"], snap["coverage"], snap["escalations"]
    tiles = "".join((tile("Im Dienst", len(snap["on_duty"]), f'üblich {cov["typical"]:g} · {cov["state"]}'),
                     tile("Offene Tickets", sup["open"], f'{sup["unclaimed"]} ohne Bearbeiter'),
                     tile("Über der Antwortzeit", sup["overdue"], f'Voice-Warteraum: {sup["voice_waiting"]}'),
                     tile("Offene Aufgaben", snap["tasks"]["open"], f'{snap["tasks"]["overdue"]} überfällig · {snap["tasks"]["unassigned"]} unzugewiesen'),
                     tile("Offene Vorgänge", esc_["open_cases"], f'{len(esc_["urgent_cases"])} dringend · {esc_["stale_reports"]} Meldungen > {STALE_REPORT_HOURS} h')))
    duty = "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title"><a href="/member/{esc(d["id"])}">{esc(d["name"])}</a></div>'
                   f'<div class="pulse-row-meta">{"Pause" if d["status"] == "break" else "im Dienst"}{"" if d["minutes"] is None else f" · seit {d["minutes"]} Min."}</div></div></div>'
                   for d in snap["on_duty"]) or empty("Niemand im Dienst.")
    queue = "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{esc(q["category"])} · {esc(q["who"])}</div>'
                    f'<div class="pulse-row-meta">{esc(q["priority"])} · wartet {"?" if q["minutes"] is None else q["minutes"]} Min.</div></div>'
                    f'{"<span class=" + chr(34) + "pulse-pill bad" + chr(34) + ">überfällig</span>" if q["overdue"] else ""}</div>' for q in sup["queue"]) or empty("Niemand wartet auf Unterstützung.")
    hints = "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{esc(f["title"])}</div><div class="pulse-row-meta">{esc(f["why"])}</div>'
                    f'<a class="underline text-xs" href="{esc(f["next"]["url"])}">{esc(f["next"]["label"])} →</a></div></div>' for f in esc_["findings"])
    hints += "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{esc(c["no"])} · {esc(c["title"])}</div>'
                     f'<div class="pulse-row-meta">Vorgang · Priorität {esc(c["priority"])}</div><a class="underline text-xs" href="/ultimate/cases/{esc(c["id"])}">Vorgang öffnen →</a></div></div>'
                     for c in esc_["urgent_cases"])
    load = "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title"><a href="/member/{esc(r["id"])}">{esc(r["name"])}</a></div>'
                   f'<div class="pulse-row-meta">{r["tasks"]} Aufgaben · {r["tickets"]} Tickets · {r["points"]:g} Punkte</div></div><span class="pulse-pill warn">{esc(r["level"])}</span></div>'
                   for r in snap["workload"]) or empty("Niemand ist hoch ausgelastet.")
    events = "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{esc(e["title"])}</div><div class="pulse-row-meta">{esc(e["at"].replace("T", " "))} UTC</div></div></div>'
                     for e in snap["upcoming"]) or empty("Keine Termine in den nächsten 7 Tagen.")
    deps = "".join(f'<tr><td>{esc(d["name"])}</td><td>{d["members"]}</td><td>{d["on_duty"]}</td><td>{d["open_tasks"]}</td><td>{d["open_tickets"]}</td><td>{d["hours_30d"]:g}</td></tr>'
                   for d in snap["departments"])
    dep_html = (f'<div style="overflow-x:auto"><table class="pulse-table"><thead><tr><th>Abteilung</th><th>Mitglieder</th><th>Im Dienst</th><th>Offene Aufgaben</th><th>Offene Tickets</th><th>Stunden 30 Tage</th></tr></thead><tbody>{deps}</tbody></table></div>'
                if deps else empty("Noch keine Abteilungen angelegt."))
    script = ("<script>(function(){setInterval(function(){var a=document.activeElement;"
              "if(document.hidden||(a&&/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)))return;location.reload();},60000);})();</script>")
    return (f'<div class="pulse-stat-grid">{tiles}</div><div class="pulse-grid mt-4"><div class="space-y-4">'
            f'{card("🟢 Im Dienst", duty, "/team-status")}{card("🎫 Support-Warteschlange", queue, "/tickets")}{card("⚠ Eskalationen und Hinweise", hints or empty("Keine Eskalationen."))}</div>'
            f'<div class="space-y-4">{card("⚖ Auslastung", load, "/team/planung?ansicht=auslastung")}{card("📅 Nächste Termine", events, "/calendar")}'
            f'{card("🏢 Abteilungen", dep_html, "/ultimate/departments")}</div></div>'
            f'<p class="text-[11px] text-slate-400 mt-3">Stand {esc(snap["at"].replace("T", " "))} UTC · aktualisiert sich jede Minute, solange der Tab sichtbar ist. Reine Übersicht – Entscheidungen treffen berechtigte Personen im jeweiligen Bereich.</p>{script}')
