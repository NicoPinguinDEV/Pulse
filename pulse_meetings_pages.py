"""Audit #51: Dashboard-Seiten des Besprechungsmodus (``/besprechungen``). Anlegen und Leiten nur für Führung, Lesen für das Team."""
from __future__ import annotations

from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

import pulse_meetings as meetings

STATE_LABEL = {"planned": "geplant", "running": "läuft", "closed": "abgeschlossen"}
ATT_LABEL = {"invited": "eingeladen", "present": "anwesend", "absent": "abwesend"}


def is_lead(perms: dict) -> bool:
    perms = perms or {}
    return bool(perms.get("can_promote") or perms.get("is_admin"))


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page
    e = ws.esc

    def lead_or_403(ctx):
        if not is_lead(ctx.perms):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")

    @app.get("/besprechungen", response_class=HTMLResponse)
    async def meetings_page(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        rows = "".join(
            f'<a class="block pulse-row" href="/besprechungen/{e(m["id"])}"><div class="pulse-row-main"><div class="pulse-row-title">{e(m["title"])}</div>'
            f'<div class="pulse-row-meta">{e(m["scheduled_at"][:16].replace("T", " "))} · {e(STATE_LABEL.get(m["status"], m["status"]))} · {m["decisions"]} Beschlüsse</div></div></a>'
            for m in meetings.list_meetings()) or '<div class="pulse-empty">Noch keine Besprechungen.</div>'
        form = ""
        if is_lead(ctx.perms):
            form = (f'<h2 class="text-sm font-bold mt-4 mb-1">Neue Besprechung{__import__("pulse_help").tip("meeting")}</h2><form method="post" action="/besprechungen/neu" class="space-y-2">'
                    f'<input class="{ws.INPUT} w-full" name="title" required maxlength="160" placeholder="Titel">'
                    f'<input class="{ws.INPUT}" type="datetime-local" name="scheduled_at">'
                    f'<textarea class="{ws.INPUT} w-full" name="agenda" required rows="4" placeholder="Agenda, ein Punkt pro Zeile"></textarea>'
                    f'<label class="text-xs flex gap-2 items-center"><input type="checkbox" name="alle" value="1" checked> Das gesamte Team einladen</label>'
                    f'<button class="{ws.BTN} py-2 px-4">Besprechung anlegen</button></form>')
        body = (f'<div class="pulse-crumb"><a href="/me">Mein Pulse</a> › Besprechungen</div><section class="pulse-card"><div class="pulse-card-b">{rows}{form}</div></section>')
        return HTMLResponse(render_pro_page(ws, "Besprechungen", ctx, "meetings", body))

    @app.post("/besprechungen/neu")
    async def meeting_new(request: Request, title: str = Form(...), scheduled_at: str = Form(""), agenda: str = Form(...), alle: str = Form(""),
                          user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None)
        lead_or_403(ctx)
        import pulse_access
        name = ctx.user.get("global_name") or "Team"
        people = [(str(m.id), getattr(m, "display_name", str(m.id))) for m in pulse_access.team_members(ctx.guild, ctx.config)[:meetings.MAX_PARTICIPANTS]] if alle == "1" else []
        try:
            meeting = meetings.create(title, scheduled_at, agenda.splitlines(), people, facilitator_id=ctx.user["id"], facilitator_name=name)
        except ValueError as exc:
            return ws.back("/besprechungen", str(exc), False)
        ws.log_audit(name, ctx.user["id"], "Besprechung angelegt", meeting["title"])
        try:
            import pulse_inbox_contract as inbox
            ids = [uid for uid, _n in people if uid != str(ctx.user["id"])]
            if ids:
                inbox.deliver(ids, "team", "meeting_invited", f"📅 Besprechung: {meeting['title']}"[:160], "Agenda und Beschlüsse findest du in der Besprechung.",
                              correlation_id=f"meeting:{meeting['id']}", notifier=lambda uid, h, t, kind="info", url="", dedupe_key=None, category=None:
                              __import__("pulse_db").notify(uid, h, t, kind, f"/besprechungen/{meeting['id']}", dedupe_key, 86400, category))
        except Exception:  # noqa: BLE001 - the meeting exists even if the invitation fails
            pass
        return ws.back(f"/besprechungen/{meeting['id']}", "Besprechung angelegt.")

    @app.get("/besprechungen/{meeting_id}", response_class=HTMLResponse)
    async def meeting_detail(request: Request, meeting_id: str, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        m = meetings.get(meeting_id)
        if not m:
            raise HTTPException(status_code=404, detail="Besprechung nicht gefunden.")
        lead = is_lead(ctx.perms)
        agenda = "".join(f"<li>{e(a['title'])}</li>" for a in m["agenda"])

        def att_controls(p):
            if not (lead and m["status"] != "closed"):
                return ""
            return "".join(f'<button name="user_id" value="{e(p["user_id"])}" formaction="/besprechungen/{e(m["id"])}/anwesenheit?status={s}" class="pulse-btn ghost">{l}</button>'
                           for s, l in (("present", "anwesend"), ("absent", "abwesend")))
        people = "".join(f'<form method="post" class="inline"><span class="text-xs">{e(p["user_name"])} · {e(ATT_LABEL.get(p["attendance"], p["attendance"]))}</span> {att_controls(p)}</form><br>' for p in m["participants"]) \
            or '<span class="text-xs">Keine Teilnehmenden eingeladen.</span>'
        decisions = "".join(f'<li>{e(d["text"])}{" · 📋 Aufgabe " + e(d["task_id"]) if d["task_id"] else ""} <span class="text-[11px] text-slate-400">({e(d["actor_name"])})</span></li>' for d in m["decisions"]) \
            or '<li class="text-xs">Noch keine Beschlüsse.</li>'
        controls = ""
        if lead and m["status"] == "planned":
            controls = f'<form method="post" action="/besprechungen/{e(m["id"])}/start"><button class="{ws.BTN} py-2 px-4">Besprechung starten</button></form>'
        if lead and m["status"] == "running":
            controls = (f'<form method="post" action="/besprechungen/{e(m["id"])}/beschluss" class="space-y-2 mt-2"><input class="{ws.INPUT} w-full" name="text" required maxlength="1000" placeholder="Beschluss">'
                        f'<input class="{ws.INPUT}" name="assignee_id" maxlength="22" placeholder="Aufgabe für Discord-ID (optional)"><input class="{ws.INPUT}" name="task_title" maxlength="160" placeholder="Aufgabentitel (optional)">'
                        f'<button class="{ws.BTN} py-2 px-4">Beschluss festhalten</button></form>'
                        f'<form method="post" action="/besprechungen/{e(m["id"])}/abschliessen" class="mt-2"><button class="pulse-btn ghost">Besprechung abschließen</button></form>')
        body = (f'<div class="pulse-crumb"><a href="/besprechungen">Besprechungen</a> › {e(m["title"])}</div><section class="pulse-card"><div class="pulse-card-b">'
                f'<h1 class="text-lg font-bold">{e(m["title"])}</h1><p class="text-xs text-slate-400">{e(m["scheduled_at"][:16].replace("T", " "))} · {e(STATE_LABEL[m["status"]])} · Leitung {e(m["facilitator_name"])}</p>'
                f'<h2 class="text-sm font-bold mt-3">Agenda</h2><ol class="list-decimal ml-5 text-sm">{agenda}</ol><h2 class="text-sm font-bold mt-3">Teilnehmende</h2>{people}'
                f'<h2 class="text-sm font-bold mt-3">Beschlüsse</h2><ul class="list-disc ml-5 text-sm">{decisions}</ul>{controls}</div></section>')
        return HTMLResponse(render_pro_page(ws, m["title"], ctx, "meetings", body))

    def result(meeting_id, ok, messages, code):
        return ws.back(f"/besprechungen/{meeting_id}", messages.get(code, "Nicht möglich."), ok)

    @app.post("/besprechungen/{meeting_id}/start")
    async def meeting_start(request: Request, meeting_id: str, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None)
        lead_or_403(ctx)
        ok, code = meetings.start(meeting_id)
        if code == "not_found":
            raise HTTPException(status_code=404, detail="Besprechung nicht gefunden.")
        if ok:
            ws.log_audit(ctx.user.get("global_name") or "Team", ctx.user["id"], "Besprechung gestartet", meeting_id)
        return result(meeting_id, ok, {"ok": "Besprechung gestartet.", "wrong_state": "Die Besprechung wurde schon gestartet."}, code)

    @app.post("/besprechungen/{meeting_id}/anwesenheit")
    async def meeting_attendance(request: Request, meeting_id: str, status: str = "present", user_id: str = Form(...), user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None)
        lead_or_403(ctx)
        ok, code = meetings.set_attendance(meeting_id, user_id, status)
        if code == "not_found":
            raise HTTPException(status_code=404, detail="Besprechung nicht gefunden.")
        return result(meeting_id, ok, {"ok": "Anwesenheit gespeichert.", "closed": "Das Protokoll ist abgeschlossen.", "invalid": "Ungültiger Status.",
                                        "unknown_participant": "Diese Person ist nicht eingeladen."}, code)

    @app.post("/besprechungen/{meeting_id}/beschluss")
    async def meeting_decision(request: Request, meeting_id: str, text: str = Form(...), assignee_id: str = Form(""), task_title: str = Form(""),
                               user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None)
        lead_or_403(ctx)
        name = ctx.user.get("global_name") or "Team"
        task = None
        if assignee_id.strip():
            member = ctx.guild.get_member(int(assignee_id)) if assignee_id.strip().isdigit() else None
            if member is None:
                return result(meeting_id, False, {}, "assignee")
            task = {"assignee_id": member.id, "assignee_name": member.display_name, "title": task_title.strip() or text.strip()[:160]}
        ok, code, _decision = meetings.add_decision(meeting_id, text, actor_id=ctx.user["id"], actor_name=name, task=task, perms=ctx.perms)
        if code == "not_found":
            raise HTTPException(status_code=404, detail="Besprechung nicht gefunden.")
        if ok:
            ws.log_audit(name, ctx.user["id"], "Beschluss festgehalten", meeting_id)
        return result(meeting_id, ok, {"ok": "Beschluss festgehalten.", "not_running": "Beschlüsse sind nur in einer laufenden Besprechung möglich.",
                                        "invalid": "Der Beschluss fehlt oder ist zu lang.", "limit": "Zu viele Beschlüsse.", "forbidden": "Für Aufgaben fehlt dir die Berechtigung."}, code)

    @app.post("/besprechungen/{meeting_id}/abschliessen")
    async def meeting_close(request: Request, meeting_id: str, user_session: str = Cookie(None)):
        import pulse_db
        ctx = ws.auth(request, user_session, perm=None)
        lead_or_403(ctx)
        ok, code = meetings.close(meeting_id)
        if code == "not_found":
            raise HTTPException(status_code=404, detail="Besprechung nicht gefunden.")
        if ok:
            m = meetings.get(meeting_id)
            ws.log_audit(ctx.user.get("global_name") or "Team", ctx.user["id"], "Besprechung abgeschlossen", m["title"])
            for p in m["participants"]:
                if p["attendance"] == "present" and p["user_id"] != str(ctx.user["id"]):
                    try:
                        pulse_db.notify(p["user_id"], f"📝 Protokoll: {m['title']}"[:160], meetings.summary_text(m)[:1000], "info", f"/besprechungen/{m['id']}", f"meeting-closed:{m['id']}:{p['user_id']}", 86400)
                    except Exception:  # noqa: BLE001
                        pass
        return result(meeting_id, ok, {"ok": "Besprechung abgeschlossen, das Protokoll ist jetzt gesperrt.", "wrong_state": "Nur eine laufende Besprechung kann abgeschlossen werden."}, code)
