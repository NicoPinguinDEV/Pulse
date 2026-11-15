"""Audit #58 and #70: live activity feed and real-time status for the dashboard (polling, no extra process).

Transport: the browser asks two small JSON endpoints every ``POLL_SECONDS`` seconds while the tab is visible
(``/api/live/status`` for everybody with dashboard access, ``/api/live/feed`` for leadership). Polling reuses the
normal session, rate limits and security headers, keeps no open connections and needs no extra memory on the VPS.

Privacy: the feed shows *who did what* from the audit log (actor + action), never the detail text. Warnings, internal
notes and recruiting actions are filtered per permission, exactly like the other views (E-3 / D-08).
"""
from __future__ import annotations

from fastapi import Cookie, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

POLL_SECONDS = 15
FEED_LIMIT = 15
_WARN_WORDS = ("warn", "verwarn")
_NOTE_WORDS = ("notiz", "note")
_APP_WORDS = ("bewerb", "interview", "gespräch", "application", "recruit")


def is_lead(perms: dict) -> bool:
    return bool((perms or {}).get("is_admin") or (perms or {}).get("can_promote"))


def visible_action(action: str, perms: dict) -> bool:
    """May this viewer see that an audit action happened? (Detail text is never shown in the feed.)"""
    text = str(action or "").casefold()
    perms = perms or {}
    if any(w in text for w in _WARN_WORDS) and not (perms.get("can_warn") or perms.get("can_manage_team_warnings") or perms.get("is_admin")):
        return False
    if any(w in text for w in _NOTE_WORDS) and not (perms.get("can_add_notes") or is_lead(perms)):
        return False
    if any(w in text for w in _APP_WORDS) and not (perms.get("can_manage_applications") or is_lead(perms)):
        return False
    return True


def feed_items(entries, perms: dict, after: str = "", limit: int = FEED_LIMIT) -> list[dict]:
    """Newest-first feed rows ``{id, at, actor, action}``; with ``after`` only entries newer than that audit id."""
    rows = [e for e in (entries or ()) if isinstance(e, dict) and e.get("id")]
    if after:
        ids = [str(e["id"]) for e in rows]
        if after in ids:
            rows = rows[ids.index(after) + 1:]
    out = []
    for entry in reversed(rows):
        if not visible_action(entry.get("action"), perms):
            continue
        out.append({"id": str(entry["id"]), "at": str(entry.get("timestamp") or "")[:16], "actor": str(entry.get("actor") or "System")[:80],
                    "action": str(entry.get("action") or "")[:120]})
        if len(out) >= max(1, min(int(limit), 50)):
            break
    return out


def status_snapshot(ws, ctx) -> dict:
    """Small counters for the status strip; every number comes from data the viewer may already see on the dashboard."""
    import pulse_db
    import pulse_presence
    bot = getattr(ws.app.state, "bot", None)
    shifts = ws.load_shifts_raw() if hasattr(ws, "load_shifts_raw") else {}
    snap = {"on_duty": pulse_presence.on_duty(shifts), "open_tickets": pulse_presence.open_tickets(),
            "unread": int(pulse_db.unread_count(ctx.user["id"])), "bot_ready": bool(bot is not None and getattr(bot, "is_ready", lambda: False)()),
            "poll_seconds": POLL_SECONDS}
    latency = getattr(bot, "latency", None)
    snap["latency_ms"] = int(latency * 1000) if isinstance(latency, (int, float)) and latency == latency and latency != float("inf") else None
    return snap


def widget_html(esc, lead: bool) -> str:
    """Home widget: status strip for everybody, activity list for leadership. Updates itself while the tab is visible."""
    feed = ('<ul id="liveFeed" class="text-xs space-y-1 mt-3" aria-live="polite"></ul>' if lead else "")
    script = ("<script>(function(){const s=document.getElementById('liveStatus');if(!s)return;let after='',timer=null;"
              "const h=v=>String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
              "async function tick(){if(document.hidden)return;try{"
              "const r=await fetch('/api/live/status',{credentials:'same-origin'});if(!r.ok)throw 0;const d=await r.json();"
              "s.innerHTML='<span>🟢 Im Dienst: <b>'+h(d.on_duty)+'</b></span> · <span>🎫 Offene Tickets: <b>'+h(d.open_tickets)+'</b></span> · <span>🔔 Ungelesen: <b>'+h(d.unread)+'</b></span> · <span>🤖 Bot: <b>'+(d.bot_ready?'online':'offline')+'</b></span>';"
              "const f=document.getElementById('liveFeed');if(f){const q=await fetch('/api/live/feed'+(after?'?after='+encodeURIComponent(after):''),{credentials:'same-origin'});"
              "if(q.ok){const j=await q.json();if(j.items.length){after=j.items[0].id;const li=j.items.map(x=>'<li><span class=\"text-slate-400\">'+h(x.at)+'</span> · <b>'+h(x.actor)+'</b> – '+h(x.action)+'</li>').join('');"
              "f.innerHTML=after&&f.dataset.init?li+f.innerHTML:li;f.dataset.init='1';while(f.children.length>15)f.removeChild(f.lastChild);}}}"
              "}catch(e){s.textContent='Live-Status gerade nicht erreichbar.';}}"
              # #117/#70: push via the event bus (SSE) as soon as something happens; the interval below stays as the fallback.
              "let es=null,deb=null;function closeStream(){if(es){es.close();es=null;}}"
              "function openStream(){if(es||document.hidden||!window.EventSource)return;try{es=new EventSource('/api/events/stream');"
              "es.addEventListener('pulse',()=>{clearTimeout(deb);deb=setTimeout(tick,400);});"
              "es.onerror=()=>{if(es&&es.readyState===2)es=null;};}catch(e){es=null;}}"
              f"tick();openStream();timer=setInterval(()=>{{tick();openStream();}},{POLL_SECONDS * 1000});"
              "document.addEventListener('visibilitychange',()=>{if(document.hidden){closeStream();}else{tick();openStream();}});"
              "window.addEventListener('pagehide',closeStream);})();</script>")
    return ('<div id="liveStatus" class="text-xs" role="status">Lädt …</div>' + feed
            + (f'<p class="text-[11px] text-slate-400 mt-2">Aktualisiert sich sofort bei neuen Ereignissen und sonst alle {POLL_SECONDS} Sekunden, solange dieser Tab sichtbar ist.</p>')
            + script)


def register(app) -> None:
    import webserver as ws

    @app.get("/api/live/status")
    async def live_status(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session)
        return JSONResponse(status_snapshot(ws, ctx), headers={"Cache-Control": "no-store"})

    @app.get("/api/live/feed")
    async def live_feed(request: Request, user_session: str = Cookie(None)):
        ctx = ws.auth(request, user_session, perm=None)
        if not is_lead(ctx.perms):
            raise HTTPException(status_code=403, detail="Den Aktivitätsfeed sehen nur Führungskräfte.")
        entries = ws.load_json(ws.AUDIT_FILE, [])
        after = str(request.query_params.get("after") or "")[:40]
        items = feed_items(entries if isinstance(entries, list) else [], ctx.perms, after)
        return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})

    @app.get("/api/events/poll")
    async def events_poll(request: Request, user_session: str = Cookie(None)):
        """#117: guaranteed fallback transport of the event bus (cursor polling, filtered per viewer)."""
        import pulse_eventbus
        ctx = ws.auth(request, user_session)
        cursor = request.query_params.get("cursor")
        data = pulse_eventbus.poll(ctx.perms, cursor if cursor not in (None, "") else None)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.get("/api/events/stream")
    async def events_stream(request: Request, user_session: str = Cookie(None)):
        """#117: Server-Sent Events on the same cursor; capped per user and globally (503 when full)."""
        import pulse_eventbus
        ctx = ws.auth(request, user_session)
        uid = str(ctx.user.get("id"))
        if not pulse_eventbus.acquire_slot(uid):
            raise HTTPException(status_code=503, detail="Zu viele offene Live-Verbindungen.")
        cursor = request.headers.get("last-event-id") or request.query_params.get("cursor")

        async def body():
            try:
                async for frame in pulse_eventbus.stream(ctx.perms, cursor if cursor not in (None, "") else None, request.is_disconnected):
                    yield frame
            finally:
                pulse_eventbus.release_slot(uid)

        return StreamingResponse(body(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
