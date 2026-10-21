"""Ticket-Zentrale (/tickets): guided case handling on top of the existing /tickets/action route.

Shows who is waiting on whom and why a ticket is flagged, sorts by an explainable rule
(overdue → priority → longest wait), filters/searches/paginates, and offers the existing,
server-side authorised actions (claim, release, priority, close with transcript, reopen).
"""
from __future__ import annotations

import os
from urllib.parse import quote, urlencode

from pulse_ticket_design import automation_settings
from pulse_ticket_triage import format_wait, matches_filter, triage

PAGE_SIZE = 25
VIEWS = (
    ("active", "Alle offenen"),
    ("waiting", "Wartet auf Team"),
    ("overdue", "Überfällig"),
    ("mine", "Meine"),
    ("open", "Unübernommen"),
    ("claimed", "Übernommen"),
    ("closed", "Geschlossen"),
    ("all", "Alle"),
)
STATE_PILL = {"new": "warn", "waiting_team": "warn", "waiting_user": "", "in_progress": "good", "closed": ""}
PRIORITY_PILL = {"urgent": ("🚨 Dringend", "bad"), "high": ("🟠 Hoch", "warn"), "normal": ("🟢 Normal", "good"), "low": ("⚪ Niedrig", "")}


def render_ticket_center(ws, ctx, request, render_pro_page) -> str:
    import pulse_db as db
    from datetime import datetime, timezone

    e = ws.esc
    guild = ctx.guild
    params = request.query_params
    view = params.get("status", "active")
    if view not in {key for key, _ in VIEWS}:
        view = "active"
    query = str(params.get("q", "")).strip()[:100]
    category = str(params.get("category", "")).strip()[:80]
    try:
        page = max(1, int(params.get("page", "1")))
    except ValueError:
        page = 1
    manager = bool(ctx.perms.get("can_manage_tickets") or ctx.perms.get("can_promote") or ctx.perms.get("is_admin"))
    actor_id = str(ctx.user["id"])
    now = datetime.now(timezone.utc)
    settings = automation_settings(ws.pulse_ticket_config().get("automation"))
    registry = ws.pulse_ticket_category_registry()

    tickets = [t for t in db.list_tickets(limit=2000) if str(t.get("guild_id")) in {"", "None", str(guild.id)}]
    rows = [(t, triage(t, now, settings)) for t in tickets]
    open_rows = [(t, i) for t, i in rows if i["state"] != "closed"]
    counts = {key: sum(1 for t, i in rows if matches_filter(t, i, key, actor_id)) for key, _ in VIEWS}

    selected = [(t, i) for t, i in rows if matches_filter(t, i, view, actor_id)]
    if category in registry:
        selected = [(t, i) for t, i in selected if str(t.get("category")) == category]
    if query:
        needle = query.casefold()
        selected = [
            (t, i) for t, i in selected
            if needle in " ".join(str(x or "") for x in (
                t.get("id"), t.get("user_name"), t.get("user_id"), t.get("claimed_by_name"),
                (registry.get(str(t.get("category"))) or {}).get("name"), t.get("close_reason"),
            )).casefold()
        ]
    selected.sort(key=lambda pair: pair[1]["sort_key"])
    total = len(selected)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    visible = selected[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

    def link(**changes) -> str:
        values = {"status": view, "q": query, "category": category, "page": page}
        values.update(changes)
        return "/tickets?" + urlencode({k: v for k, v in values.items() if v not in ("", None)})

    def pill(text, kind=""):
        return f'<span class="pulse-pill {kind}">{e(text)}</span>'

    def row(t, info):
        tid = e(t.get("id"))
        meta = registry.get(str(t.get("category")), {})
        cat = f'{meta.get("emoji") or "🎫"} {meta.get("name") or t.get("category") or "Ticket"}'
        pr = PRIORITY_PILL.get(str(t.get("priority") or "normal"), PRIORITY_PILL["normal"])
        state_text = info["label"] + (f" · {format_wait(info['waited_seconds'])}" if info["waited_seconds"] is not None and info["state"] != "closed" else "")
        state_kind = "bad" if info["overdue"] else STATE_PILL.get(info["state"], "")
        channel_id = str(t.get("channel_id") or "")
        actions = []
        if channel_id.isdigit() and info["state"] != "closed":
            actions.append(f'<a class="pulse-btn ghost" target="_blank" rel="noopener" href="https://discord.com/channels/{guild.id}/{channel_id}">Discord ↗</a>')
        if manager and info["state"] != "closed":
            if not t.get("claimed_by_id"):
                actions.append(f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{tid}"><input type="hidden" name="action" value="claim"><button class="pulse-btn primary">📥 Übernehmen</button></form>')
            elif str(t.get("claimed_by_id")) == actor_id or ctx.perms.get("is_admin"):
                actions.append(f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{tid}"><input type="hidden" name="action" value="unclaim"><button class="pulse-btn ghost">↩ Freigeben</button></form>')
            options = "".join(
                f'<option value="{key}" {"selected" if str(t.get("priority") or "normal") == key else ""}>{label}</option>'
                for key, (label, _) in PRIORITY_PILL.items()
            )
            actions.append(
                f'<form method="post" action="/tickets/action" class="flex gap-1"><input type="hidden" name="ticket_id" value="{tid}"><input type="hidden" name="action" value="priority">'
                f'<select name="priority" class="pulse-input !w-auto !py-2 !text-xs" aria-label="Priorität">{options}</select><button class="pulse-btn ghost">✓</button></form>'
            )
            actions.append(
                f'<details class="tc-close"><summary class="pulse-btn bad">🔒 Schließen</summary>'
                f'<form method="post" action="/tickets/action" class="flex flex-col sm:flex-row gap-2 mt-2" onsubmit="return confirm(\'Ticket schließen? Das Transkript wird gespeichert und der Kanal gelöscht.\')">'
                f'<input type="hidden" name="ticket_id" value="{tid}"><input type="hidden" name="action" value="close">'
                f'<input name="reason" class="pulse-input" required maxlength="500" placeholder="Abschlussgrund (Pflicht)"><button class="pulse-btn bad">Schließen</button></form></details>'
            )
        if info["state"] == "closed":
            if t.get("transcript_path"):
                actions.append(f'<a class="pulse-btn ghost" href="/ticket/transcript/{quote(os.path.basename(str(t.get("transcript_path"))))}">📄 Transkript</a>')
            if manager:
                actions.append(f'<form method="post" action="/tickets/action"><input type="hidden" name="ticket_id" value="{tid}"><input type="hidden" name="action" value="reopen"><button class="pulse-btn ghost">🔓 Wieder öffnen</button></form>')
        detail = (
            f'<div class="text-[11px] mt-1 {"text-rose-500" if info["overdue"] else "text-slate-400"}">{e(info["reason"])}</div>'
            + (f'<div class="text-[11px] text-indigo-500 mt-0.5">Nächster Schritt: {e(info["next_step"])}</div>' if info["state"] != "closed" else "")
            + (f'<div class="text-[11px] text-slate-400 mt-0.5">Grund: {e(t.get("close_reason"))}</div>' if info["state"] == "closed" and t.get("close_reason") else "")
        )
        return (
            f'<article class="pulse-row tc-row {"tc-overdue" if info["overdue"] else ""}">'
            f'<div class="pulse-row-main" style="flex:1;min-width:220px"><div class="flex flex-wrap items-center gap-2">'
            f'<span class="pulse-row-title">{e(t.get("user_name") or "Unbekannt")}</span>{pill(pr[0], pr[1])}{pill(state_text, state_kind)}</div>'
            f'<div class="pulse-row-meta">{e(cat)} · <code>{tid}</code> · eröffnet {e(str(t.get("opened_at") or "")[:16].replace("T", " "))} UTC · Bearbeiter: {e(t.get("claimed_by_name") or "niemand")}</div>'
            f'{detail}</div><div class="flex flex-wrap gap-2 items-start justify-end">{"".join(actions)}</div></article>'
        )

    tabs = "".join(
        f'<a class="tc-tab {"active" if key == view else ""}" href="{e(link(status=key, page=1))}">{label} <span>{counts[key]}</span></a>'
        for key, label in VIEWS
    )
    category_options = '<option value="">Alle Kategorien</option>' + "".join(
        f'<option value="{e(key)}" {"selected" if key == category else ""}>{e(meta.get("emoji") or "🎫")} {e(meta.get("name") or key)}</option>'
        for key, meta in registry.items()
    )
    pager = ""
    if pages > 1:
        prev_link = f'<a class="pulse-btn ghost" href="{e(link(page=page - 1))}">← Zurück</a>' if page > 1 else ""
        next_link = f'<a class="pulse-btn ghost" href="{e(link(page=page + 1))}">Weiter →</a>' if page < pages else ""
        pager = f'<div class="flex items-center justify-between gap-2 mt-3 text-xs text-slate-400">{prev_link}<span>Seite {page} von {pages} · {total} Tickets</span>{next_link}</div>'
    empty_text = "Keine Tickets für diese Suche." if (query or category) else "Hier ist gerade nichts offen. 🎉"
    overdue_n = counts["overdue"]
    stats = [
        ("▣", "Offen", len(open_rows)),
        ("⏳", "Wartet auf Team", counts["waiting"]),
        ("⏰", "Überfällig", overdue_n),
        ("🚨", "Dringend", sum(1 for t, i in open_rows if t.get("priority") == "urgent")),
        ("👤", "Meine", counts["mine"]),
    ]
    stat_html = "".join(f'<div class="pulse-stat"><div class="icon">{i}</div><div class="label">{l}</div><div class="value">{v}</div></div>' for i, l, v in stats)
    hint = "" if manager else '<div class="pulse-alert mb-3">Du kannst Tickets ansehen. Übernehmen, Schließen und Priorität ändern erfordert die Berechtigung „Tickets verwalten“.</div>'
    admin_link = '<a href="/tickets/panels" class="pulse-btn ghost">🎛️ Panel-Builder</a>' if ctx.perms.get("is_admin") else ""

    css = """<style>
.tc-tabs{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px;margin-bottom:12px}
.tc-tab{flex:0 0 auto;padding:8px 12px;border-radius:12px;border:1px solid var(--border);background:var(--surface);font-size:12px;font-weight:700;white-space:nowrap}
.tc-tab span{margin-left:4px;font-size:10px;opacity:.7}.tc-tab.active{background:linear-gradient(135deg,var(--p),var(--p2));color:#fff;border-color:transparent}
.tc-row{align-items:flex-start;flex-wrap:wrap}.tc-overdue{border-color:rgba(240,68,56,.35);background:rgba(240,68,56,.04)}
.tc-close summary{list-style:none;cursor:pointer}.tc-close summary::-webkit-details-marker{display:none}
.tc-close[open]{flex-basis:100%}
</style>"""
    body = f'''{css}
<div class="pulse-topbar"><div><div class="pulse-section-title">▣ Ticket-Zentrale</div><div class="pulse-section-sub">Wer wartet auf wen – sortiert nach Überfälligkeit, Priorität und Wartezeit.</div></div>
 <div class="flex gap-2">{admin_link}<a href="/me" class="pulse-btn ghost">★ Mein Pulse</a></div></div>
<div class="pulse-stat-grid">{stat_html}</div>
<div class="pulse-live-strip pulse-row-meta mb-3" data-live-view role="status" aria-live="polite"><span class="pulse-status-dot bg-emerald-500" aria-hidden="true"></span> Live: <b data-live="open_tickets">…</b> offen · <b data-live="unclaimed_tickets">…</b> ohne Bearbeiter · zuletzt aktualisiert <span data-live-time>…</span><span class="pulse-live-error"> · Verbindung gestört, neuer Versuch läuft</span></div>
{hint}
<section class="pulse-card"><div class="pulse-card-b">
 <nav class="tc-tabs">{tabs}</nav>
 <form method="get" action="/tickets" class="flex flex-col sm:flex-row gap-2 mb-3">
  <input type="hidden" name="status" value="{e(view)}">
  <input name="q" value="{e(query)}" class="pulse-input" placeholder="Suche: Ticket-ID, Name, Discord-ID, Bearbeiter, Grund …">
  <select name="category" class="pulse-input sm:!w-60">{category_options}</select>
  <button class="pulse-btn primary">Suchen</button>{f'<a class="pulse-btn ghost" href="/tickets?status={e(view)}">Zurücksetzen</a>' if (query or category) else ''}
 </form>
 <div class="pulse-list">{"".join(row(t, i) for t, i in visible) or f'<div class="pulse-empty">{empty_text}</div>'}</div>
 {pager}
 <p class="text-[10px] text-slate-400 mt-3">Überfällig nach den Schwellen der Ticket-Automatik: Übernahme {settings["unclaimed_ping_minutes"] or 30} Min., Team-Antwort {settings["staff_reply_hours"] or 12} Std., Ersteller-Antwort {settings["user_reminder_hours"] or 24} Std. Wartezeiten stammen aus den letzten Nachrichten im Ticket; für ältere Tickets kann die Angabe fehlen.</p>
</div></section>'''
    return render_pro_page(ws, "Tickets", ctx, "tickets", body)
