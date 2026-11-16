"""Dashboard pages for the team file and team planning (audit #71, #73, #75, #76, #81, #87, #88, #89, #92, #94).

* ``/team/planung``  - leadership only: workload, availability matrix, shift anomalies, performance, absence calendar, report.
* ``member_section`` - block on ``/member/{id}``: 360° overview, development goals and promotion package.
* goal routes        - leadership creates / closes development goals of a person (audited).

Everything is read-only analysis except the development goals; nothing here promotes, warns or removes anybody.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import pulse_db
import pulse_insights
import pulse_intel as intel
import pulse_teamakte as ta

BERLIN = ZoneInfo("Europe/Berlin")
VIEWS = (("auslastung", "Auslastung"), ("verfuegbarkeit", "Verfügbarkeit"), ("anomalien", "Dienstzeit-Anomalien"),
         ("leistung", "Leistung"), ("abwesenheit", "Abwesenheit"), ("bericht", "Bericht"))
INTEL_VIEWS = (("hinweise", "Hinweise"), ("tagesbericht", "Tagesbericht"), ("wochenbericht", "Wochenbericht"),
               ("uebergabe", "Schichtübergabe"), ("fragen", "Fragen"))
DAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def is_lead(perms: dict) -> bool:
    return bool((perms or {}).get("is_admin") or (perms or {}).get("can_promote"))


def _e(value) -> str:
    import webserver as ws
    return ws.esc(value)


def collect(ws, ctx, now: datetime | None = None, with_moderation: bool = False, with_training: bool = False) -> dict:
    """Read-only snapshot of everything the analytics need (bounded queries, no writes)."""
    import pulse_access
    now = now or datetime.now(timezone.utc)
    members = [(str(m.id), getattr(m, "display_name", str(m.id))) for m in pulse_access.team_members(ctx.guild, ctx.config)][:300]
    data = {"now": now, "members": members, "history": ws.load_shifts_raw().get("history", []),
            "tasks": pulse_db.list_tasks(limit=1000, include_archived=True), "tickets": pulse_db.list_tickets(limit=2000),
            "loas": ws.get_loas(), "apps": ws.load_json(ws.APPS_FILE, {})}
    if with_training:  # #94: query attempts only for the performance view, not every team/profile page
        try:
            data["training_attempts"] = pulse_db.attempts(limit=5000)
            data["training_attempts_available"] = True
        except Exception:  # noqa: BLE001 - unavailable data is left missing, never counted as zero
            data["training_attempts"] = []
            data["training_attempts_available"] = False
    if with_moderation:  # #129/#137: only for the intelligence page (member pages and planning do not need them)
        try:
            import pulse_intel
            data.update(pulse_intel.moderation_sources(getattr(getattr(ctx, "guild", None), "id", None)))
        except Exception:  # noqa: BLE001 - each source tolerates its own failure; this is the last guard
            pass
    return data


def _absent_days(loas: dict, uid: str, now: datetime, days: int = 30) -> int:
    absence = loas.get(str(uid))
    if not absence:
        return 0
    von, bis = ta._day(absence.get("von")), ta._day(absence.get("bis"))
    if bis is None:
        return 0
    start = (von or bis)
    lo, hi = max(start, (now - timedelta(days=days)).date()), min(bis, now.date())
    return max(0, (hi - lo).days + 1)


def _performance_rows(data: dict) -> list[dict]:
    now, since = data["now"], data["now"] - timedelta(days=30)
    # Count distinct courses with a successful attempt in the same 30-day window as the other signals.
    # Repeated attempts at one course never inflate the person's training contribution.
    training_counts = {} if data.get("training_attempts_available") is True else None
    if training_counts is not None:
        for attempt in data.get("training_attempts") or ():
            if not isinstance(attempt, dict):
                continue
            try:
                passed = int(attempt.get("passed") or 0)
            except (TypeError, ValueError):
                continue
            uid, training_id = ta._uid(attempt.get("user_id")), ta._uid(attempt.get("training_id"))
            completed = ta._dt(attempt.get("created_at"))
            if not passed or not uid or not training_id or completed is None or not (since <= completed <= now):
                continue
            training_counts.setdefault(uid, set()).add(training_id)
    rows = []
    for uid, name in data["members"]:
        seconds = sum(int(ta.credited_shift_seconds(s)) for s, _st in ta._shifts_of(data["history"], uid, since=since))
        closed = sum(1 for t in data["tickets"] if ta._uid(t.get("claimed_by_id")) == uid and str(t.get("status")).lower() == "closed"
                     and (c := ta._dt(t.get("closed_at"))) and c >= since)
        done = sum(1 for t in data["tasks"] if ta._uid(t.get("assignee_id")) == uid and t.get("status") == "done"
                   and (u := ta._dt(t.get("updated_at"))) and u >= since)
        rows.append({"id": uid, "name": name, "hours": seconds / 3600, "tickets": closed, "tasks_done": done,
                     "trainings": None, "absent_days": _absent_days(data["loas"], uid, now)})
    return rows


def _heat(value: float, top: float) -> str:
    alpha = 0 if top <= 0 else min(1.0, value / top)
    return f"background:rgba(99,102,241,{round(0.12 + 0.75 * alpha, 2) if value else 0})"


def view_html(view: str, data: dict) -> str:
    now = data["now"]
    if view == "verfuegbarkeit":
        m = ta.availability_matrix(data["history"], now, weeks=8, tz=BERLIN)
        top = max((v for row in m["cells"] for v in row), default=0)
        head = "".join(f"<th>{h}</th>" for h in range(24))
        rows = "".join(f"<tr><th>{DAYS[d]}</th>" + "".join(
            f'<td style="{_heat(v, top)};text-align:center;font-size:10px" title="{v} Personen im Schnitt">{"" if not v else f"{v:g}"}</td>'
            for v in m["cells"][d]) + "</tr>" for d in range(7))
        return (f'<p class="text-xs text-slate-400 mb-2">Durchschnittlich Personen im Dienst je Wochentag und Stunde (Berlin-Zeit, Prognose aus den letzten '
                f'{m["weeks"]} Wochen echter Schichten, keine Zusage).</p><div style="overflow-x:auto"><table class="pulse-table"><thead><tr><th></th>{head}</tr></thead>'
                f"<tbody>{rows}</tbody></table></div>")
    if view == "anomalien":
        found = ta.shift_anomalies(data["history"], now)
        if not found:
            return '<div class="pulse-empty">Keine auffälligen Schichten in den letzten 90 Tagen.</div>'
        return ('<p class="text-xs text-slate-400 mb-2">Jede Zeile nennt die Regel, die sie markiert hat. Ein Hinweis ist ein Anlass zum Nachsehen, kein Befund.</p>'
                + "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title"><a href="/member/{_e(r["user_id"])}">{_e(r["name"])}</a> · {_e(r["at"][:16].replace("T", " "))} UTC · {r["hours"]:g} h</div>'
                            f'<div class="pulse-row-meta">{_e(r["text"])}</div></div></div>' for r in found[:100]))
    if view == "leistung":
        rows = ta.performance(_performance_rows(data))
        w = ta.WEIGHTS
        explain = " · ".join(f"{k} {int(v * 100)} %" for k, v in w.items())
        body = "".join(
            f'<tr><td><a href="/member/{_e(r["id"])}">{_e(r["name"])}</a></td><td><b>{"–" if r["score"] is None else r["score"]}</b></td>'
            + "".join(f'<td>{r["parts"].get(k, "–")}</td>' for k in w)
            + f'<td>{r["absent_days"] or ""}</td><td>{_e(", ".join(r["missing"]))}</td></tr>' for r in rows)
        return (f'<p class="text-xs text-slate-400 mb-2">Anteil am Ziel in Prozent (30 Tage), Gewichtung: {_e(explain)}. Die Stunden-Zielvorgabe wird bei Abwesenheit anteilig gekürzt; '
                f'fehlende Signale (Schulungen sind hier noch nicht eingerechnet) werden ausgelassen. Verwarnungen fließen nicht in die Punktzahl ein. Kein Ranking-Urteil über Personen.</p>'
                f'<div style="overflow-x:auto"><table class="pulse-table"><thead><tr><th>Person</th><th>Punkte</th>'
                + "".join(f"<th>{k}</th>" for k in w) + f"<th>Abw.-Tage</th><th>Ohne Daten</th></tr></thead><tbody>{body}</tbody></table></div>")
    if view == "abwesenheit":
        cal = ta.absence_calendar(data["members"], data["loas"], data["history"], start=now.date(), days=14)
        sym = {"absent": ("🚫", "abwesend"), "shift": ("🟢", "Schicht"), "event": ("📅", "Termin")}
        head = "".join(f"<th>{d[8:10]}.{d[5:7]}.</th>" for d in cal["dates"])
        rows = "".join(f'<tr><th><a href="/member/{_e(r["id"])}">{_e(r["name"])}</a></th>' + "".join(
            f'<td title="{sym[r["days"][d]][1] if d in r["days"] else ""}" style="text-align:center">{sym[r["days"][d]][0] if d in r["days"] else ""}</td>' for d in cal["dates"])
            + "</tr>" for r in cal["rows"])
        return ('<p class="text-xs text-slate-400 mb-2">🚫 Abmeldung · 🟢 Schicht · 📅 Kalendertermin. Eine Abmeldung hat Vorrang vor Schicht und Termin.</p>'
                f'<div style="overflow-x:auto"><table class="pulse-table"><thead><tr><th></th>{head}</tr></thead><tbody>{rows}</tbody></table></div>')
    if view == "bericht":
        parts = []
        for kind, label in (("week", "Letzte volle Woche"), ("month", "Letzter voller Monat")):
            rep = ta.report(data["history"], data["tickets"], data["tasks"], now, kind)

            def fmt(v):
                return "–" if v is None else f"{v:g}"
            rows = "".join(f"<tr><td title=\"{_e(rep['definitions'][k])}\">{k}</td><td>{fmt(rep['current'][k])}</td><td>{fmt(rep['previous'][k])}</td>"
                           f"<td>{'–' if rep['delta'][k] is None else f'{rep['delta'][k]:+g}'}</td></tr>" for k in rep["current"])
            parts.append(f'<h3 class="font-semibold mt-3">{label} ({_e(rep["start"])} bis {_e(rep["end"])})</h3><table class="pulse-table"><thead><tr><th>Kennzahl</th><th>Zeitraum</th><th>Vorher</th><th>Änderung</th></tr></thead><tbody>{rows}</tbody></table>')
        defs = "".join(f"<li><b>{k}</b>: {_e(v)}</li>" for k, v in ta.KPI_DEFINITIONS.items())
        return "".join(parts) + f'<h3 class="font-semibold mt-4">Definitionen</h3><ul class="text-xs text-slate-400 list-disc pl-5">{defs}</ul>'
    rows = ta.workload(data["members"], data["tasks"], data["tickets"], now)
    if not rows:
        return '<div class="pulse-empty">Keine Teammitglieder gefunden.</div>'
    badge = {"ok": "good", "hoch": "warn", "überlastet": "bad"}
    return (f'<p class="text-xs text-slate-400 mb-2">Offene Aufgaben nach Priorität gewichtet (+1 je überfälliger Aufgabe), offene Tickets je 1,5. Ab {ta.LOAD_HIGH:g} Punkten „hoch“, ab {ta.LOAD_CRITICAL:g} „überlastet“.</p>'
            + "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title"><a href="/member/{_e(r["id"])}">{_e(r["name"])}</a></div>'
                      f'<div class="pulse-row-meta">{r["tasks"]} Aufgaben ({r["overdue"]} überfällig) · {r["tickets"]} Tickets · {r["points"]:g} Punkte</div></div>'
                      f'<span class="pulse-pill {badge[r["level"]]}">{_e(r["level"])}</span></div>' for r in rows))


def intel_html(view: str, data: dict, question: str = "") -> str:
    """Local rule-based leadership intelligence (#129-#132, #136, #137, #139): drafts and hints, never actions."""
    now = data["now"]
    if view in ("tagesbericht", "wochenbericht", "uebergabe"):
        draft = {"tagesbericht": intel.daily_draft, "wochenbericht": intel.weekly_draft, "uebergabe": intel.handover_draft}[view](data, now)
        return (f'<p class="text-xs text-slate-400 mb-2">Entwurf aus den Daten von Pulse – bitte prüfen und bei Bedarf ergänzen. Es wird nichts automatisch verschickt.</p>'
                f'<pre class="text-xs whitespace-pre-wrap p-3 rounded-xl border border-slate-200 dark:border-slate-700">{_e(chr(10).join(draft["lines"]))}</pre>'
                f'<p class="text-[11px] text-slate-400 mt-2">Quellen: {_e("; ".join(draft["sources"]))}</p>')
    if view == "fragen":
        form = ('<form method="get" action="/team/intelligenz" class="flex flex-wrap gap-2 mb-3"><input type="hidden" name="ansicht" value="fragen">'
                f'<input class="pulse-input flex-1 min-w-[240px]" name="q" maxlength="200" value="{_e(question)}" placeholder="z. B. Welche Tickets sind überfällig?">'
                '<button class="pulse-btn primary">Fragen</button></form>')
        if not question.strip():
            return form + '<p class="text-xs text-slate-400">Unterstützte Fragen: ' + _e(" · ".join(intel.EXAMPLES)) + "</p>"
        result = intel.ask(question, data, now)
        rows = "".join((f'<li><a class="underline" href="{_e(url)}">{_e(text)}</a></li>' if url else f"<li>{_e(text)}</li>") for text, url in result["rows"])
        src = f'<p class="text-[11px] text-slate-400 mt-2">Quellen: {_e("; ".join(result["sources"]))}</p>' if result["sources"] else ""
        return f'{form}<p class="font-semibold">{_e(result["answer"])}</p><ul class="text-xs list-disc pl-5 mt-2 space-y-1">{rows}</ul>{src}'
    found = intel.findings(data, now)
    if not found:
        return '<div class="pulse-empty">Aktuell keine auffälligen Hinweise.</div>'
    kind = {"hoch": "bad", "mittel": "warn", "info": "good"}
    return ('<p class="text-xs text-slate-400 mb-2">Regelbasierte Hinweise mit Begründung. Der nächste Schritt ist nur ein Link – Pulse führt nichts selbst aus.</p>'
            + "".join(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{_e(f["title"])}</div><div class="pulse-row-meta">{_e(f["area"])} · {_e(f["why"])}</div>'
                      f'<a class="underline text-xs" href="{_e(f["next"]["url"])}">{_e(f["next"]["label"])} →</a></div><span class="pulse-pill {kind[f["severity"]]}">{_e(f["severity"])}</span></div>' for f in found))


def member_section(ws, c, target, entry: dict, active_warns: list) -> str:
    """360° block for ``/member/{id}``; leadership sees everything, the person themself sees their own overview and
    goals, everybody else gets nothing. Never raises into the profile page."""
    try:
        lead = is_lead(c.perms)
        own = str(target.id) == str(c.user["id"])
        if not (lead or own):
            return ""
        data = collect(ws, c)
        uid, now = str(target.id), data["now"]
        history, entry = data["history"], entry or {}
        stats = pulse_insights.member_stats(uid, now=now, shift_history=history, tickets=pulse_db.list_tickets(status="closed", limit=5000),
                                            attempts=pulse_db.attempts(limit=5000), entry=entry, active_warns=active_warns)
        see_warns = ws.can_see_warnings(c.perms, c.user.get("id"), target.id)
        load = next((r for r in ta.workload(data["members"], data["tasks"], data["tickets"], now) if r["id"] == uid), None)
        anomalies = [a for a in ta.shift_anomalies(history, now) if a["user_id"] == uid]
        goal_rows = ta.goals(uid)
        absence = data["loas"].get(uid)
        d = ta.dossier(uid=uid, name=getattr(target, "display_name", uid), stats=stats, workload_row=load, anomalies=anomalies, goals_=goal_rows,
                       absence=absence if absence and absence.get("active") else None, warns=len(active_warns) if see_warns else None,
                       certificates=int(stats.get("trainings") or 0))
        cells = [("Dienst (30 Tage)", f'{d["hours_30d"]:g} h'), ("Tickets (30 Tage)", d["tickets_30d"]),
                 ("Ø Bewertung", "–" if d["rating_avg"] is None else d["rating_avg"]), ("Schulungen", d["trainings"]),
                 ("Im Team seit", "unbekannt" if d["days_in_team"] is None else f'{d["days_in_team"]} Tagen'),
                 ("Auslastung", "–" if not d["load"] else f'{d["load"]["level"]} ({d["load"]["points"]:g})'),
                 ("Warnungen", "🔒" if d["warns"] is None else d["warns"]), ("Dienstzeit-Hinweise", d["anomalies"]),
                 ("Abmeldung", f'bis {d["absence"]["bis"]}' if d["absence"] else "keine")]
        grid = "".join(f'<div class="pulse-stat"><div class="label">{_e(k)}</div><div class="value">{_e(v)}</div></div>' for k, v in cells)
        status_names = {"open": "offen", "done": "erledigt", "dropped": "verworfen"}

        def goal_row(g):
            controls = ""
            if lead and g["status"] == "open":
                controls = "".join(
                    f'<form method="post" action="/team/{_e(uid)}/entwicklung/{_e(g["id"])}/status" class="inline"><input type="hidden" name="status" value="{s}">'
                    f'<button class="pulse-btn ghost" style="padding:2px 8px">{label}</button></form>' for s, label in (("done", "✓ erledigt"), ("dropped", "verwerfen")))
            due = f' · fällig {_e(g["due"])}' if g["due"] else ""
            return (f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{_e(g["title"])}</div>'
                    f'<div class="pulse-row-meta">{status_names.get(g["status"], "")}{due}{" · " + _e(g["detail"]) if g["detail"] else ""}</div></div>{controls}</div>')
        goals_html = "".join(goal_row(g) for g in goal_rows) or '<div class="pulse-empty">Noch keine Entwicklungsziele.</div>'
        form = (f'<form method="post" action="/team/{_e(uid)}/entwicklung/neu" class="flex flex-wrap gap-2 mt-3">'
                '<input class="pulse-input flex-1 min-w-[200px]" name="title" maxlength="160" required placeholder="Neues Entwicklungsziel">'
                '<input class="pulse-input" name="due" placeholder="Fällig (TT.MM.JJJJ)" style="width:150px">'
                '<input class="pulse-input flex-1 min-w-[200px]" name="detail" maxlength="1000" placeholder="Details / vereinbarte Schritte">'
                '<button class="pulse-btn primary">Ziel anlegen</button></form>') if lead else ""
        package_html = ""
        if lead:
            criteria_all = pulse_insights.normalize_criteria(pulse_db.get_setting(pulse_insights.CRITERIA_KEY, {}))
            team_ids = [int(x) for x in c.config.get("team_role_ids", []) if str(x).isdigit()]
            top = max((r for r in target.roles if r.id in team_ids), key=lambda r: r.position, default=None)
            criteria = criteria_all.get(str(getattr(top, "id", "")), criteria_all["default"])
            stats_view = stats if see_warns else dict(stats, active_warns=0, warn_free_days=None)
            ready = pulse_insights.readiness(stats_view, criteria if see_warns else dict(criteria, warn_free_days=0))
            prev = pulse_insights.member_stats(uid, now=now - timedelta(days=30), shift_history=history, tickets=[], attempts=[], entry=entry, active_warns=[])
            pkg = ta.promotion_package(stats_view, ready, prev["hours_30d"], goal_rows, see_warns, _absent_days(data["loas"], uid, now))
            checks = "".join(f'<div class="text-xs">{"✅" if ok else "▫"} {_e(label)}: {_e(value)} <span class="pulse-row-meta">(Ziel {_e(target_)})</span></div>'
                             for label, value, target_, ok in ready["checks"] if see_warns or label != "Warnfrei")
            trend = "" if not pkg["trend"] else (f'<div class="text-xs mt-2">Dienstzeit-Trend: {pkg["trend"]["direction"]} '
                                                 f'({pkg["trend"]["diff"]:+g} h gegenüber den 30 Tagen davor)</div>')
            gaps = "" if not pkg["gaps"] else f'<div class="text-xs mt-2 text-amber-500">Datenlücken: {_e("; ".join(pkg["gaps"]))}</div>'
            absent = f'<div class="text-xs mt-2">Abwesenheit in den letzten 30 Tagen: {pkg["absent_days"]} Tage</div>' if pkg["absent_days"] else ""
            package_html = (f'<section class="pulse-card mt-4" id="befoerderungspaket"><div class="pulse-card-h"><div><div class="pulse-section-title">⬆ Beförderungspaket</div>'
                            f'<div class="pulse-section-sub">{_e(pkg["note"])} Der Antrag läuft weiter über den bestehenden Ablauf mit 4-Augen-Prinzip.</div></div></div>'
                            f'<div class="pulse-card-b">{"🟢 Alle Kriterien erfüllt" if pkg["ready"] else f"⚪ {len(pkg['missing'])} von {len(ready['checks'])} Kriterien offen"}'
                            f'<div class="mt-2">{checks}</div>{trend}{absent}{gaps}'
                            f'<div class="text-xs mt-2">Offene Entwicklungsziele: {len(pkg["open_goals"])}</div></div></section>')
        return (f'<section class="pulse-card mt-4" id="akte360"><div class="pulse-card-h"><div><div class="pulse-section-title">🧭 360°-Übersicht</div>'
                f'<div class="pulse-section-sub">Dienst, Tickets, Auslastung, Schulungen und Hinweise auf einen Blick.</div></div></div><div class="pulse-card-b"><div class="pulse-stat-grid">{grid}</div></div></section>'
                f'<section class="pulse-card mt-4" id="entwicklung"><div class="pulse-card-h"><div><div class="pulse-section-title">🌱 Entwicklung</div>'
                f'<div class="pulse-section-sub">Vereinbarte Ziele und nächste Schritte.</div></div></div><div class="pulse-card-b">{goals_html}{form}</div></section>{package_html}')
    except Exception as exc:  # noqa: BLE001 - the profile page must keep working
        print(f"⚠️ Teamakte-Übersicht nicht verfügbar: {type(exc).__name__}")
        return ""


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page

    def lead_ctx(request, session):
        ctx = ws.auth(request, session, perm=None)
        if not is_lead(ctx.perms):
            raise HTTPException(status_code=403, detail="Die Teamplanung sehen nur Führungskräfte.")
        return ctx

    @app.get("/team/planung", response_class=HTMLResponse)
    async def team_planning(request: Request, user_session: str = Cookie(None)):
        ctx = lead_ctx(request, user_session)
        view = request.query_params.get("ansicht", "auslastung")
        if view not in {k for k, _ in VIEWS}:
            view = "auslastung"
        tabs = " ".join(f'<a href="/team/planung?ansicht={k}" class="pulse-btn {"primary" if k == view else "ghost"}">{label}</a>' for k, label in VIEWS) + ' <a href="/team/intelligenz" class="pulse-btn ghost">🧠 Intelligenz</a>'
        body = (f'<div class="pulse-crumb"><a href="/team">Team</a> › Teamplanung</div><div class="flex flex-wrap gap-2 mb-3">{tabs}</div>'
                f'<section class="pulse-card"><div class="pulse-card-b">{view_html(view, collect(ws, ctx))}</div></section>')
        return HTMLResponse(render_pro_page(ws, "Teamplanung", ctx, "team", body))

    @app.get("/team/intelligenz", response_class=HTMLResponse)
    async def team_intelligence(request: Request, user_session: str = Cookie(None)):
        ctx = lead_ctx(request, user_session)
        view = request.query_params.get("ansicht", "hinweise")
        if view not in {k for k, _ in INTEL_VIEWS}:
            view = "hinweise"
        tabs = " ".join(f'<a href="/team/intelligenz?ansicht={k}" class="pulse-btn {"primary" if k == view else "ghost"}">{label}</a>' for k, label in INTEL_VIEWS)
        body = (f'<div class="pulse-crumb"><a href="/team">Team</a> › Intelligenz</div><div class="flex flex-wrap gap-2 mb-3">{tabs}</div>'
                f'<section class="pulse-card"><div class="pulse-card-b">{intel_html(view, collect(ws, ctx, with_moderation=True), request.query_params.get("q", "")[:200])}</div></section>')
        return HTMLResponse(render_pro_page(ws, "Pulse Intelligenz", ctx, "team", body))

    @app.get("/leitstelle", response_class=HTMLResponse)
    async def control_room(request: Request, user_session: str = Cookie(None)):
        """#173/#177: control room for leadership (read-only aggregation)."""
        import pulse_leitstelle
        ctx = lead_ctx(request, user_session)
        snap = pulse_leitstelle.build(pulse_leitstelle.collect(ws, ctx), tz=BERLIN)
        body = ('<div class="pulse-crumb"><a href="/team">Team</a> › Leitstelle</div>'
                f'<h1 class="text-2xl font-bold mb-3">BOCHUM RP Leitstelle</h1>{pulse_leitstelle.render(snap, _e)}')
        return HTMLResponse(render_pro_page(ws, "Leitstelle", ctx, "team", body))

    def target_member(ctx, user_id: str):
        member = ctx.guild.get_member(int(user_id)) if str(user_id).isdigit() else None
        if member is None:
            raise HTTPException(status_code=404, detail="Teammitglied nicht gefunden.")
        return member

    @app.post("/team/{user_id}/entwicklung/neu")
    async def goal_create(request: Request, user_id: str, title: str = Form(""), detail: str = Form(""), due: str = Form(""), user_session: str = Cookie(None)):
        ctx = lead_ctx(request, user_session)
        member = target_member(ctx, user_id)
        actor = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
        try:
            ta.add_goal(member.id, title, detail, due, ctx.user["id"], actor)
        except ValueError as exc:
            return ws.back(f"/member/{member.id}#entwicklung", str(exc), False)
        ws.log_audit(actor, ctx.user["id"], "Entwicklungsziel angelegt", f"{member.id}: {title.strip()[:60]}")
        return RedirectResponse(f"/member/{member.id}#entwicklung", status_code=303)

    @app.post("/team/{user_id}/entwicklung/{goal_id}/status")
    async def goal_status(request: Request, user_id: str, goal_id: str, status: str = Form(""), user_session: str = Cookie(None)):
        ctx = lead_ctx(request, user_session)
        member = target_member(ctx, user_id)
        try:
            changed = ta.set_goal_status(goal_id, member.id, status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Ungültiger Status.") from None
        if not changed:
            raise HTTPException(status_code=404, detail="Entwicklungsziel nicht gefunden.")
        actor = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
        ws.log_audit(actor, ctx.user["id"], "Entwicklungsziel geändert", f"{member.id}: {goal_id} → {status}")
        return RedirectResponse(f"/member/{member.id}#entwicklung", status_code=303)
