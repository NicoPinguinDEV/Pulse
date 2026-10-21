"""Mein Pulse: personal start page for every team member (shift, goal, tasks, status)."""
from __future__ import annotations

from datetime import datetime

from fastapi import Cookie, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import pulse_db as db
import pulse_flags
from pulse_home_utils import (
    WEEKDAY_SHORT,
    daily_seconds,
    due_state,
    format_hm,
    goal_progress,
    greeting,
    shift_streak,
)

DUE_PILL = {
    "overdue": ("bad", "Überfällig"),
    "today": ("warn", "Heute fällig"),
    "soon": ("warn", "Bald fällig"),
    "later": ("", "Geplant"),
}


def _top_team_role(member, team_role_ids):
    ids = {int(x) for x in team_role_ids or [] if str(x).isdigit()}
    roles = [r for r in getattr(member, "roles", []) if r.id in ids]
    return max(roles, key=lambda r: r.position) if roles else None


def register(app) -> None:
    import webserver as ws
    from pulse_pro import parse_due, render_pro_page

    e = ws.esc

    def card(title, inner, action=""):
        return (
            f'<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">{title}</div>{action}</div>'
            f'<div class="pulse-card-b">{inner}</div></section>'
        )

    # Buttons sit on the purple hero, so they use white/translucent styles for contrast.
    HERO_BTN = {
        "primary": "background:#fff;color:#4338ca;border-color:#fff",
        "ghost": "background:rgba(255,255,255,.14);color:#fff;border-color:rgba(255,255,255,.35)",
    }

    def shift_button(action, label, kind):
        return (
            '<form action="/shift/action" method="post" class="inline">'
            f'<input type="hidden" name="shift_action" value="{action}"><input type="hidden" name="return_to" value="/me">'
            f'<button class="pulse-btn" style="{HERO_BTN[kind]}">{label}</button></form>'
        )

    @app.get("/me", response_class=HTMLResponse)
    async def my_pulse(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        uid = str(ctx.user["id"])
        now = ws.now_de()
        today = now.date()
        perms = ctx.perms

        # ---- Schicht & Wochenziel ----
        shifts = ws.load_shifts()
        active = shifts.get("active_shifts", {})
        history = shifts.get("history", [])
        current = active.get(uid)
        status = current.get("status") if current else "offline"
        elapsed = ws.shift_elapsed(current) if current else 0
        weekly = ws.calculate_weekly_seconds(uid, history, active)
        goal = float(ctx.config.get("weekly_goal_hours", 3.0) or 0)
        pct, remaining = goal_progress(weekly, goal)
        try:
            active_date = datetime.strptime(str(current.get("date")), "%Y-%m-%d").date() if current else None
        except (TypeError, ValueError):
            active_date = today if current else None
        days = daily_seconds(history, uid, today, 7, active_date=active_date, active_seconds=elapsed)
        streak = shift_streak(history, uid, today, active_today=bool(current))

        # ---- Status: Warnungen & Abmeldung ----
        team_db = ws.load_json(ws.DATA_FILE, {})
        entry = dict(team_db.get(uid) or {})
        warns = ws.active_warns(entry)
        loa = ws.get_loas().get(uid)
        loa_active = bool(loa and loa.get("active"))

        # ---- Aufgaben, Tickets, Inbox, Meeting ----
        tasks = [t for t in db.list_tasks(assignee_id=uid, limit=60) if t.get("status") not in ("done", "archived")]
        tasks.sort(key=lambda t: (parse_due(t.get("due_at")) is None, str(t.get("due_at") or "")))
        my_tickets = [t for t in db.list_tickets(claimed_by_id=uid, limit=50) if t.get("status") != "closed"]
        unread = [n for n in db.notifications(uid, 20) if not n.get("read_at")][:5]
        meeting = ws.load_meetings()
        my_rsvp = (meeting.get("rsvps", {}).get(uid) or {}).get("status")

        member = ctx.member
        name = (member.display_name if member else None) or ctx.user.get("global_name") or ctx.user.get("username") or "Team"
        role = _top_team_role(member, ctx.config.get("team_role_ids", [])) if member else None

        # ---- Hero mit Schichtsteuerung ----
        status_label = {"online": "Im Dienst", "break": "Pause", "offline": "Nicht im Dienst"}.get(status, "Nicht im Dienst")
        if status == "online":
            buttons = shift_button("break", "⏸ Pause", "ghost") + shift_button("end", "■ Schicht beenden", "ghost")
        elif status == "break":
            buttons = shift_button("resume", "▶ Fortsetzen", "primary") + shift_button("end", "■ Schicht beenden", "ghost")
        else:
            buttons = shift_button("start", "▶ Schicht starten", "primary")
        hero = f'''
        <div class="pulse-hero"><div class="pulse-hero-grid">
          <div><div class="pulse-kicker">Mein Pulse · {e(now.strftime("%d.%m.%Y"))}</div>
            <div class="pulse-title">{e(greeting(now.hour))}, {e(name)}</div>
            <div class="pulse-sub">{e(role.name) if role else "Teammitglied"} · {status_label}{" · Abmeldung bis " + e(ws.fmt_date(loa.get("bis"))) if loa_active else ""}</div>
            <div class="flex flex-wrap gap-2 mt-4">{buttons}</div></div>
          <div class="pulse-hero-box"><div class="label">Aktuelle Schicht</div><div class="value pulse-time" id="meShiftTimer">{format_hm(elapsed)}</div>
            <div class="label mt-2">Diese Woche: {format_hm(weekly)} von {goal:g} h</div></div>
        </div></div>'''

        # ---- Kennzahlen ----
        stats = [
            ("◷", "Woche", format_hm(weekly)),
            ("◎", "Wochenziel", f"{pct} %"),
            ("🔥", "Serie", f"{streak} {'Tag' if streak == 1 else 'Tage'}"),
            ("▣", "Meine Tickets", str(len(my_tickets))),
            ("✓", "Offene Aufgaben", str(len(tasks))),
            ("⚠", "Aktive Warnungen", f"{len(warns)}/5"),
        ]
        stat_html = "".join(
            f'<div class="pulse-stat"><div class="icon">{icon}</div><div class="label">{label}</div><div class="value">{e(value)}</div></div>'
            for icon, label, value in stats
        )

        # ---- 7-Tage-Verlauf ----
        daily_goal = goal * 3600 / 7 if goal else 0
        peak = max([s for _, s in days] + [daily_goal, 1])
        bars = "".join(
            f'<div class="flex-1 flex flex-col items-center gap-1" title="{e(day.strftime("%d.%m."))}: {format_hm(sec)}">'
            f'<div class="text-[9px] text-slate-400 pulse-time">{e(format_hm(sec)) if sec else "–"}</div>'
            f'<div class="w-full rounded-lg bg-slate-100 dark:bg-slate-800/60 flex items-end" style="height:110px">'
            f'<div class="w-full rounded-lg" style="height:{max(3, int(sec * 100 / peak)) if sec else 0}%;background:linear-gradient(180deg,var(--p),var(--p2))"></div></div>'
            f'<div class="text-[10px] font-semibold {"text-indigo-500" if day == today else "text-slate-500"}">{WEEKDAY_SHORT[day.weekday()]}</div></div>'
            for day, sec in days
        )
        goal_text = (
            f"Noch {format_hm(remaining)} bis zum Wochenziel." if remaining else "Wochenziel erreicht – stark! 🎉"
        )
        progress = (
            f'<div class="flex justify-between text-[11px] mb-2"><span>{goal_text}</span><b>{pct} %</b></div>'
            f'<div class="pulse-progress"><span style="width:{pct}%"></span></div>'
            f'<div class="flex gap-2 mt-5">{bars}</div>'
        )

        # ---- Aufgaben ----
        def task_row(t):
            due = parse_due(t.get("due_at"))
            state = due_state(due, now)
            kind, label = DUE_PILL.get(state, ("", "Ohne Deadline"))
            due_text = ws.fmt_date(str(t.get("due_at"))[:10]) if due else ""
            return (
                f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(t.get("title"))}</div>'
                f'<div class="pulse-row-meta">{e(due_text)}{" · " if due_text else ""}{e(t.get("priority") or "normal")}</div></div>'
                f'<div class="flex items-center gap-2"><span class="pulse-pill {kind}">{label}</span>'
                f'<form action="/tasks/update" method="post"><input type="hidden" name="task_id" value="{e(t.get("id"))}">'
                f'<input type="hidden" name="status" value="done"><button class="pulse-btn good" title="Als erledigt markieren">✓</button></form></div></div>'
            )
        task_html = "".join(task_row(t) for t in tasks[:6]) or '<div class="pulse-empty">Keine offenen Aufgaben – alles erledigt.</div>'
        if len(tasks) > 6:
            task_html += f'<a href="/tasks" class="block text-center text-[11px] text-indigo-500 mt-2">Alle {len(tasks)} Aufgaben ansehen →</a>'

        # ---- Mein Status ----
        warn_rows = "".join(
            f'<div class="pulse-alert"><strong>⚠ {e(w.get("reason") or "Ohne Grund")}</strong>'
            f'<div class="text-slate-400 mt-1">{e(w.get("date") or "")}'
            f'{" · läuft ab " + e(str(w.get("expires_at"))[:10]) if w.get("expires_at") else ""}</div></div>'
            for w in warns
        ) or '<div class="pulse-alert"><strong>✓ Keine aktiven Warnungen</strong><div class="text-slate-400 mt-1">Weiter so!</div></div>'
        loa_html = (
            f'<div class="pulse-alert mt-2"><strong>☾ Abgemeldet bis {e(ws.fmt_date(loa.get("bis")))}</strong>'
            f'<div class="text-slate-400 mt-1">{e(loa.get("grund") or "")}</div></div>'
            if loa_active else
            '<a href="/loa" class="pulse-alert mt-2 block"><strong>☾ Keine aktive Abmeldung</strong><div class="text-slate-400 mt-1">Abwesend? Hier abmelden →</div></a>'
        )

        # ---- Meeting ----
        rsvp_text = {"accepted": "✓ Zugesagt", "declined": "✕ Abgesagt"}.get(my_rsvp, "Noch keine Rückmeldung")
        rsvp_forms = "".join(
            f'<form action="/action" method="post"><input type="hidden" name="action" value="meeting_rsvp">'
            f'<input type="hidden" name="rsvp_status" value="{value}"><button class="pulse-btn {kind}">{label}</button></form>'
            for value, label, kind in (("accepted", "Zusagen", "good"), ("declined", "Absagen", "ghost"))
            if value != my_rsvp
        )
        meeting_html = (
            f'<div class="pulse-row-title">{e(meeting.get("title"))}</div>'
            f'<div class="pulse-row-meta mt-1">🗓 {e(meeting.get("date_time"))}</div>'
            f'<div class="flex flex-wrap items-center gap-2 mt-3"><span class="pulse-pill">{rsvp_text}</span>{rsvp_forms}</div>'
        )

        # ---- Inbox ----
        inbox_html = "".join(
            f'<a href="{e(n.get("url") or "/pulse-inbox") if str(n.get("url") or "").startswith("/") else "/pulse-inbox"}" class="pulse-row">'
            f'<div class="pulse-row-main"><div class="pulse-row-title">{e(n.get("title"))}</div><div class="pulse-row-meta">{e(n.get("body"))}</div></div></a>'
            for n in unread
        ) or '<div class="pulse-empty">Keine ungelesenen Benachrichtigungen.</div>'

        # ---- Wartet auf dich (Führung) ----
        waiting = ""
        if perms.get("can_manage_applications") or perms.get("can_promote") or perms.get("is_admin"):
            apps = sum(1 for a in ws.load_json(ws.APPS_FILE, {}).values() if isinstance(a, dict) and a.get("status") == "pending")
            if apps:
                waiting += f'<a href="/applications" class="pulse-alert block"><strong>✎ {apps} offene Bewerbungen</strong></a>'
        if perms.get("can_promote") or perms.get("is_admin"):
            promos = len(db.promotion_requests("pending", 200))
            if promos:
                waiting += f'<a href="/promotions" class="pulse-alert block"><strong>⇧ {promos} Beförderungsanträge</strong></a>'
        if perms.get("can_manage_tickets") or perms.get("can_promote") or perms.get("is_admin"):
            unclaimed = sum(1 for t in db.list_tickets(limit=300) if t.get("status") == "open" and not t.get("claimed_by_id"))
            if unclaimed:
                waiting += f'<a href="/tickets" class="pulse-alert block"><strong>▣ {unclaimed} Tickets ohne Bearbeiter</strong></a>'
        # ---- Meine Tickets: who is waiting on whom, with the reason (same rules as /tickets) ----
        from pulse_ticket_design import automation_settings
        from pulse_ticket_triage import triage
        ticket_settings = automation_settings(ws.pulse_ticket_config().get("automation"))
        triaged = sorted(((t, triage(t, now, ticket_settings)) for t in my_tickets), key=lambda pair: pair[1]["sort_key"])
        ticket_rows = "".join(
            f'<div class="pulse-row" style="flex-wrap:wrap"><div class="pulse-row-main" style="flex:1;min-width:160px">'
            f'<div class="pulse-row-title">{e(t.get("user_name") or "Ticket")}</div>'
            f'<div class="pulse-row-meta {"text-rose-500" if info["overdue"] else ""}">{e(info["reason"])}</div>'
            f'<div class="pulse-row-meta text-indigo-500">→ {e(info["next_step"])}</div></div>'
            + (f'<a class="pulse-btn ghost" target="_blank" rel="noopener" href="https://discord.com/channels/{ctx.guild.id}/{e(t.get("channel_id"))}">Öffnen ↗</a>'
               if str(t.get("channel_id") or "").isdigit() else "")
            + "</div>"
            for t, info in triaged[:5]
        ) or '<div class="pulse-empty">Du bearbeitest gerade keine Tickets.</div>'
        tickets_card = card("🎫 Meine Tickets", ticket_rows, '<a href="/tickets?status=mine" class="text-[11px] text-indigo-500">Alle →</a>')

        cockpit_link = (
            '<a href="/cockpit" class="text-[11px] text-indigo-500">Cockpit →</a>'
            if perms.get("can_promote") or perms.get("is_admin") else ""
        )
        waiting_card = card("⏳ Wartet auf dich", waiting, cockpit_link) if waiting else ""

        # U-02: personal start page after login and on "/"
        current_start = ws.start_page_for(ctx.user["id"])
        start_options = "".join(f'<option value="{e(path)}"{" selected" if path == current_start else ""}>{e(label)}</option>'
                                for path, label in ws.START_PAGES.items())
        start_page_form = (f'<form method="post" action="/me/start-page" class="flex flex-wrap gap-2 items-center">'
                           f'<label class="sr-only" for="startPage">Startseite</label>'
                           f'<select id="startPage" name="start_page" class="pulse-input flex-1">{start_options}</select>'
                           f'<button class="pulse-btn ghost" type="submit">Speichern</button></form>'
                           f'<p class="text-[11px] text-slate-400 mt-2">Diese Seite öffnet sich nach dem Login.</p>'
                           f'<a href="/me/sessions" class="text-[11px] text-indigo-500 mt-2 inline-block">🔐 Meine Sitzungen verwalten →</a>'
                           + (' <a href="/me/month" class="text-[11px] text-indigo-500 mt-2 inline-block">📅 Mein Monat →</a>'
                              if pulse_flags.enabled("my_month") else ""))
        body = f'''
        {hero}
        <div class="pulse-stat-grid">{stat_html}</div>
        <div class="pulse-grid">
          <div class="space-y-5">
            {card("📈 Meine Woche", progress, '<a href="/stats" class="text-[11px] text-indigo-500">Analytics →</a>')}
            {tickets_card}
            {card("📋 Meine Aufgaben", task_html, '<a href="/tasks" class="text-[11px] text-indigo-500">Alle →</a>')}
          </div>
          <div class="space-y-5">
            {waiting_card}
            {card("🛡 Mein Status", warn_rows + loa_html)}
            {card("🎙 Nächste Besprechung", meeting_html, '<a href="/meetings" class="text-[11px] text-indigo-500">Details →</a>')}
            {card("📥 Neu für mich", inbox_html, '<a href="/pulse-inbox" class="text-[11px] text-indigo-500">Inbox →</a>')}
            {card("🏠 Meine Startseite", start_page_form)}
          </div>
        </div>'''
        head = (
            "<script>document.addEventListener('DOMContentLoaded',()=>{const el=document.getElementById('meShiftTimer');"
            f"if(!el)return;const base={int(elapsed)};const running={'true' if status == 'online' else 'false'};const t0=Date.now();"
            "function tick(){const s=base+(running?Math.floor((Date.now()-t0)/1000):0);"
            "el.textContent=Math.floor(s/3600)+'h '+String(Math.floor((s%3600)/60)).padStart(2,'0')+'m';}"
            "tick();if(running)setInterval(tick,15000);});</script>"
        )
        return render_pro_page(ws, "Mein Pulse", ctx, "me", body, head)

    @app.post("/me/start-page")
    async def my_start_page(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        form = await request.form()
        choice = str(form.get("start_page") or "")
        if choice not in ws.START_PAGES:
            return ws.back("/me", "Diese Startseite gibt es nicht.", False)
        db.set_setting(f"start_page:{ctx.user['id']}", choice)
        return ws.back("/me", f"Startseite gespeichert: {ws.START_PAGES[choice]}.", True)

    @app.get("/me/sessions", response_class=HTMLResponse)
    async def my_sessions(request: Request, user_session: str = Cookie(None)):
        """U-14: own dashboard sessions with device and last activity."""
        import pulse_sessions
        from datetime import datetime, timezone
        ctx = ws.auth(request, user_session)
        current = (ws.verify_session_token(user_session, ws.TOKEN_DASHBOARD) or {}).get("sid")
        iso = lambda ts: datetime.fromtimestamp(float(ts), timezone.utc).isoformat(timespec="seconds")
        rows = "".join(
            f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(s["device"])}'
            f'{" · <b>dieses Gerät</b>" if s["sid"] == current else ""}</div><div class="pulse-row-meta">angemeldet {ws.time_tag(iso(s["created_at"]))}'
            f' · zuletzt aktiv {ws.time_tag(iso(s["last_seen"]))}</div></div>'
            + ("" if s["sid"] == current else
               f'<form method="post" action="/me/sessions/revoke"><input type="hidden" name="sid" value="{e(s["sid"])}">'
               f'<button class="pulse-btn ghost" type="submit">Abmelden</button></form>')
            + "</div>"
            for s in pulse_sessions.list_for(ctx.user["id"])
        ) or ws.empty_state("Keine gespeicherten Sitzungen – du bist mit einem älteren Login angemeldet.", "🔐")
        idle = pulse_sessions.IDLE_SECONDS // 3600
        body = (f'<div class="pulse-topbar"><div><div class="pulse-section-title">🔐 Meine Sitzungen</div>'
                f'<div class="pulse-section-sub">Geräte, auf denen du im Dashboard angemeldet bist. Nach {idle} h ohne Aktivität endet eine Sitzung automatisch.</div></div>'
                f'<a href="/me" class="pulse-btn ghost whitespace-nowrap">← Mein Pulse</a></div>'
                f'<section class="pulse-card"><div class="pulse-card-b space-y-2">{rows}</div></section>'
                f'<section class="pulse-card mt-4"><div class="pulse-card-b flex flex-wrap gap-2">'
                f'<form method="post" action="/me/sessions/revoke-others"><button class="pulse-btn ghost" type="submit">Alle anderen Geräte abmelden</button></form>'
                f'<form method="post" action="/me/sessions/revoke-all" onsubmit="return confirm(\'Wirklich überall abmelden – auch hier?\')">'
                f'<button class="pulse-btn bad" type="submit">Überall abmelden</button></form></div></section>')
        return render_pro_page(ws, "Meine Sitzungen", ctx, "me", body)

    @app.post("/me/sessions/revoke")
    async def my_session_revoke(request: Request, user_session: str = Cookie(None)):
        import pulse_sessions
        ctx = ws.auth(request, user_session)
        sid = str((await request.form()).get("sid") or "")
        if not pulse_sessions.revoke(ctx.user["id"], sid):
            return ws.back("/me/sessions", "Diese Sitzung gibt es nicht (mehr).", False)
        return ws.back("/me/sessions", "Gerät abgemeldet.", True)

    @app.post("/me/sessions/revoke-others")
    async def my_session_revoke_others(request: Request, user_session: str = Cookie(None)):
        import pulse_sessions
        ctx = ws.auth(request, user_session)
        current = (ws.verify_session_token(user_session, ws.TOKEN_DASHBOARD) or {}).get("sid")
        if not current:
            return ws.back("/me/sessions", "Dein Login ist älter als die Sitzungsliste – bitte „Überall abmelden“ nutzen.", False)
        count = pulse_sessions.revoke_all(ctx.user["id"], reason="andere Geräte abgemeldet", keep_sid=current)
        return ws.back("/me/sessions", f"{count} andere Sitzung(en) abgemeldet.", True)

    @app.post("/me/sessions/revoke-all")
    async def my_session_revoke_all(request: Request, user_session: str = Cookie(None)):
        import pulse_sessions
        ctx = ws.auth(request, user_session)
        pulse_sessions.revoke_all(ctx.user["id"])
        ws.log_audit(ctx.user.get("global_name"), ctx.user["id"], "Überall abgemeldet", "alle Dashboard-Sitzungen beendet")
        response = RedirectResponse(url="/", status_code=303)
        response.delete_cookie(key="user_session")
        return response
