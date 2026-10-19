"""Verwaltung (administration area): hub page and "Discord-Auftritt".

/admin            – Einstieg in die Verwaltung; zeigt nur Bereiche, die die Person darf.
/admin/discord    – Embed-Branding sowie Willkommen & Abschied (Vorlagen, Kanäle, Banner,
                    Vorschau, kontrollierte Testnachricht). Nur Administratoren.

Every route authorizes server-side; the navigation only mirrors these checks.
"""
from __future__ import annotations

import html
import os
import re
import secrets
from datetime import datetime, timezone

from fastapi import Cookie, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

import pulse_db
from pulse_embed_design import DEFAULT_COLORS, branding, normalize_hex, save_branding
from pulse_welcome import (
    BANNER_DIR, KINDS, MAX_BANNER_BYTES, PLACEHOLDERS, SETTING_KEY, build_member_message,
    normalize_settings, sample_member, validate_settings,
)

# Pages that belong to the administration area (sidebar switches to "Verwaltung").
ADMIN_PAGE_KEYS = frozenset({
    "admin", "admin-discord", "ticket-panels", "settings", "backups", "system", "announcements", "social",
})


def admin_sections(perms: dict) -> list[tuple]:
    """(key, href, icon, label, description) of administration areas this person may open."""
    is_admin = bool(perms.get("is_admin"))
    announcements = is_admin or bool(perms.get("can_manage_announcements"))
    sections = [
        ("admin-discord", "/admin/discord", "🎨", "Discord-Auftritt", "Embed-Design, Willkommen & Abschied, Banner", is_admin),
        ("ticket-panels", "/tickets/panels", "🎛️", "Ticket-Panels", "Panels, Kategorien, Begrüßungen, Formulare", is_admin),
        ("announcements", "/announcements", "📢", "Ankündigungen", "Globale Team- und Servernachrichten", announcements),
        ("social", "/social", "📣", "Social Media", "Creator-Kanäle und Video-Ankündigungen", announcements),
        ("settings", "/settings", "⚙", "Einstellungen & Rechte", "Rollen, Dashboard-Rechte, Module, Kanäle", is_admin),
        ("backups", "/backups", "🗄", "Backups", "Sicherungen erstellen und wiederherstellen", is_admin),
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


def render_preview(payload: dict | None, guild=None, banner_href: str = "") -> str:
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
        body = f'''{PREVIEW_CSS}
<div class="pulse-hero"><div class="pulse-hero-grid"><div>
 <div class="pulse-kicker">Verwaltung</div><div class="pulse-title">Konfiguration &amp; Systembetrieb</div>
 <div class="pulse-sub">Hier liegen alle Einstellungen, die den Server oder den Bot global verändern. Die tägliche Teamarbeit findest du im Bereich <a href="/me" style="color:inherit;font-weight:800;text-decoration:underline">Team</a>.</div></div>
 <div class="pulse-hero-box"><div class="label">Discord-Nachrichten</div><div class="mt-2 flex flex-wrap gap-2">{"".join(status)}</div></div>
</div></div>
<div class="grid gap-4" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">{cards}</div>'''
        return HTMLResponse(render_pro_page(ws, "Verwaltung", ctx, "admin", body))

    # ------------------------------------------------------------------ Discord-Auftritt
    def discord_page(ctx, settings: dict, brand: dict, errors: list[str] | None = None) -> HTMLResponse:
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
<form method="post" action="/admin/discord/welcome" enctype="multipart/form-data" class="space-y-5">
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
        try:
            data = await upload.read(MAX_BANNER_BYTES + 1)
        finally:
            await upload.close()
        if len(data) > MAX_BANNER_BYTES:
            return "", f"Das Banner ist zu groß (maximal {MAX_BANNER_BYTES // (1024 * 1024)} MB)."
        ext = detect_image_type(data)
        if not ext:
            return "", "Als Banner sind nur PNG-, JPG-, GIF- oder WEBP-Bilder erlaubt."
        name = f"{kind}_banner_{secrets.token_hex(4)}.{ext}"
        banner_root().mkdir(parents=True, exist_ok=True)
        temporary = banner_root() / (name + ".uploading")
        temporary.write_bytes(data)
        os.replace(temporary, banner_root() / name)
        return name, ""

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
                   "colors": {key: form.get(f"color_{key}", "") for key in DEFAULT_COLORS}}
            invalid = [key for key, value in raw["colors"].items() if str(value).strip() and not normalize_hex(value, "")]
            if invalid:
                return ws.back("/admin/discord", "Ungültige Farbe – bitte den Farbwähler verwenden.", False)
            value = save_branding(raw)
        ws.log_audit(ctx.user.get("global_name") or ctx.user.get("username"), ctx.user["id"], "Embed-Design geändert",
                     f"Footer: {value['footer_text']}")
        return ws.back("/admin/discord", "Embed-Design gespeichert.", True)

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

    @app.get("/admin/discord/banner/{filename}")
    async def admin_discord_banner(request: Request, filename: str, user_session: str = Cookie(None)):
        admin_ctx(request, user_session)
        name = os.path.basename(filename)
        if not re.fullmatch(r"(welcome|leave)_banner_[0-9a-f]{8}\.(png|jpg|gif|webp)", name):
            raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
        path = (banner_root() / name).resolve()
        if path.parent != banner_root().resolve() or not path.is_file():
            raise HTTPException(status_code=404, detail="Bild nicht gefunden.")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})
