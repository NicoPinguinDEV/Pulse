"""Dashboard page for creator video announcements (/social)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Cookie, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from pulse_social_utils import detect_platform, platform_meta


def register(app) -> None:
    import webserver as ws
    from pulse_pro import render_pro_page

    e = ws.esc

    def ctx_for(request, session):
        ctx = ws.auth(request, session, perm=None)
        if not (ctx.perms.get("is_admin") or ctx.perms.get("can_manage_announcements")):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung (Ankündigungen verwalten).")
        return ctx

    def cog_for(request):
        bot = getattr(request.app.state, "bot", None)
        cog = bot.get_cog("SocialPromo") if bot else None
        if cog is None:
            raise ValueError("Das Social-Modul ist im laufenden Bot nicht geladen.")
        return cog

    def resolve_member(guild, raw: str):
        raw = str(raw or "").strip().lstrip("@")
        if not raw:
            return None, ""
        if raw.strip("<>!").isdigit():
            member = guild.get_member(int(raw.strip("<>!")))
            return member, "" if member else "Discord-Mitglied mit dieser ID wurde nicht gefunden."
        lowered = raw.casefold()
        matches = [m for m in guild.members if not m.bot and lowered in {m.name.casefold(), m.display_name.casefold()}]
        if len(matches) == 1:
            return matches[0], ""
        return None, ("Mehrere Mitglieder passen – bitte die Discord-ID angeben." if matches else "Discord-Mitglied wurde nicht gefunden.")

    def ago(value) -> str:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return "—"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        minutes = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
        if minutes < 1:
            return "gerade eben"
        if minutes < 60:
            return f"vor {minutes} Min."
        if minutes < 1440:
            return f"vor {minutes // 60} Std."
        return f"vor {minutes // 1440} Tagen"

    @app.get("/social", response_class=HTMLResponse)
    async def social_page(request: Request, user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        guild = ctx.guild
        try:
            cog = cog_for(request)
            sources = list(cog.sources.values())
            module_ok = True
        except ValueError:
            cog, sources, module_ok = None, [], False
        import cogs.social_promo as social
        channel = guild.get_channel(social.TARGET_CHANNEL_ID)
        role = guild.get_role(social.SOCIAL_PING_ROLE_ID)

        rows = []
        for source in sorted(sources, key=lambda s: str(s.get("name", "")).lower()):
            sid = e(source.get("id"))
            label, color, emoji, _ = platform_meta(source.get("platform", "rss"))
            if not source.get("enabled", True):
                pill = '<span class="pulse-pill">⏸ pausiert</span>'
            elif source.get("feed_status") == "pending" or not source.get("initialized"):
                pill = '<span class="pulse-pill warn">🟡 wartet auf Feed</span>'
            elif source.get("feed_status") == "degraded":
                pill = '<span class="pulse-pill warn">🟠 Feed gestört</span>'
            else:
                pill = '<span class="pulse-pill good">🟢 aktiv</span>'
            creator = guild.get_member(int(source["creator_id"])) if str(source.get("creator_id") or "").isdigit() else None
            error = (f'<details class="mt-2"><summary class="text-[11px] text-amber-500 cursor-pointer">Letzter Fehler anzeigen</summary>'
                     f'<p class="text-[11px] text-slate-400 mt-1">{e(source.get("last_error"))}</p></details>') if source.get("last_error") else ""
            toggle_value = "0" if source.get("enabled", True) else "1"
            rows.append(f'''
<div class="pulse-row" style="align-items:flex-start;flex-wrap:wrap">
 <div class="pulse-avatar" style="background:#{color:06x}22;color:#{color:06x}">{emoji}</div>
 <div class="pulse-row-main" style="flex:1;min-width:200px">
  <div class="pulse-row-title">{e(source.get("name"))} <span class="text-slate-400 font-normal">· {e(label)}</span></div>
  <div class="pulse-row-meta">{("🎥 " + e(creator.display_name) + " · ") if creator else ""}geprüft {ago(source.get("last_checked"))} · {int(source.get("announced_count", 0) or 0)} Ankündigungen{(" · zuletzt " + ago(source.get("last_announced_at"))) if source.get("last_announced_at") else ""}</div>
  <div class="mt-1">{pill}</div>{error}
  <details class="mt-2"><summary class="text-[11px] text-indigo-500 cursor-pointer">Bearbeiten</summary>
   <form method="post" action="/social/update" class="grid sm:grid-cols-3 gap-2 mt-2">
    <input type="hidden" name="source_id" value="{sid}">
    <input class="pulse-input" name="name" value="{e(source.get("name"))}" maxlength="100" placeholder="Anzeigename">
    <input class="pulse-input" name="creator" value="{e(source.get("creator_id"))}" placeholder="Discord-ID oder @Name (leer = keiner)">
    <button class="pulse-btn ghost" type="submit">Speichern</button>
   </form></details>
 </div>
 <div class="flex flex-wrap gap-2">
  <form method="post" action="/social/announce-latest" onsubmit="return confirm('Das neueste Video dieser Quelle jetzt ankündigen (mit Ping)?')"><input type="hidden" name="source_id" value="{sid}"><button class="pulse-btn ghost" type="submit">📣 Letztes Video ankündigen</button></form>
  <form method="post" action="/social/toggle"><input type="hidden" name="source_id" value="{sid}"><input type="hidden" name="enabled" value="{toggle_value}"><button class="pulse-btn ghost" type="submit">{"⏸ Pausieren" if toggle_value == "0" else "▶ Fortsetzen"}</button></form>
  <form method="post" action="/social/remove" onsubmit="return confirm('Quelle wirklich entfernen?')"><input type="hidden" name="source_id" value="{sid}"><button class="pulse-btn bad" type="submit">🗑</button></form>
 </div>
</div>''')
        listing = "".join(rows) or '<div class="pulse-empty">Noch keine Creator verknüpft. Füge oben den ersten Kanal-Link ein.</div>'
        active = sum(1 for s in sources if s.get("enabled", True) and s.get("feed_status") == "active")
        total_announced = sum(int(s.get("announced_count", 0) or 0) for s in sources)
        warning = "" if module_ok else '<div class="pulse-alert" style="border-color:rgba(240,68,56,.3)"><strong>⚠ Das Social-Modul ist im Bot nicht geladen.</strong> Bitte den Bot neu starten.</div>'

        body = f'''
<div class="pulse-hero"><div class="pulse-hero-grid">
 <div><div class="pulse-kicker">Creator-Programm</div><div class="pulse-title">Social Media</div>
  <div class="pulse-sub">Verknüpfe Kanäle eurer Content Creator per Link. Pulse prüft automatisch alle 2 Minuten auf neue Videos und kündigt sie professionell an – mit Ping, Vorschaubild und „Jetzt ansehen“-Button.</div></div>
 <div class="pulse-hero-box"><div class="label">Ankündigungskanal</div><div class="value" style="font-size:18px">#{e(channel.name if channel else social.TARGET_CHANNEL_ID)}</div>
  <div class="label mt-2">Ping: @{e(role.name if role else social.SOCIAL_PING_ROLE_ID)}</div></div>
</div></div>
{warning}
<div class="pulse-stat-grid">
 <div class="pulse-stat"><div class="icon">🎥</div><div class="label">Verknüpfte Creator</div><div class="value">{len(sources)}</div></div>
 <div class="pulse-stat"><div class="icon">🟢</div><div class="label">Aktiv</div><div class="value">{active}</div></div>
 <div class="pulse-stat"><div class="icon">📣</div><div class="label">Ankündigungen</div><div class="value">{total_announced}</div></div>
</div>
<div class="pulse-grid">
 <div class="space-y-5">
  <section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">🔗 Creator per Link verknüpfen</div></div><div class="pulse-card-b">
   <form method="post" action="/social/add" class="grid gap-3">
    <label class="grid gap-1 text-xs font-semibold">Link zum Kanal oder Profil
     <input class="pulse-input" name="link" required maxlength="300" placeholder="https://www.youtube.com/@kanalname · https://www.tiktok.com/@name · https://www.twitch.tv/name"></label>
    <div class="grid sm:grid-cols-2 gap-3">
     <label class="grid gap-1 text-xs font-semibold">Discord-Mitglied (optional)<input class="pulse-input" name="creator" maxlength="100" placeholder="Discord-ID oder @Name"></label>
     <label class="grid gap-1 text-xs font-semibold">Anzeigename (optional)<input class="pulse-input" name="name" maxlength="100" placeholder="z. B. Marvin Gaming"></label>
    </div>
    <button class="pulse-btn primary" type="submit">Verknüpfen &amp; überwachen</button>
    <p class="text-[11px] text-slate-400">Die Plattform wird am Link erkannt. Bereits veröffentlichte Videos werden nicht rückwirkend angekündigt, Videos älter als 3 Tage nie.</p>
   </form></div></section>
  <section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">📡 Überwachte Kanäle</div>
   <form method="post" action="/social/test"><button class="pulse-btn ghost" type="submit">🧪 Testankündigung senden</button></form></div>
   <div class="pulse-card-b"><div class="pulse-list">{listing}</div></div></section>
 </div>
 <div class="space-y-5">
  <section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">ℹ️ Plattformen</div></div><div class="pulse-card-b text-xs space-y-2">
   <div class="pulse-alert"><strong>▶️ YouTube – sehr zuverlässig</strong><div class="text-slate-400 mt-1">Offizieller YouTube-Feed, Videos und Shorts werden erkannt, inkl. Vorschaubild.</div></div>
   <div class="pulse-alert"><strong>🟣 Twitch-Videos</strong><div class="text-slate-400 mt-1">Über RSSHub (VODs/Archiv).</div></div>
   <div class="pulse-alert"><strong>🎵 TikTok · 📸 Instagram · ✖️ X</strong><div class="text-slate-400 mt-1">Über RSSHub. Diese Plattformen blockieren öffentliche Abrufe häufig – Pulse versucht mehrere Server und meldet Störungen hier. Für dauerhaften Betrieb empfiehlt sich eine eigene RSSHub-Instanz (PULSE_RSSHUB_BASE_URL).</div></div>
  </div></section>
  <section class="pulse-card"><div class="pulse-card-h"><div class="pulse-section-title">💬 Discord-Befehle</div></div><div class="pulse-card-b text-xs space-y-1">
   <div><code>/social_link</code> – Creator per Link verknüpfen</div><div><code>/social_list</code> · <code>/social_toggle</code> · <code>/social_remove</code> · <code>/social_test</code></div>
  </div></section>
 </div>
</div>'''
        from pulse_pro import render_pro_page as _render
        return HTMLResponse(_render(ws, "Social Media", ctx, "social", body))

    @app.post("/social/add")
    async def social_add_link(request: Request, link: str = Form(...), creator: str = Form(""), name: str = Form(""), user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        try:
            platform, quelle = detect_platform(link)
            member, error = resolve_member(ctx.guild, creator)
            if error:
                raise ValueError(error)
            source, pending = await cog_for(request).add_source(platform, quelle, name.strip() or None, str(member.id) if member else None, str(ctx.user["id"]))
        except ValueError as exc:
            return ws.back("/social", str(exc)[:400], False)
        ws.log_audit(ctx.user.get("global_name"), ctx.user["id"], "Creator verknüpft", f"{source['name']} ({source['platform_label']}) · {source['id']}")
        message = (f"„{source['name']}“ gespeichert – der Feed ist gerade nicht erreichbar, Pulse versucht es automatisch weiter."
                   if pending else f"„{source['name']}“ ({source['platform_label']}) wird jetzt überwacht.")
        return ws.back("/social", message, True)

    @app.post("/social/update")
    async def social_update(request: Request, source_id: str = Form(...), name: str = Form(""), creator: str = Form(""), user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        try:
            member, error = resolve_member(ctx.guild, creator)
            if error:
                raise ValueError(error)
            source = cog_for(request).update_source(source_id, name=name, creator_id=str(member.id) if member else "")
        except ValueError as exc:
            return ws.back("/social", str(exc), False)
        return ws.back("/social", f"„{source['name']}“ aktualisiert.", True)

    @app.post("/social/toggle")
    async def social_toggle_web(request: Request, source_id: str = Form(...), enabled: str = Form("1"), user_session: str = Cookie(None)):
        ctx_for(request, user_session)
        try:
            source = cog_for(request).set_enabled(source_id, enabled == "1")
        except ValueError as exc:
            return ws.back("/social", str(exc), False)
        return ws.back("/social", f"„{source['name']}“ {'fortgesetzt' if source['enabled'] else 'pausiert'}.", True)

    @app.post("/social/remove")
    async def social_remove_web(request: Request, source_id: str = Form(...), user_session: str = Cookie(None)):
        ctx = ctx_for(request, user_session)
        try:
            removed = cog_for(request).remove_source(source_id)
        except ValueError as exc:
            return ws.back("/social", str(exc), False)
        ws.log_audit(ctx.user.get("global_name"), ctx.user["id"], "Creator entfernt", f"{removed.get('name')} · {removed.get('id')}")
        return ws.back("/social", f"„{removed.get('name')}“ entfernt.", True)

    @app.post("/social/announce-latest")
    async def social_announce_latest(request: Request, source_id: str = Form(...), user_session: str = Cookie(None)):
        ctx_for(request, user_session)
        try:
            title = await cog_for(request).announce_latest(source_id)
        except Exception as exc:  # network/feed errors are shown to the admin, not raised
            return ws.back("/social", f"Ankündigung nicht möglich: {str(exc)[:300]}", False)
        return ws.back("/social", f"Angekündigt: {title[:120]}", True)

    @app.post("/social/test")
    async def social_test_web(request: Request, user_session: str = Cookie(None)):
        ctx_for(request, user_session)
        try:
            cog = cog_for(request)
            channel = await cog._target_channel()
            if channel is None:
                raise ValueError("Ankündigungskanal nicht erreichbar.")
            import cogs.social_promo as social
            sample_source = {"platform": "youtube", "name": "Beispiel-Creator", "source_key": "youtube"}
            sample_item = {"title": "🧪 Testankündigung – so sehen neue Videos aus", "url": "https://www.youtube.com/",
                           "description": "Dies ist eine Testnachricht von Pulse.", "published": datetime.now(timezone.utc).isoformat()}
            content, embed, view = cog.build_announcement(sample_source, sample_item)
            import discord
            await channel.send(content=content, embed=embed, view=view, allowed_mentions=discord.AllowedMentions(
                roles=[discord.Object(id=social.SOCIAL_PING_ROLE_ID)], users=False, everyone=False))
        except Exception as exc:
            return ws.back("/social", f"Test fehlgeschlagen: {str(exc)[:300]}", False)
        return ws.back("/social", "Testankündigung wurde gesendet.", True)
