"""Audit #123: Dashboard-Verwaltung der zentralen Vorlagenbibliothek (``/vorlagen``) für Ticket-Schnellantworten, Bewerber-Nachrichten,
Ankündigungen und Moderationsgründe. Rechte je Art kommen aus ``pulse_templates.KINDS`` (Policy, fail-closed): wer eine Art nicht
verwalten darf, sieht sie nicht und kann sie nicht ändern. Alle Regeln (Versionen, Archiv statt Löschen, Platzhalter-Prüfung,
Konflikterkennung) liegen in ``pulse_templates``.
"""
from __future__ import annotations

from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

import pulse_templates as templates


def announcement_picker(ws, ctx) -> str:
    """Auswahl einer Ankündigungsvorlage über dem Ankündigungsformular (füllt Titel und Text, nichts wird gesendet). ``""`` ohne Vorlagen."""
    try:
        items = templates.list_templates(ctx.perms, ctx.guild.id, kind="announcement", limit=50)
    except Exception:  # noqa: BLE001
        return ""
    if not items:
        return ""
    e = ws.esc
    options = "".join(f'<option value="{e(t["id"])}" data-title="{e(t["title"])}" data-body="{e(t["body"])}">{e(t["title"])}</option>' for t in items)
    return ('<div class="pulse-card p-3 mb-3 text-xs"><label for="annTemplate" class="font-semibold">Vorlage verwenden</label> '
            f'<select id="annTemplate" class="pulse-input"><option value="">– keine –</option>{options}</select> '
            '<a class="underline" href="/vorlagen">Vorlagen verwalten</a>'
            '<script>document.getElementById("annTemplate").addEventListener("change",function(){var o=this.selectedOptions[0];if(!o||!o.value)return;'
            'var f=document.querySelector(\'form[action="/announcements/create"]\');if(!f)return;'
            'f.elements.title.value=o.dataset.title;f.elements.content.value=o.dataset.body;});</script></div>')


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page
    e = ws.esc

    def actor(ctx):
        return ctx.user["id"], ctx.user.get("global_name") or ctx.user.get("username") or "Team"

    def visible_kinds(perms):
        return [k for k in templates.KINDS if templates.can_use(perms, k)]

    def require_writer(ctx):
        """Schreibende Routen: ohne irgendein Vorlagenrecht gleich 403 (bevor Eingaben oder Existenz geprüft werden)."""
        if not visible_kinds(ctx.perms):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")

    def placeholders(kind):
        return ", ".join("{" + p + "}" for p in templates.KINDS[kind]["placeholders"])

    @app.get("/vorlagen", response_class=HTMLResponse)
    async def templates_page(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        kinds = visible_kinds(ctx.perms)
        archived = request.query_params.get("archiv") == "1"
        rows = templates.list_templates(ctx.perms, ctx.guild.id, include_archived=archived, limit=200)
        sections = ""
        for kind in kinds:
            spec = templates.KINDS[kind]
            items = [t for t in rows if t["kind"] == kind]
            listing = "".join(
                f'<div class="pulse-row"><div class="pulse-row-main"><a class="pulse-row-title" href="/vorlagen/{e(t["id"])}">{e(t["title"])}</a>'
                f'<div class="pulse-row-meta">{e(t["scope"])} · Version {int(t["current_version"])} · {e(t["edited_by_name"])}'
                f'{" · <b>archiviert</b>" if t.get("archived_at") else ""}</div></div></div>' for t in items) or '<div class="pulse-empty">Noch keine Vorlage.</div>'
            form = (f'<form method="post" action="/vorlagen/neu" class="space-y-2 mt-3"><input type="hidden" name="kind" value="{e(kind)}">'
                    f'<input class="{ws.INPUT} w-full" name="title" required maxlength="{templates.MAX_TITLE}" placeholder="Titel">'
                    f'<input class="{ws.INPUT} w-full" name="scope" maxlength="{templates.MAX_SCOPE}" placeholder="Bereich (z. B. Ticket-Kategorie, leer = alle)">'
                    f'<textarea class="{ws.INPUT} w-full" name="body" required rows="4" maxlength="{spec["max_body"]}" placeholder="Text – Platzhalter: {e(placeholders(kind))}"></textarea>'
                    f'<button class="{ws.BTN} py-1.5 px-3">Vorlage anlegen</button></form>')
            sections += f'<section class="pulse-card mb-4"><div class="pulse-card-b"><h2 class="text-sm font-bold mb-2">{e(spec["label"])}</h2>{listing}{form}</div></section>'
        if not kinds:
            sections = '<div class="pulse-empty">Für dich ist keine Vorlagenart freigegeben.</div>'
        toggle = (f'<a class="text-[11px] underline" href="/vorlagen{"" if archived else "?archiv=1"}">{"Archivierte ausblenden" if archived else "Archivierte anzeigen"}</a>')
        legacy = ""
        if ctx.perms.get("is_admin"):
            legacy = ('<form method="post" action="/vorlagen/import" class="mt-2"><button class="pulse-btn ghost">Bestehende Texte übernehmen (Bewerber-Nachrichten, Ticket-Antworten)</button></form>'
                      '<p class="text-[11px] text-slate-400">Kopiert die alten Texte in die Bibliothek; die alten Speicher bleiben unverändert, doppelte Importe werden übersprungen.</p>')
        body = (f'<div class="pulse-crumb"><a href="/me">Mein Pulse</a> › Vorlagen</div><h1 class="text-lg font-bold mb-1">Vorlagenbibliothek{__import__("pulse_help").tip("templates")}</h1>'
                f'<p class="mb-3">{toggle}</p>{sections}{legacy}')
        return HTMLResponse(render_pro_page(ws, "Vorlagen", ctx, "settings", body))

    @app.get("/vorlagen/{template_id}", response_class=HTMLResponse)
    async def template_detail(request: Request, template_id: str, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        try:
            t = templates.get(ctx.perms, ctx.guild.id, template_id)
        except Exception:  # noqa: BLE001 - missing right looks like a missing template
            t = None
        if t is None:
            raise HTTPException(status_code=404, detail="Vorlage nicht gefunden.")
        history = "".join(f'<li>Version {v["version"]} · {e(v["created_by_name"])} · {e(str(v["created_at"])[:16].replace("T", " "))}</li>'
                          for v in templates.versions(ctx.perms, ctx.guild.id, template_id))
        spec = templates.KINDS[t["kind"]]
        archived = bool(t.get("archived_at"))
        form = (f'<form method="post" action="/vorlagen/{e(t["id"])}/speichern" class="space-y-2"><input type="hidden" name="erwartet" value="{int(t["current_version"])}">'
                f'<input class="{ws.INPUT} w-full" name="title" required maxlength="{templates.MAX_TITLE}" value="{e(t["title"])}">'
                f'<textarea class="{ws.INPUT} w-full" name="body" required rows="6" maxlength="{spec["max_body"]}">{e(t["body"])}</textarea>'
                f'<p class="text-[11px] text-slate-400">Platzhalter: {e(placeholders(t["kind"]))}</p><button class="{ws.BTN} py-1.5 px-3">Als neue Version speichern</button></form>')
        action = (f'<form method="post" action="/vorlagen/{e(t["id"])}/{"wiederherstellen" if archived else "archivieren"}" class="mt-3"><button class="pulse-btn ghost">'
                  f'{"Wiederherstellen" if archived else "Archivieren"}</button></form>')
        body = (f'<div class="pulse-crumb"><a href="/vorlagen">Vorlagen</a> › {e(t["title"])}</div><section class="pulse-card"><div class="pulse-card-b">'
                f'<p class="text-xs text-slate-400">{e(spec["label"])} · {e(t["scope"])}{" · archiviert" if archived else ""}</p>{form}{action}'
                f'<h2 class="text-sm font-bold mt-4">Versionen</h2><ul class="text-xs list-disc ml-5">{history}</ul></div></section>')
        return HTMLResponse(render_pro_page(ws, t["title"], ctx, "settings", body))

    def fail(path, exc):
        return ws.back(path, str(exc), False)

    @app.post("/vorlagen/neu")
    async def template_create(request: Request, kind: str = Form(...), title: str = Form(...), body: str = Form(...), scope: str = Form(""), user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        require_writer(ctx)
        if not templates.can_use(ctx.perms, kind):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        uid, name = actor(ctx)
        try:
            created = templates.create(ctx.perms, ctx.guild.id, kind, scope, title, body, uid, name)
        except Exception as exc:  # noqa: BLE001 - PermissionDenied, ValueError, TemplateConflict -> message; rights failures stay generic
            if isinstance(exc, PermissionError):
                raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
            return fail("/vorlagen", exc)
        ws.log_audit(name, uid, "Vorlage angelegt", f'{created["id"]} · {kind}')
        return ws.back(f'/vorlagen/{created["id"]}', "Vorlage angelegt.")

    @app.post("/vorlagen/{template_id}/speichern")
    async def template_save(request: Request, template_id: str, title: str = Form(...), body: str = Form(...), erwartet: str = Form(""), user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        require_writer(ctx)
        uid, name = actor(ctx)
        try:
            existing = templates.get(ctx.perms, ctx.guild.id, template_id)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        if existing is None:
            raise HTTPException(status_code=404, detail="Vorlage nicht gefunden.")
        try:
            updated = templates.update(ctx.perms, ctx.guild.id, template_id, title, body, uid, name, expected_version=int(erwartet) if erwartet.isdigit() else None)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        except LookupError:
            raise HTTPException(status_code=404, detail="Vorlage nicht gefunden.")
        except Exception as exc:  # noqa: BLE001 - ValueError / TemplateConflict
            return fail(f"/vorlagen/{template_id}", exc)
        ws.log_audit(name, uid, "Vorlage geändert", f'{template_id} · Version {updated["version"]}')
        return ws.back(f"/vorlagen/{template_id}", f'Als Version {updated["version"]} gespeichert.')

    def toggle(archive: bool):
        async def handler(request: Request, template_id: str, user_session: str = Cookie(None)):
            ctx = ws.auth(request, user_session)
            require_writer(ctx)
            uid, name = actor(ctx)
            try:
                done = (templates.archive if archive else templates.restore)(ctx.perms, ctx.guild.id, template_id, uid, name)
            except PermissionError:
                raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
            except Exception as exc:  # noqa: BLE001
                return fail(f"/vorlagen/{template_id}", exc)
            if not done:
                raise HTTPException(status_code=404, detail="Vorlage nicht gefunden.")
            ws.log_audit(name, uid, "Vorlage archiviert" if archive else "Vorlage wiederhergestellt", template_id)
            return ws.back("/vorlagen", "Vorlage archiviert." if archive else "Vorlage wiederhergestellt.")
        return handler

    app.add_api_route("/vorlagen/{template_id}/archivieren", toggle(True), methods=["POST"])
    app.add_api_route("/vorlagen/{template_id}/wiederherstellen", toggle(False), methods=["POST"])

    @app.post("/vorlagen/import")
    async def template_import(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None, admin=True)
        uid, name = actor(ctx)
        try:
            result = templates.import_legacy(ctx.perms, ctx.guild.id, uid, name)
        except PermissionError:
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        ws.log_audit(name, uid, "Vorlagen übernommen", str(result)[:200])
        return ws.back("/vorlagen", "Bestehende Texte übernommen.")
