"""Phase 7 dashboard pages (F-01 … F-10). Every page answers 404 while its switch is off (pulse_flags).

/admin/features             switches + maintenance mode (F-04)                         admins
/admin/rights               F-01 "Wer darf was?" matrix and simulation                  admins
/admin/visibility           F-02 channels visible to unverified members (read-only)     admins
/admin/settings-history     F-03 settings history with diff and revert                  admins
/team/capacity              F-05 capacity heatmap                                       analytics / leadership
/team/readiness             F-06 promotion readiness (hint only)                        leadership
/applications/board         F-07 application board                                      application managers
/status, /status.json       F-08 public status (+ F-09 game status)                     everyone, no login
/me/month                   F-10 personal monthly review, DM opt-in                     every team member

All routes authorize server-side (webserver.auth); state-changing routes are POST and audited.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

from fastapi import Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

import pulse_db
import pulse_flags

PAGE_KEYS = frozenset({"admin-features", "admin-rights", "admin-visibility", "admin-settings-history",
                       "team-capacity", "team-readiness", "applications-board"})
MONTH_OPTIN_KEY = "my_month_dm_optin"
MONTH_SENT_KEY = "my_month_dm_sent"


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _actor(ctx) -> str:
    return ctx.user.get("global_name") or ctx.user.get("username") or "Team"


def sections(perms: dict) -> list[tuple]:
    """Administration-area entries of the switched-on functions (same tuple shape as pulse_admin)."""
    is_admin = bool(perms.get("is_admin"))
    lead = is_admin or bool(perms.get("can_promote"))
    out = [("admin-features", "/admin/features", "✚", "Neue Funktionen", "Phase-7-Funktionen ein- und ausschalten, Wartungsmodus", is_admin)]
    flags = pulse_flags.states()
    optional = [
        ("rights_check", ("admin-rights", "/admin/rights", "🔐", "Wer darf was?", "Rechte-Matrix und Simulation", is_admin)),
        ("visibility_check", ("admin-visibility", "/admin/visibility", "👁", "Kanal-Sichtprüfung", "Was sehen Unverifizierte?", is_admin)),
        ("settings_history", ("admin-settings-history", "/admin/settings-history", "🕘", "Einstellungs-Historie",
                              "Wer hat wann was geändert", is_admin)),
        ("capacity_heatmap", ("team-capacity", "/team/capacity", "▦", "Kapazität", "Aufkommen gegen Dienstzeiten",
                              lead or bool(perms.get("can_view_analytics")))),
        ("promotion_readiness", ("team-readiness", "/team/readiness", "⬆", "Beförderungs-Reife", "Hinweise nach Kriterien", lead)),
        ("application_board", ("applications-board", "/applications/board", "▤", "Bewerbungs-Board", "Bewerbungen als Spalten",
                               lead or bool(perms.get("can_manage_applications")))),
    ]
    out += [entry for flag, entry in optional if flags.get(flag)]
    return [s for s in out if s[5]]


def queue_month_reviews(ws, now: datetime | None = None) -> int:
    """F-10: on the first days of a month queue the previous month's review as DM for everyone who opted in.
    Runs on the existing automation cycle; once per month (MONTH_SENT_KEY); DMs go through the bounded,
    quiet-hours-aware notification queue (BOT-12)."""
    import pulse_insights
    import pulse_notify_prefs
    if not pulse_flags.enabled("my_month"):
        return 0
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(pulse_insights.BERLIN)
    month_key = local.strftime("%Y-%m")
    if local.day > 3 or pulse_db.get_setting(MONTH_SENT_KEY, "") == month_key:
        return 0
    optin = pulse_db.get_setting(MONTH_OPTIN_KEY, {}) or {}
    people = [uid for uid, on in optin.items() if on] if isinstance(optin, dict) else []
    pulse_db.set_setting(MONTH_SENT_KEY, month_key)  # first: a crash must not send twice
    if not people:
        return 0
    start, end = pulse_insights.month_bounds(now, previous=True)
    history = ws.load_shifts_raw().get("history", [])
    tickets = pulse_db.list_tickets(status="closed", limit=5000)
    attempts = pulse_db.attempts(limit=5000)
    queued = 0
    for uid in people[:pulse_notify_prefs.DM_QUEUE_MAX // 2]:
        review = pulse_insights.month_review(uid, start=start, end=end, shift_history=history, tickets=tickets,
                                             attempts=attempts, unlocked=_unlocked(uid))
        pulse_notify_prefs.queue_dm(uid, f"Dein {review['label']}", pulse_insights.month_text(review), "/me/month")
        queued += 1
    return queued


def _unlocked(user_id) -> list[dict]:
    with pulse_db.connect() as db:
        rows = db.execute("SELECT a.name, p.achievement_id, p.unlocked_at FROM achievement_progress p "
                          "LEFT JOIN achievements a ON a.id=p.achievement_id WHERE p.user_id=? AND p.unlocked_at IS NOT NULL",
                          (str(user_id),)).fetchall()
    return [dict(r) for r in rows]


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page

    def admin_ctx(request, session):
        return ws.auth(request, session, perm=None, admin=True)

    def page(title, ctx, key, crumb, body):
        crumb_html = f'<div class="pulse-crumb"><a href="/admin">Verwaltung</a> › {_e(crumb)}</div>'
        return HTMLResponse(render_pro_page(ws, title, ctx, key, crumb_html + body))

    def card(title, sub, inner):
        return (f'<section class="pulse-card mb-4"><div class="pulse-card-h"><div><div class="pulse-section-title">{title}</div>'
                f'<div class="pulse-section-sub">{sub}</div></div></div><div class="pulse-card-b">{inner}</div></section>')

    # ------------------------------------------------------------------ switches + F-04
    @app.get("/admin/features", response_class=HTMLResponse)
    async def features_page(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        states = pulse_flags.states()
        rows = []
        for key, (fid, label, desc) in pulse_flags.FEATURES.items():
            on = states[key]
            rows.append(
                f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{_e(fid)} · {_e(label)} '
                f'<span class="pulse-pill {"good" if on else ""}">{"an" if on else "aus"}</span></div>'
                f'<div class="pulse-row-meta">{_e(desc)}</div></div>'
                f'<form method="post" action="/admin/features/toggle"><input type="hidden" name="key" value="{_e(key)}">'
                f'<input type="hidden" name="on" value="{"0" if on else "1"}">'
                f'<button class="pulse-btn {"ghost" if on else ""}" type="submit">{"Ausschalten" if on else "Einschalten"}</button></form></div>')
        body = card("✚ Neue Funktionen (Phase 7)", "Alle Funktionen sind zunächst aus. Ausschalten löscht keine Daten.",
                    '<div class="space-y-2">' + "".join(rows) + "</div>")
        if states["maintenance"]:
            state = pulse_flags.maintenance()
            since = f' seit {ws.time_tag(state["since"])} von {_e(state["by"])}' if state["active"] else ""
            body += card("🛠 Wartungsmodus",
                         "Während der Wartung sehen alle ein Banner, nur Administratoren können speichern, und die Bot-Automationen "
                         "pausieren (Voice-Support läuft weiter). Slash-Befehle bleiben nutzbar.",
                         f'<p class="mb-2">Status: <strong>{"aktiv" if state["active"] else "aus"}</strong>{since}</p>'
                         '<form method="post" action="/admin/features/maintenance" class="flex flex-wrap gap-2 items-center">'
                         f'<input class="pulse-input" name="message" maxlength="300" placeholder="Hinweis für das Team (optional)" value="{_e(state["message"])}" style="max-width:420px">'
                         f'<input type="hidden" name="active" value="{"0" if state["active"] else "1"}">'
                         f'<button class="pulse-btn" type="submit">{"Wartung beenden" if state["active"] else "Wartung starten"}</button></form>')
        return page("Neue Funktionen", ctx, "admin-features", "Neue Funktionen", body)

    @app.post("/admin/features/toggle")
    async def features_toggle(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        key, on = str(form.get("key") or ""), form.get("on") == "1"
        try:
            pulse_flags.set_enabled(key, on)
        except ValueError as exc:
            return ws.back("/admin/features", str(exc), False)
        fid, label, _ = pulse_flags.FEATURES[key]
        ws.log_audit(_actor(ctx), ctx.user["id"], "Funktion " + ("eingeschaltet" if on else "ausgeschaltet"), f"{fid} {label}")
        return ws.back("/admin/features", f"{label} ist jetzt {'an' if on else 'aus'}.", True)

    @app.post("/admin/features/maintenance")
    async def features_maintenance(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        pulse_flags.require("maintenance")
        form = await request.form()
        active = form.get("active") == "1"
        state = pulse_flags.set_maintenance(active, str(form.get("message") or ""), _actor(ctx))
        ws.log_audit(_actor(ctx), ctx.user["id"], "Wartungsmodus " + ("gestartet" if active else "beendet"), state["message"] or "–")
        return ws.back("/admin/features", "Wartungsmodus gestartet." if active else "Wartungsmodus beendet.", True)

    # ------------------------------------------------------------------ F-01
    @app.get("/admin/rights", response_class=HTMLResponse)
    async def rights_page(request: Request, user_id: str = "", user_session: str = Cookie(None)):
        import pulse_access
        ctx = admin_ctx(request, user_session)
        pulse_flags.require("rights_check")
        keys = list(pulse_access.LABELS)
        head = "".join(f'<th title="{_e(pulse_access.LABELS[k])}" style="writing-mode:vertical-rl;font-size:11px">{_e(pulse_access.LABELS[k])}</th>' for k in keys)
        rows = []
        for row in pulse_access.matrix(ctx.guild, ctx.config):
            m = row["member"]
            cells = "".join(
                f'<td style="text-align:center" title="{_e(" · ".join(row["grants"].get(k, [])) or "nicht erteilt")}">{"✅" if k in row["grants"] else "·"}</td>'
                for k in keys)
            rows.append(f'<tr><td><a href="/admin/rights?user_id={m.id}">{_e(m.display_name)}</a>{" 👑" if row["is_admin"] else ""}</td>{cells}</tr>')
        table = (f'<div style="overflow-x:auto"><table class="pulse-table"><thead><tr><th>Person</th>{head}</tr></thead>'
                 f'<tbody>{"".join(rows)}</tbody></table></div>' if rows else ws.empty_state("Keine Teammitglieder gefunden.", "🔐"))
        body = card("🔐 Wer darf was?", "Alle Teammitglieder mit ihren Dashboard-Rechten. Fahre über ein ✅, um die Herkunft zu sehen. Nur lesend.", table)
        member = ctx.guild.get_member(int(user_id)) if user_id.isdigit() else None
        if member is not None:
            team_ids = {int(x) for x in ctx.config.get("team_role_ids", []) if str(x).isdigit()}
            chosen = request.query_params.getlist("role")
            role_ids = [int(r) for r in chosen if r.isdigit()] if "simulate" in request.query_params else [r.id for r in member.roles if r.id in team_ids]
            options = "".join(
                f'<label class="flex items-center gap-2 text-sm"><input type="checkbox" name="role" value="{rid}"{" checked" if rid in role_ids else ""}> '
                f'{_e(getattr(ctx.guild.get_role(rid), "name", rid))}</label>' for rid in sorted(team_ids) if ctx.guild.get_role(rid))
            sim = pulse_access.simulate(ctx.guild, member, role_ids, ctx.config)
            result = "".join(
                f'<div class="pulse-row"><div class="pulse-row-title">{"✅" if k in sim["grants"] else "⛔"} {_e(pulse_access.LABELS[k])}</div>'
                f'<div class="pulse-row-meta">{_e(" · ".join(sim["grants"].get(k, [])) or "nicht erteilt")}</div></div>' for k in keys)
            body += card(f"🧪 Simulation: {_e(member.display_name)}",
                         "Welche Rechte hätte diese Person mit genau diesen Teamrollen? Es wird nichts in Discord geändert."
                         + (" Diese Person ist Administrator/Inhaber und hat immer alle Rechte." if sim["is_admin"] else ""),
                         f'<form method="get" action="/admin/rights" class="space-y-1 mb-3"><input type="hidden" name="user_id" value="{member.id}">'
                         f'<input type="hidden" name="simulate" value="1">{options}<button class="pulse-btn mt-2" type="submit">Simulieren</button></form>{result}')
        return page("Wer darf was?", ctx, "admin-rights", "Wer darf was?", body)

    # ------------------------------------------------------------------ F-02
    @app.get("/admin/visibility", response_class=HTMLResponse)
    async def visibility_page(request: Request, user_session: str = Cookie(None)):
        import pulse_access
        import pulse_verification
        ctx = admin_ctx(request, user_session)
        pulse_flags.require("visibility_check")
        expected = {int(x) for x in (pulse_db.get_setting(pulse_access.EXPECTED_KEY, []) or []) if str(x).isdigit()}
        result = pulse_access.visibility(ctx.guild, pulse_verification.UNVERIFIED_ROLE_ID, expected)

        def row(channel, warn):
            action = ("Als erwartet markieren" if warn else "Markierung entfernen")
            return (f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{"⚠️" if warn else "✅"} #{_e(channel.name)}</div>'
                    f'<div class="pulse-row-meta">{_e(getattr(getattr(channel, "category", None), "name", "") or "ohne Kategorie")}</div></div>'
                    f'<form method="post" action="/admin/visibility/expected"><input type="hidden" name="channel_id" value="{channel.id}">'
                    f'<input type="hidden" name="expected" value="{"1" if warn else "0"}"><button class="pulse-btn ghost" type="submit">{action}</button></form></div>')
        warn_ids = {c.id for c in result["warnings"]}
        note = "" if result["role_found"] else "<p class=\"mb-2\">⚠️ Die Rolle für Unverifizierte gibt es auf dem Server nicht – geprüft wird nur @everyone.</p>"
        body = card(f"👁 Kanal-Sichtprüfung · {len(result['warnings'])} Warnung(en)",
                    "Kanäle, die @everyone zusammen mit der Rolle „Unverifiziert“ sehen kann. Nur lesend – Pulse ändert keine Kanalrechte. "
                    "Kanäle, die sichtbar sein sollen (Regeln, Verifizierung), markierst du als erwartet.",
                    note + ('<div class="space-y-1">' + "".join(row(c, c.id in warn_ids) for c in result["visible"]) + "</div>"
                            if result["visible"] else ws.empty_state("Unverifizierte sehen keinen Kanal.", "👁")))
        return page("Kanal-Sichtprüfung", ctx, "admin-visibility", "Kanal-Sichtprüfung", body)

    @app.post("/admin/visibility/expected")
    async def visibility_expected(request: Request, user_session: str = Cookie(None)):
        import pulse_access
        ctx = admin_ctx(request, user_session)
        pulse_flags.require("visibility_check")
        form = await request.form()
        cid = str(form.get("channel_id") or "")
        if not cid.isdigit() or ctx.guild.get_channel(int(cid)) is None:
            return ws.back("/admin/visibility", "Unbekannter Kanal.", False)
        expected = {str(x) for x in (pulse_db.get_setting(pulse_access.EXPECTED_KEY, []) or [])}
        (expected.add if form.get("expected") == "1" else expected.discard)(cid)
        pulse_db.set_setting(pulse_access.EXPECTED_KEY, sorted(expected))
        ws.log_audit(_actor(ctx), ctx.user["id"], "Kanal-Sichtprüfung angepasst", f"Kanal {cid} " + ("erwartet" if form.get("expected") == "1" else "nicht erwartet"))
        return ws.back("/admin/visibility", "Gespeichert.", True)

    # ------------------------------------------------------------------ F-03
    @app.get("/admin/settings-history", response_class=HTMLResponse)
    async def history_page(request: Request, user_session: str = Cookie(None)):
        import pulse_settings_history as sh
        ctx = admin_ctx(request, user_session)
        pulse_flags.require("settings_history")
        items = []
        for entry in sh.entries(200):
            diff = "".join(
                f'<div style="font-family:monospace;font-size:12px;white-space:pre-wrap;color:{"#dc2626" if sign == "-" else ("#16a34a" if sign == "+" else "inherit")}">'
                f'{_e(sign)} {_e(line)}</div>' for sign, line in sh.diff_lines(entry["before_json"], entry["after_json"])[:60])
            revert = (f'<form method="post" action="/admin/settings-history/revert" onsubmit="return confirm(\'Diesen Stand wirklich wiederherstellen?\')">'
                      f'<input type="hidden" name="entry_id" value="{entry["id"]}"><button class="pulse-btn ghost" type="submit">↶ Zurücksetzen</button></form>'
                      if entry["revertible"] else '<span class="pulse-row-meta">nicht automatisch zurücksetzbar</span>')
            source = "config.json" if entry["source"] == "config" else "Einstellung"
            items.append(f'<details class="pulse-row" style="display:block"><summary><strong>{_e(entry["key"])}</strong> · {source} · '
                         f'{_e(entry["actor_name"])} · {ws.time_tag(entry["at"])}</summary><div class="mt-2">{diff or "<em>keine sichtbare Änderung</em>"}</div>'
                         f'<div class="mt-2">{revert}</div></details>')
        body = card("🕘 Einstellungs-Historie", "Änderungen aus dem Dashboard seit dem Einschalten (höchstens 1000). Geheimnisse werden nie gespeichert.",
                    '<div class="space-y-2">' + "".join(items) + "</div>" if items else ws.empty_state("Noch keine Änderungen aufgezeichnet.", "🕘"))
        return page("Einstellungs-Historie", ctx, "admin-settings-history", "Einstellungs-Historie", body)

    @app.post("/admin/settings-history/revert")
    async def history_revert(request: Request, user_session: str = Cookie(None)):
        import pulse_settings_history as sh
        ctx = admin_ctx(request, user_session)
        pulse_flags.require("settings_history")
        raw = str((await request.form()).get("entry_id") or "")
        if not raw.isdigit():
            return ws.back("/admin/settings-history", "Ungültiger Eintrag.", False)
        try:
            entry = sh.revert(int(raw), load_config=ws.load_config, save_config=lambda c: ws.save_json(ws.CONFIG_FILE, c))
        except ValueError as exc:
            return ws.back("/admin/settings-history", str(exc), False)
        ws.log_audit(_actor(ctx), ctx.user["id"], "Einstellung zurückgesetzt", f"{entry['key']} auf den Stand vor {entry['at']}")
        return ws.back("/admin/settings-history", f"„{entry['key']}“ wurde zurückgesetzt.", True)

    # ------------------------------------------------------------------ F-05
    @app.get("/team/capacity", response_class=HTMLResponse)
    async def capacity_page(request: Request, days: int = 28, user_session: str = Cookie(None)):
        import pulse_insights
        ctx = ws.auth(request, user_session, perm=None)
        pulse_flags.require("capacity_heatmap")
        if not (ctx.perms.get("is_admin") or ctx.perms.get("can_promote") or ctx.perms.get("can_view_analytics")):
            raise HTTPException(status_code=403, detail="Die Kapazitätsübersicht ist für Leitung und Analytics freigegeben.")
        with pulse_db.connect() as db:
            voice = [dict(r) for r in db.execute("SELECT created_at, support_started_at, closed_at FROM voice_support_case_history "
                                                 "ORDER BY closed_at DESC LIMIT 5000").fetchall()]
        data = pulse_insights.heatmap(pulse_db.list_tickets(limit=5000), voice, ws.load_shifts_raw().get("history", []),
                                      now=datetime.now(timezone.utc), days=days)
        peak = max([v for row in data["demand"] for v in row] + [1])

        def cell(d, h):
            demand, staff = data["demand"][d][h], data["staff"][d][h]
            alpha = min(1.0, demand / peak)
            outline = "outline:2px solid #ef4444;" if demand >= 1 and staff < 0.5 else ""
            return (f'<td title="{pulse_insights.WEEKDAYS[d]} {h:02d} Uhr: {demand:g} Anfragen/Woche · {staff:g} im Dienst" '
                    f'style="background:rgba(99,102,241,{alpha:.2f});{outline}text-align:center;font-size:10px;min-width:22px">{staff:.1f}</td>')
        head = "".join(f'<th style="font-size:10px">{h:02d}</th>' for h in range(24))
        rows = "".join(f'<tr><th>{pulse_insights.WEEKDAYS[d]}</th>{"".join(cell(d, h) for h in range(24))}</tr>' for d in range(7))
        hints = "".join(
            f'<li>{"🔴 Unbesetzt" if x["kind"] == "unbesetzt" else "🟠 Knapp"}: {pulse_insights.WEEKDAYS[x["day"]]} {x["hour"]:02d}–{(x["hour"] + 1) % 24:02d} Uhr '
            f'· {x["demand"]:g} Anfragen/Woche · {x["staff"]:g} im Dienst</li>' for x in data["hints"]) or "<li>Keine Auffälligkeiten.</li>"
        body = card("▦ Kapazitäts-Heatmap",
                    f"Farbe = Tickets und Voice-Fälle pro Woche, Zahl = durchschnittlich Personen im Dienst (Berlin-Zeit, letzte {data['weeks']:g} Wochen). "
                    "Rot umrandet: Anfragen ohne Dienst.",
                    f'<form method="get" class="mb-2">Zeitraum: <select name="days" class="pulse-input" style="width:auto" onchange="this.form.submit()">'
                    + "".join(f'<option value="{n}"{" selected" if n == days else ""}>{n} Tage</option>' for n in (14, 28, 56, 84))
                    + f'</select></form><div style="overflow-x:auto"><table class="pulse-table"><thead><tr><th></th>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
                    f'<div class="pulse-section-title mt-4">Hinweise für den Dienstplan</div><ul class="text-sm mt-1">{hints}</ul>')
        return page("Kapazität", ctx, "team-capacity", "Kapazität", body)

    # ------------------------------------------------------------------ F-06
    @app.get("/team/readiness", response_class=HTMLResponse)
    async def readiness_page(request: Request, user_session: str = Cookie(None)):
        import pulse_access
        import pulse_insights
        ctx = ws.auth(request, user_session, perm=None)
        pulse_flags.require("promotion_readiness")
        if not (ctx.perms.get("is_admin") or ctx.perms.get("can_promote")):
            raise HTTPException(status_code=403, detail="Beförderungs-Hinweise sieht nur die Leitung.")
        see_warns = bool(ctx.perms.get("is_admin") or ctx.perms.get("can_warn"))  # E-3
        criteria_all = pulse_insights.normalize_criteria(pulse_db.get_setting(pulse_insights.CRITERIA_KEY, {}))
        team_ids = [int(x) for x in ctx.config.get("team_role_ids", []) if str(x).isdigit()]
        now = datetime.now(timezone.utc)
        history = ws.load_shifts_raw().get("history", [])
        tickets = pulse_db.list_tickets(status="closed", limit=5000)
        attempts = pulse_db.attempts(limit=5000)
        team_db = ws.load_json(ws.DATA_FILE, {})
        cards = []
        for member in pulse_access.team_members(ctx.guild, ctx.config):
            top = max((r for r in member.roles if r.id in team_ids), key=lambda r: r.position, default=None)
            criteria = criteria_all.get(str(getattr(top, "id", "")), criteria_all["default"])
            entry = team_db.get(str(member.id)) if isinstance(team_db.get(str(member.id)), dict) else {}
            stats = pulse_insights.member_stats(member.id, now=now, shift_history=history, tickets=tickets, attempts=attempts,
                                                entry=entry, active_warns=ws.active_warns(dict(entry)) if entry else [])
            if not see_warns:
                stats = dict(stats, active_warns=0, warn_free_days=None)
            result = pulse_insights.readiness(stats, dict(criteria, warn_free_days=0) if not see_warns else criteria)
            checks = "".join(
                f'<div class="text-xs">{"✅" if ok else "▫"} {_e(label)}: {_e(value)} <span class="pulse-row-meta">(Ziel {_e(target)})</span></div>'
                for label, value, target, ok in result["checks"] if see_warns or label != "Warnfrei")
            cards.append((result["ready"], member.display_name.lower(),
                          f'<div class="pulse-row" style="display:block"><div class="pulse-row-title">{"🟢 Kriterien erfüllt" if result["ready"] else "⚪"} · '
                          f'<a href="/member/{member.id}">{_e(member.display_name)}</a> <span class="pulse-row-meta">{_e(getattr(top, "name", ""))}</span></div>{checks}</div>'))
        cards.sort(key=lambda c: (not c[0], c[1]))
        fields = "".join(
            f'<label class="text-xs">{_e(label)}<input class="pulse-input" name="{key}" value="{criteria_all["default"][key]:g}" inputmode="decimal"></label>'
            for key, (label, _) in pulse_insights.CRITERIA_FIELDS.items())
        role_opts = "".join(f'<option value="{rid}">{_e(getattr(ctx.guild.get_role(rid), "name", rid))}</option>' for rid in team_ids if ctx.guild.get_role(rid))
        body = card("⬆ Beförderungs-Reife",
                    "Nur ein Hinweis: entschieden wird immer von Menschen über den bestehenden Beförderungsablauf mit 4-Augen-Prinzip. "
                    "Pulse ändert hier keine Rollen und stellt keine Anträge." + ("" if see_warns else " Warnungen sind für dich nicht sichtbar und werden nicht berücksichtigt."),
                    '<div class="space-y-2">' + "".join(c[2] for c in cards) + "</div>" if cards else ws.empty_state("Keine Teammitglieder.", "⬆"))
        body += card("Kriterien", "Standard für alle Ränge; für einen Rang lassen sich eigene Werte speichern.",
                     f'<form method="post" action="/team/readiness/criteria" class="grid gap-2" style="grid-template-columns:repeat(auto-fill,minmax(180px,1fr))">'
                     f'<label class="text-xs">Rang<select class="pulse-input" name="rank"><option value="default">Standard (alle Ränge)</option>{role_opts}</select></label>'
                     f'{fields}<button class="pulse-btn" type="submit">Speichern</button></form>')
        return page("Beförderungs-Reife", ctx, "team-readiness", "Beförderungs-Reife", body)

    @app.post("/team/readiness/criteria")
    async def readiness_criteria(request: Request, user_session: str = Cookie(None)):
        import pulse_insights
        ctx = ws.auth(request, user_session, perm=None)
        pulse_flags.require("promotion_readiness")
        if not (ctx.perms.get("is_admin") or ctx.perms.get("can_promote")):
            raise HTTPException(status_code=403, detail="Kriterien ändert nur die Leitung.")
        form = await request.form()
        rank = str(form.get("rank") or "default")
        if rank != "default" and not (rank.isdigit() and rank in {str(x) for x in ctx.config.get("team_role_ids", [])}):
            return ws.back("/team/readiness", "Unbekannter Rang.", False)
        stored = pulse_insights.normalize_criteria(pulse_db.get_setting(pulse_insights.CRITERIA_KEY, {}))
        stored.update(pulse_insights.normalize_criteria({rank: {k: form.get(k) for k in pulse_insights.CRITERIA_FIELDS}}))
        pulse_db.set_setting(pulse_insights.CRITERIA_KEY, stored)
        ws.log_audit(_actor(ctx), ctx.user["id"], "Beförderungs-Kriterien geändert", rank)
        return ws.back("/team/readiness", "Kriterien gespeichert.", True)

    # ------------------------------------------------------------------ F-07
    @app.get("/applications/board", response_class=HTMLResponse)
    async def board_page(request: Request, user_session: str = Cookie(None)):
        import pulse_board
        ctx = ws.auth(request, user_session, perm=None)
        pulse_flags.require("application_board")
        if not (ctx.perms.get("is_admin") or ctx.perms.get("can_promote") or ctx.perms.get("can_manage_applications")):
            raise HTTPException(status_code=403, detail="Das Bewerbungs-Board ist für Bewerbungsverantwortliche.")
        deadlines = pulse_board.normalize_deadlines(pulse_db.get_setting(pulse_board.DEADLINES_KEY, {}))
        columns = pulse_board.board(ws.load_json(ws.APPS_FILE, {}), ws.load_json(ws.DATA_FILE, {}), deadlines, datetime.now(timezone.utc))
        cols = []
        for key, label in pulse_board.STAGES:
            cards = "".join(
                f'<a href="/applications{"?view=history" if key in ("team", "abgelehnt", "probezeit") else ""}#app-{_e(c["id"])}" class="pulse-card" style="display:block;padding:10px;margin-bottom:8px;text-decoration:none'
                f'{";border-left:4px solid #ef4444" if c["overdue"] else ""}"><div class="text-sm font-bold">{_e(c["name"])}</div>'
                f'<div class="text-xs">{_e(c["career"])}</div><div class="text-xs pulse-row-meta">'
                f'{"seit " + str(c["days"]) + " Tagen" if c["days"] is not None else "Datum unbekannt"}'
                f'{" · Frist " + str(c["deadline"]) + " Tage überschritten" if c["overdue"] else ""}'
                f'{" · Termin " + _e(c["interview_at"].replace("T", " ")) if c["interview_at"] else ""}</div></a>'
                for c in columns[key])
            cols.append(f'<div style="min-width:220px;flex:1"><div class="pulse-section-title mb-2">{label} <span class="pulse-pill">{len(columns[key])}</span></div>'
                        f'{cards or "<div class=\"pulse-row-meta\">–</div>"}</div>')
        body = card("▤ Bewerbungs-Board",
                    "Spalten aus dem echten Bewerbungsstatus. Entscheiden, einladen und Termine planen geht wie bisher unter "
                    "<a href=\"/applications\">Bewerbungen</a> (dort auch die DM-Vorlagen). Rot = Frist überschritten.",
                    f'<div style="display:flex;gap:12px;overflow-x:auto">{"".join(cols)}</div>')
        fields = "".join(
            f'<label class="text-xs">{label} (Tage)<input class="pulse-input" name="{key}" value="{deadlines[key]}" inputmode="numeric"></label>'
            for key, label in (("eingang", "Eingang"), ("pruefung", "Prüfung"), ("gespraech", "Gespräch")))
        body += card("Fristen", "Nach wie vielen Tagen eine Bewerbung in der Spalte als überfällig gilt (0 = keine Frist).",
                     f'<form method="post" action="/applications/board/deadlines" class="flex flex-wrap gap-2 items-end">{fields}'
                     '<button class="pulse-btn" type="submit">Speichern</button></form>')
        return page("Bewerbungs-Board", ctx, "applications-board", "Bewerbungs-Board", body)

    @app.post("/applications/board/deadlines")
    async def board_deadlines(request: Request, user_session: str = Cookie(None)):
        import pulse_board
        ctx = ws.auth(request, user_session, perm=None)
        pulse_flags.require("application_board")
        if not (ctx.perms.get("is_admin") or ctx.perms.get("can_promote") or ctx.perms.get("can_manage_applications")):
            raise HTTPException(status_code=403, detail="Fristen ändern nur Bewerbungsverantwortliche.")
        form = await request.form()
        saved = pulse_board.normalize_deadlines({k: form.get(k) for k in pulse_board.DEFAULT_DEADLINES})
        pulse_db.set_setting(pulse_board.DEADLINES_KEY, saved)
        ws.log_audit(_actor(ctx), ctx.user["id"], "Bewerbungs-Fristen geändert", ", ".join(f"{k} {v}" for k, v in saved.items()))
        return ws.back("/applications/board", "Fristen gespeichert.", True)

    # ------------------------------------------------------------------ F-08 / F-09
    def public_data(request) -> dict:
        import pulse_public_status as ps
        bot = getattr(request.app.state, "bot", None)
        guild = bot.get_guild(ws.GUILD_ID) if bot else None
        if guild is None:
            raise HTTPException(status_code=503, detail="Status gerade nicht verfügbar.")

        def build():
            from pulse_pro import voice_waiting
            return ps.snapshot(guild, voice_cfg=pulse_db.get_setting("voice_support_config", {}) or {},
                               open_tickets=pulse_db.list_tickets(status="open", limit=2000), voice_waiting=voice_waiting(guild),
                               rp_status=pulse_db.get_setting("rp_status", {}), now=datetime.now(timezone.utc))
        return dict(ps.cached(build))

    @app.get("/status.json")
    async def public_status_json(request: Request):
        import pulse_public_status as ps
        pulse_flags.require("public_status")
        data = public_data(request)
        data["game"] = await ps.game_status(ws._roblox_http_client)
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=30"})

    @app.get("/status", response_class=HTMLResponse)
    async def public_status_page(request: Request):
        import pulse_public_status as ps
        pulse_flags.require("public_status")
        data = public_data(request)
        game = await ps.game_status(ws._roblox_http_client)

        def tile(label, value, good=None):
            color = "#16a34a" if good is True else ("#dc2626" if good is False else "#4f46e5")
            return (f'<div style="background:#fff;border-radius:16px;padding:18px;box-shadow:0 1px 3px rgba(0,0,0,.08)">'
                    f'<div style="font-size:12px;color:#64748b">{_e(label)}</div><div style="font-size:24px;font-weight:800;color:{color}">{_e(value)}</div></div>')
        support = "geschlossen" if not data["support_active"] else ("geöffnet" if data["support_open"] else "außerhalb der Zeiten")
        rp = {"start": "läuft", "stop": "beendet"}.get(data["rp_state"], "unbekannt")
        if data["rp_state"] == "stop" and data["rp_next_start"]:
            rp += f" · nächster Start {data['rp_next_start']}"
        tiles = [tile("Support", support, data["support_active"] and data["support_open"]),
                 tile("Tickets in der Warteschlange", data["queue_tickets"]), tile("Im Voice-Warteraum", data["queue_voice"]),
                 tile("Roleplay", rp, {"start": True, "stop": False}.get(data["rp_state"]))]
        if game is not None:
            tiles.append(tile(f"Spiel {game['name']}".strip(), f"{game['playing']} / {game['max_players'] or '–'} online"))
        elif ps.universe_id() and pulse_flags.enabled("game_status"):
            tiles.append(tile("Spiel", "nicht erreichbar", False))
        html_page = (f'<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                     f'<meta http-equiv="refresh" content="60"><title>Status · {_e(data["server"])}</title></head>'
                     f'<body style="margin:0;font-family:system-ui,sans-serif;background:#f1f5f9;color:#0f172a">'
                     f'<main style="max-width:880px;margin:0 auto;padding:24px 16px"><h1 style="font-size:22px">Status · {_e(data["server"])}</h1>'
                     f'<div style="display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(200px,1fr))">{"".join(tiles)}</div>'
                     f'<p style="font-size:12px;color:#64748b;margin-top:16px">Aktualisiert sich jede Minute · ohne personenbezogene Daten</p></main></body></html>')
        return HTMLResponse(html_page, headers={"Cache-Control": "public, max-age=30"})

    # ------------------------------------------------------------------ F-10
    @app.get("/me/month", response_class=HTMLResponse)
    async def my_month_page(request: Request, user_session: str = Cookie(None)):
        import pulse_insights
        ctx = ws.auth(request, user_session)
        pulse_flags.require("my_month")
        uid = str(ctx.user["id"])
        now = datetime.now(timezone.utc)
        history = ws.load_shifts_raw().get("history", [])
        tickets = pulse_db.list_tickets(status="closed", limit=5000)
        attempts = pulse_db.attempts(user_id=uid, limit=1000)
        unlocked = _unlocked(uid)
        blocks = []
        for previous in (False, True):
            start, end = pulse_insights.month_bounds(now, previous=previous)
            r = pulse_insights.month_review(uid, start=start, end=end, shift_history=history, tickets=tickets, attempts=attempts, unlocked=unlocked)
            rating = "–" if r["rating_avg"] is None else f'{r["rating_avg"]:g} ★ ({r["ratings"]})'
            stats = "".join(f'<div class="pulse-row"><div class="pulse-row-title">{_e(a)}</div><div class="pulse-row-meta">{_e(b)}</div></div>' for a, b in (
                ("⏱ Dienstzeit", f'{r["hours"]:g} h in {r["shifts"]} Schichten'), ("🎫 Tickets abgeschlossen", r["tickets"]),
                ("★ Ø Bewertung", rating), ("🎓 Schulungen bestanden", r["trainings"]),
                ("🏅 Neue Erfolge", ", ".join(map(str, r["achievements"])) or "–")))
            blocks.append(card(("Bisher im " if not previous else "") + _e(r["label"]), "laufender Monat" if not previous else "Vormonat", stats))
        optin = pulse_db.get_setting(MONTH_OPTIN_KEY, {}) or {}
        on = bool(isinstance(optin, dict) and optin.get(uid))
        blocks.append(card("📬 Rückblick per DM", "Am Monatsanfang schickt Pulse dir deinen Vormonat als Discord-DM (Ruhezeiten werden beachtet).",
                           f'<form method="post" action="/me/month/optin"><input type="hidden" name="on" value="{"0" if on else "1"}">'
                           f'<button class="pulse-btn" type="submit">{"DM abbestellen" if on else "DM am Monatsanfang erhalten"}</button></form>'))
        body = '<div class="pulse-crumb"><a href="/me">Mein Pulse</a> › Mein Monat</div>' + "".join(blocks)
        return HTMLResponse(render_pro_page(ws, "Mein Monat", ctx, "me", body))

    @app.post("/me/month/optin")
    async def my_month_optin(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        pulse_flags.require("my_month")
        on = (await request.form()).get("on") == "1"
        optin = pulse_db.get_setting(MONTH_OPTIN_KEY, {}) or {}
        optin = dict(optin) if isinstance(optin, dict) else {}
        if on:
            optin[str(ctx.user["id"])] = True
        else:
            optin.pop(str(ctx.user["id"]), None)
        pulse_db.set_setting(MONTH_OPTIN_KEY, optin)
        return ws.back("/me/month", "Du bekommst deinen Monatsrückblick per DM." if on else "Monats-DM abbestellt.", True)
