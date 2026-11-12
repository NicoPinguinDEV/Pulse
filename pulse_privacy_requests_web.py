"""Audit #171: dashboard pages for data-subject requests (admins only, deny by default, every write audited)."""
import asyncio
from datetime import datetime, timezone

from fastapi import Cookie, Request
from fastapi.responses import HTMLResponse

import pulse_privacy_requests as dsr

BASE = "/admin/privacy/requests"
HINTS = {
    "access": "Auskunft: JSON-Export herunterladen, prüfen und der Person sicher übermitteln.",
    "rectification": "Berichtigung: falsche Angaben im jeweiligen Bereich von Hand korrigieren und hier dokumentieren.",
    "erasure": ("Löschung: Datenbestand prüfen, Aufbewahrungsgründe abwägen, Daten von Hand löschen und hier dokumentieren. "
                "Backups laufen nach ihrer eigenen Frist aus."),
    "restriction": "Einschränkung: betroffene Daten kennzeichnen und nicht weiter verarbeiten, bis die Prüfung abgeschlossen ist.",
    "objection": "Widerspruch: Verarbeitung prüfen und begründet fortsetzen oder einstellen.",
}


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page

    e = ws.esc

    def admin_ctx(request, session):
        return ws.auth(request, session, perm=None, admin=True)

    def actor(ctx):
        return str(ctx.user["id"]), ctx.user.get("global_name") or ctx.user.get("username") or "Admin"

    def pill(item) -> str:
        if item["overdue"]:
            return '<span class="pulse-pill bad">überfällig</span>'
        soon = item["is_open"] and item["days_left"] is not None and item["days_left"] <= 7
        tone = {"completed": "good", "waiting": "warn"}.get(item["status"], "warn" if soon else "")
        return f'<span class="pulse-pill {tone}">{e(item["status_label"])}</span>'

    def due_text(item) -> str:
        if not item["is_open"]:
            return f'abgeschlossen {ws.time_tag(item.get("closed_at"))}'
        days = item["days_left"]
        if days is None:
            return "Frist unbekannt"
        when = ws.time_tag(item["due_at"])
        if days < 0:
            return f"Frist seit {-days} Tag(en) abgelaufen ({when})"
        return f"Frist in {days} Tag(en) ({when})" + (" · verlängert" if item.get("extended_reason") else "")

    @app.get(BASE, response_class=HTMLResponse)
    async def privacy_requests_page(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        state = request.query_params.get("state") or "open"
        state = state if state in {"open", "overdue", "closed", "all"} else "open"
        rows = await asyncio.to_thread(dsr.list_requests, ws.GUILD_ID, state=state)
        counts = await asyncio.to_thread(dsr.summary, ws.GUILD_ID)
        current = ' aria-current="page"'
        tabs = "".join(
            f'<a class="pulse-btn {"primary" if key == state else "ghost"}" href="{BASE}?state={key}"{current if key == state else ""}>{label}</a>'
            for key, label in (("open", f"Offen ({counts['open']})"), ("overdue", f"Überfällig ({counts['overdue']})"),
                               ("closed", "Abgeschlossen"), ("all", "Alle")))
        items = "".join(
            f'<a class="pulse-row" href="{BASE}/{e(r["id"])}"><div class="pulse-row-main"><div class="pulse-row-title">'
            f'<code>{e(r["id"])}</code> · {e(r["kind_label"])} · {e(r["user_name"] or r["user_id"])} {pill(r)}</div>'
            f'<div class="pulse-row-meta">{due_text(r)} · eingegangen {ws.time_tag(r["received_at"])} über {e(r["channel_label"])}'
            f'{" · zuständig: " + e(r["assignee_name"]) if r.get("assignee_name") else " · noch niemand zuständig"}</div></div></a>'
            for r in rows) or ws.empty_state("Keine Anfragen in dieser Ansicht.", "🔒")
        kinds = "".join(f'<option value="{k}">{e(v)}</option>' for k, v in dsr.KINDS.items())
        channels = "".join(f'<option value="{k}"{" selected" if k == "ticket" else ""}>{e(v)}</option>'
                           for k, v in dsr.CHANNELS.items() if k != "discord")
        body = (
            f'<div class="pulse-crumb"><a href="/admin">Verwaltung</a> › <a href="/admin/privacy">Datenschutz</a> › Anfragen</div>'
            '<div class="pulse-topbar"><div><div class="pulse-section-title">🔒 Datenschutzanfragen</div>'
            '<div class="pulse-section-sub">Auskunft, Berichtigung, Löschung, Einschränkung und Widerspruch mit Frist '
            f'(1 Monat, einmal um bis zu {dsr.MAX_EXTENSION_DAYS} Tage verlängerbar). Pulse ändert oder löscht keine Daten selbst – '
            'hier wird dokumentiert, was ihr erledigt.</div></div>'
            f'<div class="flex flex-wrap gap-2" role="group" aria-label="Filter">{tabs}</div></div>'
            f'<div class="pulse-grid"><section class="pulse-card"><div class="pulse-card-b"><div class="pulse-list">{items}</div></div></section>'
            '<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">Anfrage erfassen</div></div>'
            f'<div class="pulse-card-b"><form method="post" action="{BASE}/create" class="grid gap-3">'
            '<label class="grid gap-1 text-xs font-semibold">Discord-ID der betroffenen Person'
            '<input class="pulse-input" name="user_id" required inputmode="numeric" pattern="[0-9]{15,22}" maxlength="22"></label>'
            '<label class="grid gap-1 text-xs font-semibold">Name (optional)<input class="pulse-input" name="user_name" maxlength="120"></label>'
            f'<label class="grid gap-1 text-xs font-semibold">Art<select class="pulse-input" name="kind">{kinds}</select></label>'
            f'<label class="grid gap-1 text-xs font-semibold">Eingangsweg<select class="pulse-input" name="channel">{channels}</select></label>'
            '<label class="grid gap-1 text-xs font-semibold">Eingegangen am<input class="pulse-input" type="date" name="received_on"></label>'
            '<label class="grid gap-1 text-xs font-semibold">Beschreibung'
            f'<textarea class="pulse-input pulse-textarea" name="details" rows="4" maxlength="{dsr.MAX_DETAILS}"></textarea></label>'
            '<p class="text-[11px] text-slate-400">Die Frist beginnt mit dem Eingang; leer bedeutet heute. '
            'Spieler können Anfragen auch selbst mit <code>/datenschutz-anfrage</code> stellen.</p>'
            '<button class="pulse-btn primary" type="submit">Anfrage anlegen</button></form></div></section></div>'
        )
        return HTMLResponse(render_pro_page(ws, "Datenschutzanfragen", ctx, "admin-privacy", body))

    @app.post(f"{BASE}/create")
    async def privacy_requests_create(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        received = None
        raw_date = str(form.get("received_on") or "").strip()
        if raw_date:
            try:
                received = datetime.fromisoformat(raw_date).replace(hour=12, tzinfo=timezone.utc)
            except ValueError:
                return ws.back(BASE, "Ungültiges Eingangsdatum.", False)
        actor_id, actor_name = actor(ctx)
        try:
            item = await asyncio.to_thread(
                dsr.create_request, ws.GUILD_ID, form.get("user_id"), form.get("user_name") or "", str(form.get("kind") or ""),
                form.get("details") or "", channel=str(form.get("channel") or ""), created_by_id=actor_id,
                created_by_name=actor_name, received_at=received)
        except ValueError as exc:
            return ws.back(BASE, str(exc), False)
        ws.log_audit(actor_name, actor_id, "Datenschutzanfrage angelegt",
                     f"{item['id']} · {item['kind_label']} · Discord-ID {item['user_id']}")
        return ws.back(f"{BASE}/{item['id']}", f"Anfrage {item['id']} angelegt.", True)

    @app.get(BASE + "/{request_id}", response_class=HTMLResponse)
    async def privacy_request_detail(request_id: str, request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        item = await asyncio.to_thread(dsr.get_request, ws.GUILD_ID, request_id)
        if item is None:
            return ws.back(BASE, "Anfrage nicht gefunden.", False)
        actor_id, actor_name = actor(ctx)
        events = await asyncio.to_thread(dsr.history, ws.GUILD_ID, item["id"])
        overview = ('<p class="text-xs text-slate-400">Über „Datenbestand prüfen“ zählt Pulse, in welchen Bereichen Daten zu dieser '
                    'Discord-ID gespeichert sind. Es wird nichts verändert; die Prüfung steht im Audit-Log.</p>')
        if request.query_params.get("scan") == "1":
            import pulse_ip_audit
            import pulse_retention
            export = await asyncio.to_thread(pulse_retention.export_person, ws, item["user_id"])
            export["dashboard_login_ip_audit"] = await asyncio.to_thread(pulse_ip_audit.entries_for_user, item["user_id"])
            ws.log_audit(actor_name, actor_id, "Datenbestand für Datenschutzanfrage geprüft", f"{item['id']} · Discord-ID {item['user_id']}")
            rows = dsr.data_overview(export)
            overview = "".join(f'<div class="pulse-row"><div class="pulse-row-title">{e(label)}</div><span class="pulse-pill">{n}</span></div>'
                               for label, n in rows) or ws.empty_state("Zu dieser Discord-ID wurden keine gespeicherten Daten gefunden.", "✓")
            if export.get("database_export_truncated"):
                overview += '<p class="text-[11px] text-amber-500 mt-2">Einige Tabellen wurden gekürzt – Details im JSON-Export.</p>'
        timeline = "".join(
            f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(ev["actor_name"] or "Pulse")}</div>'
            f'<div class="pulse-row-meta">{ws.time_tag(ev["created_at"])} · {e(ev["action"])}</div>'
            f'<div class="text-xs mt-1" style="white-space:pre-wrap">{e(ev["note"])}</div></div></div>' for ev in events)
        people = dsr.admin_members(getattr(ctx, "guild", None))
        if actor_id not in {p[0] for p in people}:
            people.insert(0, (actor_id, actor_name))
        assignee = str(item.get("assignee_id") or "")
        assign_options = '<option value="">— niemand —</option>' + "".join(
            f'<option value="{e(pid)}"{" selected" if pid == assignee else ""}>{e(name)}</option>' for pid, name in people)
        statuses = "".join(f'<option value="{k}"{" selected" if k == item["status"] else ""}>{e(v)}</option>' for k, v in dsr.STATUSES.items())
        closed = not item["is_open"]
        rid = e(item["id"])
        disabled = " disabled" if closed else ""
        if closed or item.get("extended_reason"):
            extend_html = '<p class="text-xs text-slate-400">Bereits verlängert oder abgeschlossen.</p>'
        else:
            extend_html = (
                f'<form method="post" action="{BASE}/{rid}/extend" class="grid gap-2">'
                f'<input class="pulse-input" type="number" name="days" min="1" max="{dsr.MAX_EXTENSION_DAYS}" value="30" aria-label="Tage">'
                f'<textarea class="pulse-input pulse-textarea" name="reason" rows="2" maxlength="{dsr.MAX_NOTE}" required aria-label="Begründung" '
                'placeholder="Grund (Komplexität/Anzahl der Anfragen) – Person informieren"></textarea>'
                '<button class="pulse-btn ghost" type="submit">Verlängern</button></form>')
        resolution = f'<p class="text-xs mt-3"><strong>Ergebnis:</strong> {e(item["resolution"])}</p>' if item.get("resolution") else ""
        extension = f'<p class="text-xs mt-2"><strong>Fristverlängerung:</strong> {e(item["extended_reason"])}</p>' if item.get("extended_reason") else ""

        def card(title, inner, extra=""):
            return (f'<section class="pulse-card"{extra}><div class="pulse-card-h"><div class="pulse-section-title">{e(title)}</div></div>'
                    f'<div class="pulse-card-b">{inner}</div></section>')
        body = (
            f'<div class="pulse-crumb"><a href="/admin">Verwaltung</a> › <a href="/admin/privacy">Datenschutz</a> › <a href="{BASE}">Anfragen</a> › {rid}</div>'
            f'<div class="pulse-topbar"><div><div class="pulse-section-title">🔒 {rid} · {e(item["kind_label"])} {pill(item)}</div>'
            f'<div class="pulse-section-sub">{e(item["user_name"] or "–")} · Discord-ID <code>{e(item["user_id"])}</code> · eingegangen '
            f'{ws.time_tag(item["received_at"])} über {e(item["channel_label"])} · {due_text(item)}</div></div>'
            f'<div class="flex flex-wrap gap-2"><a class="pulse-btn ghost" href="/admin/privacy/export?discord_id={e(item["user_id"])}">⬇ JSON-Auskunft</a>'
            f'<a class="pulse-btn ghost" href="{BASE}/{rid}?scan=1#bestand">🔎 Datenbestand prüfen</a></div></div>'
            '<div class="pulse-grid"><div class="grid gap-4">'
            + card("Anliegen", f'<p class="text-sm" style="white-space:pre-wrap">{e(item["details"]) or "Keine Beschreibung."}</p>'
                   f'<p class="text-[11px] text-slate-400 mt-3">{e(HINTS.get(item["kind"], ""))}</p>{resolution}{extension}')
            + card("Datenbestand (nur lesend)", overview, ' id="bestand"')
            + card("Verlauf", f'<div class="pulse-list">{timeline}</div>')
            + '</div><div class="grid gap-4">'
            + card("Status", f'<form method="post" action="{BASE}/{rid}/status" class="grid gap-2">'
                   f'<select class="pulse-input" name="status" aria-label="Neuer Status">{statuses}</select>'
                   f'<textarea class="pulse-input pulse-textarea" name="note" rows="3" maxlength="{dsr.MAX_NOTE}" aria-label="Notiz oder Ergebnis" '
                   'placeholder="Bei erledigt/abgelehnt Pflicht: was wurde getan bzw. warum abgelehnt?"></textarea>'
                   '<button class="pulse-btn primary" type="submit">Status speichern</button></form>')
            + card("Zuständigkeit", f'<form method="post" action="{BASE}/{rid}/assign" class="grid gap-2">'
                   f'<select class="pulse-input" name="assignee_id" aria-label="Zuständig"{disabled}>{assign_options}</select>'
                   f'<button class="pulse-btn ghost" type="submit"{disabled}>Zuweisen</button></form>')
            + card("Frist verlängern", extend_html)
            + card("Interne Notiz", f'<form method="post" action="{BASE}/{rid}/note" class="grid gap-2">'
                   f'<textarea class="pulse-input pulse-textarea" name="note" rows="3" maxlength="{dsr.MAX_NOTE}" required aria-label="Notiz"></textarea>'
                   '<button class="pulse-btn ghost" type="submit">Notiz speichern</button></form>')
            + '</div></div>'
        )
        return HTMLResponse(render_pro_page(ws, f"Datenschutzanfrage {item['id']}", ctx, "admin-privacy", body))

    async def mutate(request, user_session, request_id, label, func, holder=None):
        ctx = admin_ctx(request, user_session)
        if holder is not None:
            holder["guild"] = getattr(ctx, "guild", None)
        form = await request.form()
        actor_id, actor_name = actor(ctx)
        target = f"{BASE}/{str(request_id).strip().upper()}"
        try:
            item = await asyncio.to_thread(func, form, actor_id, actor_name)
        except LookupError as exc:
            return ws.back(BASE, str(exc), False)
        except ValueError as exc:
            return ws.back(target, str(exc), False)
        ws.log_audit(actor_name, actor_id, f"Datenschutzanfrage: {label}", f"{item['id']} · {item['status_label']}")
        return ws.back(target, f"{label} gespeichert.", True)

    @app.post(BASE + "/{request_id}/status")
    async def privacy_request_status(request_id: str, request: Request, user_session: str = Cookie(None)):
        return await mutate(request, user_session, request_id, "Status",
                            lambda f, aid, an: dsr.set_status(ws.GUILD_ID, request_id, str(f.get("status") or ""), aid, an, f.get("note") or ""))

    @app.post(BASE + "/{request_id}/assign")
    async def privacy_request_assign(request_id: str, request: Request, user_session: str = Cookie(None)):
        holder = {}

        def run(form, aid, an):
            target = str(form.get("assignee_id") or "").strip()
            name = ""
            if target:
                allowed = dict(dsr.admin_members(holder.get("guild")))
                allowed.setdefault(aid, an)
                if target not in allowed:
                    raise ValueError("Zuständig sein können nur Administratoren.")
                name = allowed[target]
            return dsr.assign(ws.GUILD_ID, request_id, target or None, name, aid, an)
        return await mutate(request, user_session, request_id, "Zuständigkeit", run, holder)

    @app.post(BASE + "/{request_id}/extend")
    async def privacy_request_extend(request_id: str, request: Request, user_session: str = Cookie(None)):
        return await mutate(request, user_session, request_id, "Fristverlängerung",
                            lambda f, aid, an: dsr.extend(ws.GUILD_ID, request_id, f.get("days"), f.get("reason") or "", aid, an))

    @app.post(BASE + "/{request_id}/note")
    async def privacy_request_note(request_id: str, request: Request, user_session: str = Cookie(None)):
        return await mutate(request, user_session, request_id, "Notiz",
                            lambda f, aid, an: dsr.add_note(ws.GUILD_ID, request_id, f.get("note") or "", aid, an))
