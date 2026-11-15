"""Dashboard side of the wiki extensions (audit #111, #113): ``/wissen`` search + must-read overview, confirmation and flagging.

URLs live under ``/wissen`` because ``/wiki/{page_id}`` already captures every single-segment path below ``/wiki``.
"""
from __future__ import annotations

from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

import pulse_wiki_plus as plus
import pulse_wiki_review as review


def is_manager(perms: dict) -> bool:
    perms = perms or {}
    return bool(perms.get("can_manage_wiki") or perms.get("can_promote") or perms.get("is_admin"))


def detail_block(ws, ctx, page_id: str) -> str:
    """Must-read banner for a wiki page (confirm button for everybody, flag switch and coverage for managers). Never raises."""
    try:
        e = ws.esc
        manager = is_manager(ctx.perms)
        required = plus.is_required(page_id)
        parts = []
        if required:
            version = plus.current_version(page_id)
            pending = any(p["id"] == str(page_id) for p in plus.pending_for(ctx.user["id"]))
            if pending:
                parts.append(f'<form method="post" action="/wissen/{e(page_id)}/gelesen" class="flex flex-wrap items-center gap-3">'
                             f'<span class="text-xs">📌 <b>Pflichtlektüre</b> – bitte Version {version} lesen und bestätigen.</span>'
                             f'<button class="{ws.BTN} py-2 px-3">Gelesen und verstanden</button></form>')
            else:
                parts.append(f'<p class="text-xs">📌 Pflichtlektüre · ✅ Version {version} hast du bestätigt.</p>')
        if manager:
            parts.append(f'<form method="post" action="/wissen/{e(page_id)}/pflicht" class="mt-2"><input type="hidden" name="on" value="{"0" if required else "1"}">'
                         f'<button class="{ws.BTN} py-1.5 px-3 text-[11px]">{"Pflichtlektüre aufheben" if required else "Als Pflichtlektüre festlegen"}</button></form>')
            if required:
                import pulse_access
                team = [(str(m.id), m.display_name) for m in pulse_access.team_members(ctx.guild, ctx.config)][:300]
                cov = plus.coverage(page_id, team)
                missing = ", ".join(n for _i, n in cov["missing"]) or "niemand"
                parts.append(f'<p class="text-[11px] text-slate-400 mt-2">Version {cov["version"]}: {len(cov["confirmed"])} bestätigt · noch offen: {e(missing)}</p>')
        parts.append(review_block(ws, ctx, page_id, manager))
        parts = [p for p in parts if p]
        return f'<section class="{ws.CARD} p-4 mt-4">{"".join(parts)}</section>' if parts else ""
    except Exception as exc:  # noqa: BLE001 - the wiki page must keep working
        print(f"⚠️ Wiki-Zusatz nicht verfügbar: {type(exc).__name__}")
        return ""


def review_block(ws, ctx, page_id: str, manager: bool) -> str:
    """#111: Freigabestatus der aktuellen Version (für alle sichtbar), Aktionen nur für Wiki-Verwaltung."""
    e = ws.esc
    st = review.state(page_id)
    icon = {"none": "⚪", "pending": "⏳", "approved": "✅", "rejected": "⛔"}[st["state"]]
    detail = f' · {e(st["by"])}' if st["by"] else ""
    note = f'<br><span class="text-slate-400">„{e(st["comment"])}“</span>' if st["comment"] else ""
    html = f'<p class="text-xs mt-3">{icon} <b>Redaktionelle Freigabe</b> (Version {st["version"]}): {e(st["label"])}{detail}{note}</p>'
    if manager and st["state"] in ("none", "rejected"):
        html += (f'<form method="post" action="/wissen/{e(page_id)}/freigabe" class="mt-2"><input type="hidden" name="aktion" value="einreichen">'
                 f'<button class="{ws.BTN} py-1.5 px-3 text-[11px]">Zur Freigabe einreichen</button></form>')
    if manager and st["state"] == "pending":
        html += (f'<form method="post" action="/wissen/{e(page_id)}/freigabe" class="mt-2 flex flex-wrap gap-2 items-center">'
                 f'<input class="{ws.INPUT} flex-1" name="kommentar" maxlength="500" placeholder="Kommentar (bei Ablehnung Pflicht)">'
                 f'<button name="aktion" value="freigeben" class="{ws.BTN} py-1.5 px-3 text-[11px]">Freigeben</button>'
                 f'<button name="aktion" value="ablehnen" class="{ws.BTN} py-1.5 px-3 text-[11px]">Ablehnen</button></form>')
    return html


def list_block(ws, ctx) -> str:
    """Search box and the person's pending must-reads for the top of ``/wiki``. Never raises."""
    try:
        e = ws.esc
        pending = plus.pending_for(ctx.user["id"])
        banner = ""
        if pending:
            links = " · ".join(f'<a class="underline" href="/wiki/{e(p["id"])}">{e(p["title"])}</a>' for p in pending[:8])
            banner = f'<div class="{ws.CARD} p-3 mb-3 text-xs">📌 <b>{len(pending)} Pflichtlektüre offen:</b> {links}</div>'
        search = ('<form method="get" action="/wissen" class="flex gap-2 mb-4"><input class="' + ws.INPUT + ' flex-1" name="q" maxlength="100" placeholder="Im Wiki suchen">'
                  f'<button class="{ws.BTN} py-2 px-4">Suchen</button></form>')
        return banner + search
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ Wiki-Zusatz nicht verfügbar: {type(exc).__name__}")
        return ""


def register(app) -> None:
    import webserver as ws
    e = ws.esc

    @app.get("/wissen", response_class=HTMLResponse)
    async def wiki_search_page(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        q = str(request.query_params.get("q") or "")[:100]
        results = plus.search(q) if q.strip() else []
        rows = "".join(f'<a class="block {ws.CARD} p-4 mb-2" href="/wiki/{e(r["id"])}"><div class="text-[10px] text-indigo-500 font-semibold">{e(r["category"])}</div>'
                       f'<div class="font-semibold text-sm">{e(r["title"])}</div><div class="text-xs text-slate-500 mt-1">{e(r["snippet"])}</div></a>' for r in results)
        if q.strip() and not rows:
            rows = f'<div class="pulse-empty">Keine Treffer für „{e(q)}“.</div>'
        required = ""
        if not q.strip():
            req = plus.required_pages()
            pending_ids = {p["id"] for p in plus.pending_for(ctx.user["id"])}
            required = ("<h2 class='text-sm font-bold mt-4 mb-2'>📌 Pflichtlektüre</h2>" + "".join(
                f'<a class="block {ws.CARD} p-3 mb-2 text-xs" href="/wiki/{e(r["id"])}">{e(r["title"])} · Version {r["version"]} · {"⏳ offen" if r["id"] in pending_ids else "✅ bestätigt"}</a>'
                for r in req)) if req else ""
        body = (f'<div class="max-w-3xl"><a href="/wiki" class="text-xs text-indigo-600">← Team-Wiki</a><h1 class="text-2xl font-bold mb-3">Wiki-Suche</h1>'
                f'<form method="get" class="flex gap-2 mb-4"><input class="{ws.INPUT} flex-1" name="q" value="{e(q)}" maxlength="100" placeholder="Suchbegriffe"><button class="{ws.BTN} py-2 px-4">Suchen</button></form>'
                f'{rows}{required}</div>')
        return HTMLResponse(ws.render_page("Wiki-Suche", ctx, "wiki", body))

    @app.post("/wissen/{page_id}/gelesen")
    async def wiki_acknowledge(request: Request, page_id: str, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        version = plus.acknowledge(page_id, ctx.user["id"], ctx.user.get("global_name") or "")
        if not version:
            raise HTTPException(status_code=404, detail="Diese Seite ist keine Pflichtlektüre.")
        ws.log_audit(ctx.user.get("global_name"), ctx.user["id"], "Pflichtlektüre bestätigt", f"{page_id} · Version {version}")
        return ws.back(f"/wiki/{page_id}", f"Version {version} als gelesen bestätigt.")

    @app.post("/wissen/{page_id}/freigabe")
    async def wiki_review_action(request: Request, page_id: str, aktion: str = Form(...), kommentar: str = Form(""), user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None)
        if not is_manager(ctx.perms):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        actor = ctx.user.get("global_name") or "Team"
        uid = ctx.user["id"]
        if aktion == "einreichen":
            ok, code = review.request_review(page_id, uid, actor)
            messages = {"ok": "Zur Freigabe eingereicht.", "not_found": "Wiki-Seite nicht gefunden.",
                        "already_pending": "Die Freigabe ist bereits angefragt.", "already_approved": "Diese Version ist bereits freigegeben."}
            if code == "not_found":
                raise HTTPException(status_code=404, detail=messages[code])
            if ok:
                ws.log_audit(actor, uid, "Wiki-Freigabe angefragt", str(page_id))
            return ws.back(f"/wiki/{page_id}", messages[code], ok)
        if aktion in ("freigeben", "ablehnen"):
            ok, code = review.decide(page_id, uid, actor, aktion == "freigeben", kommentar)
            messages = {"ok": "Entscheidung gespeichert.", "not_pending": "Keine offene Freigabe für die aktuelle Version.",
                        "own_request": "Eine eigene Anfrage kannst du nicht selbst entscheiden (Vier-Augen-Prinzip).",
                        "own_version": "Die eigene Version kannst du nicht selbst freigeben (Vier-Augen-Prinzip).",
                        "comment_required": "Bitte einen Kommentar für die Ablehnung angeben."}
            if ok:
                ws.log_audit(actor, uid, "Wiki-Freigabe entschieden", f"{page_id} · {aktion}")
            return ws.back(f"/wiki/{page_id}", messages[code], ok)
        raise HTTPException(status_code=400, detail="Unbekannte Aktion.")

    @app.post("/wissen/{page_id}/pflicht")
    async def wiki_set_required(request: Request, page_id: str, on: str = Form("1"), user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None)
        if not is_manager(ctx.perms):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        required = on == "1"
        actor = ctx.user.get("global_name") or "Team"
        if not plus.set_required(page_id, required, ctx.user["id"], actor):
            raise HTTPException(status_code=404, detail="Wiki-Seite nicht gefunden.")
        ws.log_audit(actor, ctx.user["id"], "Pflichtlektüre festgelegt" if required else "Pflichtlektüre aufgehoben", str(page_id))
        if required:
            import pulse_access
            import pulse_db
            page = pulse_db.get_wiki(page_id)
            for member in pulse_access.team_members(ctx.guild, ctx.config)[:300]:
                if str(member.id) != str(ctx.user["id"]):
                    try:
                        pulse_db.notify(member.id, "📌 Neue Pflichtlektüre", str(page["title"])[:160], "warning", f"/wiki/{page_id}", f"wiki-req:{page_id}:{member.id}", 86400)
                    except Exception as exc:  # noqa: BLE001
                        print(f"⚠️ Pflichtlektüre-Meldung fehlgeschlagen: {type(exc).__name__}")
        return ws.back(f"/wiki/{page_id}", "Pflichtlektüre festgelegt." if required else "Pflichtlektüre aufgehoben.")
