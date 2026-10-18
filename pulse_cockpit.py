"""Leitungs-Cockpit: early-warning signals, week trends and one-click actions for leadership."""
from __future__ import annotations

from fastapi import Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse

import pulse_db as db
from pulse_cockpit_utils import (
    INACTIVE_AFTER_DAYS,
    LONG_SHIFT_SECONDS,
    inactive_days,
    is_inactive,
    last_shift_dates,
    seconds_per_member,
    trend,
    week_to_date_windows,
)
from pulse_home_utils import format_hm


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page

    e = ws.esc

    def _parse_iso(value):
        # parse_dt attaches the panel timezone to legacy naive timestamps, so
        # subtracting from the timezone-aware "now" never raises.
        if not value:
            return None
        try:
            return ws.parse_dt(str(value))
        except (TypeError, ValueError):
            return None

    def card(title, inner, action=""):
        return (
            f'<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">{title}</div>{action}</div>'
            f'<div class="pulse-card-b">{inner}</div></section>'
        )

    @app.get("/cockpit", response_class=HTMLResponse)
    async def leadership_cockpit(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None)
        if not (ctx.perms.get("can_promote") or ctx.perms.get("is_admin")):
            raise HTTPException(status_code=403, detail="Das Cockpit ist nur für die Führungsebene.")
        now = ws.now_de()
        today = now.date()
        guild = ctx.guild
        team_ids = {int(x) for x in ctx.config.get("team_role_ids", []) if str(x).isdigit()}
        members = sorted(
            (m for m in guild.members if not m.bot and any(r.id in team_ids for r in m.roles)),
            key=lambda m: m.display_name.lower(),
        )

        shifts = ws.load_shifts()
        active = shifts.get("active_shifts", {})
        history = shifts.get("history", [])
        (this_start, this_end), (prev_start, prev_end) = week_to_date_windows(today)
        this_week = seconds_per_member(history, this_start, this_end)
        prev_week = seconds_per_member(history, prev_start, prev_end)
        for uid, shift in active.items():
            this_week[str(uid)] = this_week.get(str(uid), 0) + ws.shift_elapsed(shift)
        last_dates = last_shift_dates(history)
        for uid in active:
            last_dates[str(uid)] = today
        loas = ws.get_loas()
        team_db = ws.load_json(ws.DATA_FILE, {})
        goal = float(ctx.config.get("weekly_goal_hours", 3.0) or 0)
        member_ids = {str(m.id) for m in members}

        def warn_count(uid):
            entry = team_db.get(uid)
            return len(ws.active_warns(dict(entry))) if isinstance(entry, dict) else 0

        def role_name(m):
            roles = [r for r in m.roles if r.id in team_ids]
            return max(roles, key=lambda r: r.position).name if roles else "Team"

        # ---- KPIs mit Vorwochenvergleich ----
        team_this = sum(v for k, v in this_week.items() if k in member_ids)
        team_prev = sum(v for k, v in prev_week.items() if k in member_ids)
        active_this = sum(1 for k, v in this_week.items() if k in member_ids and v > 0)
        active_prev = sum(1 for k, v in prev_week.items() if k in member_ids and v > 0)
        tickets = db.list_tickets(limit=3000)

        def closed_between(start, end):
            count = 0
            for t in tickets:
                closed = _parse_iso(t.get("closed_at"))
                if closed and start <= closed.date() <= end:
                    count += 1
            return count

        closed_this, closed_prev = closed_between(this_start, this_end), closed_between(prev_start, prev_end)
        apps = [a for a in ws.load_json(ws.APPS_FILE, {}).values() if isinstance(a, dict) and a.get("status") == "pending"]
        promos = db.promotion_requests("pending", 200)
        unclaimed = [t for t in tickets if t.get("status") == "open" and not t.get("claimed_by_id")]

        def kpi(icon, label, value, cur=None, prev=None):
            extra = ""
            if cur is not None:
                arrow, text = trend(cur, prev)
                color = "text-emerald-500" if arrow == "↑" else "text-rose-500" if arrow == "↓" else "text-slate-400"
                extra = f'<div class="text-[10px] mt-1 {color}">{arrow} {e(text)} ggü. Vorwoche</div>'
            return f'<div class="pulse-stat"><div class="icon">{icon}</div><div class="label">{label}</div><div class="value">{e(value)}</div>{extra}</div>'

        kpis = "".join([
            kpi("◷", "Teamstunden (Woche)", format_hm(team_this), team_this, team_prev),
            kpi("◉", "Aktive Teamler", f"{active_this}/{len(members)}", active_this, active_prev),
            kpi("▣", "Tickets geschlossen", str(closed_this), closed_this, closed_prev),
            kpi("✎", "Offene Bewerbungen", str(len(apps))),
            kpi("⇧", "Beförderungsanträge", str(len(promos))),
            kpi("☾", "Abgemeldet", str(sum(1 for k, l in loas.items() if k in member_ids and l.get("active")))),
        ])

        # ---- Handlungsbedarf ----
        signals = []  # (severity 0=kritisch, html)
        long_shifts = []
        for uid, shift in active.items():
            elapsed = ws.shift_elapsed(shift)
            started = _parse_iso(shift.get("started_at_iso"))
            age = (now - started).total_seconds() if started else elapsed
            if age >= LONG_SHIFT_SECONDS:
                long_shifts.append((uid, shift, age))
        for uid, shift, age in sorted(long_shifts, key=lambda x: -x[2]):
            signals.append((0, f"⏱ <b>{e(ws.display_of(guild, uid))}</b> ist seit {format_hm(age)} in einer Schicht – vergessen auszustempeln?"))
        critical_warns = [m for m in members if warn_count(str(m.id)) >= 4]
        for m in critical_warns:
            signals.append((0, f'⚠ <b>{e(m.display_name)}</b> hat {warn_count(str(m.id))}/5 aktive Warnungen. <a class="text-indigo-500" href="/member/{m.id}">Akte →</a>'))
        if unclaimed:
            oldest = min((_parse_iso(t.get("opened_at")) for t in unclaimed if _parse_iso(t.get("opened_at"))), default=None)
            wait = f" · ältestes seit {format_hm((now - oldest).total_seconds())}" if oldest else ""
            signals.append((1, f'▣ <b>{len(unclaimed)} Tickets</b> ohne Bearbeiter{e(wait)}. <a class="text-indigo-500" href="/tickets">Zu den Tickets →</a>'))
        if apps:
            signals.append((1, f'✎ <b>{len(apps)} Bewerbungen</b> warten auf Entscheidung. <a class="text-indigo-500" href="/applications">Bearbeiten →</a>'))
        if promos:
            signals.append((1, f'⇧ <b>{len(promos)} Beförderungsanträge</b> offen. <a class="text-indigo-500" href="/promotions">Prüfen →</a>'))
        inactive = []
        for m in members:
            uid = str(m.id)
            on_leave = bool(loas.get(uid, {}).get("active"))
            if is_inactive(last_dates.get(uid), today, on_leave):
                inactive.append((m, inactive_days(last_dates.get(uid), today)))
        if inactive:
            signals.append((2, f"💤 <b>{len(inactive)} Teamler</b> seit mindestens {INACTIVE_AFTER_DAYS} Tagen ohne Schicht (Abgemeldete ausgenommen)."))
        ending = [k for k, l in loas.items() if k in member_ids and l.get("active") and str(l.get("bis")) == today.isoformat()]
        if ending:
            signals.append((2, f'☾ <b>{len(ending)} Abmeldungen</b> enden heute. <a class="text-indigo-500" href="/loa">Übersicht →</a>'))
        signals.sort(key=lambda x: x[0])
        tone = {0: "border-rose-500/30 bg-rose-500/5", 1: "border-amber-500/30 bg-amber-500/5", 2: ""}
        signal_html = "".join(f'<div class="pulse-alert {tone[sev]}">{html}</div>' for sev, html in signals) or (
            '<div class="pulse-empty">✓ Kein Handlungsbedarf – das Team läuft rund.</div>'
        )

        # ---- Lange Schichten mit Ein-Klick-Beenden ----
        long_html = "".join(
            f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(ws.display_of(guild, uid))}</div>'
            f'<div class="pulse-row-meta">seit {format_hm(age)} · Status {e(shift.get("status"))}</div></div>'
            f'<form action="/shift/admin-end" method="post" onsubmit="return confirm(\'Schicht wirklich beenden?\')">'
            f'<input type="hidden" name="target_user_id" value="{e(uid)}"><input type="hidden" name="reason" value="Lange Schicht über Leitungs-Cockpit beendet">'
            f'<button class="pulse-btn bad">Schicht beenden</button></form></div>'
            for uid, shift, age in sorted(long_shifts, key=lambda x: -x[2])
        ) or '<div class="pulse-empty">Keine Schicht läuft länger als 12 Stunden.</div>'

        # ---- Inaktive Teamler ----
        inactive.sort(key=lambda x: (x[1] is not None, -(x[1] or 0)))
        inactive_html = "".join(
            f'<a href="/member/{m.id}" class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(m.display_name)}</div>'
            f'<div class="pulse-row-meta">{e(role_name(m))}</div></div>'
            f'<span class="pulse-pill warn">{"noch nie im Dienst" if days is None else str(days) + " Tage"}</span></a>'
            for m, days in inactive[:12]
        ) or '<div class="pulse-empty">Niemand ist inaktiv.</div>'
        if len(inactive) > 12:
            inactive_html += f'<div class="text-[11px] text-slate-400 mt-2 text-center">+ {len(inactive) - 12} weitere</div>'

        # ---- Wochenziel aller Teamler ----
        goal_seconds = goal * 3600
        ranked = sorted(members, key=lambda m: -this_week.get(str(m.id), 0))

        def goal_row(m, rank):
            uid = str(m.id)
            sec = this_week.get(uid, 0)
            pct = min(100, int(sec * 100 / goal_seconds)) if goal_seconds else 100
            badges = ""
            if uid in active:
                badges += '<span class="pulse-pill good">im Dienst</span>'
            if loas.get(uid, {}).get("active"):
                badges += '<span class="pulse-pill">abgemeldet</span>'
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}.")
            return (
                f'<a href="/member/{m.id}" class="pulse-row"><div class="w-7 text-center text-xs font-bold">{medal}</div>'
                f'<div class="pulse-row-main flex-1"><div class="flex justify-between gap-2"><div class="pulse-row-title truncate">{e(m.display_name)}</div>'
                f'<div class="text-[11px] pulse-time">{format_hm(sec)}</div></div>'
                f'<div class="pulse-progress mt-2"><span style="width:{pct}%"></span></div>'
                f'<div class="pulse-row-meta flex gap-2 mt-1 items-center">{e(role_name(m))} {badges}</div></div></a>'
            )
        goal_html = "".join(goal_row(m, i) for i, m in enumerate(ranked, start=1)) or '<div class="pulse-empty">Noch keine Teamrollen konfiguriert.</div>'
        reached = sum(1 for m in members if goal_seconds and this_week.get(str(m.id), 0) >= goal_seconds)

        body = f'''
        <div class="pulse-hero"><div class="pulse-hero-grid">
          <div><div class="pulse-kicker">Führungsebene · Woche ab {e(this_start.strftime("%d.%m."))}</div>
            <div class="pulse-title">Leitungs-Cockpit</div>
            <div class="pulse-sub">Frühwarnsignale, Trends gegenüber der Vorwoche und direkte Aktionen – alles, was heute Aufmerksamkeit braucht.</div></div>
          <div class="pulse-hero-box"><div class="label">Wochenziel erreicht</div><div class="value">{reached}/{len(members)}</div>
            <div class="label mt-2">Ziel: {goal:g} h pro Teamler</div></div>
        </div></div>
        <div class="pulse-stat-grid">{kpis}</div>
        <div class="pulse-grid">
          <div class="space-y-5">
            {card("🚨 Handlungsbedarf", signal_html)}
            {card("🏁 Wochenziel & Rangliste", goal_html, '<a href="/export/team.csv" class="text-[11px] text-indigo-500">CSV →</a>')}
          </div>
          <div class="space-y-5">
            {card("⏱ Lange Schichten", long_html)}
            {card("💤 Inaktive Teamler", inactive_html)}
          </div>
        </div>'''
        return render_pro_page(ws, "Leitungs-Cockpit", ctx, "cockpit", body)
