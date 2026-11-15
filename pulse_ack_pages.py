"""Audit #53: Dashboard-Seite ``/bestaetigungen`` (eigene offene Bestätigungen, Abdeckung für Führung, neue Dienstanweisung)."""
from __future__ import annotations

from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

import pulse_ack as ack


def is_manager(perms: dict) -> bool:
    perms = perms or {}
    return bool(perms.get("can_manage_announcements") or perms.get("can_promote") or perms.get("is_admin"))


def team_pairs(guild, config) -> list[tuple[str, str]]:
    import pulse_access
    return [(str(m.id), getattr(m, "display_name", str(m.id))) for m in pulse_access.team_members(guild, config)[:300]]


def require_and_notify(guild, config, kind: str, subject_id, title: str, *, actor_id="", actor_name="", due_days=None) -> bool:
    """Mark the post and tell every team member once through the shared inbox contract. Never raises."""
    try:
        created = ack.require(kind, subject_id, title, actor_id=actor_id, actor_name=actor_name, due_days=due_days)
        if not created:
            return False
        import pulse_inbox_contract as inbox
        import pulse_db
        recipients = [uid for uid, _name in team_pairs(guild, config) if uid != str(actor_id)]
        if recipients:
            def notifier(uid, head, text, kind="info", url="", dedupe_key=None, category=None):
                return pulse_db.notify(uid, head, text, kind, ack.LIST_URL, dedupe_key, 86400, category)
            inbox.deliver(recipients, "announcement", "ack_required", f"✅ Bestätigung erforderlich: {title}"[:160],
                          "Bitte lies den Beitrag und bestätige ihn.", correlation_id=f"ack:{kind}:{subject_id}", notifier=notifier)
        return True
    except Exception:  # noqa: BLE001 - the post itself must still be published
        return False


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page
    e = ws.esc

    @app.get("/bestaetigungen", response_class=HTMLResponse)
    async def ack_page(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        pending = ack.pending_for(ctx.user["id"])
        rows = "".join(
            f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(p["title"])}</div>'
            f'<div class="pulse-row-meta">{e(p["kind_label"])} · von {e(p["required_by_name"] or "Team")}'
            f'{" · ⏰ Frist " + e(p["due_at"][:10]) if p["due_at"] else ""}{" · <b>überfällig</b>" if p["overdue"] else ""}</div></div>'
            f'<form method="post" action="/bestaetigungen/{e(p["kind"])}/{e(p["subject_id"])}/bestaetigen"><button class="pulse-btn primary">Gelesen</button></form></div>'
            for p in pending) or '<div class="pulse-empty">Nichts zu bestätigen. 🎉</div>'
        manager_html = ""
        if is_manager(ctx.perms):
            team = team_pairs(ctx.guild, ctx.config)
            cov = "".join(
                f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(o["title"])}</div>'
                f'<div class="pulse-row-meta">{e(o["kind_label"])} · {len(o["coverage"]["confirmed"])}/{o["coverage"]["total"]} bestätigt'
                f'{" · offen: " + e(", ".join(n for _i, n in o["coverage"]["missing"][:25])) if o["coverage"]["missing"] else " · ✅ vollständig"}</div></div></div>'
                for o in ack.overview(team)) or '<div class="pulse-empty">Noch keine Bestätigungspflichten.</div>'
            form = (f'<form method="post" action="/bestaetigungen/neu" class="space-y-2 mt-3"><input class="{ws.INPUT} w-full" name="title" required maxlength="160" placeholder="Titel der Dienstanweisung">'
                    f'<textarea class="{ws.INPUT} w-full" name="content" required maxlength="4000" rows="4" placeholder="Text"></textarea>'
                    f'<input class="{ws.INPUT}" type="number" name="frist" min="1" max="{ack.MAX_DAYS}" placeholder="Frist in Tagen (optional)">'
                    f'<button class="{ws.BTN} py-2 px-4">Dienstanweisung veröffentlichen</button></form>')
            manager_html = (f'<section class="pulse-card mt-4"><div class="pulse-card-b"><h2 class="text-sm font-bold mb-2">Abdeckung (Führung)</h2>{cov}'
                            f'<h2 class="text-sm font-bold mt-4">Neue Dienstanweisung mit Bestätigung</h2>{form}</div></section>')
        body = (f'<div class="pulse-crumb"><a href="/me">Mein Pulse</a> › Bestätigungen</div><section class="pulse-card"><div class="pulse-card-b">'
                f'<h2 class="text-sm font-bold mb-2">Meine offenen Bestätigungen ({len(pending)}){__import__("pulse_help").tip("ack")}</h2>{rows}</div></section>{manager_html}')
        return HTMLResponse(render_pro_page(ws, "Bestätigungen", ctx, "announcements", body))

    @app.post("/bestaetigungen/{kind}/{subject_id}/bestaetigen")
    async def ack_confirm(request: Request, kind: str, subject_id: str, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        name = ctx.user.get("global_name") or ctx.user.get("username") or ""
        if not ack.acknowledge(kind, subject_id, ctx.user["id"], name):
            raise HTTPException(status_code=404, detail="Für diesen Beitrag ist keine Bestätigung erforderlich.")
        ws.log_audit(name, ctx.user["id"], "Lesebestätigung", f"{kind} · {subject_id}")
        return ws.back("/bestaetigungen", "Danke, als gelesen bestätigt.")

    @app.post("/bestaetigungen/neu")
    async def ack_new(request: Request, title: str = Form(...), content: str = Form(...), frist: str = Form(""), user_session: str = Cookie(None)):
        import pulse_db
        ctx = ws.auth(request, user_session, perm=None)
        if not is_manager(ctx.perms):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        name = ctx.user.get("global_name") or "Team"
        head, text = title.strip()[:160], content.strip()[:4000]
        if not head or not text:
            return ws.back("/bestaetigungen", "Titel und Text dürfen nicht leer sein.", False)
        aid = pulse_db.create_announcement(head, text, ctx.user["id"], name, "info")
        days = int(frist) if frist.strip().isdigit() else None
        require_and_notify(ctx.guild, ctx.config, "instruction", aid, head, actor_id=ctx.user["id"], actor_name=name, due_days=days)
        ws.log_audit(name, ctx.user["id"], "Dienstanweisung veröffentlicht", head)
        return ws.back("/bestaetigungen", "Dienstanweisung veröffentlicht, das Team wurde benachrichtigt.")
