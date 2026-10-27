"""Dashboard → Fraktionsverwaltung at /factions (role FACTION_MANAGER_ROLE_ID or administrators).

GET  /factions                                   counters, filterable faction list, new faction, categories
GET  /factions/{id}                              one faction: warnings (counter, history), edit, delete
POST /factions/create                            new faction
POST /factions/{id}/update                       edit name, status, category, leader, maximum, description
POST /factions/{id}/official                     probation → official
POST /factions/{id}/delete                       delete incl. warning history (checkbox + browser question)
POST /factions/{id}/warnings/add                 warn the faction (reason, date) – blocked at the maximum
POST /factions/{id}/warnings/{wid}/revoke        revoke a warning (reason), it stays in the history
POST /factions/categories/create|{cid}/update|{cid}/delete

Every route authorizes server-side (ws.auth for the login, then pulse_factions.can_manage – deny by
default) before it reads the form. Every change goes to the audit log. Nothing touches Discord.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date

from fastapi import Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse

import pulse_factions as pf

_MEMBER_ID = re.compile(r"^[0-9]{15,22}$")
DB_ERROR = "Die Fraktionsdaten konnten gerade nicht gespeichert werden. Bitte erneut versuchen."

FACTION_CSS = """<style>
.fx-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px}
.fx-form{display:grid;gap:12px}
.fx-field{display:grid;gap:5px;font-size:12px;font-weight:650}
.fx-hint{font-size:11px;color:var(--muted);font-weight:400;line-height:1.5}
.fx-filter{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.fx-filter .pulse-input{flex:1 1 160px;min-width:0}
.fx-row{flex-wrap:wrap}
.fx-row .pulse-row-main{flex:1 1 220px}
.fx-actions{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.fx-dot{width:10px;height:10px;border-radius:50%;display:inline-block;flex:0 0 auto}
.fx-meter{display:flex;gap:4px;margin-top:6px}
.fx-meter span{width:18px;height:6px;border-radius:999px;background:var(--surface2);border:1px solid var(--border)}
.fx-meter span.on{background:#f79009;border-color:#f79009}
.fx-meter.max span.on{background:#f04438;border-color:#f04438}
.fx-revoked{opacity:.62}
.fx-revoked .pulse-row-title{text-decoration:line-through}
.fx-cat{display:grid;grid-template-columns:minmax(0,1fr) 54px auto;gap:6px;align-items:center}
.fx-cat input[type=color]{height:38px;width:54px;padding:2px;border-radius:10px;border:1px solid var(--border);background:var(--surface2)}
.fx-danger{border-color:rgba(240,68,56,.35)}
section[id]{scroll-margin-top:80px}
@media(max-width:640px){.fx-cat{grid-template-columns:minmax(0,1fr) 54px}.fx-cat .fx-actions{grid-column:1/-1}}
</style>"""


def _name(user: dict) -> str:
    return str(user.get("global_name") or user.get("username") or user.get("id") or "?")


def resolve_leader(guild, raw: str, keep: dict | None = None) -> tuple[str | None, str | None]:
    """Discord-ID or exact name → (leader_id, display name). Empty → no leader.
    The stored leader stays valid even if they are no longer in the member cache."""
    raw = " ".join(str(raw or "").split())
    if not raw:
        return None, None
    if keep and keep.get("leader_id") and raw == str(keep["leader_id"]):
        member = guild.get_member(int(raw)) if raw.isdigit() else None
        return raw, getattr(member, "display_name", None) or keep.get("leader_name")
    if _MEMBER_ID.fullmatch(raw):
        member = guild.get_member(int(raw))
        if member is None or getattr(member, "bot", False):
            raise ValueError("Leiter: Zu dieser Discord-ID gibt es kein Mitglied auf dem Server.")
        return str(member.id), member.display_name
    wanted = raw.casefold().lstrip("@")
    matches = [m for m in getattr(guild, "members", None) or [] if not getattr(m, "bot", False) and wanted in {
        str(getattr(m, attr, "") or "").casefold() for attr in ("display_name", "name", "global_name")}]
    if not matches:
        raise ValueError(f"Leiter: Kein Mitglied mit dem Namen „{raw[:40]}“ gefunden. Alternativ die Discord-ID eintragen.")
    if len(matches) > 1:
        raise ValueError(f"Leiter: Mehrere Mitglieder heißen „{raw[:40]}“ – bitte die Discord-ID eintragen.")
    return str(matches[0].id), matches[0].display_name


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page

    e = ws.esc

    def ctx_for(request, session):
        ctx = ws.auth(request, session, perm=None)
        if not pf.can_manage(ctx.perms, ctx.member):
            raise HTTPException(status_code=403, detail="Die Fraktionsverwaltung ist nur für die Rolle „Fraktionsverwaltung“ und Administratoren freigegeben.")
        return ctx

    def audit(ctx, action: str, details: str) -> None:
        ws.log_audit(_name(ctx.user), ctx.user["id"], action, details)

    def render(ctx, title: str, body: str) -> HTMLResponse:
        return HTMLResponse(render_pro_page(ws, title, ctx, "factions", FACTION_CSS + body))

    def status_pill(item: dict) -> str:
        kind = {"official": "good", "probation": "warn"}.get(item["status"], "")
        return f'<span class="pulse-pill {kind}">{e(item["status_label"])}</span>'

    def category_pill(item: dict) -> str:
        if not item.get("category_name"):
            return '<span class="pulse-pill">Ohne Kategorie</span>'
        return (f'<span class="pulse-pill"><span class="fx-dot" style="background:{e(item["category_color"])}"></span>'
                f'{e(item["category_name"])}</span>')

    def meter(item: dict) -> str:
        cells = "".join(f'<span class="{"on" if i < item["warnings"] else ""}"></span>' for i in range(item["max_warnings"]))
        return (f'<div class="fx-meter{" max" if item["at_max"] else ""}" role="img" '
                f'aria-label="{item["warnings"]} von {item["max_warnings"]} Verwarnungen">{cells}</div>')

    def leader_text(guild, item: dict) -> str:
        if not item.get("leader_id"):
            return "Kein Leiter"
        member = guild.get_member(int(item["leader_id"])) if str(item["leader_id"]).isdigit() else None
        if member is not None:
            return f"Leiter: {e(member.display_name)}"
        return f'Leiter: {e(item.get("leader_name") or item["leader_id"])} <span class="fx-hint">(nicht im Server-Cache)</span>'

    def faction_form(action: str, categories: list[dict], item: dict | None, submit: str) -> str:
        item = item or {}
        status = item.get("status", "unassigned")
        statuses = "".join(f'<option value="{k}"{" selected" if k == status else ""}>{e(v)}</option>' for k, v in pf.STATUSES.items())
        cats = '<option value="">Ohne Kategorie</option>' + "".join(
            f'<option value="{c["id"]}"{" selected" if c["id"] == item.get("category_id") else ""}>{e(c["name"])}</option>' for c in categories)
        return f'''<form method="post" action="{action}" class="fx-form">
<div class="fx-grid">
<label class="fx-field">Name<input class="pulse-input" name="name" required minlength="2" maxlength="{pf.NAME_MAX}" value="{e(item.get("name", ""))}" placeholder="z. B. Polizei Bochum"></label>
<label class="fx-field">Status<select class="pulse-input" name="status">{statuses}</select></label>
<label class="fx-field">Kategorie<select class="pulse-input" name="category_id">{cats}</select></label>
<label class="fx-field">Maximale Verwarnungen<input class="pulse-input" type="number" name="max_warnings" min="1" max="{pf.MAX_WARNINGS_LIMIT}" value="{item.get("max_warnings", pf.DEFAULT_MAX_WARNINGS)}"></label>
</div>
<label class="fx-field">Leiter (optional)<input class="pulse-input" name="leader" maxlength="80" value="{e(item.get("leader_id") or "")}" placeholder="Discord-ID oder genauer Anzeigename">
<span class="fx-hint">Leer lassen, wenn die Fraktion keinen Leiter hat. Pulse ändert dabei keine Discord-Rollen.</span></label>
<label class="fx-field">Beschreibung (optional)<textarea class="pulse-input pulse-textarea" name="description" maxlength="{pf.TEXT_MAX}" rows="3">{e(item.get("description", ""))}</textarea></label>
<div><button type="submit" class="pulse-btn primary">{submit}</button></div></form>'''

    def form_data(form, guild, keep: dict | None = None) -> dict:
        leader_id, leader_name = resolve_leader(guild, form.get("leader"), keep)
        return {"name": form.get("name"), "status": form.get("status"), "category_id": form.get("category_id"),
                "max_warnings": form.get("max_warnings"), "description": form.get("description"),
                "leader_id": leader_id, "leader_name": leader_name}

    def error_page(ctx, title: str) -> HTMLResponse:
        return render(ctx, title, '''<div class="pulse-hero"><div class="pulse-kicker">Fraktionsverwaltung</div>
<div class="pulse-title">Daten nicht verfügbar</div></div>
<div class="pulse-alert mt-5" role="alert">⚠️ Die Fraktionsdaten konnten gerade nicht geladen werden. Es wurde nichts geändert.
Bitte die Seite in ein paar Sekunden <a href="" style="text-decoration:underline">neu laden</a>.</div>''')

    # ------------------------------------------------------------------ overview
    @app.get("/factions", response_class=HTMLResponse)
    async def factions_page(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        params = request.query_params
        status = params.get("status", "") if params.get("status", "") in pf.STATUSES else ""
        raw_cat = params.get("category", "")
        category = 0 if raw_cat == "none" else (int(raw_cat) if raw_cat.isdigit() else None)
        q = str(params.get("q", ""))[:60].strip()
        try:
            stats = pf.summary()
            categories = pf.list_categories()
            factions = pf.list_factions(status, category, q)
        except sqlite3.Error as exc:
            ws.pulse_logging.report_error("factions", f"Fraktionsübersicht nicht lesbar: {type(exc).__name__}")
            return error_page(ctx, "Fraktionsverwaltung")

        tiles = "".join(
            f'<a class="pulse-stat" href="{href}"><div class="icon">{icon}</div><div class="label">{label}</div><div class="value">{value}</div></a>'
            for icon, label, value, href in (
                ("✅", "Offizielle Fraktionen", stats["official"], "/factions?status=official#list"),
                ("⏳", "In Probezeit", stats["probation"], "/factions?status=probation#list"),
                ("○", "Nicht vergeben", stats["unassigned"], "/factions?status=unassigned#list"),
                ("🗂", "Kategorien", stats["categories"], "#categories"),
                ("⚠️", "Aktive Verwarnungen", stats["warnings"], "#list")))

        cat_opts = ('<option value="">Alle Kategorien</option>'
                    + "".join(f'<option value="{c["id"]}"{" selected" if category == c["id"] else ""}>{e(c["name"])}</option>' for c in categories)
                    + f'<option value="none"{" selected" if category == 0 else ""}>Ohne Kategorie</option>')
        status_opts = '<option value="">Alle Status</option>' + "".join(
            f'<option value="{k}"{" selected" if k == status else ""}>{e(v)}</option>' for k, v in pf.STATUSES.items())
        filtered = bool(status or category is not None or q)
        filter_bar = f'''<form method="get" action="/factions#list" class="fx-filter" role="search" data-no-restore>
<input class="pulse-input" type="search" name="q" value="{e(q)}" placeholder="Fraktion oder Leiter suchen" aria-label="Suche">
<select class="pulse-input" name="status" aria-label="Status">{status_opts}</select>
<select class="pulse-input" name="category" aria-label="Kategorie">{cat_opts}</select>
<button class="pulse-btn ghost" type="submit">Filtern</button>{'<a class="pulse-btn ghost" href="/factions#list">Zurücksetzen</a>' if filtered else ""}</form>'''

        rows = []
        for item in factions:
            official = (f'<form method="post" action="/factions/{item["id"]}/official" data-confirm="{e(item["name"])} jetzt offiziell machen?">'
                        f'<button class="pulse-btn good" type="submit">✓ Offiziell machen</button></form>') if item["status"] == "probation" else ""
            rows.append(f'''<div class="pulse-row fx-row"><div class="pulse-row-main">
<div class="pulse-row-title"><a href="/factions/{item["id"]}">{e(item["name"])}</a></div>
<div class="fx-actions mt-2">{status_pill(item)}{category_pill(item)}<span class="pulse-pill{" bad" if item["at_max"] else (" warn" if item["warnings"] else "")}">⚠ {item["warnings"]}/{item["max_warnings"]}</span></div>
<div class="pulse-row-meta">{leader_text(ctx.guild, item)}</div>{meter(item)}</div>
<div class="fx-actions">{official}<a class="pulse-btn ghost" href="/factions/{item["id"]}">Verwalten</a></div></div>''')
        if rows:
            list_html = "".join(rows)
        elif filtered:
            list_html = '<div class="pulse-empty">Keine Fraktion passt zum Filter. <a href="/factions#list" style="text-decoration:underline">Filter zurücksetzen</a></div>'
        else:
            list_html = '<div class="pulse-empty">Noch keine Fraktionen angelegt. Lege unten die erste Fraktion an.</div>'

        cat_rows = []
        for c in categories:
            note = f' – {c["faction_count"]} Fraktion(en) werden danach „Ohne Kategorie“' if c["faction_count"] else ""
            cat_rows.append(f'''<div class="pulse-row"><form method="post" action="/factions/categories/{c["id"]}/update" class="fx-cat" style="flex:1">
<input class="pulse-input" name="name" required minlength="2" maxlength="{pf.NAME_MAX}" value="{e(c["name"])}" aria-label="Name der Kategorie">
<input type="color" name="color" value="{e(c["color"])}" aria-label="Farbe">
<div class="fx-actions"><span class="pulse-pill">{c["faction_count"]} Fraktion(en)</span><button class="pulse-btn ghost" type="submit">Speichern</button>
<button class="pulse-btn bad" type="submit" formaction="/factions/categories/{c["id"]}/delete" data-confirm-del="Kategorie „{e(c["name"])}“ löschen{e(note)}?">Löschen</button></div></form></div>''')
        categories_html = f'''<section id="categories" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">🗂 Kategorien</div>
<div class="pulse-section-sub">Zum Beispiel Staatlich, Neutral und Illegal. Beim Löschen bleiben die Fraktionen erhalten.</div></div></div>
<div class="pulse-card-b space-y-2">{"".join(cat_rows) or '<div class="pulse-empty">Noch keine Kategorien.</div>'}
<form method="post" action="/factions/categories/create" class="fx-cat mt-3">
<input class="pulse-input" name="name" required minlength="2" maxlength="{pf.NAME_MAX}" placeholder="Neue Kategorie" aria-label="Neue Kategorie">
<input type="color" name="color" value="#64748b" aria-label="Farbe"><div class="fx-actions"><button class="pulse-btn primary" type="submit">+ Kategorie</button></div></form></div></section>'''

        body = f'''<div class="pulse-hero"><div class="pulse-hero-grid"><div><div class="pulse-kicker">Fraktionsverwaltung</div>
<div class="pulse-title">Fraktionen</div>
<div class="pulse-sub">Status, Kategorien, Leitung und Verwarnungen aller Fraktionen von {e(ctx.guild_name)}. Jede Änderung steht im Audit-Log; Pulse ändert dabei keine Discord-Rollen.</div></div>
<div class="pulse-hero-box"><div class="label">Gesamt</div><div class="text-xs mt-2">{stats["total"]} Fraktion(en)</div></div></div></div>
<div class="pulse-stat-grid mt-5">{tiles}</div>
<section id="list" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">⚑ Alle Fraktionen</div>
<div class="pulse-section-sub">{len(factions)} angezeigt</div></div></div>
<div class="pulse-card-b space-y-3">{filter_bar}<div class="pulse-list">{list_html}</div></div></section>
<section id="new" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">＋ Neue Fraktion</div></div></div>
<div class="pulse-card-b">{faction_form("/factions/create", categories, None, "Fraktion anlegen")}</div></section>
{categories_html}
<script>document.addEventListener('click',ev=>{{const b=ev.target.closest('[data-confirm-del]');if(b&&!confirm(b.dataset.confirmDel))ev.preventDefault();}});</script>'''
        return render(ctx, "Fraktionsverwaltung", body)

    # ------------------------------------------------------------------ detail
    @app.get("/factions/{faction_id:int}", response_class=HTMLResponse)
    async def faction_detail(faction_id: int, request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        try:
            item = pf.get_faction(faction_id)
            categories = pf.list_categories()
            history = pf.warnings_for(faction_id) if item else []
        except sqlite3.Error as exc:
            ws.pulse_logging.report_error("factions", f"Fraktion {faction_id} nicht lesbar: {type(exc).__name__}")
            return error_page(ctx, "Fraktion")
        if not item:
            return ws.back("/factions", "Diese Fraktion gibt es nicht (mehr).", False)

        entries = []
        for w in history:
            when = date.fromisoformat(w["warned_on"]).strftime("%d.%m.%Y")
            if w["revoked_at"]:
                entries.append(f'''<div class="pulse-row fx-row fx-revoked"><div class="pulse-row-main"><div class="pulse-row-title">{e(w["reason"])}</div>
<div class="pulse-row-meta">{when} · von {e(w["issued_by_name"] or "?")} · zurückgezogen von {e(w["revoked_by_name"] or "?")}: {e(w["revoke_reason"] or "")}</div></div>
<span class="pulse-pill">Zurückgezogen</span></div>''')
            else:
                entries.append(f'''<div class="pulse-row fx-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(w["reason"])}</div>
<div class="pulse-row-meta">{when} · von {e(w["issued_by_name"] or "?")}</div></div>
<details><summary class="pulse-btn ghost">Zurückziehen</summary>
<form method="post" action="/factions/{item["id"]}/warnings/{w["id"]}/revoke" class="fx-form mt-2">
<input class="pulse-input" name="reason" required minlength="3" maxlength="{pf.REASON_MAX}" placeholder="Grund für das Zurückziehen" aria-label="Grund für das Zurückziehen">
<button class="pulse-btn bad" type="submit">Verwarnung zurückziehen</button></form></details></div>''')
        today = date.today().isoformat()
        if item["at_max"]:
            warn_form = (f'<div class="pulse-alert" role="status">Maximum erreicht ({item["warnings"]}/{item["max_warnings"]}). '
                         'Pulse unternimmt dadurch nichts automatisch – die Entscheidung liegt bei der Fraktionsverwaltung. '
                         'Für eine weitere Verwarnung erst eine zurückziehen oder das Maximum erhöhen.</div>')
        else:
            warn_form = f'''<form method="post" action="/factions/{item["id"]}/warnings/add" class="fx-form">
<div class="fx-grid"><label class="fx-field">Grund<input class="pulse-input" name="reason" required minlength="3" maxlength="{pf.REASON_MAX}" placeholder="Was ist passiert?"></label>
<label class="fx-field">Datum<input class="pulse-input" type="date" name="warned_on" value="{today}" max="{today}" required></label></div>
<div><button class="pulse-btn primary" type="submit">⚠ Verwarnung eintragen ({item["warnings"] + 1}/{item["max_warnings"]})</button></div></form>'''
        official = (f'<form method="post" action="/factions/{item["id"]}/official" data-confirm="{e(item["name"])} jetzt offiziell machen?">'
                    f'<button class="pulse-btn good" type="submit">✓ Offiziell machen</button></form>') if item["status"] == "probation" else ""
        since = ""
        if item["status"] == "probation" and item.get("probation_since"):
            since = f' · in Probezeit seit {e(item["probation_since"][:10])}'
        elif item["status"] == "official" and item.get("official_since"):
            since = f' · offiziell seit {e(item["official_since"][:10])}'

        body = f'''<div class="pulse-crumb" style="font-size:11px;color:#94a3b8;margin-bottom:8px"><a href="/factions" style="color:#6366f1">Fraktionsverwaltung</a> › {e(item["name"])}</div>
<div class="pulse-hero"><div class="pulse-hero-grid"><div><div class="pulse-kicker">Fraktion</div>
<div class="pulse-title">{e(item["name"])}</div>
<div class="fx-actions mt-2">{status_pill(item)}{category_pill(item)}</div>
<div class="pulse-sub">{leader_text(ctx.guild, item)}{since}</div>
{f'<div class="pulse-sub">{e(item["description"])}</div>' if item["description"] else ""}</div>
<div class="pulse-hero-box"><div class="label">Verwarnungen</div><div class="pulse-title">{item["warnings"]}/{item["max_warnings"]}</div>{meter(item)}
<div class="fx-actions mt-3">{official}</div></div></div></div>
<section id="warnings" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">⚠ Verwarnungen</div>
<div class="pulse-section-sub">Zurückgezogene Verwarnungen bleiben im Verlauf, zählen aber nicht mehr.</div></div></div>
<div class="pulse-card-b space-y-3">{warn_form}<div class="pulse-list">{"".join(entries) or '<div class="pulse-empty">Keine Verwarnungen – sauber.</div>'}</div></div></section>
<section id="edit" class="pulse-card mt-5"><div class="pulse-card-h"><div><div class="pulse-section-title">✏️ Bearbeiten</div></div></div>
<div class="pulse-card-b">{faction_form(f"/factions/{item['id']}/update", categories, item, "Änderungen speichern")}</div></section>
<section id="delete" class="pulse-card mt-5 fx-danger"><div class="pulse-card-h"><div><div class="pulse-section-title">🗑 Fraktion löschen</div>
<div class="pulse-section-sub">Entfernt die Fraktion und ihren Verwarnungsverlauf aus Pulse. Das Audit-Log behält den Eintrag. Discord bleibt unverändert.</div></div></div>
<div class="pulse-card-b"><form method="post" action="/factions/{item["id"]}/delete" class="fx-form" data-confirm="{e(item["name"])} wirklich endgültig löschen?">
<label class="fx-field" style="display:flex;gap:8px;align-items:center;font-weight:500"><input type="checkbox" name="confirm" value="1" required> Ich möchte „{e(item["name"])}“ samt Verlauf löschen.</label>
<div><button class="pulse-btn bad" type="submit">Endgültig löschen</button></div></form></div></section>'''
        return render(ctx, item["name"], body)

    # ------------------------------------------------------------------ faction writes
    @app.post("/factions/create")
    async def faction_create(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        form = await request.form()
        try:
            item = pf.create_faction(form_data(form, ctx.guild))
        except ValueError as exc:
            return ws.back("/factions#new", str(exc), False)
        except sqlite3.Error:
            return ws.back("/factions#new", DB_ERROR, False)
        audit(ctx, "Fraktion angelegt", f"{item['name']} (#{item['id']}) · {item['status_label']} · "
                                        f"{item['category_name'] or 'ohne Kategorie'} · Leiter: {item['leader_name'] or '–'}")
        return ws.back(f"/factions/{item['id']}", f"Fraktion „{item['name']}“ angelegt.")

    @app.post("/factions/{faction_id:int}/update")
    async def faction_update(faction_id: int, request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        form = await request.form()
        url = f"/factions/{faction_id}"
        try:
            current = pf.get_faction(faction_id)
            if not current:
                return ws.back("/factions", "Diese Fraktion gibt es nicht (mehr).", False)
            before, after = pf.update_faction(faction_id, form_data(form, ctx.guild, keep=current))
        except LookupError as exc:
            return ws.back("/factions", str(exc), False)
        except ValueError as exc:
            return ws.back(url + "#edit", str(exc), False)
        except sqlite3.Error:
            return ws.back(url + "#edit", DB_ERROR, False)
        labels = {"name": "Name", "status_label": "Status", "category_name": "Kategorie", "leader_name": "Leiter",
                  "max_warnings": "Maximum", "description": "Beschreibung"}
        changes = [f"{label}: {before[k] or '–'} → {after[k] or '–'}" if k != "description" else "Beschreibung geändert"
                   for k, label in labels.items() if before[k] != after[k]]
        if not changes:
            return ws.back(url, "Keine Änderungen.")
        audit(ctx, "Fraktion bearbeitet", f"{after['name']} (#{faction_id}) · " + "; ".join(changes))
        return ws.back(url, f"„{after['name']}“ gespeichert.")

    @app.post("/factions/{faction_id:int}/official")
    async def faction_official(faction_id: int, request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        try:
            item = pf.make_official(faction_id)
        except LookupError as exc:
            return ws.back("/factions", str(exc), False)
        except ValueError as exc:
            return ws.back(f"/factions/{faction_id}", str(exc), False)
        except sqlite3.Error:
            return ws.back(f"/factions/{faction_id}", DB_ERROR, False)
        audit(ctx, "Fraktion offiziell gemacht", f"{item['name']} (#{faction_id}) · Probezeit → Offiziell")
        return ws.back(f"/factions/{faction_id}", f"„{item['name']}“ ist jetzt offiziell.")

    @app.post("/factions/{faction_id:int}/delete")
    async def faction_delete(faction_id: int, request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        form = await request.form()
        if form.get("confirm") != "1":
            return ws.back(f"/factions/{faction_id}#delete", "Bitte das Löschen mit dem Häkchen bestätigen.", False)
        try:
            item, removed = pf.delete_faction(faction_id)
        except LookupError as exc:
            return ws.back("/factions", str(exc), False)
        except sqlite3.Error:
            return ws.back(f"/factions/{faction_id}#delete", DB_ERROR, False)
        audit(ctx, "Fraktion gelöscht", f"{item['name']} (#{faction_id}) · {item['status_label']} · "
                                        f"{item['warnings']} aktive / {removed} gespeicherte Verwarnung(en) entfernt")
        return ws.back("/factions", f"Fraktion „{item['name']}“ gelöscht.")

    # ------------------------------------------------------------------ warnings
    @app.post("/factions/{faction_id:int}/warnings/add")
    async def faction_warn(faction_id: int, request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        form = await request.form()
        url = f"/factions/{faction_id}#warnings"
        try:
            warning, item = pf.add_warning(faction_id, form.get("reason"), form.get("warned_on"), ctx.user["id"], _name(ctx.user))
        except LookupError as exc:
            return ws.back("/factions", str(exc), False)
        except ValueError as exc:
            return ws.back(url, str(exc), False)
        except sqlite3.Error:
            return ws.back(url, DB_ERROR, False)
        audit(ctx, "Fraktion verwarnt", f"{item['name']} (#{faction_id}) · {item['warnings']}/{item['max_warnings']} · "
                                        f"{warning['warned_on']} · Grund: {warning['reason']}")
        note = " Maximum erreicht." if item["at_max"] else ""
        return ws.back(url, f"Verwarnung eingetragen ({item['warnings']}/{item['max_warnings']}).{note}")

    @app.post("/factions/{faction_id:int}/warnings/{warning_id:int}/revoke")
    async def faction_warn_revoke(faction_id: int, warning_id: int, request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        form = await request.form()
        url = f"/factions/{faction_id}#warnings"
        try:
            warning, item = pf.revoke_warning(faction_id, warning_id, _name(ctx.user), form.get("reason"))
        except LookupError as exc:
            return ws.back(url, str(exc), False)
        except ValueError as exc:
            return ws.back(url, str(exc), False)
        except sqlite3.Error:
            return ws.back(url, DB_ERROR, False)
        audit(ctx, "Fraktionsverwarnung zurückgezogen", f"{item['name']} (#{faction_id}) · Verwarnung #{warning_id} "
                                                        f"({warning['reason']}) · Grund: {warning['revoke_reason']} · jetzt {item['warnings']}/{item['max_warnings']}")
        return ws.back(url, f"Verwarnung zurückgezogen ({item['warnings']}/{item['max_warnings']}).")

    # ------------------------------------------------------------------ categories
    @app.post("/factions/categories/create")
    async def faction_category_create(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        form = await request.form()
        try:
            cat = pf.create_category(form.get("name"), form.get("color"))
        except ValueError as exc:
            return ws.back("/factions#categories", str(exc), False)
        except sqlite3.Error:
            return ws.back("/factions#categories", DB_ERROR, False)
        audit(ctx, "Fraktionskategorie angelegt", f"{cat['name']} (#{cat['id']})")
        return ws.back("/factions#categories", f"Kategorie „{cat['name']}“ angelegt.")

    @app.post("/factions/categories/{category_id:int}/update")
    async def faction_category_update(category_id: int, request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        form = await request.form()
        try:
            before, after = pf.update_category(category_id, form.get("name"), form.get("color"))
        except (LookupError, ValueError) as exc:
            return ws.back("/factions#categories", str(exc), False)
        except sqlite3.Error:
            return ws.back("/factions#categories", DB_ERROR, False)
        if (before["name"], before["color"]) == (after["name"], after["color"]):
            return ws.back("/factions#categories", "Keine Änderungen.")
        audit(ctx, "Fraktionskategorie bearbeitet", f"#{category_id}: {before['name']} → {after['name']} · Farbe {after['color']}")
        return ws.back("/factions#categories", f"Kategorie „{after['name']}“ gespeichert.")

    @app.post("/factions/categories/{category_id:int}/delete")
    async def faction_category_delete(category_id: int, request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        try:
            cat, moved = pf.delete_category(category_id)
        except LookupError as exc:
            return ws.back("/factions#categories", str(exc), False)
        except sqlite3.Error:
            return ws.back("/factions#categories", DB_ERROR, False)
        audit(ctx, "Fraktionskategorie gelöscht", f"{cat['name']} (#{category_id}) · {moved} Fraktion(en) jetzt ohne Kategorie")
        return ws.back("/factions#categories", f"Kategorie „{cat['name']}“ gelöscht.")
