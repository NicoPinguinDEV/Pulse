"""Verwaltung (administration area): hub page and "Discord-Auftritt".

/admin            – Einstieg in die Verwaltung; zeigt nur Bereiche, die die Person darf.
/admin/discord    – Embed-Branding sowie Willkommen & Abschied (Vorlagen, Kanäle, Banner,
                    Vorschau, kontrollierte Testnachricht). Nur Administratoren.

Every route authorizes server-side; the navigation only mirrors these checks.
"""
from __future__ import annotations

import asyncio
import html
import os
import re
import secrets
from datetime import datetime, timezone

from fastapi import Cookie, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

import pulse_db
from pulse_embed_design import DEFAULT_COLORS, branding, normalize_hex, save_branding
import pulse_verification as verification
from pulse_welcome import (
    BANNER_DIR, KINDS, MAX_BANNER_BYTES, PLACEHOLDERS, SETTING_KEY, build_member_message,
    normalize_settings, sample_member, validate_settings,
)

# Pages that belong to the administration area (sidebar switches to "Verwaltung").
ADMIN_PAGE_KEYS = frozenset({
    "admin", "admin-discord", "ticket-panels", "settings", "backups", "system", "announcements", "social",
    "admin-privacy",
})


def admin_sections(perms: dict) -> list[tuple]:
    """(key, href, icon, label, description) of administration areas this person may open."""
    is_admin = bool(perms.get("is_admin"))
    announcements = is_admin or bool(perms.get("can_manage_announcements"))
    sections = [
        ("admin-discord", "/admin/discord", "🎨", "Discord-Auftritt", "Verifikation, Willkommen & Abschied, Embed-Design", is_admin),
        ("ticket-panels", "/tickets/panels", "🎛️", "Ticket-Panels", "Panels, Kategorien, Begrüßungen, Formulare", is_admin),
        ("admin-panels", "/admin/panels", "▦", "Discord-Panels", "Alle Panels, Status, neu veröffentlichen", is_admin),
        ("mod-cases", "/mod-cases", "⚖", "Bürger-Moderation", "Fallakte: Verwarnungen, Timeouts, Kicks, Bans",
         is_admin or bool(perms.get("can_moderate_kicks") or perms.get("can_moderate_bans"))),
        ("announcements", "/announcements", "📢", "Ankündigungen", "Globale Team- und Servernachrichten", announcements),
        ("social", "/social", "📣", "Social Media", "Creator-Kanäle und Video-Ankündigungen", announcements),
        ("settings", "/settings", "⚙", "Einstellungen & Rechte", "Rollen, Dashboard-Rechte, Module, Kanäle", is_admin),
        ("backups", "/backups", "🗄", "Backups", "Sicherungen erstellen und wiederherstellen", is_admin),
        ("admin-privacy", "/admin/privacy", "🔒", "Datenschutz", "Aufbewahrungsfristen, Auskunft, Hinweis", is_admin),
        ("system", "/system", "♥", "Systemstatus", "Health, RAM, Diagnose", is_admin),
    ]
    return [s[:5] for s in sections if s[5]]


def can_open_admin(perms: dict) -> bool:
    return bool(admin_sections(perms))


# --------------------------------------------------------------------------- preview
_E = html.escape


def _inline_markdown(text: str, guild=None) -> str:
    """Tiny Discord-markdown preview: **bold**, *italic*, line breaks, <#channel>, <t:…>."""
    out = _E(str(text or ""))
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", out)

    def channel(match):
        cid = int(match.group(1))
        ch = guild.get_channel(cid) if guild is not None and hasattr(guild, "get_channel") else None
        return f'<span class="dc-mention">#{_E(getattr(ch, "name", None) or str(cid))}</span>'

    out = re.sub(r"&lt;#(\d+)&gt;", channel, out)

    def stamp(match):
        try:
            dt = datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc)
            return dt.strftime("%d.%m.%Y") if match.group(2) == "D" else dt.strftime("%d.%m.%Y %H:%M")
        except (OverflowError, ValueError):
            return "–"

    out = re.sub(r"&lt;t:(\d+):([a-zA-Z])&gt;", stamp, out)
    out = out.replace("@Beispielmitglied", '<span class="dc-mention">@Beispielmitglied</span>')
    return out.replace("\n", "<br>")


def render_preview(payload: dict | None, guild=None, banner_href: str = "", button_label: str = "") -> str:
    """HTML mock of the Discord message in the currently selected style."""
    if not payload:
        return '<div class="pulse-empty">Keine Vorschau verfügbar.</div>'
    spec = payload["spec"]
    image = spec.get("image_url") or ""
    if image.startswith("attachment://"):
        image = banner_href
    fields = "".join(
        f'<div class="dc-field"><div class="dc-field-name">{_E(n)}</div><div>{_inline_markdown(v, guild)}</div></div>'
        for n, v, _ in spec["fields"]
    )
    thumb = f'<img class="dc-thumb" src="{_E(spec["thumbnail_url"])}" alt="Profilbild">' if spec.get("thumbnail_url") else ""
    img = f'<img class="dc-image" src="{_E(image)}" alt="Banner">' if image else '<div class="dc-noimage">Kein Banner gesetzt – das Embed bleibt ohne großes Bild.</div>'
    button = (f'<div style="margin-top:10px"><span class="dc-button">✅ {_E(button_label)}</span></div>' if button_label else "")
    brand = branding()
    if brand.get("style") == "modern":
        # Components V2: no separate content and (by default) no colored bar – like GalaxyBot.
        mention = payload.get("content") or ""
        mention_html = f'{_inline_markdown(mention, guild)}<br>' if mention and mention not in spec["description"] else ""
        accent = f"border-left:4px solid #{spec['color']:06x};" if brand.get("accent_bar") else ""
        img_modern = f'<img class="dc-image" src="{_E(image)}" alt="Banner">' if image else ""
        return f'''<div class="dc-embed dc-modern" style="{accent}">
 <div class="dc-body"><div class="dc-main">{mention_html}
  <div class="dc-title dc-h3">{_E(spec["title"])}</div>
  <div class="dc-desc">{_inline_markdown(spec["description"], guild)}</div>{fields}
 </div>{thumb}</div>{img_modern}{button}
 <div class="dc-sep"></div><div class="dc-footer">{_E(spec["footer"])}</div></div>'''
    content = f'<div class="dc-content">{_inline_markdown(payload.get("content"), guild)}</div>' if payload.get("content") else ""
    return f'''{content}<div class="dc-embed" style="border-left-color:#{spec["color"]:06x}">
 <div class="dc-body"><div class="dc-main">
  <div class="dc-title">{_E(spec["title"])}</div>
  <div class="dc-desc">{_inline_markdown(spec["description"], guild)}</div>{fields}
 </div>{thumb}</div>{img}
 <div class="dc-footer">{_E(spec["footer"])} · Heute</div></div>'''


PREVIEW_CSS = """<style>
.dc-wrap{background:#313338;border-radius:14px;padding:14px;color:#dbdee1;font-size:13px;line-height:1.45}
.dc-content{margin-bottom:6px}.dc-mention{background:rgba(88,101,242,.3);color:#c9cdfb;border-radius:3px;padding:0 2px}
.dc-embed{background:#2b2d31;border-left:4px solid #5b5cf0;border-radius:4px;padding:10px 14px 12px;max-width:520px}
.dc-modern{border-left:0;border:1px solid #3f4147;background:#393a41;border-radius:8px;padding:14px 16px}
.dc-h3{font-size:16px;font-weight:800}.dc-sep{height:1px;background:#4e5058;margin:12px 0 8px}
.dc-button{display:inline-block;background:#248046;color:#fff;border-radius:6px;padding:6px 14px;font-size:13px;font-weight:600}
.dc-body{display:flex;gap:14px;justify-content:space-between}.dc-main{min-width:0;flex:1}
.dc-title{font-weight:700;color:#f2f3f5;margin-bottom:6px;word-break:break-word}.dc-desc{word-break:break-word}
.dc-thumb{width:72px;height:72px;border-radius:6px;object-fit:cover;flex-shrink:0}
.dc-image{margin-top:12px;width:100%;max-height:260px;object-fit:cover;border-radius:6px}
.dc-noimage{margin-top:10px;font-size:11px;color:#949ba4;border:1px dashed #4e5058;border-radius:6px;padding:10px;text-align:center}
.dc-field{margin-top:8px}.dc-field-name{font-weight:700;color:#f2f3f5;font-size:12px}
.dc-footer{margin-top:10px;font-size:11px;color:#949ba4}
.pulse-check{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600}
.pulse-check input{width:16px;height:16px}
.pulse-crumb{font-size:11px;color:#94a3b8;margin-bottom:8px}.pulse-crumb a{color:#6366f1}
</style>"""


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page
    from pulse_ticket_design import detect_image_type

    e = ws.esc
    def banner_root():
        return ws.BASE_DIR / BANNER_DIR

    def admin_ctx(request, session):
        return ws.auth(request, session, perm=None, admin=True)

    def load_settings() -> dict:
        return normalize_settings(pulse_db.get_setting(SETTING_KEY, {}))

    def channel_options(guild, selected: str) -> str:
        options = ['<option value="">— Kanal wählen —</option>']
        channels = sorted(getattr(guild, "text_channels", []) or [], key=lambda c: (getattr(c, "position", 0), c.name))
        for channel in channels:
            sel = " selected" if str(channel.id) == str(selected) else ""
            options.append(f'<option value="{channel.id}"{sel}>#{e(channel.name)}</option>')
        if selected and not any(str(c.id) == str(selected) for c in channels):
            options.append(f'<option value="{e(selected)}" selected>⚠ Unbekannter Kanal {e(selected)}</option>')
        return "".join(options)

    def preview_member(ctx) -> dict:
        count = getattr(ctx.guild, "member_count", None)
        return sample_member(ctx.guild_name, count if isinstance(count, int) else None)

    # ------------------------------------------------------------------ hub
    @app.get("/admin", response_class=HTMLResponse)
    async def admin_hub(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None)
        sections = admin_sections(ctx.perms)
        if not sections:
            raise HTTPException(status_code=403, detail="Die Verwaltung ist nur für Leitung und Administratoren freigegeben.")
        cards = "".join(
            f'<a href="{href}" class="pulse-card" style="display:block;padding:18px;text-decoration:none">'
            f'<div style="font-size:24px">{icon}</div><div class="pulse-section-title" style="margin-top:8px">{e(label)}</div>'
            f'<div class="text-xs text-slate-400 mt-1">{e(desc)}</div></a>'
            for _, href, icon, label, desc in sections
        )
        settings = load_settings()
        status = []
        for kind, label in (("welcome", "Willkommen"), ("leave", "Abschied")):
            on = settings[kind]["enabled"] and settings[kind]["channel_id"]
            status.append(f'<span class="pulse-pill {"good" if on else ""}">{"🟢" if on else "⚪"} {label}</span>')
        vset = verification.normalize_settings(pulse_db.get_setting(verification.SETTING_KEY, {}))
        v_on = vset["enabled"] and vset["channel_id"]
        status.append(f'<span class="pulse-pill {"good" if v_on else ""}">{"🟢" if v_on else "⚪"} Verifikation</span>')
        body = f'''{PREVIEW_CSS}
<div class="pulse-hero"><div class="pulse-hero-grid"><div>
 <div class="pulse-kicker">Verwaltung</div><div class="pulse-title">Konfiguration &amp; Systembetrieb</div>
 <div class="pulse-sub">Hier liegen alle Einstellungen, die den Server oder den Bot global verändern. Die tägliche Teamarbeit findest du im Bereich <a href="/me" style="color:inherit;font-weight:800;text-decoration:underline">Team</a>.</div></div>
 <div class="pulse-hero-box"><div class="label">Discord-Nachrichten</div><div class="mt-2 flex flex-wrap gap-2">{"".join(status)}</div></div>
</div></div>
<div class="grid gap-4" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">{cards}</div>'''
        return HTMLResponse(render_pro_page(ws, "Verwaltung", ctx, "admin", body))

    # ------------------------------------------------------------------ Discord-Auftritt
    def load_verification() -> dict:
        return verification.normalize_settings(pulse_db.get_setting(verification.SETTING_KEY, {}))

    def verification_status(guild) -> list[str]:
        """Role/permission problems, computed from the live guild (empty = ready)."""
        try:
            from cogs.verification import problems_for
            return problems_for(guild)
        except Exception:
            return ["Rollenstatus konnte nicht geprüft werden (Bot nicht bereit)."]

    def verification_section(ctx, vset: dict, verrors: list[str] | None) -> str:
        guild = ctx.guild
        problems = verification_status(guild)

        def role_line(role_id, label):
            role = guild.get_role(role_id) if hasattr(guild, "get_role") else None
            mark = "🟢" if role else "🔴"
            return f'<div>{mark} {label}: <strong>{e(getattr(role, "name", None) or "nicht gefunden")}</strong> <span class="text-slate-400">({role_id})</span></div>'

        status_rows = role_line(verification.UNVERIFIED_ROLE_ID, "Beim Beitritt") + role_line(verification.CITIZEN_ROLE_ID, "Nach Verifikation")
        problem_html = "".join(f"<li>{e(p)}</li>" for p in problems)
        problem_box = (f'<div class="pulse-alert mt-2" style="border-color:rgba(240,68,56,.35)"><strong>Noch nicht bereit:</strong><ul class="list-disc pl-5 mt-1">{problem_html}</ul></div>'
                       if problems else '<div class="text-xs text-emerald-500 mt-2">✓ Rollen vorhanden, Pulse darf sie vergeben.</div>')
        err_alert = ""
        if verrors:
            err_alert = ('<div class="pulse-alert" style="border-color:rgba(240,68,56,.35)"><strong>Nicht gespeichert:</strong><ul class="list-disc pl-5 mt-1">'
                         + "".join(f"<li>{e(x)}</li>" for x in verrors) + "</ul></div>")
        recent = "".join(f'<li><span class="text-slate-400">{e(str(x.get("at", ""))[:16].replace("T", " "))}</span> {e(x.get("text", ""))}</li>'
                         for x in reversed(vset.get("errors", [])))
        recent_html = (f'<details class="mt-3"><summary class="text-xs font-semibold cursor-pointer text-amber-500">Letzte Probleme ({len(vset["errors"])})</summary><ul class="text-[11px] mt-2 space-y-1">{recent}</ul></details>'
                       if recent else "")
        published = ""
        if vset.get("message_id") and vset.get("published_channel_id"):
            link = f"https://discord.com/channels/{e(getattr(guild, 'id', ''))}/{e(vset['published_channel_id'])}/{e(vset['message_id'])}"
            published = f'<a class="text-xs text-indigo-500 font-semibold" href="{link}" target="_blank" rel="noopener">Veröffentlichte Nachricht öffnen ↗</a>'
        banner_href = f"/admin/discord/banner/{e(vset['banner_file'])}" if vset.get("banner_file") else ""
        remove_banner_box = ('<label class="pulse-check"><input type="checkbox" name="remove_banner" value="1"> Hochgeladenes Banner entfernen</label>'
                             if vset.get("banner_file") else "")
        preview = render_preview({"spec": verification.build_panel_spec(vset, icon_url="", base_dir=str(ws.BASE_DIR)), "content": ""},
                                 guild, banner_href)
        if branding().get("style") == "modern":
            preview = render_preview({"spec": verification.build_panel_spec(vset, icon_url="", base_dir=str(ws.BASE_DIR)), "content": ""},
                                     guild, banner_href, button_label=vset["button_label"])
        else:
            preview += f'<div style="margin-top:8px"><span class="dc-button">✅ {e(vset["button_label"])}</span></div>'
        active = vset["enabled"] and vset["channel_id"]
        unverified_role = guild.get_role(verification.UNVERIFIED_ROLE_ID) if hasattr(guild, "get_role") else None
        waiting = len(getattr(unverified_role, "members", []) or []) if unverified_role else 0
        reminder_state = f" Aktuell unverifiziert: <strong>{waiting}</strong>."
        if vset.get("reminder_last_at"):
            reminder_state += f" Letzte Erinnerung: {e(vset['reminder_last_at'][:16].replace('T', ' '))} UTC."
        if unverified_role is not None and not getattr(unverified_role, "mentionable", True):
            reminder_state += (" ⚠️ Die Rolle ist nicht erwähnbar – Pulse braucht dann das Recht „@everyone, @here und alle Rollen"
                               " erwähnen“ im Kanal, sonst kommt der Ping nicht an.")
        return f'''
<section class="pulse-card" id="verification"><div class="pulse-card-h"><div class="pulse-section-title">🛡️ Verifikation neuer Mitglieder</div>
 <span class="pulse-pill {"good" if active else ""}">{"aktiv" if active else "aus"}</span></div>
 <div class="pulse-card-b">{err_alert}<div class="grid lg:grid-cols-2 gap-5">
  <div>
   <div class="text-xs space-y-1">{status_rows}</div>{problem_box}
   <form method="post" action="/admin/discord/verification" enctype="multipart/form-data" class="grid gap-3 mt-4">
    <label class="pulse-check"><input type="checkbox" name="enabled" value="1"{" checked" if vset["enabled"] else ""}> Verifikation aktiv (Rolle beim Beitritt + Button)</label>
    <label class="grid gap-1 text-xs font-semibold">Verifikationskanal<select class="pulse-input" name="channel_id">{channel_options(guild, vset["channel_id"])}</select></label>
    <label class="grid gap-1 text-xs font-semibold">Titel<input class="pulse-input" name="title" maxlength="256" value="{e(vset["title"])}"></label>
    <label class="grid gap-1 text-xs font-semibold">Text<textarea class="pulse-input" name="description" rows="8" maxlength="3500">{e(vset["description"])}</textarea></label>
    <div class="grid sm:grid-cols-2 gap-3">
     <label class="grid gap-1 text-xs font-semibold">Button-Text<input class="pulse-input" name="button_label" maxlength="80" value="{e(vset["button_label"])}"></label>
    </div>
    <div class="grid gap-2 rounded-xl border border-slate-200 dark:border-slate-800 p-3">
     <div class="text-xs font-bold">Banner (großes Bild unten)</div>
     <input type="file" name="banner_upload" accept="image/png,image/jpeg,image/gif,image/webp" class="text-xs">
     <input class="pulse-input" name="banner_url" maxlength="1500" value="{e(vset["banner_url"])}" placeholder="…oder öffentliche Bild-URL (https://)">
     {remove_banner_box}
     <div class="text-[11px] text-slate-400">PNG, JPG, GIF oder WEBP bis {MAX_BANNER_BYTES // (1024 * 1024)} MB. Ein hochgeladenes Bild hat Vorrang vor der URL und wird als Anhang mitgeschickt. Nach dem Speichern „Panel veröffentlichen / aktualisieren“ klicken, damit Discord das neue Banner zeigt.</div>
    </div>
    <div class="grid gap-2 rounded-xl border border-slate-200 dark:border-slate-800 p-3">
     <div class="text-xs font-bold">🔔 Erinnerung für Unverifizierte</div>
     <label class="pulse-check"><input type="checkbox" name="reminder_enabled" value="1"{" checked" if vset["reminder_enabled"] else ""}> Unverifizierte regelmäßig im Verifikationskanal erwähnen</label>
     <div class="grid sm:grid-cols-3 gap-3">
      <label class="grid gap-1 text-xs font-semibold">Alle … Minuten<input class="pulse-input" type="number" name="reminder_minutes" min="{verification.REMINDER_MIN_MINUTES}" max="{verification.REMINDER_MAX_MINUTES}" value="{vset["reminder_minutes"]}"></label>
      <label class="grid gap-1 text-xs font-semibold sm:col-span-2">Text<input class="pulse-input" name="reminder_text" maxlength="300" value="{e(vset["reminder_text"])}"></label>
     </div>
     <div class="text-[11px] text-slate-400">Es steht immer nur <strong>eine</strong> Erinnerung im Kanal: Pulse löscht die vorherige, bevor es eine neue sendet. Ist niemand mehr unverifiziert, wird nicht gepingt und die letzte Erinnerung entfernt. Gepingt wird ausschließlich die Unverifiziert-Rolle.{reminder_state}</div>
    </div>
    <button class="pulse-btn primary" type="submit">💾 Verifikation speichern</button>
   </form>
   <form method="post" action="/admin/discord/verification/publish" class="mt-3 flex flex-wrap items-center gap-3" onsubmit="return confirm('Verifikations-Panel jetzt im gespeicherten Kanal veröffentlichen bzw. aktualisieren?')">
    <button class="pulse-btn ghost" type="submit">📌 Panel veröffentlichen / aktualisieren</button>{published}
   </form>
   <p class="text-[11px] text-slate-400 mt-2">Reihenfolge: Kanal speichern → Panel veröffentlichen → Verifikation aktivieren. Beim Veröffentlichen wird die bestehende Nachricht bearbeitet; nur wenn sie gelöscht wurde, entsteht eine neue. Prüfe in Discord, dass die Unverifiziert-Rolle nur Begrüßung, Regeln und Verifikation sehen kann.</p>
   {recent_html}
  </div>
  <div><div class="text-xs font-bold mb-2">Vorschau (wird nicht gesendet)</div><div class="dc-wrap">{preview}</div></div>
 </div></div></section>'''

    # K-12 / K-02: moderation log channel and who may use the RP panel (besides admins and leaders).
    def selectable_roles(guild) -> list:
        guild_id = getattr(guild, "id", None)
        roles = [r for r in (getattr(guild, "roles", []) or [])
                 if getattr(r, "id", None) != guild_id and not getattr(r, "managed", False)]
        return sorted(roles, key=lambda r: getattr(r, "position", 0), reverse=True)

    def logs_section(ctx) -> str:
        from cogs.advanced_logs import LOG_SETTING_KEY, log_channel_status
        from cogs.rp_verwaltung import ROLE_SETTING_KEY
        guild = ctx.guild
        log_id = str(pulse_db.get_setting(LOG_SETTING_KEY, "") or "")
        rp_roles = {str(v) for v in (pulse_db.get_setting(ROLE_SETTING_KEY, []) or [])}
        problem = log_channel_status(guild)
        state = (f'<div class="text-xs text-amber-500 mt-1">⚠️ {e(problem)}</div>' if problem
                 else '<div class="text-xs text-emerald-500 mt-1">✓ Protokolliert Bearbeitungen, Voice- und Rollenänderungen.</div>')
        role_boxes = "".join(
            f'<label class="pulse-check"><input type="checkbox" name="rp_role_ids" value="{r.id}"{" checked" if str(r.id) in rp_roles else ""}> {e(r.name)}</label>'
            for r in selectable_roles(guild)) or '<div class="text-[11px] text-slate-400">Keine Rollen gefunden (Bot nicht bereit?).</div>'
        return f'''
<form method="post" action="/admin/discord/logs" class="mt-5">
<section class="pulse-card" id="logs"><div class="pulse-card-h"><div class="pulse-section-title">📜 Moderations-Log &amp; RP-Panel</div>
 <span class="pulse-pill {"" if problem else "good"}">{"aus" if problem else "aktiv"}</span></div>
 <div class="pulse-card-b grid lg:grid-cols-2 gap-5">
  <div><label class="grid gap-1 text-xs font-semibold">Log-Kanal (Bearbeitungen, Voice, Rollen)<select class="pulse-input" name="log_channel_id">{channel_options(guild, log_id)}</select></label>{state}</div>
  <div><div class="text-xs font-semibold mb-1">Zusätzliche Rollen für das RP-Panel (RP Start/Stop)</div>
   <div class="grid gap-1 max-h-48 overflow-auto rounded-xl border border-slate-200 dark:border-slate-800 p-2">{role_boxes}</div>
   <div class="text-[11px] text-slate-400 mt-1">Admins und Leitung (Beförderungsrecht) dürfen immer. Alle anderen nur mit einer der gewählten Rollen.</div></div>
  <div><button class="pulse-btn primary" type="submit">💾 Log &amp; RP-Panel speichern</button></div>
 </div></section></form>''' + log_routing_section(ctx)

    def log_routing_section(ctx) -> str:
        """BOT-02: own channel per log category, ignore lists, voice switch."""
        import pulse_logcfg
        guild = ctx.guild
        cfg = pulse_logcfg.load()
        selects = "".join(
            f'<label class="grid gap-1 text-xs font-semibold">{e(label)}<select class="pulse-input" name="channel_{key}">'
            f'{channel_options(guild, str(cfg["channels"].get(key, "")))}</select></label>'
            for key, label in pulse_logcfg.CATEGORIES.items())
        ignore_channels = "".join(
            f'<label class="pulse-check"><input type="checkbox" name="ignore_channels" value="{c.id}"{" checked" if c.id in cfg["ignore_channels"] else ""}> #{e(c.name)}</label>'
            for c in sorted(getattr(guild, "text_channels", []) or [], key=lambda c: (getattr(c, "position", 0), c.name)))
        ignore_roles = "".join(
            f'<label class="pulse-check"><input type="checkbox" name="ignore_roles" value="{r.id}"{" checked" if r.id in cfg["ignore_roles"] else ""}> {e(r.name)}</label>'
            for r in selectable_roles(guild))
        return f'''
<form method="post" action="/admin/discord/log-routing" class="mt-5">
<section class="pulse-card" id="log-routing"><div class="pulse-card-h"><div class="pulse-section-title">🗂 Log-Kategorien</div></div>
 <div class="pulse-card-b space-y-4">
  <div class="text-xs text-slate-400">Ohne eigenen Kanal landet eine Kategorie im Log-Kanal oben. Moderation (Bans, Kicks, Timeouts mit Täter) braucht das Bot-Recht „Audit-Log anzeigen“.</div>
  <div class="grid md:grid-cols-2 xl:grid-cols-3 gap-3">{selects}</div>
  <div class="grid lg:grid-cols-2 gap-4">
   <div><div class="text-xs font-semibold mb-1">Diese Kanäle nicht protokollieren</div><div class="grid gap-1 max-h-48 overflow-auto rounded-xl border border-slate-200 dark:border-slate-800 p-2">{ignore_channels or '<div class="text-[11px] text-slate-400">Keine Kanäle gefunden.</div>'}</div></div>
   <div><div class="text-xs font-semibold mb-1">Personen mit diesen Rollen nicht protokollieren</div><div class="grid gap-1 max-h-48 overflow-auto rounded-xl border border-slate-200 dark:border-slate-800 p-2">{ignore_roles or '<div class="text-[11px] text-slate-400">Keine Rollen gefunden.</div>'}</div></div>
  </div>
  <label class="pulse-check"><input type="checkbox" name="ignore_bots" value="1"{" checked" if cfg["ignore_bots"] else ""}> Bots nicht protokollieren</label>
  <label class="pulse-check"><input type="checkbox" name="voice" value="1"{" checked" if cfg["voice"] else ""}> Voice-Bewegungen protokollieren (höchstens ein Eintrag pro Person alle 10 s)</label>
  <div><button class="pulse-btn primary" type="submit">💾 Log-Kategorien speichern</button></div>
 </div></section></form>'''

    def discord_page(ctx, settings: dict, brand: dict, errors: list[str] | None = None,
                     vset: dict | None = None, verrors: list[str] | None = None) -> HTMLResponse:
        guild = ctx.guild
        alert = ""
        if errors:
            alert = ('<div class="pulse-alert" style="border-color:rgba(240,68,56,.35)"><strong>Nicht gespeichert:</strong><ul class="mt-1 list-disc pl-5">'
                     + "".join(f"<li>{e(x)}</li>" for x in errors) + "</ul></div>")
        placeholder_help = " ".join(f'<code title="{e(desc)}">{{{name}}}</code>' for name, desc in PLACEHOLDERS.items())

        def kind_form(kind: str) -> str:
            entry = settings[kind]
            label = "Willkommen" if kind == "welcome" else "Abschied"
            payload = build_member_message(kind, settings, preview_member(ctx), now=None,
                                           base_dir=str(ws.BASE_DIR), force=True)
            banner_href = f"/admin/discord/banner/{e(entry['banner_file'])}" if entry.get("banner_file") else ""
            welcome_only = ""
            if kind == "welcome":
                welcome_only = f'''
   <label class="pulse-check"><input type="checkbox" name="welcome_mention" value="1"{" checked" if entry["mention"] else ""}> Neues Mitglied erwähnen (nur diese Person wird gepingt, nie @everyone/@here)</label>
   <div class="grid sm:grid-cols-2 gap-3">
    <label class="grid gap-1 text-xs font-semibold">Regeln-Kanal (optional)<select class="pulse-input" name="welcome_rules_channel_id">{channel_options(guild, entry["rules_channel_id"])}</select></label>
    <label class="grid gap-1 text-xs font-semibold">Support-Kanal (optional)<select class="pulse-input" name="welcome_support_channel_id">{channel_options(guild, entry["support_channel_id"])}</select></label>
   </div>'''
            current_banner = ""
            if entry.get("banner_file"):
                current_banner = f'<label class="pulse-check"><input type="checkbox" name="{kind}_remove_banner" value="1"> Hochgeladenes Banner entfernen</label>'
            return f'''
<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">{"👋" if kind == "welcome" else "🚪"} {label}</div>
 <span class="pulse-pill {"good" if entry["enabled"] and entry["channel_id"] else ""}">{"aktiv" if entry["enabled"] and entry["channel_id"] else "aus"}</span></div>
 <div class="pulse-card-b"><div class="grid lg:grid-cols-2 gap-5">
  <div class="grid gap-3">
   <label class="pulse-check"><input type="checkbox" name="{kind}_enabled" value="1"{" checked" if entry["enabled"] else ""}> {label}snachricht senden</label>
   <label class="grid gap-1 text-xs font-semibold">Zielkanal<select class="pulse-input" name="{kind}_channel_id">{channel_options(guild, entry["channel_id"])}</select></label>
   <label class="grid gap-1 text-xs font-semibold">Titel<input class="pulse-input" name="{kind}_title" maxlength="256" value="{e(entry["title"])}"></label>
   <label class="grid gap-1 text-xs font-semibold">Text<textarea class="pulse-input" name="{kind}_message" rows="6" maxlength="3500">{e(entry["message"])}</textarea></label>
   {welcome_only}
   <label class="pulse-check"><input type="checkbox" name="{kind}_show_avatar" value="1"{" checked" if entry["show_avatar"] else ""}> Profilbild oben rechts anzeigen</label>
   <div class="grid gap-2 rounded-xl border border-slate-200 dark:border-slate-800 p-3">
    <div class="text-xs font-bold">Banner (großes Bild unten)</div>
    <input type="file" name="{kind}_banner_upload" accept="image/png,image/jpeg,image/gif,image/webp" class="text-xs">
    <input class="pulse-input" name="{kind}_banner_url" maxlength="1500" value="{e(entry["banner_url"])}" placeholder="…oder öffentliche Bild-URL (https://)">
    {current_banner}
    <div class="text-[11px] text-slate-400">PNG, JPG, GIF oder WEBP bis {MAX_BANNER_BYTES // (1024 * 1024)} MB. Ein hochgeladenes Bild hat Vorrang vor der URL und wird als Anhang mitgeschickt – es braucht keine öffentliche Adresse. Empfohlen: 1100 × 400 px.</div>
   </div>
  </div>
  <div><div class="text-xs font-bold mb-2">Vorschau (Beispielmitglied, gespeicherter Stand)</div>
   <div class="dc-wrap">{render_preview(payload, guild, banner_href)}</div>
   <div class="text-[11px] text-slate-400 mt-2">Datumswerte erscheinen in Discord in der Ortszeit des Lesers.</div>
  </div>
 </div></div></section>'''

        colors = "".join(
            f'<label class="grid gap-1 text-xs font-semibold">{label}<input type="color" class="pulse-input" style="height:40px;padding:4px" name="color_{key}" value="{e(brand["colors"][key])}"></label>'
            for key, label in (("brand", "Pulse / Information"), ("success", "Erfolg"), ("warning", "Warnung"),
                               ("danger", "Fehler / kritisch"), ("neutral", "Protokoll / neutral"), ("highlight", "Auszeichnung"))
        )
        test_buttons = "".join(
            f'<form method="post" action="/admin/discord/test" onsubmit="return confirm(\'{label}-Testnachricht jetzt mit deinem eigenen Profil in den konfigurierten Kanal senden?\')">'
            f'<input type="hidden" name="kind" value="{kind}"><button class="pulse-btn ghost" type="submit">🧪 {label} testen</button></form>'
            for kind, label in (("welcome", "Willkommen"), ("leave", "Abschied"))
        )
        body = f'''{PREVIEW_CSS}
<div class="pulse-crumb"><a href="/admin">Verwaltung</a> › Discord-Auftritt</div>
<div class="pulse-hero"><div class="pulse-hero-grid"><div>
 <div class="pulse-kicker">Verwaltung · Discord-Auftritt</div><div class="pulse-title">So wirkt Pulse in Discord</div>
 <div class="pulse-sub">Einheitliche Farben und Footer für alle Pulse-Nachrichten sowie eigene Begrüßungen und Verabschiedungen mit Profilbild und Banner. Änderungen gelten sofort, ohne Neustart.</div></div>
 <div class="pulse-hero-box"><div class="label">Platzhalter</div><div class="text-[11px] mt-2 leading-6">{placeholder_help}</div></div>
</div></div>
{alert}
{verification_section(ctx, vset or load_verification(), verrors)}
{logs_section(ctx)}
<form method="post" action="/admin/discord/welcome" enctype="multipart/form-data" class="space-y-5 mt-5">
 {kind_form("welcome")}
 {kind_form("leave")}
 <section class="pulse-card"><div class="pulse-card-b flex flex-wrap items-center gap-3">
  <label class="pulse-check"><input type="checkbox" name="include_bots" value="1"{" checked" if settings["include_bots"] else ""}> Auch Bots begrüßen/verabschieden</label>
  <div class="flex-1"></div>
  <button class="pulse-btn primary" type="submit">💾 Willkommen &amp; Abschied speichern</button>
 </div></section>
</form>
<section class="pulse-card mt-5"><div class="pulse-card-h"><div class="pulse-section-title">🧪 Kontrollierter Test</div></div>
 <div class="pulse-card-b"><p class="text-xs text-slate-400 mb-3">Sendet genau eine Nachricht mit <strong>deinem</strong> Profil in den gespeicherten Zielkanal – auch wenn die Nachricht deaktiviert ist. Es wird niemand außer dir erwähnt.</p>
 <div class="flex flex-wrap gap-2">{test_buttons}</div></div></section>
<form method="post" action="/admin/discord/branding" class="mt-5">
<section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">🎨 Embed-Design (alle Pulse-Nachrichten)</div></div>
 <div class="pulse-card-b grid gap-4">
  <div class="grid sm:grid-cols-2 gap-3">
   <label class="grid gap-1 text-xs font-semibold">Markenname<input class="pulse-input" name="brand_name" maxlength="60" value="{e(brand["brand_name"])}"></label>
   <label class="grid gap-1 text-xs font-semibold">Footer-Text<input class="pulse-input" name="footer_text" maxlength="160" value="{e(brand["footer_text"])}"></label>
  </div>
  <div class="grid sm:grid-cols-2 gap-3">
   <label class="grid gap-1 text-xs font-semibold">Nachrichten-Stil<select class="pulse-input" name="style">
    <option value="modern"{" selected" if brand["style"] == "modern" else ""}>✨ Modern – Container wie GalaxyBot (empfohlen)</option>
    <option value="classic"{" selected" if brand["style"] == "classic" else ""}>Klassisch – Discord-Embed mit Farbbalken</option></select></label>
   <label class="pulse-check" style="align-self:end;padding-bottom:10px"><input type="checkbox" name="accent_bar" value="1"{" checked" if brand["accent_bar"] else ""}> Farbigen Rand links auch im modernen Stil zeigen</label>
  </div>
  <p class="text-[11px] text-slate-400">Der Stil gilt für Willkommen, Abschied und das Verifikations-Panel. Nach einem Stilwechsel das Verifikations-Panel einmal neu veröffentlichen – Pulse ersetzt dann die alte Nachricht.</p>
  <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">{colors}</div>
  <div class="flex flex-wrap gap-2"><button class="pulse-btn primary" type="submit">💾 Design speichern</button>
   <button class="pulse-btn ghost" type="submit" name="reset" value="1" onclick="return confirm('Farben und Footer auf den Pulse-Standard zurücksetzen?')">↺ Standard wiederherstellen</button></div>
  <p class="text-[11px] text-slate-400">Pulse verwendet die Farben nach Bedeutung: Erfolg, Information, Warnung, Fehler, Protokoll und Auszeichnung. Ticket-Panels behalten ihre eigene, im Panel-Builder gewählte Farbe.</p>
 </div></section></form>'''
        return HTMLResponse(render_pro_page(ws, "Discord-Auftritt", ctx, "admin-discord", body))

    @app.get("/admin/discord", response_class=HTMLResponse)
    async def admin_discord(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        return discord_page(ctx, load_settings(), branding())

    async def store_banner(upload: UploadFile, kind: str):
        from pulse_uploads import save_image_upload
        # Streamed to disk in chunks instead of reading up to 8 MB into RAM.
        return await save_image_upload(
            upload, banner_root(), lambda ext: f"{kind}_banner_{secrets.token_hex(4)}.{ext}",
            MAX_BANNER_BYTES, detect=detect_image_type,
            too_large=f"Das Banner ist zu groß (maximal {MAX_BANNER_BYTES // (1024 * 1024)} MB).",
            wrong_type="Als Banner sind nur PNG-, JPG-, GIF- oder WEBP-Bilder erlaubt.",
        )

    def remove_banner(name: str):
        path = (banner_root() / os.path.basename(str(name or ""))).resolve()
        if name and path.parent == banner_root().resolve() and path.is_file():
            try:
                path.unlink()
            except OSError:
                pass

    @app.post("/admin/discord/welcome", response_class=HTMLResponse)
    async def admin_discord_welcome(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        current = load_settings()
        raw = {"include_bots": form.get("include_bots") == "1"}
        for kind in KINDS:
            raw[kind] = {
                "enabled": form.get(f"{kind}_enabled") == "1",
                "channel_id": form.get(f"{kind}_channel_id", ""),
                "title": form.get(f"{kind}_title", ""),
                "message": form.get(f"{kind}_message", ""),
                "show_avatar": form.get(f"{kind}_show_avatar") == "1",
                "banner_url": str(form.get(f"{kind}_banner_url", "") or "").strip(),
                "banner_file": current[kind]["banner_file"],
            }
            if kind == "welcome":
                raw[kind]["mention"] = form.get("welcome_mention") == "1"
                raw[kind]["rules_channel_id"] = form.get("welcome_rules_channel_id", "")
                raw[kind]["support_channel_id"] = form.get("welcome_support_channel_id", "")
        candidate = normalize_settings(raw)
        errors = validate_settings(candidate)
        for kind in KINDS:
            url = raw[kind]["banner_url"]
            if url and not candidate[kind]["banner_url"]:
                errors.append(f"{'Willkommen' if kind == 'welcome' else 'Abschied'}: Die Banner-URL muss mit https:// beginnen.")
        uploads = {kind: form.get(f"{kind}_banner_upload") for kind in KINDS}
        if errors:
            for upload in uploads.values():
                if upload is not None and hasattr(upload, "close"):
                    await upload.close()
            return discord_page(ctx, candidate, branding(), errors)
        replaced = []
        for kind in KINDS:
            upload = uploads[kind]
            if form.get(f"{kind}_remove_banner") == "1" and candidate[kind]["banner_file"]:
                replaced.append(candidate[kind]["banner_file"])
                candidate[kind]["banner_file"] = ""
            if upload is not None and getattr(upload, "filename", ""):
                stored, error = await store_banner(upload, kind)
                if error:
                    return discord_page(ctx, candidate, branding(), [error])
                if candidate[kind]["banner_file"]:
                    replaced.append(candidate[kind]["banner_file"])
                candidate[kind]["banner_file"] = stored
        pulse_db.set_setting(SETTING_KEY, candidate)
        for name in replaced:
            remove_banner(name)
        ws.log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Discord-Auftritt geändert",
                     f"Willkommen {'an' if candidate['welcome']['enabled'] else 'aus'}, Abschied {'an' if candidate['leave']['enabled'] else 'aus'}")
        return ws.back("/admin/discord", "Willkommen & Abschied gespeichert – gilt ab dem nächsten Beitritt/Austritt.", True)

    @app.post("/admin/discord/branding")
    async def admin_discord_branding(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        if form.get("reset") == "1":
            value = save_branding({})
        else:
            raw = {"brand_name": form.get("brand_name", ""), "footer_text": form.get("footer_text", ""),
                   "colors": {key: form.get(f"color_{key}", "") for key in DEFAULT_COLORS},
                   "style": form.get("style", "modern"), "accent_bar": form.get("accent_bar") == "1"}
            invalid = [key for key, value in raw["colors"].items() if str(value).strip() and not normalize_hex(value, "")]
            if invalid:
                return ws.back("/admin/discord", "Ungültige Farbe – bitte den Farbwähler verwenden.", False)
            value = save_branding(raw)
        ws.log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Embed-Design geändert",
                     f"Footer: {value['footer_text']}")
        return ws.back("/admin/discord", "Embed-Design gespeichert.", True)

    # ------------------------------------------------------------------ Datenschutz (D-08)
    @app.get("/admin/privacy", response_class=HTMLResponse)
    async def admin_privacy(request: Request, user_session: str = Cookie(None)):
        import pulse_retention
        ctx = admin_ctx(request, user_session)
        settings = pulse_retention.load_settings()
        preview = pulse_retention.plan(ws, settings)
        backup = pulse_retention.recent_full_backup(ws)
        last = settings.get("last_report") or {}
        last_line = (f'Letzte Bereinigung {e(settings["last_run"])}: {e(last.get("applications", 0))} Bewerbungen, '
                     f'{e(last.get("audit", 0))} Audit-Einträge.' if settings.get("last_run") else "Noch keine Bereinigung gelaufen.")
        backup_line = (f'<span class="text-emerald-500">✓ Aktuelles Komplett-Backup: {e(backup)}</span>' if backup else
                       '<span class="text-amber-500">⚠️ Kein Komplett-Backup der letzten 2 Tage – die Bereinigung wartet, bis eins existiert (Verwaltung → Backups).</span>')
        body = f'''
<div class="pulse-crumb"><a href="/admin">Verwaltung</a> › Datenschutz</div>
<div class="pulse-hero"><div><div class="pulse-kicker">Verwaltung · Datenschutz</div><div class="pulse-title">Aufbewahrung &amp; Auskunft</div>
 <div class="pulse-sub">Fristen nach eurer Entscheidung (E-9). Standard: aus. Bereinigt wird höchstens einmal täglich, nur mit frischem Komplett-Backup, und jede Bereinigung steht im Audit-Log.</div></div></div>
<form method="post" action="/admin/privacy" class="mt-5"><section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">🗓️ Aufbewahrungsfristen</div>
 <span class="pulse-pill {"good" if settings["enabled"] else ""}">{"aktiv" if settings["enabled"] else "aus"}</span></div>
 <div class="pulse-card-b grid gap-3">
  <label class="pulse-check"><input type="checkbox" name="enabled" value="1"{" checked" if settings["enabled"] else ""}> Automatische Bereinigung aktivieren</label>
  <div class="grid sm:grid-cols-2 gap-3">
   <label class="grid gap-1 text-xs font-semibold">Abgeschlossene Bewerbungen löschen nach (Tagen)<input class="pulse-input" type="number" name="applications_days" min="30" max="3650" value="{settings["applications_days"]}"></label>
   <label class="grid gap-1 text-xs font-semibold">Audit-Log-Einträge löschen nach (Tagen)<input class="pulse-input" type="number" name="audit_days" min="90" max="3650" value="{settings["audit_days"]}"></label>
  </div>
  <label class="grid gap-1 text-xs font-semibold">Kontakt für Auskunft/Löschung (steht im Hinweis auf /apply)<input class="pulse-input" name="contact" maxlength="300" value="{e(settings["contact"])}" placeholder="z. B. die Serverleitung über ein Support-Ticket"></label>
  <div class="text-[11px] text-slate-400">Mit diesen Werten würden jetzt <strong>{preview["applications"]}</strong> Bewerbungen und <strong>{preview["audit"]}</strong> Audit-Einträge entfernt. Offene Bewerbungen (wartend/Gespräch) werden nie gelöscht. KI-Moderationsfälle löscht Pulse bereits nach 30 Tagen; Ticket-Transkripte bleiben unbegrenzt (E-7). {backup_line} {last_line}</div>
  <div><button class="pulse-btn primary" type="submit">💾 Speichern</button></div>
 </div></section></form>
<section class="pulse-card mt-5"><div class="pulse-card-h"><div class="pulse-section-title">📄 Auskunft: alle Daten zu einer Discord-ID</div></div>
 <div class="pulse-card-b"><form method="get" action="/admin/privacy/export" class="flex flex-wrap gap-2">
  <input class="pulse-input" name="discord_id" pattern="[0-9]{{15,22}}" required placeholder="Discord-ID">
  <button class="pulse-btn ghost" type="submit">⬇️ JSON herunterladen</button></form>
  <p class="text-[11px] text-slate-400 mt-2">Enthält Teamakte, Bewerbungen, Schichten, Abmeldungen, Tickets, Ereignisse und eigene Audit-Einträge. Der Export wird im Audit-Log vermerkt. Löschen einzelner Personen erfolgt weiterhin bewusst von Hand.</p>
 </div></section>'''
        return HTMLResponse(render_pro_page(ws, "Datenschutz", ctx, "admin-privacy", body))

    @app.post("/admin/privacy")
    async def admin_privacy_save(request: Request, user_session: str = Cookie(None)):
        import pulse_retention
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        try:
            values = {"enabled": form.get("enabled") == "1", "applications_days": int(form.get("applications_days") or 0),
                      "audit_days": int(form.get("audit_days") or 0), "contact": str(form.get("contact") or "").strip()[:300]}
        except ValueError:
            return ws.back("/admin/privacy", "Bitte ganze Zahlen für die Fristen eingeben.", False)
        saved = pulse_retention.save_settings(values)
        ws.log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Datenschutz-Einstellungen geändert",
                     f"Bereinigung {'an' if saved['enabled'] else 'aus'} · Bewerbungen {saved['applications_days']} Tage · Audit {saved['audit_days']} Tage")
        return ws.back("/admin/privacy", "Datenschutz-Einstellungen gespeichert.", True)

    @app.get("/admin/privacy/export")
    async def admin_privacy_export(request: Request, discord_id: str = "", user_session: str = Cookie(None)):
        import json as _json
        import pulse_retention
        from fastapi.responses import Response
        ctx = admin_ctx(request, user_session)
        uid = str(discord_id or "").strip()
        if not re.fullmatch(r"[0-9]{15,22}", uid):
            return ws.back("/admin/privacy", "Bitte eine gültige Discord-ID eingeben.", False)
        data = await asyncio.to_thread(pulse_retention.export_person, ws, uid)  # file/DB scan off the event loop
        ws.log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Datenauskunft exportiert", f"Discord-ID {uid}")
        return Response(_json.dumps(data, ensure_ascii=False, indent=2, default=str), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="pulse_auskunft_{uid}.json"'})

    # ------------------------------------------------------------------ BOT-14: panel overview
    @app.get("/admin/panels", response_class=HTMLResponse)
    async def admin_panels(request: Request, user_session: str = Cookie(None)):
        import pulse_panels
        ctx = admin_ctx(request, user_session)
        items = await pulse_panels.check(ctx.guild, pulse_panels.collect(ctx.guild.id))
        labels = {"ok": ("Aktiv", "good"), "missing_message": ("Nachricht fehlt", "bad"), "missing_channel": ("Kanal fehlt", "bad"),
                  "not_set_up": ("Nicht eingerichtet", "warn"), "unknown": ("Nicht prüfbar", "warn")}
        rows = []
        for item in items:
            label, kind = labels.get(item.get("state"), ("?", "warn"))
            where = (f'#{e(item["channel_name"])}' if item.get("channel_name") else (f'Kanal {item["channel_id"]}' if item["channel_id"] else "–"))
            link = (f' · <a class="text-indigo-500" target="_blank" rel="noopener" href="https://discord.com/channels/{ctx.guild.id}/{item["channel_id"]}/{item["message_id"]}">Nachricht öffnen</a>'
                    if item["channel_id"] and item["message_id"] and item.get("state") == "ok" else "")
            action = (f'<form method="post" action="/admin/panels/republish"><input type="hidden" name="kind" value="{e(item["key"])}">'
                      f'<button class="pulse-btn ghost" type="submit">↻ Neu veröffentlichen</button></form>'
                      if item["republish"] else f'<span class="pulse-row-meta">{e(item["how"])}</span>')
            rows.append(f'<div class="pulse-row"><div class="pulse-row-main"><div class="pulse-row-title">{e(item["name"])} '
                        f'<span class="pulse-pill {kind}">{e(label)}</span></div><div class="pulse-row-meta">{where}{link} · Einrichtung: {e(item["how"])}</div></div>'
                        f'{action}</div>')
        body = f'''
<div class="pulse-crumb"><a href="/admin">Verwaltung</a> › Discord-Panels</div>
<section class="pulse-card"><div class="pulse-card-h"><div><div class="pulse-section-title">▦ Discord-Panels</div>
<div class="pulse-section-sub">Alle dauerhaften Panels mit Kanal, Nachricht und Status. „Neu veröffentlichen“ nutzt die vorhandene Funktion des jeweiligen Panels; andere Panels richtest du mit dem genannten Befehl im Zielkanal neu ein.</div></div></div>
<div class="pulse-card-b space-y-2">{"".join(rows)}</div></section>'''
        return HTMLResponse(render_pro_page(ws, "Discord-Panels", ctx, "admin-panels", body))

    @app.post("/admin/panels/republish")
    async def admin_panels_republish(request: Request, user_session: str = Cookie(None)):
        import pulse_panels
        ctx = admin_ctx(request, user_session)
        kind = str((await request.form()).get("kind") or "")
        if kind not in pulse_panels.REPUBLISH:
            return ws.back("/admin/panels", "Für dieses Panel gibt es kein automatisches Neuveröffentlichen.", False)
        bot = getattr(request.app.state, "bot", None)
        if bot is None:
            return ws.back("/admin/panels", "Der Bot ist nicht verbunden.", False)
        try:
            message = await pulse_panels.republish(bot, ctx.guild, kind)
        except ValueError as exc:
            return ws.back("/admin/panels", str(exc), False)
        except Exception as exc:  # Discord errors: visible, with type, never as success
            return ws.back("/admin/panels", f"Veröffentlichen fehlgeschlagen ({type(exc).__name__}). Kanalrechte prüfen.", False)
        ws.log_audit(ctx.user.get("global_name"), ctx.user["id"], "Panel neu veröffentlicht", kind)
        return ws.back("/admin/panels", message, True)

    @app.post("/admin/commands/sync")
    async def admin_commands_sync(request: Request, user_session: str = Cookie(None)):
        """P-09: manual slash-command sync (normally only when the command tree changed)."""
        from pulse_command_sync import sync_commands
        ctx = admin_ctx(request, user_session)
        bot = getattr(request.app.state, "bot", None)
        if bot is None:
            return ws.back("/system", "Der Bot ist nicht verbunden – Sync nicht möglich.", False)
        try:
            result = await sync_commands(bot.tree, force=True)
        except Exception as exc:
            return ws.back("/system", f"Sync fehlgeschlagen ({type(exc).__name__}). Discord begrenzt Syncs – später erneut versuchen.", False)
        ws.log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Slash-Befehle synchronisiert", f"{result['count']} Befehle")
        return ws.back("/system", f"{result['count']} Slash-Befehle synchronisiert.", True)

    @app.post("/admin/discord/logs")
    async def admin_discord_logs(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        from cogs.advanced_logs import LOG_SETTING_KEY
        from cogs.rp_verwaltung import ROLE_SETTING_KEY
        form = await request.form()
        guild = ctx.guild
        raw_channel = str(form.get("log_channel_id", "") or "").strip()
        if raw_channel and (not raw_channel.isdigit()
                            or not any(str(c.id) == raw_channel for c in (getattr(guild, "text_channels", []) or []))):
            return ws.back("/admin/discord#logs", "Der Log-Kanal muss ein Textkanal dieses Servers sein.", False)
        known = {str(r.id) for r in selectable_roles(guild)}
        if not known:  # role cache not ready – never wipe the stored roles by accident
            return ws.back("/admin/discord#logs", "Rollen konnten nicht geladen werden (Bot nicht bereit). Nichts gespeichert.", False)
        role_ids = [rid for rid in dict.fromkeys(str(v) for v in form.getlist("rp_role_ids")) if rid in known]
        pulse_db.set_setting(LOG_SETTING_KEY, raw_channel)
        pulse_db.set_setting(ROLE_SETTING_KEY, role_ids)
        ws.log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Log & RP-Panel geändert",
                     f"Log-Kanal {raw_channel or 'aus'}, RP-Rollen {', '.join(role_ids) or 'keine'}")
        return ws.back("/admin/discord#logs", "Moderations-Log und RP-Panel gespeichert.", True)

    @app.post("/admin/discord/log-routing")
    async def admin_discord_log_routing(request: Request, user_session: str = Cookie(None)):
        """BOT-02: store category channels and ignore lists (only channels/roles of this server)."""
        import pulse_logcfg
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        guild = ctx.guild
        text_ids = {str(c.id) for c in (getattr(guild, "text_channels", []) or [])}
        if not text_ids:
            return ws.back("/admin/discord#log-routing", "Kanäle konnten nicht geladen werden (Bot nicht bereit). Nichts gespeichert.", False)
        channels = {}
        for key in pulse_logcfg.CATEGORIES:
            value = str(form.get(f"channel_{key}", "") or "").strip()
            if value and value not in text_ids:
                return ws.back("/admin/discord#log-routing", "Log-Kanäle müssen Textkanäle dieses Servers sein.", False)
            if value:
                channels[key] = value
        role_ids = {str(r.id) for r in selectable_roles(guild)}
        cfg = pulse_logcfg.save({
            "channels": channels,
            "ignore_channels": [v for v in form.getlist("ignore_channels") if str(v) in text_ids],
            "ignore_roles": [v for v in form.getlist("ignore_roles") if str(v) in role_ids],
            "ignore_bots": form.get("ignore_bots") == "1",
            "voice": form.get("voice") == "1",
        })
        ws.log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Log-Kategorien geändert",
                     f"{len(cfg['channels'])} eigene Kanäle, {len(cfg['ignore_channels'])} ignorierte Kanäle, "
                     f"{len(cfg['ignore_roles'])} ignorierte Rollen, Voice {'an' if cfg['voice'] else 'aus'}")
        return ws.back("/admin/discord#log-routing", "Log-Kategorien gespeichert.", True)

    @app.post("/admin/discord/test")
    async def admin_discord_test(request: Request, kind: str = Form(...), user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        if kind not in KINDS:
            return ws.back("/admin/discord", "Unbekannter Nachrichtentyp.", False)
        if ws.rate_limited(f"welcome-test:{ctx.user['id']}", limit=6, window=600):
            return ws.back("/admin/discord", "Bitte kurz warten – höchstens 6 Testnachrichten in 10 Minuten.", False)
        settings = load_settings()
        if not settings[kind]["channel_id"]:
            return ws.back("/admin/discord", "Bitte zuerst einen Zielkanal speichern.", False)
        member = ctx.member or ctx.guild.get_member(int(ctx.user["id"]))
        if member is None:
            return ws.back("/admin/discord", "Dein Discord-Profil wurde auf dem Server nicht gefunden.", False)
        from cogs.welcome import member_info, send_member_message
        import discord
        payload = build_member_message(kind, settings, member_info(member, ctx.guild), now=discord.utils.utcnow(),
                                       base_dir=str(ws.BASE_DIR), force=True)
        ok, error = await send_member_message(getattr(request.app.state, "bot", None), ctx.guild, payload)
        if not ok:
            return ws.back("/admin/discord", f"Test fehlgeschlagen: {error}", False)
        return ws.back("/admin/discord", "Testnachricht wurde gesendet.", True)

    @app.post("/admin/discord/verification", response_class=HTMLResponse)
    async def admin_verification_save(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        form = await request.form()
        current = load_verification()
        raw_banner = str(form.get("banner_url", "") or "")
        candidate = verification.normalize_settings({
            "enabled": form.get("enabled") == "1", "channel_id": form.get("channel_id", ""),
            "title": form.get("title", ""), "description": form.get("description", ""),
            "button_label": form.get("button_label", ""), "banner_url": raw_banner,
            # Publication state and diagnostics are owned by the bot; never reset them from the form.
            "message_id": current["message_id"], "published_channel_id": current["published_channel_id"],
            "errors": current["errors"], "banner_file": current["banner_file"],
            "reminder_enabled": form.get("reminder_enabled") == "1",
            "reminder_minutes": form.get("reminder_minutes", ""), "reminder_text": form.get("reminder_text", ""),
            "reminder_message_id": current["reminder_message_id"], "reminder_last_at": current["reminder_last_at"],
        })
        problems = verification.validate_settings(candidate, raw_banner)
        upload = form.get("banner_upload")
        channel_id = candidate["channel_id"]
        if channel_id and ctx.guild.get_channel(int(channel_id)) is None:
            problems.append("Der gewählte Kanal gehört nicht zu diesem Server.")
        if problems:
            if upload is not None and hasattr(upload, "close"):
                await upload.close()
            return discord_page(ctx, load_settings(), branding(), vset=candidate, verrors=problems)
        replaced = []
        if form.get("remove_banner") == "1" and candidate["banner_file"]:
            replaced.append(candidate["banner_file"])
            candidate["banner_file"] = ""
        if upload is not None and getattr(upload, "filename", ""):
            stored, error = await store_banner(upload, "verify")
            if error:
                return discord_page(ctx, load_settings(), branding(), vset=candidate, verrors=[error])
            if candidate["banner_file"]:
                replaced.append(candidate["banner_file"])
            candidate["banner_file"] = stored
        pulse_db.set_setting(verification.SETTING_KEY, candidate)
        for name in replaced:
            remove_banner(name)
        ws.log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Verifikation geändert",
                     f"Verifikation {'aktiv' if candidate['enabled'] else 'aus'}, Kanal {channel_id or '–'}")
        note = (" Klicke auf „Panel veröffentlichen / aktualisieren“, damit Discord die Änderung zeigt."
                if candidate["message_id"] else " Denk daran, das Panel zu veröffentlichen.")
        return ws.back("/admin/discord#verification", "Verifikation gespeichert." + note, True)

    @app.post("/admin/discord/verification/publish")
    async def admin_verification_publish(request: Request, user_session: str = Cookie(None)):
        ctx = admin_ctx(request, user_session)
        if ws.rate_limited(f"verify-publish:{ctx.user['id']}", limit=5, window=300):
            return ws.back("/admin/discord#verification", "Bitte kurz warten – höchstens 5 Veröffentlichungen in 5 Minuten.", False)
        bot = getattr(request.app.state, "bot", None)
        cog = bot.get_cog("Verification") if bot else None
        if cog is None:
            return ws.back("/admin/discord#verification", "Das Verifikationsmodul ist im Bot nicht geladen – bitte Bot neu starten.", False)
        try:
            result = await cog.publish_panel(ctx.guild)
        except ValueError as exc:
            return ws.back("/admin/discord#verification", str(exc), False)
        except Exception as exc:
            return ws.back("/admin/discord#verification", f"Veröffentlichen fehlgeschlagen ({type(exc).__name__}).", False)
        ws.log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Verifikations-Panel veröffentlicht",
                     f"{result['action']} in {result['channel']}")
        return ws.back("/admin/discord#verification", f"Panel {result['action']} in {result['channel']}.", True)

    @app.get("/admin/discord/banner/{filename}")
    async def admin_discord_banner(request: Request, filename: str, user_session: str = Cookie(None)):
        admin_ctx(request, user_session)
        name = os.path.basename(filename)
        if not re.fullmatch(r"(welcome|leave|verify)_banner_[0-9a-f]{8}\.(png|jpg|gif|webp)", name):
            raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
        path = (banner_root() / name).resolve()
        if path.parent != banner_root().resolve() or not path.is_file():
            raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})
