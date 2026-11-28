"""UI layout, shared page rendering, health checks and the protected live console."""

from pulse_web._compat import bind_legacy_namespace as _bind_legacy_namespace
_bind_legacy_namespace(globals())
del _bind_legacy_namespace

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Runtime bindings come from the webserver composition root until the
    # remaining legacy cross-domain globals are converted to service APIs.
    from webserver import (
        FileResponse,
        GUILD_ID,
        HTMLResponse,
        HTTPException,
        BASE_DIR,
        JSONResponse,
        JsonWriteBlocked,
        OrderedDict,
        Request,
        _shell_hooks,
        Cookie,
        Form,
        RedirectResponse,
        StarletteHTTPException,
        PULSE_VERSION,
        app,
        auth,
        can_access_melonly,
        datetime,
        display,
        get_melonly_repository,
        httpx,
        rank_roblox_candidates,
        roblox_similarity_score,
        threading,
        parse_timestamp,
        re,
        secrets,
        shell_hook,
        time,
        discord,
        esc,
        log_audit,
        pulse_db,
        pulse_logging,
        pulse_public_site,
        rate_limited,
    )

# UI-BAUSTEINE
# =============================================================
CARD = "bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-2xl shadow-sm"
INPUT = ("w-full bg-slate-50 dark:bg-[#0b0e14] border border-slate-300 dark:border-slate-700 rounded-xl "
         "p-3 text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500")
BTN = "bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded-xl transition shadow-md shadow-indigo-600/20"
BADGE_OK = "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30"
BADGE_BAD = "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30"
BADGE_WARN = "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30"

SCROLL_RESTORE_SCRIPT = "<script>\n(function () {\n    const key = \"pulse:scroll-restore:v1\";\n    function clearSavedPosition() {\n        try { sessionStorage.removeItem(key); } catch (_) {}\n    }\n\n    // Restore only when a form redirect returns to the same page, never on unrelated navigation.\n    try {\n        const raw = sessionStorage.getItem(key);\n        if (raw) {\n            const saved = JSON.parse(raw);\n            const now = Date.now();\n            const fresh = saved && typeof saved.at === \"number\" && now >= saved.at && now - saved.at < 120000;\n            if (fresh && saved.path === location.pathname && Number.isFinite(saved.y)) {\n                clearSavedPosition();\n                const restore = () => requestAnimationFrame(() => requestAnimationFrame(() => {\n                    window.scrollTo(Number(saved.x) || 0, Number(saved.y) || 0);\n                }));\n                if (document.readyState === \"complete\") restore();\n                else window.addEventListener(\"load\", restore, { once: true });\n            } else {\n                clearSavedPosition();\n            }\n        }\n    } catch (_) {\n        clearSavedPosition();\n    }\n\n    // Save the viewport before ordinary form navigation (save buttons, filters and actions).\n    document.addEventListener(\"submit\", function (event) {\n        const form = event.target;\n        if (!(form instanceof HTMLFormElement) || form.dataset.noScrollRestore === \"true\") return;\n        if (form.target && form.target.toLowerCase() !== \"_self\") return;\n        try {\n            sessionStorage.setItem(key, JSON.stringify({\n                path: location.pathname,\n                x: window.scrollX,\n                y: window.scrollY,\n                at: Date.now()\n            }));\n        } catch (_) {}\n    }, true);\n})();\n</script>"

# U-03: Tailwind is built once with the standalone CLI (tools/build_css.py) and served from
# /static/pulse.css. Only if that file is missing (E-10: fall back and warn) the old Play-CDN is used.
STATIC_CSS = BASE_DIR / "static" / "pulse.css"


def _static_css_version() -> str:
    import hashlib
    try:
        return hashlib.sha256(STATIC_CSS.read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


STATIC_CSS_VERSION = _static_css_version()
if STATIC_CSS_VERSION:
    TAILWIND_TAG = f'<link rel="stylesheet" href="/static/pulse.css?v={STATIC_CSS_VERSION}">'
else:
    print("⚠️ static/pulse.css fehlt – Dashboard wird ohne externe CDN-Ersatzressource ausgeliefert. tools/build_css.py ausführen.")
    TAILWIND_TAG = ""

HEAD_SCRIPT = TAILWIND_TAG + """
<script>
    // Phase 12: the black/red design is the primary look; light stays available via the theme switch.
    if (localStorage.theme !== 'light') {
        document.documentElement.classList.add('dark');
    } else {
        document.documentElement.classList.remove('dark');
    }
    function toggleTheme() {
        if (document.documentElement.classList.contains('dark')) {
            document.documentElement.classList.remove('dark'); localStorage.theme = 'light';
        } else {
            document.documentElement.classList.add('dark'); localStorage.theme = 'dark';
        }
    }
    function toggleSidebar() {
        const s = document.getElementById('sidebar'), b = document.getElementById('sidebarBackdrop');
        if (!s || !b) return;
        s.classList.toggle('hidden'); s.classList.toggle('flex'); b.classList.toggle('hidden');
        const button = document.querySelector('[aria-controls="sidebar"]');
        if (button) button.setAttribute('aria-expanded', String(!s.classList.contains('hidden')));
    }
    function escapeHtml(v) {
        return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
</script>
""" + SCROLL_RESTORE_SCRIPT


def get_head_html(title: str):
    extra = _shell_hooks.get("head_extra")
    return _base_head_html(title) + (extra(title) if extra is not None else "")


def _base_head_html(title: str):
    return (
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '<meta name="color-scheme" content="light dark">\n'
        '<style>:focus-visible{outline:3px solid #6366f1!important;outline-offset:3px}'
        '@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;'
        'animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}'
        '</style>\n'
        f"<title>{esc(title)}</title>\n" + HEAD_SCRIPT
    )


def get_sidebar_html(guild_name, current_page="dashboard", current_user=None, perms=None):
    renderer = _shell_hooks.get("sidebar")
    if renderer is not None:
        return renderer(guild_name, current_page, current_user, perms)
    perms = perms or {}
    user_name = esc(current_user.get("global_name", "Team Mitglied")) if current_user else "Gast"
    avatar_id = current_user.get("avatar") if current_user else None
    user_id = current_user.get("id") if current_user else None
    avatar_url = (f"https://cdn.discordapp.com/avatars/{esc(user_id)}/{esc(avatar_id)}.png"
                  if avatar_id and user_id else "https://cdn.discordapp.com/embed/avatars/0.png")
    try:
        unread = pulse_db.unread_count(user_id) if user_id else 0
    except Exception:
        unread = 0

    inbox_label = "Pulse Inbox" + (f" <span class='ml-auto text-[9px] rounded-full bg-rose-500 text-white px-1.5 py-0.5'>{unread}</span>" if unread else "")
    main_items = [
        ("ultimate", "/ultimate", "⚡", "Command Center"),
        ("dashboard", "/dashboard", "🛡️", "Moderatoren-Panel"),
        ("melonly", "/melonly", "🎮", "Melonly"),
        ("team", "/team", "👥", "Teamliste"),
        ("inbox", "/pulse-inbox", "📥", inbox_label),
        ("tasks", "/tasks", "📋", "Aufgaben"),
        ("tickets", "/tickets", "🎫", "Tickets"),
        ("meetings", "/meetings", "🎙️", "Teambesprechung"),
        ("meeting_history", "/meetings-history", "🗂️", "Meeting-Historie"),
        ("calendar", "/calendar", "🗓️", "Team-Kalender"),
        ("loa", "/loa", "🌴", "Abmeldungen (LOA)"),
        ("apps", "/applications", "📝", "Bewerbungen"),
        *([("factions", "/factions", "⚑", "Fraktionsverwaltung")] if perms.get("can_manage_factions") else []),
        ("wiki", "/wiki", "📚", "Team-Wiki"),
        ("training", "/training", "🎓", "Schulungen"),
        ("achievements", "/achievements", "🏅", "Achievements"),
        ("stats", "/stats", "📊", "Statistiken"),
    ]
    admin_items = [
        ("backups", "/backups", "💾", "Server Backups"),
        ("system", "/system", "🩺", "Systemstatus"),
        ("setup", "/setup", "🧭", "Einrichtungsassistent"),
        ("security", "/admin/security", "🛡️", "AutoMod & Anti-Nuke"),
        ("search", "/search", "🔎", "Globale Suche"),
        ("settings", "/settings", "⚙️", "Rechte & Audit-Log"),
        ("live-console", "/admin/live-console", "🖥️", "Live-Konsole"),
    ] if perms.get("is_admin") else [("search", "/search", "🔎", "Globale Suche")]

    def link(key, href, icon, label):
        cls = ("bg-indigo-600/10 text-indigo-600 dark:text-indigo-400 font-semibold border border-indigo-500/20 shadow-sm"
               if current_page == key else
               "text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800/50 hover:text-slate-900 dark:hover:text-slate-200")
        return (f'<a href="{href}" class="flex items-center gap-2.5 px-3 py-2.5 rounded-xl {cls} transition">'
                f'{icon} <span>{label}</span></a>')

    nav = '<div class="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider px-2 mb-2">Hauptmenü</div>'
    nav += "".join(link(*i) for i in main_items)
    if admin_items:
        nav += '<div class="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider px-2 mt-5 mb-2">Verwaltung</div>'
        nav += "".join(link(*i) for i in admin_items)

    return f"""
    <button onclick="toggleSidebar()" aria-label="Menü" aria-controls="sidebar" aria-expanded="false" class="md:hidden fixed top-3 left-3 z-50 w-10 h-10 rounded-xl bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 shadow text-lg">☰</button>
    <div id="sidebarBackdrop" onclick="toggleSidebar()" class="hidden fixed inset-0 bg-black/50 z-30 md:hidden"></div>
    <aside id="sidebar" aria-label="Hauptnavigation" class="hidden md:flex fixed md:sticky top-0 left-0 z-40 h-screen w-64 overflow-y-auto bg-white dark:bg-[#141824] border-r border-slate-200 dark:border-slate-800/80 flex-col justify-between p-4 shrink-0 transition-colors duration-200">
        <div class="space-y-6">
            <div class="flex items-center gap-3 px-2">
                <div class="w-9 h-9 rounded-xl bg-indigo-600 flex items-center justify-center font-bold text-white shadow-md shadow-indigo-600/20">B</div>
                <div>
                    <h2 class="font-bold text-slate-900 dark:text-white leading-none">{esc(guild_name)}</h2>
                    <span class="text-[10px] text-slate-500 dark:text-slate-400 font-mono">Pulse v7 TeamOS</span>
                </div>
            </div>
            <div class="bg-slate-100 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 rounded-xl p-2.5 flex items-center gap-2 shadow-inner">
                <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                <span class="text-xs font-semibold text-slate-700 dark:text-slate-200 truncate">{esc(guild_name)}</span>
            </div>
            <nav class="space-y-1 text-xs">{nav}</nav>
        </div>
        <div class="border-t border-slate-200 dark:border-slate-800/80 pt-4 px-1 space-y-3 mt-6">
            <button onclick="toggleTheme()" class="w-full flex items-center justify-between px-3 py-2.5 rounded-xl bg-slate-100 dark:bg-slate-800/60 hover:bg-slate-200 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-300 text-xs font-medium transition shadow-sm">
                <span class="flex items-center gap-2">
                    <span class="dark:hidden">🌙 Dark Mode</span>
                    <span class="hidden dark:inline">☀️ Light Mode</span>
                </span>
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-200 dark:bg-slate-700 font-mono">Umschalten</span>
            </button>
            <div class="flex items-center justify-between pt-1">
                <div class="flex items-center gap-2.5 truncate">
                    <img src="{avatar_url}" alt="" class="w-7 h-7 rounded-full border border-slate-200 dark:border-slate-700 object-cover">
                    <span class="text-xs font-medium text-slate-700 dark:text-slate-300 truncate">{user_name}</span>
                </div>
                <a href="/logout" class="text-xs text-slate-400 hover:text-rose-500 dark:hover:text-rose-400 transition font-medium">↤ Abmelden</a>
            </div>
        </div>
    </aside>
    """


def render_page(title: str, ctx, page: str, body: str, extra_head: str = "") -> str:
    """Gemeinsamer Seitenrahmen (vorher in jeder Route kopiert) inkl. Toast-Meldung."""
    body_hook = shell_hook("body_extra")
    if body_hook is not None:
        body = body_hook() + body
    toast = ""
    if getattr(ctx, "msg", ""):
        color = BADGE_OK if ctx.msg_ok else BADGE_BAD
        icon = "✅" if ctx.msg_ok else "⚠️"
        # U-04: the message sticks to the top of the viewport (forms scroll back to their position after
        # submitting) and errors stay until closed instead of vanishing after 6 seconds.
        auto_close = 'setTimeout(()=>{const t=document.getElementById("toast"); if(t) t.remove();}, 6000);' if ctx.msg_ok else ""
        toast = (f'<div id="toast" role="{"status" if ctx.msg_ok else "alert"}" class="pulse-toast mb-5 rounded-xl shadow-lg">'
                 f'<div class="border {color} rounded-xl px-4 py-3 text-xs font-semibold flex items-center justify-between gap-3">'
                 f'<span>{icon} {esc(ctx.msg)}</span>'
                 f'<button onclick="document.getElementById(\'toast\').remove()" class="opacity-60 hover:opacity-100" aria-label="Meldung schließen">✕</button></div></div>'
                 f'<script>{auto_close}history.replaceState(null,"",location.pathname+location.hash);</script>')
    return f"""<!DOCTYPE html>
    <html lang="de">
    <head>{get_head_html(f"{title} - {ctx.guild_name}")}{extra_head}</head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-800 dark:text-slate-200 font-sans min-h-screen flex transition-colors duration-200">
        <a href="#main-content" class="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-xl focus:bg-white focus:px-4 focus:py-3 focus:text-indigo-700 focus:shadow-lg">Zum Hauptinhalt springen</a>
        {get_sidebar_html(ctx.guild_name, page, ctx.user, ctx.perms)}
        <main id="main-content" tabindex="-1" class="flex-1 min-w-0 p-4 pt-16 md:p-8 md:pt-8">
            {toast}
            {body}
        </main>
    </body>
    </html>"""


def time_tag(value) -> str:
    """U-09: a time shown as "vor 5 Min." in the browser, with the exact Berlin time as tooltip/fallback."""
    parsed = parse_timestamp(value)
    if parsed is None:
        return esc(str(value or "–"))
    exact = display(parsed)
    return f'<time datetime="{esc(parsed.isoformat())}" data-relative title="{esc(exact)}">{esc(exact)}</time>'


def empty_state(text: str, icon: str = "📭") -> str:
    """U-09: one look for "nothing here yet" across pages."""
    return f'<div class="pulse-empty-state" role="status"><span class="icon" aria-hidden="true">{icon}</span><span>{esc(text)}</span></div>'


def simple_page(icon: str, title: str, message: str, links: str = "") -> str:
    return f"""<!DOCTYPE html>
    <html lang="de"><head>{get_head_html(title)}</head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans">
        <div class="{CARD} p-8 max-w-md w-full text-center space-y-4">
            <div class="text-4xl">{icon}</div>
            <h1 class="text-lg font-bold">{esc(title)}</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400">{esc(message)}</p>
            <div class="flex justify-center gap-3 text-xs pt-2">{links}</div>
        </div>
    </body></html>"""


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in (301, 302, 303, 307) and exc.headers and "Location" in exc.headers:
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    icons = {400: "⚠️", 403: "🔒", 404: "🔍", 503: "⏳"}
    if exc.status_code in icons:
        links = ('<a href="/dashboard" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zum Dashboard</a>'
                 '<a href="/logout" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Abmelden</a>')
        return HTMLResponse(simple_page(icons[exc.status_code], f"Fehler {exc.status_code}", str(exc.detail), links),
                            status_code=exc.status_code)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    """P-11: friendly page with an error ID instead of "Internal Server Error"; the traceback goes to the log only."""
    import traceback as _traceback
    error_id = secrets.token_hex(3).upper()
    print(f"❌ Dashboard-Fehler [Fehler-ID {error_id}] {request.method} {request.url.path}: {type(exc).__name__}: {exc}")
    import pulse_logging
    pulse_logging.report_error(f"web.{request.method} {request.url.path}", f"{type(exc).__name__}: {exc}", error_id)
    _traceback.print_exception(type(exc), exc, exc.__traceback__)
    wants_json = request.url.path.startswith(("/api/", "/ultimate/api/", "/suite/api/")) or \
        "application/json" in request.headers.get("accept", "")
    if wants_json:
        return JSONResponse({"detail": "Interner Fehler", "error_id": error_id}, status_code=500)
    links = ('<a href="/dashboard" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zum Dashboard</a>'
             '<a href="javascript:history.back()" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Zurück</a>')
    return HTMLResponse(simple_page("⚠️", "Da ist etwas schiefgelaufen",
                                    f"Ein unerwarteter Fehler ist aufgetreten. Fehler-ID {error_id} – das Team findet sie im Log. "
                                    "Deine letzte Aktion wurde eventuell nicht ausgeführt.", links), status_code=500)


@app.exception_handler(JsonWriteBlocked)
async def json_write_blocked_handler(request: Request, exc: JsonWriteBlocked):
    links = '<a href="/system" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Systemstatus</a>'
    return HTMLResponse(simple_page("🛑", "Änderung nicht gespeichert",
                                    f"{exc}. Die Änderung wurde nicht gespeichert, damit keine Daten verloren gehen. "
                                    "Bitte die Datei reparieren oder aus einem Backup wiederherstellen.", links),
                        status_code=503)


# =============================================================
# API-ROUTE: ROBLOX LIVE USER LOOKUP (nur eingeloggt, mit Cache)
# =============================================================
# Small bounded LRU cache: both expiry and size are enforced so a stream of
# unique Roblox usernames cannot grow process memory without limit.
_ROBLOX_CACHE_TTL_SECONDS = 300
_ROBLOX_CACHE_MAX_ENTRIES = 300
_roblox_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()


def _prune_roblox_cache(now: float | None = None) -> None:
    now = time.time() if now is None else now
    expired = [
        key for key, (created_at, _value) in _roblox_cache.items()
        if now - created_at >= _ROBLOX_CACHE_TTL_SECONDS
    ]
    for key in expired:
        _roblox_cache.pop(key, None)
    while len(_roblox_cache) > _ROBLOX_CACHE_MAX_ENTRIES:
        _roblox_cache.popitem(last=False)


def _roblox_http_client() -> httpx.AsyncClient:
    """Reuse a low-concurrency HTTP pool instead of opening a client per lookup."""
    client = getattr(app.state, "roblox_http_client", None)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=3.0),
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
                keepalive_expiry=15.0,
            ),
        )
        app.state.roblox_http_client = client
    return client


@app.get("/api/roblox-user")
async def roblox_user_lookup(request: Request, username: str, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    if not can_access_melonly(ctx):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung für Melonly.")
    user = ctx.user
    client_key = f"roblox-lookup:{user.get('id')}:{request.client.host if request.client else 'unknown'}"
    if rate_limited(client_key, limit=60, window=60):
        return JSONResponse({"success": False, "message": "Zu viele Roblox-Abfragen. Bitte kurz warten."}, status_code=429)
    name = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", name):
        return JSONResponse({"success": False, "message": "Ungültiger Roblox-Name (3-20 Zeichen, A-Z, 0-9, _)"})

    cache_key = name.lower()
    cached = _roblox_cache.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _ROBLOX_CACHE_TTL_SECONDS:
        _roblox_cache.move_to_end(cache_key)
        info = dict(cached[1])
    else:
        if cached:
            _roblox_cache.pop(cache_key, None)
        client = _roblox_http_client()
        try:
            res = await client.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [name], "excludeBannedUsers": False}, timeout=5.0)
            res.raise_for_status()
            data = res.json()
            if not data.get("data"):
                return JSONResponse({"success": False, "message": "Nutzer auf Roblox nicht gefunden"})
            u = data["data"][0]
            avatar_url = "https://www.roblox.com/headshot-thumbnail/image"
            try:
                thumb = await client.get(
                    f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={u['id']}&size=150x150&format=Png&isCircular=true",
                    timeout=5.0)
                thumb.raise_for_status()
                td = thumb.json()
                if td.get("data"):
                    avatar_url = td["data"][0].get("imageUrl", avatar_url)
            except Exception:
                pass
            info = {"success": True, "id": str(u["id"]), "username": u["name"],
                    "displayName": u.get("displayName", u["name"]), "avatarUrl": avatar_url}
            _roblox_cache[cache_key] = (time.time(), info)
            _roblox_cache.move_to_end(cache_key)
            _prune_roblox_cache()
        except Exception:
            return JSONResponse({"success": False, "message": "Roblox ist gerade nicht erreichbar"})

    # Vorstrafen-Hinweis aus dem aktiven Melonly-Repository.
    previous = get_melonly_repository().roblox_user_history(info["id"], info["username"])
    counts = {}
    for l in previous:
        counts[l.get("type", "Log")] = counts.get(l.get("type", "Log"), 0) + 1
    info["previous_total"] = len(previous)
    info["previous_summary"] = ", ".join(f"{v}x {k}" for k, v in counts.items())
    return JSONResponse(info)


# =============================================================
# API: ROBLOX SUCHVORSCHLÄGE FÜR MELOONLY
# =============================================================
@app.get("/api/roblox-search")
async def roblox_search(request: Request, query: str, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    if not can_access_melonly(ctx):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung für Melonly.")
    user = ctx.user
    client_key = f"roblox-search:{user.get('id')}:{request.client.host if request.client else 'unknown'}"
    if rate_limited(client_key, limit=120, window=60):
        return JSONResponse({"success": False, "users": [], "message": "Zu viele Roblox-Suchanfragen. Bitte kurz warten."}, status_code=429)
    clean = (query or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{2,20}", clean):
        return JSONResponse({"success": True, "users": []})

    # Lokale Einträge fangen Roblox-Ausfälle ab; fuzzy Treffer helfen auch bei Tippfehlern.
    users_map = {}
    query_lower = clean.lower()
    for entry in get_melonly_repository().roblox_suggestions(5000):
        saved_name = str(entry.get("target_user") or "").strip()
        saved_id = str(entry.get("roblox_id") or "")
        if not saved_name:
            continue
        if query_lower not in saved_name.lower() and roblox_similarity_score(clean, saved_name) < 0.42:
            continue
        users_map.setdefault(saved_name.lower(), {
            "id": saved_id,
            "name": saved_name,
            "displayName": saved_name,
        })

    try:
        client = _roblox_http_client()
        res = await client.get(
            "https://users.roblox.com/v1/users/search",
            params={"keyword": clean, "limit": 8},
            timeout=5.0,
        )
        res.raise_for_status()
        data = res.json()
        for u in data.get("data", [])[:8]:
            name = str(u.get("name") or "")
            if not name:
                continue
            users_map[name.lower()] = {
                "id": str(u.get("id", "")),
                "name": name,
                "displayName": str(u.get("displayName") or name),
            }
    except Exception:
        # Bereits protokollierte ähnliche Namen werden bei Roblox-Ausfällen weiterhin angezeigt.
        pass

    ranked_users = rank_roblox_candidates(clean, list(users_map.values()), limit=8)
    return JSONResponse({
        "success": True,
        "users": ranked_users,
        "best_match": ranked_users[0] if ranked_users else None,
    })


# =============================================================
# ADMINISTRATOR: SICHERE LIVE-KONSOLE
# =============================================================
# Intentionally no shell/eval/subprocess execution here. Commands are allowlisted.
from collections import deque as _console_deque
import logging as _console_logging

_CONSOLE_LOG_LIMIT = 250
_console_log_buffer = _console_deque(maxlen=_CONSOLE_LOG_LIMIT)
_console_log_lock = threading.Lock()
_console_handler_installed = False
_CONSOLE_COMMANDS = {"help", "status", "health", "version", "logs", "sync"}


class _PulseConsoleLogHandler(_console_logging.Handler):
    def emit(self, record):
        try:
            item = {
                "time": datetime.fromtimestamp(record.created).astimezone().strftime("%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name[:80],
                "message": self.format(record)[:1200],
            }
            with _console_log_lock:
                _console_log_buffer.append(item)
        except Exception:
            pass


def _ensure_console_log_handler():
    global _console_handler_installed
    if _console_handler_installed:
        return
    with _console_log_lock:
        if _console_handler_installed:
            return
        handler = _PulseConsoleLogHandler()
        handler.setLevel(_console_logging.INFO)
        handler.setFormatter(_console_logging.Formatter("%(message)s"))
        _console_logging.getLogger().addHandler(handler)
        _console_handler_installed = True


def _console_logs(limit=100):
    _ensure_console_log_handler()
    with _console_log_lock:
        return list(_console_log_buffer)[-max(1, min(int(limit), _CONSOLE_LOG_LIMIT)):]


def _console_result(command, ctx, bot=None):
    bot = bot or getattr(ctx, "bot", None)
    if command == "help":
        return "Erlaubte Befehle: help, status, health, version, logs, sync"
    if command == "version":
        return f"Pulse TeamOS {PULSE_VERSION}"
    if command in {"status", "health"}:
        bot = getattr(ctx, "bot", None)
        ready = bool(bot and bot.is_ready())
        guild = bot.get_guild(GUILD_ID) if bot else None
        try:
            with pulse_db.connect() as db:
                db.execute("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False
        return (
            f"Version: {PULSE_VERSION}\nBot bereit: {ready}\n"
            f"Discord-Server verfügbar: {bool(guild)}\n"
            f"Datenbank erreichbar: {db_ok}\n"
            f"Mitglieder im Cache: {len(guild.members) if guild else 0}"
        )
    return "Befehl kann hier nicht direkt ausgeführt werden."


@app.get("/admin/live-console", response_class=HTMLResponse)
async def admin_live_console(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    _ensure_console_log_handler()
    logs = _console_logs(80)
    rows = "".join(
        '<div class="border-b border-slate-800/70 py-1.5">'
        f'<span class="text-slate-500">{esc(item["time"])}</span> '
        f'<span class="text-indigo-300">{esc(item["level"])}</span> '
        f'<span class="text-slate-400">{esc(item["logger"])}</span> '
        f'<span>{esc(item["message"])}</span></div>'
        for item in logs
    ) or '<div class="text-slate-400 p-4">Noch keine Python-Logmeldungen seit Aktivierung der Konsole.</div>'
    body = f"""
    <div class="max-w-6xl mx-auto space-y-6">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div><div class="text-xs uppercase tracking-widest text-indigo-400 font-bold">Administrator · Systemzugriff</div>
        <h1 class="text-3xl font-black mt-2">🖥️ Live-Konsole</h1>
        <p class="text-slate-500 mt-2">Live-Anwendungslogs und sichere Diagnosebefehle für Pulse.</p></div>
        <span class="px-3 py-2 rounded-xl bg-emerald-500/10 text-emerald-400 text-sm">● Admin-Zugriff aktiv</span>
      </div>
      <div class="grid md:grid-cols-3 gap-3">
        <div class="rounded-2xl border border-slate-200 dark:border-slate-800 p-4"><div class="text-sm text-slate-500">Version</div><div class="text-xl font-bold mt-1">{esc(PULSE_VERSION)}</div></div>
        <div class="rounded-2xl border border-slate-200 dark:border-slate-800 p-4"><div class="text-sm text-slate-500">Logpuffer</div><div class="text-xl font-bold mt-1">{len(logs)} / {_CONSOLE_LOG_LIMIT}</div></div>
        <div class="rounded-2xl border border-slate-200 dark:border-slate-800 p-4"><div class="text-sm text-slate-500">Befehle</div><div class="text-xl font-bold mt-1">6 freigegebene</div></div>
      </div>
      <div class="rounded-2xl border border-slate-200 dark:border-slate-800 p-5 space-y-4">
        <h2 class="text-lg font-bold">Befehl ausführen</h2>
        <p class="text-sm text-slate-500">Erlaubt: <code>help</code>, <code>status</code>, <code>health</code>, <code>version</code>, <code>logs</code>, <code>sync</code>. Keine beliebigen Linux-/Shell-Befehle.</p>
        <form method="post" action="/admin/live-console/command" class="flex flex-col sm:flex-row gap-3">
          <input name="command" maxlength="40" required autocomplete="off" placeholder="z. B. status"
            class="flex-1 rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 px-4 py-3 font-mono">
          <button class="rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold px-5 py-3">Ausführen</button>
        </form>
        <div class="text-xs text-slate-500">Jeder Befehl wird protokolliert. Synchronisieren ist auf höchstens einmal je 10 Minuten begrenzt.</div>
      </div>
      <div class="rounded-2xl border border-slate-200 dark:border-slate-800 overflow-hidden">
        <div class="flex flex-wrap items-center justify-between gap-3 px-5 py-4 border-b border-slate-200 dark:border-slate-800">
          <h2 class="font-bold">Anwendungslogs</h2>
          <div class="flex gap-2"><button type="button" onclick="refreshConsole()" class="rounded-lg border border-slate-300 dark:border-slate-700 px-3 py-2 text-sm">Jetzt aktualisieren</button>
          <label class="flex items-center gap-2 text-sm"><input id="autoRefresh" type="checkbox" checked> Auto-Refresh</label></div>
        </div>
        <div id="consoleLogs" class="bg-slate-950 text-slate-200 p-4 font-mono text-xs leading-5 max-h-[520px] overflow-auto">{rows}</div>
      </div>
      <div class="text-xs text-slate-500">Hinweis: Die Webkonsole zeigt Python-Anwendungslogs ab Aktivierung. Für vollständige Docker-Startlogs weiterhin die Serverkonsole verwenden.</div>
    </div>
    <script>
    async function refreshConsole() {{
      try {{
        const r = await fetch('/admin/live-console/logs', {{credentials:'same-origin',cache:'no-store'}});
        if (!r.ok) return;
        const data = await r.json();
        const root = document.getElementById('consoleLogs');
        root.replaceChildren();
        for (const item of data.logs || []) {{
          const row = document.createElement('div');
          row.className = 'border-b border-slate-800/70 py-1.5';
          for (const [value, cls] of [[item.time,'text-slate-500'],[item.level,'text-indigo-300'],[item.logger,'text-slate-400'],[item.message,'']]) {{
            const span = document.createElement('span'); span.className = cls; span.textContent = value || '';
            row.appendChild(span); row.appendChild(document.createTextNode(' '));
          }}
          root.appendChild(row);
        }}
        root.scrollTop = root.scrollHeight;
      }} catch (_) {{}}
    }}
    setInterval(() => {{ if (document.getElementById('autoRefresh')?.checked) refreshConsole(); }}, 3000);
    </script>
    """
    return HTMLResponse(render_page("Live-Konsole", ctx, "live-console", body))


@app.get("/admin/live-console/logs")
async def admin_live_console_logs(request: Request, user_session: str = Cookie(None)):
    auth(request, user_session, perm=None, admin=True)
    return JSONResponse({"logs": _console_logs(150), "limit": _CONSOLE_LOG_LIMIT})


@app.post("/admin/live-console/command", response_class=HTMLResponse)
async def admin_live_console_command(
    request: Request, command: str = Form(...), user_session: str = Cookie(None)
):
    ctx = auth(request, user_session, perm=None, admin=True)
    normalized = str(command or "").strip().lower()
    if len(normalized) > 40 or normalized not in _CONSOLE_COMMANDS:
        log_audit(ctx.user.get("global_name", "Administrator"), ctx.user["id"],
                  "Live-Konsole: abgelehnt", f"Nicht erlaubter Befehl: {normalized[:40]!r}")
        raise HTTPException(status_code=400, detail="Befehl nicht erlaubt. Nutze help für die Befehlsliste.")
    if rate_limited(f"live-console:{ctx.user['id']}", limit=30, window=60):
        raise HTTPException(status_code=429, detail="Zu viele Konsolenbefehle. Bitte kurz warten.")
    result = ""
    ok = True
    if normalized == "logs":
        result = "\n".join(
            f'[{item["time"]}] {item["level"]} {item["logger"]}: {item["message"]}'
            for item in _console_logs(50)
        ) or "Noch keine Anwendungslogs vorhanden."
    elif normalized == "sync":
        if rate_limited(f"live-console-sync:{ctx.user['id']}", limit=1, window=600):
            result, ok = "Synchronisierung wurde aus Sicherheitsgründen auf einmal je 10 Minuten begrenzt.", False
        else:
            bot = getattr(request.app.state, "bot", None)
            if not bot or not bot.is_ready():
                result, ok = "Bot ist nicht bereit; Synchronisierung nicht ausgeführt.", False
            else:
                try:
                    synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
                    result = f"Discord-Befehle synchronisiert: {len(synced)}"
                except Exception as exc:
                    pulse_logging.report_error("live-console", f"Discord-Befehle synchronisieren: {type(exc).__name__}: {str(exc)[:240]}")
                    result, ok = f"Synchronisierung fehlgeschlagen ({type(exc).__name__}). Details: Befehl „logs“.", False
    else:
        result = _console_result(normalized, ctx, getattr(request.app.state, "bot", None))
    log_audit(ctx.user.get("global_name", "Administrator"), ctx.user["id"],
              "Live-Konsole: " + normalized, result[:1000])
    escaped = esc(result)
    status_class = "text-emerald-500" if ok else "text-amber-500"
    body = f"""
    <div class="max-w-5xl mx-auto space-y-5">
      <a href="/admin/live-console" class="text-indigo-500 hover:underline">← Zurück zur Live-Konsole</a>
      <h1 class="text-3xl font-black">Konsolenausgabe</h1>
      <div class="rounded-2xl border border-slate-200 dark:border-slate-800 p-5">
        <div class="text-sm text-slate-500 mb-2">Befehl: <code>{esc(normalized)}</code></div>
        <pre class="whitespace-pre-wrap break-words bg-slate-950 text-slate-100 rounded-xl p-4 overflow-auto">{escaped}</pre>
        <div class="mt-3 font-semibold {status_class}">{"Erfolgreich" if ok else "Nicht erfolgreich"}</div>
      </div>
      <a href="/admin/live-console" class="inline-flex rounded-xl bg-indigo-600 text-white px-4 py-3 font-semibold">Zur Konsole</a>
    </div>
    """
    return HTMLResponse(render_page("Konsolenausgabe", ctx, "live-console", body))



# =============================================================
# HEALTH CHECK
# =============================================================
def _system_alert(key: str, message: str) -> None:
    """O-07: throttled alert in PULSE_SYSTEM_ALERT_CHANNEL_ID (posted by the automation loop)."""
    try:
        import pulse_alerts
        pulse_alerts.raise_alert(key, message)
    except Exception:
        pass


# A-01/A-02: GET "/healthz" lives in pulse_health_api.py; same URL, same position, same status contract.
import pulse_health_api  # noqa: E402

pulse_health_api.register(app)
pulse_public_site.register(app)
healthz = pulse_health_api.healthz


# =============================================================
# ROUTEN: LOGIN, LOGOUT & OAUTH CALLBACK
# =============================================================
# U-02: every team member chooses their own start page (default: Mein Pulse).
START_PAGES = {"/me": "Mein Pulse", "/dashboard": "Übersicht", "/cockpit": "Leitungs-Cockpit",
               "/ultimate": "Command Center", "/suite": "Operations Suite", "/tickets": "Tickets"}
DEFAULT_START_PAGE = "/me"


def start_page_for(user_id) -> str:
    try:
        chosen = pulse_db.get_setting(f"start_page:{user_id}", DEFAULT_START_PAGE)
    except Exception:
        chosen = DEFAULT_START_PAGE
    return chosen if chosen in START_PAGES else DEFAULT_START_PAGE


@app.get("/static/pulse.css")
async def static_pulse_css():
    """U-03: the prebuilt Tailwind CSS; the ?v=<hash> in the link changes with every rebuild."""
    if not STATIC_CSS.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(STATIC_CSS, media_type="text/css", headers={"Cache-Control": "public, max-age=604800, immutable"})


