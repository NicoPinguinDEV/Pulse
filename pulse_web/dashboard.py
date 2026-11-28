"""Public entry, OAuth callbacks, dashboard pages, shift actions and activity helpers."""

from pulse_web._compat import bind_legacy_namespace as _bind_legacy_namespace
_bind_legacy_namespace(globals())
del _bind_legacy_namespace

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, user_session: str = Cookie(None)):
    """Show the public homepage only after explicit release; otherwise enter the private TeamOS."""
    if not pulse_public_site.public_access_enabled():
        session = dashboard_session(user_session)
        if session:
            return RedirectResponse(url=start_page_for(session.get("id")), status_code=303)
        response = RedirectResponse(url="/team-login", status_code=303)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        if user_session:
            response.delete_cookie(key="user_session")
        return response

    response = pulse_public_site.home_response(request)
    if user_session and not dashboard_session(user_session):
        response.delete_cookie(key="user_session")
    return response


@app.get("/team-login", response_class=HTMLResponse)
async def team_login_page(user_session: str = Cookie(None)):
    session = dashboard_session(user_session)
    if session:
        return RedirectResponse(url=start_page_for(session.get("id")), status_code=303)
    apply_cta = (
        '<div class="mt-6 pt-6 border-t border-slate-100 dark:border-slate-800">'
        '<a href="/apply" class="pulse-login-link text-xs text-indigo-600 dark:text-indigo-400 hover:underline font-medium">Du möchtest dich ins Team bewerben? Hier klicken!</a></div>'
        if pulse_public_site.public_access_enabled() else ""
    )
    page = f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>{get_head_html("Bochum RP Panel Login")}</head>
    <body class="bg-slate-50 dark:bg-[#0b0e14] text-slate-900 dark:text-white min-h-screen flex items-center justify-center p-4 font-sans transition-colors duration-200">
        <div class="absolute top-6 right-6">
            <button onclick="toggleTheme()" class="px-3 py-2 rounded-xl bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 text-xs shadow-sm font-medium">
                <span class="dark:hidden">🌙 Dark</span><span class="hidden dark:inline">☀️ Light</span>
            </button>
        </div>
        <div class="{CARD} pulse-login-card p-8 shadow-xl w-full max-w-md text-center">
            <div class="flex justify-center mb-4">
                <div class="pulse-login-mark" aria-hidden="true">P</div>
            </div>
            <div class="text-[10px] font-bold uppercase tracking-widest text-slate-500 dark:text-slate-400 mb-2">Pulse TeamOS</div>
            <h1 class="text-2xl font-bold tracking-tight mb-2">Team Management Panel</h1>
            <p class="text-slate-500 dark:text-slate-400 text-xs mb-5">Melde dich mit deinem Discord-Account an, um Zugriff zu erhalten.</p>
            <p class="text-slate-500 dark:text-slate-400 text-[11px] leading-relaxed mb-4">Für Kontosicherheit protokolliert Pulse erfolgreiche Dashboard-Anmeldungen mit IP-Adresse, Zeitpunkt und Browserkennung. Nur Administratoren sehen die Einträge; Daten, die älter als 30 Tage sind, werden bei der nächsten Anmeldung oder Datenschutzprüfung bereinigt. Details: Verwaltung → Datenschutz.</p>
            <a href="/login" class="pulse-login-btn inline-flex items-center justify-center gap-3 w-full bg-[#5865F2] hover:bg-[#4752C4] text-white font-semibold py-3 px-4 rounded-xl transition shadow-lg shadow-[#5865F2]/20 text-sm">
                Mit Discord anmelden
            </a>
            {apply_cta}
        </div>
    </body>
    </html>
    """
    response = HTMLResponse(page)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "no-store"
    if user_session:
        response.delete_cookie(key="user_session")  # ended or revoked session: clear it, show the login
    return response


@app.get("/login")
async def login(request: Request):
    if rate_limited(request_rate_key(request, "oauth-login"), limit=15, window=600):
        return rate_limit_response("Zu viele Login-Versuche. Bitte in einer Minute erneut versuchen.")
    state = secrets.token_urlsafe(24)  # CSRF-Schutz für den OAuth-Ablauf
    redirect_uri = oauth_redirect_uri(request)
    url = (f"https://discord.com/oauth2/authorize?client_id={CLIENT_ID}"
           f"&redirect_uri={quote(redirect_uri, safe='')}&response_type=code&scope=identify&state={state}")
    response = RedirectResponse(url=url, status_code=303)
    response.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="lax",
                        secure=oauth_cookie_secure(request))
    return response


@app.get("/logout", response_class=HTMLResponse)
async def logout_confirm():
    """U-13: GET no longer logs out (a foreign page could otherwise do it via an image link);
    existing /logout links show a one-click confirmation that posts the form."""
    form = ('<form method="post" action="/logout" class="inline"><button class="px-4 py-2 rounded-xl bg-indigo-600 '
            'text-white font-semibold" autofocus>Jetzt abmelden</button></form>'
            '<a href="/me" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Abbrechen</a>')
    return HTMLResponse(simple_page("↤", "Abmelden", "Möchtest du dich vom Pulse-Dashboard abmelden?", form))


@app.post("/logout")
async def logout(user_session: str = Cookie(None)):
    data = verify_session_token(user_session, TOKEN_DASHBOARD) if user_session else None
    if data:
        try:
            import pulse_sessions  # U-14: the server-side session ends too
            if data.get("sid"):
                pulse_sessions.revoke(data["id"], data["sid"])
            else:  # SEC-03: a copied cookie without sid must not outlive the logout either
                pulse_sessions.revoke_unbound(data["id"])
        except Exception as exc:  # P-10
            pulse_logging.report_error("sessions", f"Sitzung beim Logout nicht widerrufen: {type(exc).__name__}")
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="user_session")
    response.delete_cookie(key="apply_session")  # AUTH-5: signing out of the dashboard also ends a shared-device application session
    return response


@app.post("/apply/logout")
async def apply_logout():
    """AUTH-5: applicants can end their (stateless, max. 12 h) application session on a shared device."""
    response = RedirectResponse(url="/apply", status_code=303)
    response.delete_cookie(key="apply_session")
    return response


@app.get("/callback")
async def callback(request: Request, code: str = None, state: str = None, error: str = None,
                   oauth_state: str = Cookie(None), apply_oauth_state: str = Cookie(None),
                   recovery_oauth_state: str = Cookie(None)):
    if rate_limited(request_rate_key(request, "oauth-callback"), limit=30, window=600):
        return rate_limit_response("Zu viele Anmeldeversuche. Bitte in einer Minute erneut versuchen.")
    application_flow = bool(
        state and apply_oauth_state and hmac.compare_digest(state, apply_oauth_state)
    )
    recovery_flow = bool(
        state and recovery_oauth_state and hmac.compare_digest(state, recovery_oauth_state)
    )
    retry = (
        '<a href="/apply" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zur Bewerbung</a>'
        if application_flow else
        '<a href="/" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Erneut versuchen</a>'
    )
    if error or not code:
        title = ("Bewerbungs-Login fehlgeschlagen" if application_flow else
                 "Backup-Freigabe abgebrochen" if recovery_oauth_state else "Login abgebrochen")
        response = HTMLResponse(simple_page("❌", title, "Du hast die Anmeldung nicht bestätigt. Bitte erneut versuchen.", retry))
        if recovery_oauth_state:
            response.delete_cookie("recovery_oauth_state")
        return response
    state_valid = application_flow or recovery_flow or bool(
        state and oauth_state and hmac.compare_digest(state, oauth_state)
    )
    if not state_valid:
        title = ("Bewerbungs-Login fehlgeschlagen" if apply_oauth_state else
                 "Backup-Freigabe fehlgeschlagen" if recovery_oauth_state else "Login fehlgeschlagen")
        response = HTMLResponse(simple_page("❌", title, "Ungültiger Sicherheits-Token. Bitte erneut anmelden.", retry), status_code=400)
        if recovery_oauth_state:
            response.delete_cookie("recovery_oauth_state")
        return response

    if recovery_flow:
        import pulse_recovery
        recovery_ok, recovery_message, _ = await pulse_recovery.complete_consent(
            code, oauth_redirect_uri(request), getattr(request.app.state, "bot", None)
        )
        recovery_action = '<a href="/" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">Zurück zu Pulse</a>'
        response = HTMLResponse(simple_page(
            "🛡️" if recovery_ok else "❌",
            "Backup-Zugang eingerichtet" if recovery_ok else "Backup-Zugang nicht eingerichtet",
            recovery_message,
            recovery_action,
        ), status_code=200 if recovery_ok else 400)
        response.delete_cookie("recovery_oauth_state")
        return response

    redirect_uri = oauth_redirect_uri(request, application=application_flow)
    user_data, problem = await discord_oauth_identify(code, redirect_uri)  # P-12
    if user_data is None:
        title = "Bewerbungs-Login fehlgeschlagen" if application_flow else "Login fehlgeschlagen"
        return HTMLResponse(simple_page("❌", title, problem, retry), status_code=502)

    if application_flow:
        application_session = {
            "id": str(user_data.get("id")),
            "username": user_data.get("username"),
            "global_name": user_data.get("global_name") or user_data.get("username"),
        }
        response = RedirectResponse(url="/apply", status_code=303)
        response.headers["location"] = apply_return_path(request)
        set_apply_session_cookie(response, request, application_session)
        response.delete_cookie("apply_oauth_state")
        response.delete_cookie(APPLY_RETURN_COOKIE)
        return response

    # Nur Mitglieder mit Dashboard-Recht dürfen überhaupt eine Session bekommen
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    if not guild:
        return HTMLResponse(simple_page("⏳", "Bot startet noch", "Bitte in ein paar Sekunden erneut versuchen.", retry), status_code=503)
    perms, _ = compute_perms(guild, user_data.get("id"), load_config())
    if not perms["can_view_dashboard"]:
        return HTMLResponse(simple_page("🔒", "Kein Zugriff", "Du bist nicht im Team oder deine Rolle darf das Dashboard nicht sehen.",
                                        '<a href="/" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Zurück</a>'), status_code=403)

    # Privacy-aware security telemetry: record successful dashboard logins only, not page views.
    # request.client.host respects only the trusted reverse-proxy configuration in main.py.
    try:
        import pulse_ip_audit
        peer = request.client.host if request.client else ""
        pulse_ip_audit.record_login(
            str(user_data.get("id")), str(user_data.get("global_name") or user_data.get("username") or ""),
            peer, request.headers.get("user-agent", ""),
        )
    except Exception as exc:
        pulse_logging.report_error("ip-audit", f"Dashboard-Anmeldeprotokoll konnte nicht gespeichert werden ({type(exc).__name__}).")

    session = {
        "id": str(user_data.get("id")),
        "username": user_data.get("username"),
        "global_name": user_data.get("global_name") or user_data.get("username"),
        "avatar": user_data.get("avatar"),
        "exp": int(time.time() + SESSION_DAYS * 86400),
    }
    try:
        import pulse_sessions  # U-14: server-side session for the session list
        session["sid"] = pulse_sessions.create(session["id"], request.headers.get("user-agent", ""))
    except Exception as exc:
        # SEC-03: without a server-side session the cookie could be neither ended on logout nor by the
        # idle timeout – refuse the login (fail closed) instead of issuing an unrevocable 7-day cookie.
        pulse_logging.report_error("sessions", f"Sitzung konnte nicht gespeichert werden: {type(exc).__name__}")
        return HTMLResponse(simple_page("⏳", "Anmeldung gerade nicht möglich",
                                        "Die Sitzung konnte nicht gespeichert werden. Bitte gleich erneut versuchen.", retry),
                            status_code=503)
    response = RedirectResponse(url=start_page_for(session["id"]), status_code=303)  # U-02
    response.set_cookie(
        "user_session",
        sign_payload(session, typ=TOKEN_DASHBOARD),
        httponly=True,
        samesite="lax",
        secure=oauth_cookie_secure(request),
        max_age=SESSION_DAYS * 86400,
    )
    response.delete_cookie("oauth_state")
    return response

# =============================================================
# GEMEINSAME KLEINTEILE
# =============================================================
def display_of(guild, uid) -> str:
    try:
        m = guild.get_member(int(uid))
    except Exception:
        m = None
    return m.display_name if m else f"User {uid}"


def role_hex(role) -> str:
    return f"#{role.color.value:06x}" if role.color.value else "#6366f1"


def stat_card(icon, label, value, tint):
    return f"""
    <div class="{CARD} p-4 flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl bg-{tint}-500/10 text-{tint}-500 flex items-center justify-center font-bold text-lg">{icon}</div>
        <div>
            <div class="text-xs text-slate-400 font-medium">{label}</div>
            <div class="text-xl font-bold text-slate-900 dark:text-white">{value}</div>
        </div>
    </div>"""


LOG_TYPE_STYLE = {
    "Ban": "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/30",
    "Kick": "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30",
    "Warn": "bg-yellow-500/10 text-yellow-600 dark:text-yellow-400 border-yellow-500/30",
    "Notiz": "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/30",
    "Ban BOLO": "bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400 border-fuchsia-500/30",
}

DASHBOARD_HEAD = """
<style>
    .type-chip { cursor:pointer; transition:.15s; }
    .type-chip.active { background:#4f46e5 !important; color:#fff !important; border-color:#4f46e5 !important; }
    .pulse-glass{background:linear-gradient(135deg,rgba(255,255,255,.82),rgba(248,250,252,.68));backdrop-filter:blur(18px);border:1px solid rgba(148,163,184,.20)}
    .dark .pulse-glass{background:linear-gradient(135deg,rgba(20,24,36,.88),rgba(11,14,20,.78));border-color:rgba(148,163,184,.12)}
    .pulse-hero{background:radial-gradient(700px 260px at 0% 0%,rgba(99,102,241,.22),transparent 60%),radial-gradient(500px 240px at 100% 100%,rgba(6,182,212,.14),transparent 60%),linear-gradient(135deg,rgba(99,102,241,.08),rgba(6,182,212,.05))}
    .dark .pulse-hero{background:radial-gradient(700px 260px at 0% 0%,rgba(99,102,241,.28),transparent 60%),radial-gradient(500px 240px at 100% 100%,rgba(6,182,212,.16),transparent 60%),linear-gradient(135deg,rgba(15,23,42,.92),rgba(8,15,27,.96))}
    .pulse-hover{transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease}
    .pulse-hover:hover{transform:translateY(-2px);box-shadow:0 16px 40px rgba(15,23,42,.10)}
    .dark .pulse-hover:hover{box-shadow:0 18px 44px rgba(0,0,0,.28)}
    .pulse-ring{box-shadow:0 0 0 1px rgba(99,102,241,.10),0 12px 32px rgba(99,102,241,.10)}
    .pulse-dot{box-shadow:0 0 0 4px rgba(16,185,129,.08)}
    @keyframes pulseFloat{from{transform:translateY(0)}to{transform:translateY(-3px)}}
    .pulse-float{animation:pulseFloat 2.8s ease-in-out infinite alternate}
    @media(max-width:800px){.pulse-hide-mobile{display:none}.pulse-grid-mobile{grid-template-columns:1fr}}
</style>
<script>
    document.addEventListener("DOMContentLoaded", function () {
        const el = document.getElementById("liveShiftTimer");
        if (!el) return;
        const base = parseInt(el.dataset.base || "0", 10);
        const running = el.dataset.running === "1";
        const t0 = Date.now();
        function tick() {
            const s = base + (running ? Math.floor((Date.now() - t0) / 1000) : 0);
            el.innerText = Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m " + (s % 60) + "s";
        }
        tick();
        if (running) setInterval(tick, 1000);
    });

    let activeType = "all";
    function setTypeFilter(t) {
        activeType = t;
        document.querySelectorAll(".type-chip").forEach(c => c.classList.toggle("active", c.dataset.type === t));
        filterLogs();
    }
    function filterLogs() {
        const q = (document.getElementById("logSearch").value || "").toLowerCase();
        document.querySelectorAll(".log-card").forEach(card => {
            const okType = activeType === "all" || card.dataset.type === activeType;
            const okText = (card.dataset.search || "").includes(q);
            card.style.display = (okType && okText) ? "" : "none";
        });
    }

    // Pulse Command Palette (Ctrl/Cmd+K)
    document.addEventListener("keydown", function(e){
        if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();const q=prompt("Pulse Suche – Name, Ticket, Aufgabe, Bewerbung oder Wiki:");if(q) window.location.href="/search?q="+encodeURIComponent(q);}
    });
    function pulseClock(){
        const el=document.getElementById("pulseLiveClock");
        if(!el) return;
        const d=new Date();
        el.textContent=d.toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit",second:"2-digit"});
    }
    pulseClock(); setInterval(pulseClock,1000);
    // PWA
    if("serviceWorker" in navigator){ navigator.serviceWorker.register("/sw.js").catch(()=>{}); }

    let robloxSearchTimeout = null;
    let robloxSearchRequestId = 0;
    let lookupTimeout = null;
    let lookupRequestId = 0;
    let resolvedRobloxUsername = "";

    function normalizedRobloxInput(value) {
        return String(value || "").trim().replace(/^@+/, "").toLowerCase();
    }

    function searchRobloxUsers(val) {
        clearTimeout(robloxSearchTimeout);
        const input = String(val || "").trim().replace(/^@+/, "");
        const list = document.getElementById("robloxUserSuggestions");
        const panel = document.getElementById("robloxSuggestionPanel");
        const requestId = ++robloxSearchRequestId;
        if (!list || input.length < 2) {
            if (list) list.innerHTML = "";
            if (panel) { panel.replaceChildren(); panel.classList.add("hidden"); }
            return;
        }
        robloxSearchTimeout = setTimeout(() => {
            fetch("/api/roblox-search?query=" + encodeURIComponent(input), {cache: "no-store"})
                .then(r => r.json())
                .then(data => {
                    if (requestId !== robloxSearchRequestId) return;
                    const usernameInput = document.getElementById("robloxUsernameInput");
                    if (!usernameInput || normalizedRobloxInput(usernameInput.value) !== input.toLowerCase()) return;
                    if (!data.success) {
                        list.innerHTML = "";
                        if (panel) { panel.replaceChildren(); panel.classList.add("hidden"); }
                        return;
                    }
                    const users = Array.isArray(data.users) ? data.users : [];
                    list.innerHTML = users.map(u => '<option value="' + escapeHtml(u.name) + '">' + escapeHtml(u.displayName || u.name) + " · ID " + escapeHtml(u.id) + "</option>").join("");
                    if (panel) {
                        panel.replaceChildren();
                        users.forEach((u, index) => {
                            const username = String(u.name || "").trim();
                            if (!username) return;
                            const button = document.createElement("button");
                            button.type = "button";
                            button.className = "flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left hover:bg-indigo-50 dark:hover:bg-indigo-950/40 border-b last:border-b-0 border-slate-100 dark:border-slate-800 focus:outline-none focus:bg-indigo-50 dark:focus:bg-indigo-950/40";
                            button.setAttribute("role", "option");
                            const left = document.createElement("span");
                            left.className = "min-w-0 flex flex-col";
                            const name = document.createElement("span");
                            name.className = "font-semibold text-xs text-slate-900 dark:text-white truncate";
                            name.textContent = (index === 0 ? "⭐ " : "") + username;
                            const display = document.createElement("span");
                            display.className = "text-[10px] text-slate-500 dark:text-slate-400 truncate";
                            display.textContent = String(u.displayName || username);
                            left.append(name, display);
                            const right = document.createElement("span");
                            right.className = "shrink-0 text-[10px] font-mono text-indigo-600 dark:text-indigo-300";
                            right.textContent = (index === 0 ? "Bester Treffer · " : "") + "ID " + String(u.id || "unbekannt");
                            button.append(left, right);
                            button.addEventListener("click", () => selectRobloxSuggestion(u));
                            panel.appendChild(button);
                        });
                        panel.classList.toggle("hidden", panel.childElementCount === 0);
                    }
                    const exact = users.find(u => String(u.name || "").toLowerCase() === input.toLowerCase());
                    const idInput = document.getElementById("robloxIdInput");
                    if (exact && idInput && /^\\d+$/.test(String(exact.id || ""))) {
                        idInput.value = String(exact.id);
                        resolvedRobloxUsername = String(exact.name).toLowerCase();
                    }
                })
                .catch(() => {
                    if (requestId !== robloxSearchRequestId) return;
                    list.innerHTML = "";
                    if (panel) { panel.replaceChildren(); panel.classList.add("hidden"); }
                });
        }, 220);
    }

    function selectRobloxSuggestion(user) {
        const username = String(user && user.name || "").trim();
        if (!username) return;
        const usernameInput = document.getElementById("robloxUsernameInput");
        const idInput = document.getElementById("robloxIdInput");
        const panel = document.getElementById("robloxSuggestionPanel");
        clearTimeout(robloxSearchTimeout);
        robloxSearchRequestId += 1;
        if (usernameInput) usernameInput.value = username;
        if (idInput) idInput.value = /^\\d+$/.test(String(user.id || "")) ? String(user.id) : "";
        resolvedRobloxUsername = username.toLowerCase();
        if (panel) { panel.replaceChildren(); panel.classList.add("hidden"); }
        lookupRobloxUser(username, true);
    }

    function renderRobloxPreview(data) {
        const infoDiv = document.getElementById("robloxUserPreview");
        if (!infoDiv) return;
        const prev = Number(data.previous_total || 0) > 0
            ? '<div class="text-[10px] text-amber-600 dark:text-amber-400 font-semibold mt-0.5">⚠️ ' + Number(data.previous_total) + ' frühere Logs (' + escapeHtml(data.previous_summary || "") + ')</div>'
            : '<div class="text-[10px] text-emerald-600 dark:text-emerald-400 mt-0.5">✔ Keine früheren Logs</div>';
        infoDiv.innerHTML =
            '<div class="flex items-center gap-3 p-2.5 bg-indigo-50/50 dark:bg-indigo-950/20 border border-indigo-200 dark:border-indigo-800/50 rounded-xl">' +
                (data.avatarUrl ? '<img src="' + escapeHtml(data.avatarUrl) + '" class="w-9 h-9 rounded-full border border-indigo-300 dark:border-indigo-700">' : '') +
                '<div class="truncate">' +
                    '<div class="font-bold text-slate-900 dark:text-white text-xs">' + escapeHtml(data.displayName || data.username || data.name || "") +
                    ' <span class="text-slate-400 text-[10px]">(@' + escapeHtml(data.username || data.name || "") + ')</span></div>' +
                    '<div class="text-[10px] text-indigo-600 dark:text-indigo-400 font-mono font-semibold">Roblox ID: ' + escapeHtml(data.id || "") + '</div>' +
                    (data.previous_total === undefined ? '' : prev) +
                '</div>' +
            '</div>';
        infoDiv.classList.remove("hidden");
    }

    function lookupRobloxUser(val, fromSuggestion) {
        val = String(val || "").trim().replace(/^@+/, "");
        const usernameInput = document.getElementById("robloxUsernameInput");
        const idInput = document.getElementById("robloxIdInput");
        const infoDiv = document.getElementById("robloxUserPreview");
        if (!fromSuggestion) {
            searchRobloxUsers(val);
            if (resolvedRobloxUsername && normalizedRobloxInput(val) !== resolvedRobloxUsername) {
                resolvedRobloxUsername = "";
                if (idInput) idInput.value = "";
            }
            if (infoDiv) infoDiv.classList.add("hidden");
        } else {
            clearTimeout(robloxSearchTimeout);
            robloxSearchRequestId += 1;
        }
        clearTimeout(lookupTimeout);
        const requestId = ++lookupRequestId;
        if (!val || val.length < 3) {
            if (infoDiv) infoDiv.classList.add("hidden");
            return;
        }
        lookupTimeout = setTimeout(() => {
            fetch("/api/roblox-user?username=" + encodeURIComponent(val), {cache: "no-store"})
                .then(r => r.json())
                .then(data => {
                    if (requestId !== lookupRequestId) return;
                    if (!usernameInput || normalizedRobloxInput(usernameInput.value) !== val.toLowerCase()) return;
                    if (data.success) {
                        if (idInput) idInput.value = String(data.id || "");
                        resolvedRobloxUsername = String(data.username || val).toLowerCase();
                        renderRobloxPreview(data);
                    } else if (fromSuggestion) {
                        renderRobloxPreview({name: val, username: val, id: idInput ? idInput.value : "", displayName: val});
                    } else {
                        const panel = document.getElementById("robloxSuggestionPanel");
                        if (panel && !panel.classList.contains("hidden")) return;
                        if (infoDiv) {
                            infoDiv.innerHTML = '<span class="text-rose-500 text-[11px] block px-1">⚠️ ' + escapeHtml(data.message || "Roblox-Nutzer nicht gefunden") + '</span>';
                            infoDiv.classList.remove("hidden");
                        }
                    }
                })
                .catch(() => {
                    if (requestId !== lookupRequestId) return;
                    if (fromSuggestion && idInput && idInput.value) {
                        renderRobloxPreview({name: val, username: val, id: idInput.value, displayName: val});
                    } else if (infoDiv) {
                        infoDiv.classList.add("hidden");
                    }
                });
        }, fromSuggestion ? 80 : 380);
    }
</script>
"""


# =============================================================
# ROUTE 1: HAUPT-DASHBOARD
# =============================================================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_main(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    mod_id = ctx.user["id"]

    shifts_db = load_shifts()
    melonly_repository = get_melonly_repository()
    logs_total = melonly_repository.count()
    apps_db = load_json(APPS_FILE, {})
    active_shifts = shifts_db["active_shifts"]
    loas = get_loas()
    team_role_ids = ctx.config.get("team_role_ids", [])

    active_staff_count = len([s for s in active_shifts.values() if s.get("status") in ("online", "break")])
    loa_count = len([l for l in loas.values() if l["active"]])
    pending_apps_count = len([a for a in apps_db.values() if a.get("status") in {"pending", "interview"}])

    current_shift = active_shifts.get(mod_id)
    shift_status = current_shift.get("status") if current_shift else "offline"
    elapsed = shift_elapsed(current_shift) if current_shift else 0
    status_label = {"online": "IM DIENST", "break": "PAUSE", "offline": "OFFLINE"}.get(shift_status, "OFFLINE")

    if shift_status == "offline":
        shift_buttons = '<button name="shift_action" value="start" class="col-span-2 bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 px-4 rounded-xl transition shadow-md shadow-emerald-900/10 text-xs">▶️ Schicht Starten</button>'
    else:
        middle = ('<button name="shift_action" value="break" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 font-semibold py-2.5 px-3 rounded-xl transition text-xs">⏸️ Pause</button>'
                  if shift_status == "online" else
                  '<button name="shift_action" value="resume" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 font-semibold py-2.5 px-3 rounded-xl transition text-xs">▶️ Fortsetzen</button>')
        shift_buttons = middle + '<button name="shift_action" value="end" class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 font-semibold py-2.5 px-3 rounded-xl transition text-xs">⏹️ Beenden</button>'

    # Wochen-Rangliste (inkl. laufender Schichten)
    board = []
    for member in guild.members:
        if any(r.id in team_role_ids for r in member.roles):
            secs = calculate_weekly_seconds(str(member.id), shifts_db["history"], active_shifts)
            if secs > 0:
                board.append((member.display_name, secs))
    board.sort(key=lambda x: x[1], reverse=True)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    leaderboard_html = "".join(f"""
        <div class="flex items-center justify-between bg-slate-50 dark:bg-[#0b0e14] px-3.5 py-2.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs">
            <div class="flex items-center gap-2"><span class="font-bold text-sm">{medals[i]}</span>
            <span class="text-slate-700 dark:text-slate-200 font-medium truncate max-w-[140px]">{esc(n)}</span></div>
            <span class="text-indigo-600 dark:text-indigo-400 font-mono font-bold">{s / 3600:.1f}h</span>
        </div>""" for i, (n, s) in enumerate(board[:3], 1))

    # Wer ist gerade im Dienst?
    duty_html = ""
    for uid, s in active_shifts.items():
        on_break = s.get("status") == "break"
        duty_html += f"""
        <div class="flex items-center justify-between text-xs bg-slate-50 dark:bg-[#0b0e14] px-3.5 py-2.5 rounded-xl border border-slate-200 dark:border-slate-800">
            <span class="flex items-center gap-2 font-medium truncate"><span class="w-2 h-2 rounded-full {'bg-amber-500' if on_break else 'bg-emerald-500 animate-pulse'}"></span>{esc(display_of(guild, uid))}</span>
            <span class="font-mono text-slate-500">{'☕ ' if on_break else ''}{fmt_duration(shift_elapsed(s))}</span>
        </div>"""

    # Logs / Melonly
    logs_html = ""
    edit_log_id = str(request.query_params.get("edit_log") or "")
    is_manager = ctx.perms["can_promote"] or ctx.perms["is_admin"]
    type_labels = {"Warn":"⚠️ Warn","Kick":"🚪 Kick","Ban":"🚫 Ban","Ban BOLO":"🚨 Ban BOLO","Notiz":"📝 Notiz"}

    # Search the complete archive for a player's history, not only the latest 50 records.
    player_history_query = str(request.query_params.get("player_history") or "").strip()[:80]
    normalized_query = player_history_query.lstrip("@").strip().casefold()

    player_history_matches = melonly_repository.player_history(player_history_query) if normalized_query else []
    history_active = bool(normalized_query)
    logs_to_render = (
        reversed(player_history_matches)
        if history_active else reversed(melonly_repository.latest(50))
    )
    history_query_param = f"player_history={quote(player_history_query, safe='')}&" if history_active else ""
    history_return_to = (
        f"/dashboard?player_history={quote(player_history_query, safe='')}#playerlog"
        if history_active else "/dashboard#playerlog"
    )

    history_players = sorted(
        {str(log.get("target_user") or "").strip().lstrip("@") for log in player_history_matches
         if str(log.get("target_user") or "").strip()},
        key=str.casefold,
    )
    if history_active:
        history_warns = sum(1 for log in player_history_matches if log.get("type") == "Warn")
        history_kicks = sum(1 for log in player_history_matches if log.get("type") == "Kick")
        history_bans = sum(1 for log in player_history_matches if log.get("type") in {"Ban", "Ban BOLO"})
        history_notes = sum(1 for log in player_history_matches if log.get("type") == "Notiz")
        matched_people = ", ".join("@" + name for name in history_players[:4]) or player_history_query
        if len(history_players) > 4:
            matched_people += f" (+{len(history_players) - 4} weitere)"
        player_history_summary = f"""
            <div class="{CARD} p-4 space-y-3">
                <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0">
                        <div class="text-sm font-bold text-slate-900 dark:text-white">🔎 Spieler-Verlauf</div>
                        <div class="text-xs text-slate-500 dark:text-slate-400 mt-1 break-words">{esc(matched_people)}</div>
                    </div>
                    <span class="shrink-0 rounded-full px-2.5 py-1 text-[11px] font-bold bg-indigo-500/10 text-indigo-600 dark:text-indigo-300">{len(player_history_matches)} Vorgänge</span>
                </div>
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div class="rounded-lg bg-slate-50 dark:bg-[#0b0e14] p-2"><div class="text-[10px] text-slate-500">Warns</div><div class="font-bold text-sm">{history_warns}</div></div>
                    <div class="rounded-lg bg-slate-50 dark:bg-[#0b0e14] p-2"><div class="text-[10px] text-slate-500">Kicks</div><div class="font-bold text-sm">{history_kicks}</div></div>
                    <div class="rounded-lg bg-slate-50 dark:bg-[#0b0e14] p-2"><div class="text-[10px] text-slate-500">Bans / BOLO</div><div class="font-bold text-sm">{history_bans}</div></div>
                    <div class="rounded-lg bg-slate-50 dark:bg-[#0b0e14] p-2"><div class="text-[10px] text-slate-500">Notizen</div><div class="font-bold text-sm">{history_notes}</div></div>
                </div>
                <p class="text-[11px] text-slate-500 dark:text-slate-400">Alle gespeicherten Vorgänge dieses Suchtreffers werden angezeigt – auch ältere Einträge außerhalb der normalen letzten 50.</p>
            </div>"""
    else:
        player_history_summary = ""
    empty_logs_html = (
        f'<div class="text-xs text-slate-500 dark:text-slate-400 bg-white dark:bg-[#141824] p-6 rounded-2xl border border-slate-200 dark:border-slate-800 text-center shadow-sm">Keine Vorgänge für „{esc(player_history_query)}“ gefunden. Bitte prüfe den Roblox-@Namen oder die ID.</div>'
        if history_active else
        "<div class='text-xs text-slate-400 italic bg-white dark:bg-[#141824] p-6 rounded-2xl border border-slate-200 dark:border-slate-800 text-center shadow-sm'>Noch keine Logs eingetragen.</div>"
    )

    for log in logs_to_render:
        ltype = log.get("type", "Log")
        badge = LOG_TYPE_STYLE.get(ltype, "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300")
        search = f"{log.get('target_user','')} {log.get('roblox_id','')} {log.get('moderator','')} {ltype} {log.get('reason','')}".lower()
        can_edit = can_manage_melonly_entry(ctx, log)
        edit_button = (
            f'<a href="/dashboard?{history_query_param}edit_log={quote(str(log.get("id") or ""))}#playerlog" class="text-indigo-500 hover:underline font-semibold">✏️ Bearbeiten</a>'
            if can_edit else ""
        )
        delete_form = f"""
                <form action="/log/delete" method="post" onsubmit="return confirm('Diesen Log-Eintrag wirklich löschen?');">
                    <input type="hidden" name="log_id" value="{esc(log.get('id'))}">
                    <input type="hidden" name="return_to" value="{esc(history_return_to)}">
                    <button class="text-rose-500 hover:underline font-semibold">🗑️ Löschen</button>
                </form>""" if can_edit else ""
        edit_form = ""
        if edit_log_id == str(log.get("id")) and can_edit:
            edit_options = "".join(
                f'<option value="{esc(key)}" {"selected" if ltype == key else ""}>{esc(label)}</option>'
                for key, label in type_labels.items()
            )
            edit_form = f"""
            <form action="/log/edit" method="post" class="mt-3 pt-3 border-t border-slate-100 dark:border-slate-800/60 space-y-2.5">
                <input type="hidden" name="log_id" value="{esc(log.get('id'))}">
                <input type="hidden" name="return_to" value="{esc(history_return_to)}">
                <input class="{INPUT}" name="target_user" maxlength="50" value="{esc(log.get('target_user'))}" required>
                <input class="{INPUT}" name="roblox_id" maxlength="15" value="{esc(log.get('roblox_id','N/A'))}" placeholder="Roblox ID">
                <select class="{INPUT}" name="log_type">{edit_options}</select>
                <textarea class="{INPUT} h-24" name="reason" maxlength="1000" required>{esc(log.get('reason'))}</textarea>
                <div class="flex gap-2">
                    <button class="{BTN} px-4 py-2 text-xs">💾 Änderungen speichern</button>
                    <a href="/dashboard#playerlog" class="px-4 py-2 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold">Abbrechen</a>
                </div>
            </form>"""
        logs_html += f"""
        <div class="log-card {CARD} p-4 space-y-2 hover:shadow transition-all" data-type="{esc(ltype)}" data-search="{esc(search)}">
            <div class="flex items-center justify-between border-b border-slate-100 dark:border-slate-800/60 pb-2">
                <div class="flex items-center gap-2 min-w-0">
                    <span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold border {badge}">{esc(ltype)}</span>
                    <span class="text-xs font-semibold text-slate-900 dark:text-white truncate">{esc(log.get('target_user'))}</span>
                </div>
                <span class="text-[10px] text-slate-400 font-mono shrink-0">{esc(log.get('created_at'))}</span>
            </div>
            <div class="text-xs text-slate-600 dark:text-slate-300 space-y-1">
                <div><span class="text-slate-400">Roblox ID:</span> <span class="font-mono text-slate-800 dark:text-slate-200">{esc(log.get('roblox_id', 'N/A'))}</span></div>
                <div><span class="text-slate-400">Grund:</span> <span class="text-slate-700 dark:text-slate-200 break-words">{esc(log.get('reason'))}</span></div>
            </div>
            <div class="text-[10px] text-slate-400 pt-1 border-t border-slate-100 dark:border-slate-800/40 flex justify-between items-center gap-3">
                <span>Moderator: {esc(log.get('moderator'))}</span>
                <span class="flex items-center gap-3">{edit_button}{delete_form}</span>
            </div>
            {edit_form}
        </div>"""

    chips = "".join(
        f'<button type="button" data-type="{t}" onclick="setTypeFilter(\'{t}\')" class="type-chip {"active" if t == "all" else ""} px-2.5 py-1 rounded-lg text-[11px] font-semibold border border-slate-200 dark:border-slate-700 bg-white dark:bg-[#141824] text-slate-600 dark:text-slate-300">{label}</button>'
        for t, label in [("all", "Alle"), ("Warn", "⚠️ Warn"), ("Kick", "🚪 Kick"), ("Ban", "🚫 Ban"), ("Ban BOLO", "🚨 Ban BOLO"), ("Notiz", "📝 Notiz")])

    body = f"""
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {stat_card("🛡️", "Team im Dienst", active_staff_count, "emerald")}
        {stat_card("📜", "Registrierte Logs", logs_total, "indigo")}
        {stat_card("🌴", "Aktive Abmeldungen", loa_count, "amber")}
        {stat_card("📋", "Offene Bewerbungen", pending_apps_count, "purple")}
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
        <div class="lg:col-span-4 space-y-6">
            <div class="{CARD} p-6 space-y-5">
                <div class="flex items-center justify-between">
                    <div>
                        <h2 class="text-lg font-bold text-slate-900 dark:text-white">Schicht-Steuerung</h2>
                        <p class="text-xs text-slate-500 dark:text-slate-400">Pausen zählen nicht zur Arbeitszeit</p>
                    </div>
                    <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border {BADGE_OK}">
                        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>{active_staff_count} im Dienst
                    </span>
                </div>
                <div class="bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 rounded-xl p-3 flex items-center justify-between text-xs">
                    <span class="flex items-center gap-2 font-medium">
                        <span class="w-2 h-2 rounded-full {'bg-emerald-500 animate-pulse' if shift_status == 'online' else ('bg-amber-500' if shift_status == 'break' else 'bg-slate-400')}"></span>{status_label}
                    </span>
                    <span id="liveShiftTimer" data-base="{elapsed}" data-running="{1 if shift_status == 'online' else 0}" class="font-mono font-bold text-indigo-600 dark:text-indigo-400">{fmt_duration(elapsed)}</span>
                </div>
                <form action="/shift/action" method="post" class="grid grid-cols-2 gap-3">{shift_buttons}</form>
            </div>

            <div class="{CARD} p-6 space-y-3">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">🟢 Aktuell im Dienst</h3>
                <div class="space-y-2">{duty_html or "<div class='text-xs text-slate-400 italic'>Gerade ist niemand im Dienst.</div>"}</div>
            </div>

            <div class="{CARD} p-6 space-y-3">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">🏆 Wochen-Aktivität (Top 3)</h3>
                <div class="space-y-2">{leaderboard_html or "<div class='text-xs text-slate-400 italic'>Noch keine Daten vorhanden.</div>"}</div>
            </div>
        </div>

        <div class="lg:col-span-4 space-y-6">
            <div id="playerlog" class="{CARD} p-6 space-y-4">
                <div>
                    <h2 class="text-lg font-bold text-slate-900 dark:text-white">🛡️ Melonly – Spielerakte</h2>
                    <p class="text-xs text-slate-500 dark:text-slate-400">Roblox-Spieler suchen, ID automatisch übernehmen und Vorgang protokollieren. <span class="font-semibold text-indigo-500">Nur im Panel – keine Team-Updates.</span></p>
                </div>
                <form action="/log/create" method="post" class="space-y-4 text-xs">
                    <div>
                        <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Username *</label>
                        <input type="text" id="robloxUsernameInput" name="target_user" maxlength="50" list="robloxUserSuggestions" autocomplete="off" oninput="lookupRobloxUser(this.value)" onfocus="searchRobloxUsers(this.value)" placeholder="z. B. Spieler123" required class="{INPUT}">
                        <div id="robloxSuggestionPanel" class="hidden mt-2 overflow-hidden rounded-xl border border-indigo-200 dark:border-indigo-800/70 bg-white dark:bg-[#10141f] shadow-sm" role="listbox" aria-label="Roblox-Spielervorschläge"></div>
                        <datalist id="robloxUserSuggestions"></datalist>
                    </div>
                    <div id="robloxUserPreview" class="hidden"></div>
                    <div>
                        <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Player ID (Auto-Ausfüllung)</label>
                        <input type="text" name="roblox_id" id="robloxIdInput" placeholder="z. B. 12345678" class="{INPUT}">
                    </div>
                    <div>
                        <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Typ der Strafe *</label>
                        <select name="log_type" required class="{INPUT}">
                            <option value="Warn">⚠️ Verwarnung (Warn)</option>
                            <option value="Kick">🚪 Kick</option>
                            <option value="Ban">🚫 Ban</option>
                            <option value="Ban BOLO">🚨 Ban BOLO</option>
                            <option value="Notiz">📝 Notiz / Hinweis</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Begründung *</label>
                        <textarea name="reason" maxlength="1000" placeholder="Grund hier eingeben..." required class="{INPUT} h-24"></textarea>
                    </div>
                    <button class="w-full {BTN} py-3">Log Speichern</button>
                </form>
            </div>
        </div>

        <div class="lg:col-span-4 space-y-4">
            <div class="flex items-center justify-between">
                <h2 class="text-lg font-bold text-slate-900 dark:text-white">Protokoll</h2>
                <div class="flex items-center gap-2">
                    <a href="/export/logs" class="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-700 text-xs px-2.5 py-1.5 rounded-lg text-slate-700 dark:text-slate-300 transition shadow-sm">📊 CSV</a>
                    <span class="text-xs text-slate-400 font-mono">{logs_total} gesamt</span>
                </div>
            </div>
            <form action="/dashboard#playerlog" method="get" class="flex flex-col sm:flex-row gap-2">
                <input type="search" name="player_history" maxlength="80" value="{esc(player_history_query)}" placeholder="Roblox-@Name oder Spieler-ID suchen…" required class="{INPUT} flex-1">
                <button type="submit" class="{BTN} px-4 py-2.5 text-xs shrink-0">🔎 Spielerakte suchen</button>
                {('<a href="/dashboard#playerlog" class="px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold text-center">Suche zurücksetzen</a>' if history_active else '')}
            </form>
            {player_history_summary}
            <div class="flex flex-wrap gap-1.5">{chips}</div>
            <input type="text" id="logSearch" oninput="filterLogs()" placeholder="🔎 Name, ID, Moderator oder Grund..." class="w-full bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 rounded-xl px-3.5 py-2 text-xs text-slate-900 dark:text-white focus:outline-none focus:border-indigo-500 shadow-sm">
            <div class="space-y-3 max-h-[calc(100vh-300px)] overflow-y-auto pr-1">
                {logs_html or empty_logs_html}
            </div>
            <p class="text-[10px] text-slate-400">{f"Alle {len(player_history_matches)} gespeicherten Vorgänge der Spielerakte werden angezeigt." if history_active else "Die Standardansicht zeigt die letzten 50 Einträge – der CSV-Export enthält alle."}</p>
        </div>
    </div>"""
    return render_page("Moderatoren-Panel", ctx, "dashboard", body, DASHBOARD_HEAD)


# =============================================================
# ROUTE: PULSE COMMAND CENTER / ULTIMATE
# =============================================================
@app.get("/ultimate", response_class=HTMLResponse)
async def ultimate_dashboard(request: Request, user_session: str = Cookie(None)):
    """Modernes Pulse Command Center: Team, Dienstzeit, Activity, Tickets und Handlungsbedarf auf einer Seite."""
    ctx = auth(request, user_session)
    guild = ctx.guild
    uid = str(ctx.user["id"])
    team_role_ids = ctx.config.get("team_role_ids", [])
    shifts_db = load_shifts()
    active_shifts = shifts_db.get("active_shifts", {})
    apps_db = load_json(APPS_FILE, {})
    loas = get_loas()

    members = [m for m in guild.members if not m.bot and any(r.id in team_role_ids for r in m.roles)]
    members.sort(key=lambda m: m.display_name.lower())

    current_shift = active_shifts.get(uid)
    shift_status = current_shift.get("status") if current_shift else "offline"
    current_elapsed = shift_elapsed(current_shift) if current_shift else 0
    weekly_goal = max(0.0, float(ctx.config.get("weekly_goal_hours", 3.0) or 0))
    weekly_seconds = calculate_weekly_seconds(uid, shifts_db.get("history", []), active_shifts)
    weekly_hours = weekly_seconds / 3600
    weekly_pct = min(100, int((weekly_hours / weekly_goal) * 100)) if weekly_goal > 0 else 100

    activity = get_activity_today(guild.id)
    eligible = activity.get("eligible")
    confirmed = activity.get("confirmed", set())
    activity_confirmed = sum(1 for m in members if activity.get("check_id") and (eligible is None or m.id in eligible) and m.id in confirmed)
    activity_open = sum(1 for m in members if activity.get("check_id") and (eligible is None or m.id in eligible) and m.id not in confirmed)

    try: tickets = pulse_db.list_tickets(limit=3000)
    except Exception: tickets = []
    open_tickets = [t for t in tickets if t.get("status") != "closed"]
    urgent_tickets = [t for t in open_tickets if t.get("priority") == "urgent"]

    try: tasks = pulse_db.list_tasks(limit=3000)
    except Exception: tasks = []
    open_tasks = [t for t in tasks if t.get("status") not in {"done", "archived"}]

    def is_overdue(t):
        raw = t.get("due_at")
        if not raw: return False
        try:
            due = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if due.tzinfo is None: due = due.replace(tzinfo=now_de().tzinfo)
            return due <= now_de()
        except Exception: return False

    overdue_tasks = [t for t in open_tasks if is_overdue(t)]
    pending_apps = sum(1 for a in apps_db.values() if str(a.get("status", "")).lower() in {"pending", "new", "in_review", "in prüfung", "review"})
    active_loas = sum(1 for x in loas.values() if x.get("active"))

    team_db = load_json(DATA_FILE, {})
    warning_5 = 0
    for m in members:
        try:
            if len(active_warns(user_entry(team_db, str(m.id)))) >= 5: warning_5 += 1
        except Exception: pass

    presence_counts = {
        "online": sum(str(m.status) == "online" for m in members),
        "idle": sum(str(m.status) == "idle" for m in members),
        "dnd": sum(str(m.status) == "dnd" for m in members),
        "offline": sum(str(m.status) == "offline" for m in members),
    }
    duty_count = sum(1 for x in active_shifts.values() if x.get("status") in {"online", "break"})
    try: unread = pulse_db.unread_count(ctx.user["id"])
    except Exception: unread = 0

    health_rows = warning_role_health(guild, ctx.config) if (ctx.perms.get("is_admin") or ctx.perms.get("can_warn")) else []
    health_ok = not health_rows or all(r.get("ok") for r in health_rows)
    critical = bool(warning_5 or urgent_tickets or overdue_tasks or not health_ok)
    attention = bool(pending_apps or open_tickets or active_loas or activity_open)
    state_text, state_cls, state_icon = (
        ("Sofort handeln", "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20", "🔴") if critical else
        ("Aufmerksamkeit", "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20", "🟡") if attention else
        ("Alles normal", "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20", "🟢")
    )

    def person_card(m):
        duty = active_shifts.get(str(m.id))
        duty_label = "🟢 Im Dienst" if duty and duty.get("status") == "online" else "☕ Pause" if duty and duty.get("status") == "break" else ""
        presence = {"online": ("Online", "bg-emerald-500"), "idle": ("Abwesend", "bg-amber-500"), "dnd": ("Bitte nicht stören", "bg-rose-500"), "offline": ("Offline", "bg-slate-400")}.get(str(m.status), ("Offline", "bg-slate-400"))
        roles = [r for r in m.roles if r.id in team_role_ids]
        top = max(roles, key=lambda r: r.position) if roles else m.top_role
        warns_count = 0
        try: warns_count = len(active_warns(user_entry(team_db, str(m.id))))
        except Exception: pass
        return f'''
        <a href="/member/{m.id}" class="pulse-glass pulse-hover rounded-2xl p-4 flex items-center gap-3 group">
            <div class="relative shrink-0">
                <img src="{esc(m.display_avatar.url)}" alt="" class="w-11 h-11 rounded-full border border-slate-200/60 dark:border-slate-700 shadow-sm object-cover">
                <span class="absolute -right-0.5 -bottom-0.5 w-3.5 h-3.5 rounded-full {presence[1]} border-2 border-white dark:border-[#0b0e14] pulse-dot"></span>
            </div>
            <div class="min-w-0 flex-1">
                <div class="font-semibold text-sm text-slate-900 dark:text-white truncate">{esc(m.display_name)}</div>
                <div class="text-[10px] text-slate-500 dark:text-slate-400 truncate">{esc(top.name)} · {presence[0]}{(" · " + duty_label) if duty_label else ""}</div>
            </div>
            <div class="flex items-center gap-1.5">
                {f'<span class="text-[10px] px-2 py-1 rounded-full border bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20">⚠ {warns_count}</span>' if warns_count else ''}
                <span class="text-slate-400 group-hover:text-indigo-500 transition">›</span>
            </div>
        </a>'''

    team_preview = "".join(person_card(m) for m in members[:10]) or '<div class="text-xs text-slate-400 py-8 text-center">Noch keine Teammitglieder konfiguriert.</div>'

    if shift_status == "offline":
        shift_action_html = '<button name="shift_action" value="start" class="w-full rounded-2xl bg-emerald-600 hover:bg-emerald-500 text-white py-3.5 font-bold shadow-lg shadow-emerald-900/10 transition">▶️ Schicht starten</button>'
    else:
        shift_action_html = (
            '<button name="shift_action" value="break" class="flex-1 rounded-2xl border border-amber-500/20 bg-amber-500/10 hover:bg-amber-500/15 text-amber-600 dark:text-amber-400 py-3 font-bold transition">⏸ Pause</button>'
            if shift_status == "online" else
            '<button name="shift_action" value="resume" class="flex-1 rounded-2xl border border-emerald-500/20 bg-emerald-500/10 hover:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 py-3 font-bold transition">▶️ Fortsetzen</button>'
        )
        shift_action_html += '<button name="shift_action" value="end" class="flex-1 rounded-2xl border border-rose-500/20 bg-rose-500/10 hover:bg-rose-500/15 text-rose-600 dark:text-rose-400 py-3 font-bold transition">⏹ Beenden</button>'

    manager_link = '<a href="/suite" class="inline-flex items-center justify-center rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2.5 text-xs font-bold transition">Führungs-Suite öffnen</a>' if (ctx.perms.get("can_promote") or ctx.perms.get("is_admin")) else ""
    action_links = [('/team', '👥', 'Teamliste'),('/tickets', '🎫', 'Tickets'),('/applications', '📝', 'Bewerbungen'),('/meetings', '🎙️', 'Meeting'),('/wiki', '📚', 'Wiki')]
    action_html = "".join(f'<a href="{href}" class="pulse-glass pulse-hover rounded-2xl p-4 text-center"><div class="text-2xl mb-1">{icon}</div><div class="text-[11px] font-bold text-slate-700 dark:text-slate-200">{label}</div></a>' for href,icon,label in action_links)

    attention_items = []
    if warning_5: attention_items.append(f'<a href="/team" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-rose-500/5 border border-rose-500/15"><span class="text-xs text-rose-700 dark:text-rose-300">🚨 {warning_5} Teammitglied(er) bei 5/5 Warnungen</span><span class="text-[10px] font-bold">Prüfen →</span></a>')
    if urgent_tickets: attention_items.append(f'<a href="/tickets" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-rose-500/5 border border-rose-500/15"><span class="text-xs text-rose-700 dark:text-rose-300">🚨 {len(urgent_tickets)} dringende Tickets offen</span><span class="text-[10px] font-bold">Öffnen →</span></a>')
    if overdue_tasks: attention_items.append(f'<a href="/tasks" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-amber-500/5 border border-amber-500/15"><span class="text-xs text-amber-700 dark:text-amber-300">⏰ {len(overdue_tasks)} Aufgaben überfällig</span><span class="text-[10px] font-bold">Prüfen →</span></a>')
    if activity_open: attention_items.append(f'<a href="/team#activity" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-indigo-500/5 border border-indigo-500/15"><span class="text-xs text-indigo-700 dark:text-indigo-300">✅ {activity_open} Activity-Check-Antwort(en) fehlen</span><span class="text-[10px] font-bold">Ansehen →</span></a>')
    if not health_ok: attention_items.append('<a href="/settings" class="flex items-center justify-between gap-3 p-3 rounded-xl bg-amber-500/5 border border-amber-500/15"><span class="text-xs text-amber-700 dark:text-amber-300">🛡️ Warnrollen-Konfiguration prüfen</span><span class="text-[10px] font-bold">Settings →</span></a>')
    attention_html = "".join(attention_items) or '<div class="text-xs text-slate-400 py-5 text-center">Keine offenen Handlungsfelder. Gute Arbeit.</div>'

    recent_events = []
    try:
        for ev in pulse_db.events(limit=6):
            label, actor, when = esc(ev.get("event_type") or "Ereignis"), esc(ev.get("actor_name") or "System"), esc(ev.get("created_at") or "")
            recent_events.append(f'<div class="flex items-start gap-3 py-2.5 border-b border-slate-200/60 dark:border-slate-800 last:border-0"><span class="w-8 h-8 rounded-xl bg-indigo-500/10 text-indigo-500 grid place-items-center shrink-0">•</span><div class="min-w-0"><div class="text-xs font-semibold text-slate-800 dark:text-slate-200 truncate">{label}</div><div class="text-[10px] text-slate-400 truncate">{actor} · {when}</div></div></div>')
    except Exception: recent_events=[]
    events_html="".join(recent_events) or '<div class="text-xs text-slate-400 py-5">Noch keine zentralen Events vorhanden.</div>'

    hero = f'''
    <section class="pulse-glass pulse-hero pulse-ring rounded-3xl p-6 md:p-8 mb-6 overflow-hidden relative">
        <div class="absolute -right-14 -top-16 w-44 h-44 rounded-full bg-indigo-500/10 blur-2xl"></div>
        <div class="relative flex flex-col xl:flex-row xl:items-center xl:justify-between gap-6">
            <div class="min-w-0">
                <div class="text-[11px] uppercase tracking-[.18em] font-black text-indigo-500 dark:text-indigo-400 mb-2">Pulse Command Center</div>
                <div class="flex flex-wrap items-center gap-3">
                    <h1 class="text-2xl md:text-3xl font-black text-slate-900 dark:text-white">Hallo, {esc(ctx.user.get("global_name") or ctx.user.get("username") or "Team")} 👋</h1>
                    <span class="inline-flex items-center gap-2 text-[11px] font-bold px-3 py-1.5 rounded-full border {state_cls}">{state_icon} {state_text}</span>
                </div>
                <p class="text-sm text-slate-500 dark:text-slate-400 mt-2 max-w-3xl">Die wichtigsten Team-, Dienst- und Moderationsdaten an einem Ort. Änderungen kommen direkt aus Discord und Pulse.</p>
                <div class="flex flex-wrap gap-2 mt-5">{action_html}{manager_link}</div>
            </div>
            <div class="shrink-0 rounded-2xl border border-white/30 dark:border-slate-700/50 bg-white/40 dark:bg-slate-900/30 p-4 min-w-[190px]">
                <div class="text-[10px] uppercase tracking-widest font-black text-slate-400 mb-1">Lokale Zeit</div>
                <div id="pulseLiveClock" class="text-3xl font-black font-mono text-slate-900 dark:text-white">{now_de().strftime("%H:%M:%S")}</div>
                <div class="text-[10px] text-slate-500 dark:text-slate-400 mt-1">{now_de().strftime("%d.%m.%Y")} · Europe/Berlin</div>
                <div class="mt-3 text-[10px] text-slate-400">📥 {unread} ungelesene Pulse-Nachrichten</div>
            </div>
        </div>
    </section>'''

    stats_html=f'''
    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        <div class="pulse-glass pulse-hover rounded-2xl p-5"><div class="text-[10px] uppercase tracking-widest font-black text-slate-400">Team online</div><div class="text-3xl font-black mt-1">{presence_counts["online"]}</div><div class="text-[10px] text-slate-500 mt-1">{presence_counts["idle"]} abwesend · {presence_counts["dnd"]} DND</div></div>
        <div class="pulse-glass pulse-hover rounded-2xl p-5"><div class="text-[10px] uppercase tracking-widest font-black text-slate-400">Im Dienst</div><div class="text-3xl font-black mt-1">{duty_count}</div><div class="text-[10px] text-slate-500 mt-1">Schichtsystem</div></div>
        <div class="pulse-glass pulse-hover rounded-2xl p-5"><div class="text-[10px] uppercase tracking-widest font-black text-slate-400">Offene Tickets</div><div class="text-3xl font-black mt-1">{len(open_tickets)}</div><div class="text-[10px] text-slate-500 mt-1">{len(urgent_tickets)} dringend</div></div>
        <div class="pulse-glass pulse-hover rounded-2xl p-5"><div class="text-[10px] uppercase tracking-widest font-black text-slate-400">Bewerbungen</div><div class="text-3xl font-black mt-1">{pending_apps}</div><div class="text-[10px] text-slate-500 mt-1">wartend auf Prüfung</div></div>
    </div>'''

    body=f'''
    {hero}{stats_html}
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div class="xl:col-span-8 space-y-6">
            <section class="pulse-glass rounded-3xl p-5 md:p-6">
                <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5"><div><div class="text-[10px] uppercase tracking-widest font-black text-indigo-500">Dein Dienst</div><h2 class="text-lg font-black text-slate-900 dark:text-white">Persönlicher Überblick</h2></div><span class="text-[11px] font-bold px-3 py-1.5 rounded-full border {("bg-emerald-500/10 text-emerald-600 border-emerald-500/20" if shift_status=="online" else "bg-amber-500/10 text-amber-600 border-amber-500/20" if shift_status=="break" else "bg-slate-500/10 text-slate-500 border-slate-500/20")}">{"🟢 IM DIENST" if shift_status=="online" else "☕ PAUSE" if shift_status=="break" else "⚪ OFFLINE"}</span></div>
                <div class="grid md:grid-cols-3 gap-4">
                    <div class="rounded-2xl bg-slate-50/80 dark:bg-slate-950/30 border border-slate-200/70 dark:border-slate-800 p-4"><div class="text-[10px] text-slate-400 uppercase tracking-widest font-black">Aktuelle Schicht</div><div class="text-2xl font-black mt-1" id="ultimateShiftTimer">{fmt_duration(current_elapsed)}</div><div class="text-[10px] text-slate-500 mt-1">Pausen werden nicht gutgeschrieben</div></div>
                    <div class="md:col-span-2 rounded-2xl bg-slate-50/80 dark:bg-slate-950/30 border border-slate-200/70 dark:border-slate-800 p-4"><div class="flex justify-between text-[10px] uppercase tracking-widest font-black text-slate-400"><span>Wochenziel</span><span>{weekly_hours:.1f}h / {weekly_goal:g}h</span></div><div class="mt-3 h-3 rounded-full bg-slate-200 dark:bg-slate-800 overflow-hidden"><div class="h-full rounded-full bg-gradient-to-r from-indigo-500 to-cyan-400 transition-all" style="width:{weekly_pct}%"></div></div><div class="flex justify-between mt-2 text-[10px] text-slate-500"><span>{weekly_pct}% erreicht</span><span>⏱ {fmt_duration(weekly_seconds)}</span></div></div>
                </div>
                <form action="/shift/action" method="post" class="flex flex-col sm:flex-row gap-2 mt-4">{shift_action_html}</form>
            </section>
            <section class="pulse-glass rounded-3xl p-5 md:p-6">
                <div class="flex items-center justify-between gap-3 mb-5"><div><div class="text-[10px] uppercase tracking-widest font-black text-indigo-500">Live Team</div><h2 class="text-lg font-black text-slate-900 dark:text-white">Wer ist gerade da?</h2></div><a href="/team" class="text-xs font-bold text-indigo-500 hover:underline">Gesamte Teamliste →</a></div>
                <div class="grid md:grid-cols-2 gap-3">{team_preview}</div>
                {f'<div class="text-[10px] text-slate-400 mt-4 text-center">+ {len(members)-10} weitere Teammitglieder</div>' if len(members)>10 else ''}
            </section>
        </div>
        <div class="xl:col-span-4 space-y-6">
            <section class="pulse-glass rounded-3xl p-5"><div class="flex items-center justify-between"><div><div class="text-[10px] uppercase tracking-widest font-black text-rose-500">Priorität</div><h2 class="text-lg font-black text-slate-900 dark:text-white">Handlungsbedarf</h2></div><span class="text-xl">{state_icon}</span></div><div class="mt-4 space-y-2">{attention_html}</div></section>
            <section class="pulse-glass rounded-3xl p-5"><div class="flex items-center justify-between mb-4"><div><div class="text-[10px] uppercase tracking-widest font-black text-indigo-500">Activity Check</div><h2 class="text-lg font-black text-slate-900 dark:text-white">Team-Rückmeldungen</h2></div><a href="/team#activity" class="text-xs font-bold text-indigo-500 hover:underline">Details →</a></div><div class="grid grid-cols-3 gap-2 text-center"><div class="rounded-2xl bg-emerald-500/5 border border-emerald-500/15 p-3"><div class="text-2xl font-black text-emerald-600">{activity_confirmed}</div><div class="text-[10px] text-slate-500">Bestätigt</div></div><div class="rounded-2xl bg-rose-500/5 border border-rose-500/15 p-3"><div class="text-2xl font-black text-rose-600">{activity_open}</div><div class="text-[10px] text-slate-500">Offen</div></div><div class="rounded-2xl bg-slate-500/5 border border-slate-500/15 p-3"><div class="text-2xl font-black">{len(members)}</div><div class="text-[10px] text-slate-500">Team</div></div></div><div class="mt-3 text-[10px] text-slate-500">{("Check vom " + esc(activity.get("check_date") or "")) if activity.get("check_id") else "Heute wurde noch kein Activity Check gestartet."}</div></section>
            <section class="pulse-glass rounded-3xl p-5"><div class="flex items-center justify-between mb-4"><div><div class="text-[10px] uppercase tracking-widest font-black text-indigo-500">System</div><h2 class="text-lg font-black text-slate-900 dark:text-white">Letzte Ereignisse</h2></div><a href="/search" class="text-xs font-bold text-indigo-500 hover:underline">Suche →</a></div>{events_html}</section>
        </div>
    </div>'''

    extra_js=f'''
    <script>
    (function(){{
        const base={int(current_elapsed)}; const running={"true" if shift_status=="online" else "false"}; const started=Date.now();
        const el=document.getElementById("ultimateShiftTimer");
        function tick(){{ if(!el)return; const s=base+(running?Math.floor((Date.now()-started)/1000):0); el.textContent=Math.floor(s/3600)+"h "+Math.floor((s%3600)/60)+"m "+(s%60)+"s"; }}
        tick(); if(running)setInterval(tick,1000);
    }})();
    </script>'''
    return render_page("Pulse Command Center", ctx, "ultimate", body, DASHBOARD_HEAD + extra_js)


# =============================================================
# MELOONLY – eigenständige Spielerakte mit vollständiger Verlaufssuche
# =============================================================
@app.get("/melonly", response_class=HTMLResponse)
async def melonly_alias(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    if not (ctx.perms.get("can_warn") or ctx.perms.get("can_add_notes") or ctx.perms.get("is_admin")):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")

    repository = get_melonly_repository()
    query = str(request.query_params.get("player_history") or "").strip()[:80]
    needle = query.lstrip("@").strip().casefold()
    edit_log_id = str(request.query_params.get("edit_log") or "")

    matches = repository.player_history(query) if needle else []
    searched = bool(needle)
    selected_logs = list(reversed(matches if searched else repository.latest(50)))
    return_to = f"/melonly?player_history={quote(query, safe='')}" if searched else "/melonly"
    search_param = f"player_history={quote(query, safe='')}&" if searched else ""

    type_counts = repository.count_by_type()
    ban_count = type_counts.get("Ban", 0) + type_counts.get("Ban BOLO", 0)
    kick_count = type_counts.get("Kick", 0)
    note_count = type_counts.get("Notiz", 0)
    logs_total = repository.count()

    summary = ""
    if searched:
        names = sorted({str(log.get("target_user") or "").strip().lstrip("@") for log in matches if str(log.get("target_user") or "").strip()}, key=str.casefold)
        display_names = ", ".join("@" + name for name in names[:4]) or query
        if len(names) > 4:
            display_names += f" (+{len(names) - 4} weitere)"
        count_warn = sum(1 for log in matches if log.get("type") == "Warn")
        count_kick = sum(1 for log in matches if log.get("type") == "Kick")
        count_ban = sum(1 for log in matches if log.get("type") in {"Ban", "Ban BOLO"})
        count_note = sum(1 for log in matches if log.get("type") == "Notiz")
        summary = f"""
        <div class="{CARD} p-4 space-y-3">
          <div class="flex items-start justify-between gap-3">
            <div><h2 class="font-bold text-sm">🔎 Spieler-Verlauf</h2><p class="text-xs text-slate-500 mt-1">{esc(display_names)}</p></div>
            <span class="rounded-full px-3 py-1 bg-indigo-500/10 text-indigo-600 dark:text-indigo-300 text-xs font-bold">{len(matches)} Vorgänge</span>
          </div>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] p-3"><div class="text-[10px] text-slate-500">Warns</div><div class="text-lg font-bold">{count_warn}</div></div>
            <div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] p-3"><div class="text-[10px] text-slate-500">Kicks</div><div class="text-lg font-bold">{count_kick}</div></div>
            <div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] p-3"><div class="text-[10px] text-slate-500">Bans / BOLOs</div><div class="text-lg font-bold">{count_ban}</div></div>
            <div class="rounded-xl bg-slate-50 dark:bg-[#0b0e14] p-3"><div class="text-[10px] text-slate-500">Notizen</div><div class="text-lg font-bold">{count_note}</div></div>
          </div>
          <p class="text-[11px] text-slate-500">Alle gespeicherten Treffer werden gezeigt – nicht nur die letzten 50 Einträge.</p>
        </div>"""

    logs_html = ""
    for log in selected_logs:
        log_type = str(log.get("type") or "Log")
        badge = LOG_TYPE_STYLE.get(log_type, "bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 border-slate-200")
        target = str(log.get("target_user") or "Unbekannt")
        rid = str(log.get("roblox_id") or "N/A")
        log_id = str(log.get("id") or "")
        reason = str(log.get("reason") or "")
        moderator = str(log.get("moderator") or "Unbekannt")
        can_edit = can_manage_melonly_entry(ctx, log)
        edit_link = (
            f'<a href="/dashboard?{search_param}edit_log={quote(log_id, safe="")}#playerlog" class="text-indigo-500 hover:underline font-semibold">✏️ Bearbeiten</a>'
            if can_edit else ""
        )
        delete_form = f"""
          <form action="/log/delete" method="post" onsubmit="return confirm('Diesen Eintrag wirklich löschen?');">
            <input type="hidden" name="log_id" value="{esc(log_id)}">
            <input type="hidden" name="return_to" value="{esc(return_to)}">
            <button class="text-rose-500 hover:underline font-semibold">🗑️ Löschen</button>
          </form>""" if can_edit else ""
        logs_html += f"""
        <article class="{CARD} p-4 space-y-2">
          <div class="flex items-start justify-between gap-3 border-b border-slate-100 dark:border-slate-800/60 pb-2">
            <div class="flex items-center gap-2 min-w-0"><span class="px-2.5 py-0.5 rounded-full text-[10px] font-bold border {badge}">{esc(log_type)}</span><span class="text-xs font-semibold break-all">{esc(target)}</span></div>
            <span class="text-[10px] text-slate-400 font-mono shrink-0">{esc(log.get("created_at") or "Zeit unbekannt")}</span>
          </div>
          <div class="text-xs space-y-1"><div><span class="text-slate-400">Roblox ID:</span> <span class="font-mono">{esc(rid)}</span></div><div><span class="text-slate-400">Grund:</span> <span class="break-words">{esc(reason)}</span></div></div>
          <div class="text-[10px] text-slate-400 pt-1 border-t border-slate-100 dark:border-slate-800/40 flex justify-between items-center gap-3"><span>Moderator: {esc(moderator)}</span><span class="flex items-center gap-3">{edit_link}{delete_form}</span></div>
        </article>"""

    if not logs_html:
        if searched:
            logs_html = f'<div class="{CARD} p-8 text-center"><div class="text-3xl mb-2">🔎</div><div class="font-bold text-sm">Keine Einträge gefunden</div><p class="text-xs text-slate-500 mt-2">Zu „{esc(query)}“ wurden keine gespeicherten Vorgänge gefunden. Prüfe den @-Namen oder die Roblox-ID.</p></div>'
        else:
            logs_html = f'<div class="{CARD} p-8 text-center text-xs text-slate-500">Noch keine Spieler-Vorgänge gespeichert.</div>'

    body = f"""
    <div class="max-w-7xl mx-auto space-y-6">
      <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
        <div><div class="text-[11px] font-semibold tracking-wide uppercase text-indigo-500 mb-1">Pulse TeamOS · Moderation</div><h1 class="text-2xl font-bold text-slate-900 dark:text-white">🛡️ Melonly</h1><p class="text-sm text-slate-500 dark:text-slate-400 mt-1">Roblox-Spieler direkt suchen, ID übernehmen und Moderationsvorgänge sauber dokumentieren.</p></div>
        <a href="/ultimate" class="inline-flex items-center justify-center px-3 py-2 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold">← Übersicht</a>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        {stat_card("🚫", "Bans / BOLOs", ban_count, "rose")}{stat_card("🚪", "Kicks", kick_count, "amber")}{stat_card("📝", "Notizen", note_count, "indigo")}{stat_card("📚", "Alle Einträge", logs_total, "purple")}
      </div>
      <div class="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <section class="xl:col-span-4">
          <div class="{CARD} p-5 space-y-4" id="player-create">
            <div><h2 class="text-base font-bold">🛡️ Spieler eintragen</h2><p class="text-xs text-slate-500 mt-1">Der Eintrag wird im Pulse-Moderationsprotokoll gespeichert.</p></div>
            <form action="/log/create" method="post" class="space-y-3 text-xs">
              <input type="hidden" name="return_to" value="{esc(return_to)}">
              <div><label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Username *</label><input type="text" id="robloxUsernameInput" name="target_user" maxlength="50" list="robloxUserSuggestions" autocomplete="off" oninput="lookupRobloxUser(this.value)" onfocus="searchRobloxUsers(this.value)" placeholder="@Username" required class="{INPUT}"><div id="robloxSuggestionPanel" class="hidden mt-2 overflow-hidden rounded-xl border border-indigo-200 dark:border-indigo-800/70 bg-white dark:bg-[#10141f] shadow-sm" role="listbox" aria-label="Roblox-Spielervorschläge"></div><datalist id="robloxUserSuggestions"></datalist></div>
              <div id="robloxUserPreview" class="hidden"></div>
              <div><label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Roblox Player ID</label><input type="text" name="roblox_id" id="robloxIdInput" placeholder="z. B. 12345678" class="{INPUT}"></div>
              <div><label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Aktion</label><select name="log_type" required class="{INPUT}"><option value="Warn">⚠️ Warn</option><option value="Kick">🚪 Kick</option><option value="Ban">🚫 Ban</option><option value="Ban BOLO">🚨 Ban BOLO</option><option value="Notiz">📝 Notiz</option></select></div>
              <div><label class="block text-slate-600 dark:text-slate-400 mb-1 font-semibold">Grund</label><textarea name="reason" maxlength="1000" placeholder="Begründung / Notiz…" required class="{INPUT} h-24"></textarea></div>
              <button class="w-full {BTN} py-3">✓ Spieler-Vorgang eintragen</button>
              <p class="text-[10px] leading-relaxed text-slate-500">Ban/Kick sind hier Protokoll-Aktionen und keine automatische Roblox-API-Sperre.</p>
            </form>
          </div>
        </section>
        <section class="xl:col-span-8 space-y-4" id="playerlog">
          <div class="flex items-center justify-between gap-3"><div><h2 class="text-lg font-bold">{"Spieler-Verlauf" if searched else "Letzte Spieler-Vorgänge"}</h2><p class="text-xs text-slate-500 mt-1">{"Alle passenden Einträge" if searched else "Die Übersicht zeigt die letzten 50 Vorgänge."}</p></div><a href="/export/logs" class="px-3 py-2 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold">📊 CSV</a></div>
          <form action="/melonly" method="get" class="flex flex-col sm:flex-row gap-2"><input type="search" name="player_history" maxlength="80" value="{esc(query)}" placeholder="Roblox-@Name oder Spieler-ID eingeben…" required class="{INPUT} flex-1"><button type="submit" class="{BTN} px-4 py-2.5 text-xs shrink-0">🔎 Spieler suchen</button>{('<a href="/melonly#playerlog" class="px-3 py-2.5 rounded-xl border border-slate-200 dark:border-slate-700 text-xs font-semibold text-center">Suche zurücksetzen</a>' if searched else '')}</form>
          {summary}
          <div class="space-y-3 max-h-[calc(100vh-240px)] overflow-y-auto pr-1">{logs_html}</div>
          <p class="text-[10px] text-slate-400">{"Alle " + str(len(matches)) + " gespeicherten Vorgänge des Suchtreffers werden gezeigt." if searched else "Nutze die Suche, um den vollständigen Verlauf eines einzelnen Spielers aufzurufen."}</p>
        </section>
      </div>
    </div>
    """
    return render_page("Melonly", ctx, "melonly", body, DASHBOARD_HEAD)

# =============================================================
# ACTION: SCHICHT-SYSTEM (Start / Pause / Fortsetzen / Ende)
# =============================================================
def shift_end_open_items_text(mod_id: str, now: datetime) -> str:
    """Short summary of open work at shift end: claimed open tickets and overdue tasks."""
    tickets = [t for t in pulse_db.list_tickets(claimed_by_id=mod_id, limit=50) if t.get("status") != "closed"]
    overdue = 0
    for task in pulse_db.list_tasks(assignee_id=mod_id, limit=100):
        if task.get("status") in ("done", "archived") or not task.get("due_at"):
            continue
        try:
            if parse_dt(str(task["due_at"]).replace("Z", "+00:00")) < now:
                overdue += 1
        except (TypeError, ValueError):
            continue
    parts = []
    if tickets:
        parts.append(f"{len(tickets)} übernommene Ticket{'s' if len(tickets) != 1 else ''} noch offen – bei Bedarf freigeben oder übergeben")
    if overdue:
        parts.append(f"{overdue} überfällige Aufgabe{'n' if overdue != 1 else ''}")
    return (" Noch offen: " + "; ".join(parts) + ".") if parts else " Keine offenen Tickets oder überfälligen Aufgaben – sauberer Abschluss! ✅"


SHIFT_ACTIONS = ("start", "break", "resume", "end")


def apply_shift_action(mod_id, shift_action: str) -> tuple[bool, str]:
    """BOT-03: start/pause/resume/end one person's shift – the single logic for the dashboard, the Discord
    panel and /dienst. Synchronous on purpose: load and save happen without an await in between."""
    mod_id = str(mod_id)
    shifts_db = load_shifts()
    team_db = load_json(DATA_FILE, {})
    active = shifts_db["active_shifts"]
    shift = active.get(mod_id)
    now = now_de()

    if shift_action == "start":
        if shift:
            return False, "Du hast bereits eine laufende Schicht."
        if len(active_warns(user_entry(team_db, mod_id))) >= 5:
            return False, "Schicht-Start gesperrt: Du hast bereits 5 aktive Verwarnungen!"
        # An active leave is informational, not a lock: team members may
        # voluntarily work during an LOA without deleting or shortening it.
        loa = get_loas().get(mod_id)
        leave_active = bool(loa and loa.get("active"))
        active[mod_id] = {
            "status": "online",
            "started_at_iso": now.isoformat(),
            "segment_start_iso": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "accumulated_seconds": 0,
        }
        msg = (
            f"Schicht gestartet. Deine Abmeldung bleibt bis {fmt_date(loa['bis'])} hinterlegt "
            "und wird nicht automatisch beendet."
            if leave_active
            else "Schicht gestartet – viel Erfolg!"
        )

    elif shift_action == "break" and shift and shift.get("status") == "online":
        shift["accumulated_seconds"] = shift_elapsed(shift)
        shift["status"] = "break"
        shift["segment_start_iso"] = None
        msg = "Pause gestartet."

    elif shift_action == "resume" and shift and shift.get("status") == "break":
        shift["status"] = "online"
        shift["segment_start_iso"] = now.isoformat()
        msg = "Schicht fortgesetzt."

    elif shift_action == "end" and shift:
        duration = shift_elapsed(shift)
        shifts_db["history"].append({
            "mod_id": mod_id, "date": shift.get("date"), "duration_seconds": duration,
            "started_at_iso": shift.get("started_at_iso"), "ended_at_iso": now.isoformat(),
        })
        del active[mod_id]
        msg = f"Schicht beendet – {fmt_duration(duration)} gutgeschrieben."
        # Handover hint (never blocks ending the shift): what is still open for this person.
        try:
            msg += shift_end_open_items_text(mod_id, now)
        except Exception as exc:
            print(f"⚠️ Schichtende-Übersicht nicht verfügbar: {type(exc).__name__}")
    else:
        return False, "Diese Aktion ist gerade nicht möglich."

    save_json(SHIFTS_FILE, shifts_db)
    return True, msg


def shift_status_text(mod_id) -> str:
    """One line for /dienst status and the panel answer."""
    shifts = load_shifts()
    shift = shifts.get("active_shifts", {}).get(str(mod_id))
    week = fmt_duration(calculate_weekly_seconds(str(mod_id), shifts.get("history", []), shifts.get("active_shifts", {})))
    if not shift:
        return f"Du bist nicht im Dienst. Diese Woche: {week}."
    state = "im Dienst" if shift.get("status") == "online" else "in der Pause"
    return f"Du bist {state} seit {fmt_duration(shift_elapsed(shift))}. Diese Woche: {week}."


async def sync_duty_after_shift(bot, guild, mod_id) -> None:
    """Voice-Support-Bereitschaft is a view of this same shift state, not a second independent check-in.
    Keep its Discord role and status post in sync (dashboard and Discord use this)."""
    try:
        voice_support = bot.get_cog("VoiceSupport") if bot else None
        if voice_support and guild:
            await voice_support.sync_member_duty_role(guild, str(mod_id))
            await voice_support._refresh_duty_status(guild)
    except Exception as exc:
        print(f"⚠️ Voice-Support: Bereitschaft konnte nach Schichtaktion nicht aktualisiert werden: {exc}")


@app.post("/shift/action")
async def handle_shift_action(request: Request, shift_action: str = Form(...), return_to: str = Form("/dashboard"),
                              user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    # Only known local pages; never redirect to user-supplied external URLs.
    target_page = return_to if return_to in {"/dashboard", "/me"} else "/dashboard"
    ok, msg = apply_shift_action(ctx.user["id"], shift_action)  # BOT-03: same logic as the Discord panel
    if not ok:
        return back(target_page, msg, False)
    await sync_duty_after_shift(getattr(request.app.state, "bot", None), ctx.guild, ctx.user["id"])
    return back(target_page, msg)


# =============================================================
# ACTION: LOGS ERSTELLEN & LÖSCHEN, CSV-EXPORT
# =============================================================
def safe_log_return_to(value: str) -> str:
    """Allow only local dashboard/Melonly return paths; reject external redirects."""
    value = str(value or "/dashboard").strip()
    if value in {"/dashboard", "/melonly"}:
        return value
    player_file = re.fullmatch(r"/melonly\?player_file=(\d{1,15})", value)
    if player_file:  # personal Melonly player file of one Roblox player id
        return f"/melonly?player_file={player_file.group(1)}"
    for base in ("/dashboard", "/melonly"):
        prefix = base + "?player_history="
        if value.startswith(prefix):
            raw_query = value.split("player_history=", 1)[1].split("#", 1)[0].split("&", 1)[0]
            if re.fullmatch(r"[A-Za-z0-9_%@._+-]{1,240}", raw_query):
                return f"{prefix}{raw_query}" + ("#playerlog" if base == "/dashboard" else "")
    return "/dashboard"


@app.post("/log/create")
async def create_log(request: Request, target_user: str = Form(...), roblox_id: str = Form("N/A"),
                     log_type: str = Form(...), reason: str = Form(...),
                     return_to: str = Form("/dashboard"), user_session: str = Cookie(None)):
    return_to = safe_log_return_to(return_to)
    ctx = auth(request, user_session)
    target_user = target_user.strip()[:50]
    reason = reason.strip()[:1000]
    roblox_id = roblox_id.strip()
    if log_type not in VALID_LOG_TYPES or not target_user or not reason:
        return back(return_to, "Ungültige Eingabe.", False)
    if not can_manage_melonly_type(
        log_type,
        can_warn=bool(ctx.perms.get("can_warn")),
        can_add_notes=bool(ctx.perms.get("can_add_notes")),
        can_moderate_kicks=bool(ctx.perms.get("can_moderate_kicks")),
        can_moderate_bans=bool(ctx.perms.get("can_moderate_bans")),
        can_moderate_bolo=bool(ctx.perms.get("can_moderate_bolo")),
        manager=bool(ctx.perms.get("is_admin")),
    ):
        raise HTTPException(status_code=403, detail="Für diesen Melonly-Aktionstyp fehlt dir die gesonderte Berechtigung.")
    if not re.fullmatch(r"\d{1,15}", roblox_id):
        roblox_id = "N/A"

    get_melonly_repository().append({
        "id": f"log_{uuid.uuid4().hex[:6]}",
        "target_user": target_user,
        "roblox_id": roblox_id,
        "type": log_type,
        "reason": reason,
        "moderator": ctx.user.get("global_name", "Dashboard Admin"),
        "moderator_id": ctx.user["id"],
        "created_at": now_de().strftime("%d.%m.%Y %H:%M"),
    })
    # Melonly bleibt ausschließlich im Panel. Es gibt dafür bewusst kein Discord-Team-Update.
    log_audit(
        ctx.user.get("global_name"),
        ctx.user["id"],
        "Melonly-Eintrag erstellt",
        f"Spieler: {target_user} ({log_type})",
    )
    return back(return_to, f"{log_type}-Log für {target_user} gespeichert.")


@app.post("/log/edit")
async def edit_log(
    request: Request,
    log_id: str = Form(...),
    target_user: str = Form(...),
    roblox_id: str = Form("N/A"),
    log_type: str = Form(...),
    reason: str = Form(...),
    return_to: str = Form("/dashboard"),
    user_session: str = Cookie(None),
):
    return_to = safe_log_return_to(return_to)
    ctx = auth(request, user_session)
    repository = get_melonly_repository()
    found = repository.find_first_by_id(log_id)
    if not found:
        return back(return_to, "Log nicht gefunden.", False)
    log_seq, entry = found

    if not can_manage_melonly_entry(ctx, entry, target_type=log_type):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung, diesen Melonly-Eintrag zu bearbeiten.")

    target_user = target_user.strip()[:50]
    reason = reason.strip()[:1000]
    roblox_id = roblox_id.strip()
    if log_type not in VALID_LOG_TYPES or not target_user or not reason:
        return back(return_to, "Ungültige Eingabe.", False)
    if not re.fullmatch(r"\d{1,15}", roblox_id):
        roblox_id = "N/A"

    old_target = str(entry.get("target_user") or "")
    old_type = str(entry.get("type") or "Log")
    entry.update({
        "target_user": target_user,
        "roblox_id": roblox_id,
        "type": log_type,
        "reason": reason,
        "edited_at": now_de().strftime("%d.%m.%Y %H:%M"),
        "edited_by": ctx.user.get("global_name") or ctx.user.get("username") or "Team",
    })
    if not repository.update_by_seq(log_seq, entry):
        return back(return_to, "Log konnte nicht gespeichert werden. Bitte erneut laden.", False)
    log_audit(
        ctx.user.get("global_name"),
        ctx.user["id"],
        "Melonly-Eintrag bearbeitet",
        f"{log_id}: {old_target} ({old_type}) -> {target_user} ({log_type})",
    )
    return back(return_to, "Melonly-Eintrag wurde bearbeitet.")


@app.post("/log/delete")
async def delete_log(
    request: Request,
    log_id: str = Form(...),
    return_to: str = Form("/dashboard"),
    user_session: str = Cookie(None),
):
    return_to = safe_log_return_to(return_to)
    ctx = auth(request, user_session)
    repository = get_melonly_repository()
    found = repository.find_first_by_id(log_id)
    if not found:
        return back(return_to, "Log nicht gefunden.", False)
    _log_seq, entry = found
    if not can_manage_melonly_entry(ctx, entry):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung, diesen Melonly-Eintrag zu löschen.")
    if not repository.delete_all_by_id(log_id):
        return back(return_to, "Log konnte nicht gelöscht werden. Bitte erneut laden.", False)
    log_audit(ctx.user.get("global_name"), ctx.user["id"], "Log Gelöscht",
              f"{entry.get('target_user')} ({entry.get('type')}) – ID {log_id}")
    return back(return_to, "Melonly-Eintrag gelöscht.")



def get_setting_safe(key, default=None):
    """pulse_db settings read that never breaks a page (missing table, locked database)."""
    try:
        return pulse_db.get_setting(key, default)
    except Exception:
        return default


def can_review_applications(ctx) -> bool:
    """Full applicant answers, evaluations and internal notes are restricted to recruiting staff."""
    perms = getattr(ctx, "perms", {}) or {}
    return bool(
        perms.get("can_manage_applications")
        or perms.get("can_promote")
        or perms.get("is_admin")
    )


def can_access_melonly(ctx) -> bool:
    """Return whether the member may read the Melonly player records.

    Keep this read gate aligned with the permission set used by the Melonly page.
    Per-action write permissions remain enforced separately by can_manage_melonly_entry.
    """
    perms = getattr(ctx, "perms", {}) or {}
    return bool(
        perms.get("is_admin")
        or any(perms.get(key) for key in (
            "can_warn", "can_moderate_kicks", "can_moderate_bans",
            "can_moderate_bolo", "can_add_notes",
        ))
    )



_MELONLY_REPOSITORY_CACHE = None
_MELONLY_REPOSITORY_CACHE_KEY = None


def get_melonly_repository():
    """Return the process-wide Melonly repository without changing or migrating data.

    Reusing one adapter lets the JSON backend keep its file-signature-validated
    snapshot and search indexes between dashboard requests. A changed data path,
    loader/saver, lock or backend setting creates a fresh adapter (important for
    test isolation and explicit backend selection).
    """
    global _MELONLY_REPOSITORY_CACHE, _MELONLY_REPOSITORY_CACHE_KEY
    key = (
        os.path.abspath(os.fspath(LOGS_FILE)),
        os.path.abspath(os.fspath(pulse_db.DB_PATH)),
        id(load_json),
        id(save_json),
        id(_io_lock),
        str(os.environ.get("PULSE_LOGS_BACKEND", "") or "").strip().lower(),
    )
    with _io_lock:
        if _MELONLY_REPOSITORY_CACHE is None or key != _MELONLY_REPOSITORY_CACHE_KEY:
            from pulse_melonly_repository import MelonlyRepository
            _MELONLY_REPOSITORY_CACHE = MelonlyRepository(
                json_path=LOGS_FILE,
                db_path=pulse_db.DB_PATH,
                load_json=load_json,
                save_json=save_json,
                io_lock=_io_lock,
            )
            _MELONLY_REPOSITORY_CACHE_KEY = key
        return _MELONLY_REPOSITORY_CACHE


def can_manage_melonly_entry(ctx, entry: dict, target_type: str | None = None) -> bool:
    """Enforce per-action Melonly permissions in UI and every write endpoint."""
    perms = ctx.perms
    manager = bool(perms.get("is_admin"))
    options = {
        "can_warn": bool(perms.get("can_warn")),
        "can_add_notes": bool(perms.get("can_add_notes")),
        "can_moderate_kicks": bool(perms.get("can_moderate_kicks")),
        "can_moderate_bans": bool(perms.get("can_moderate_bans")),
        "can_moderate_bolo": bool(perms.get("can_moderate_bolo")),
        "manager": manager,
    }
    existing_type = str(entry.get("type") or "Log")
    if not can_manage_melonly_type(existing_type, **options):
        return False
    if target_type is not None and not can_manage_melonly_type(target_type, **options):
        return False
    return True


def csv_safe(value) -> str:
    """Schützt vor Excel-Formel-Injection (=, +, -, @)."""
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@app.get("/export/logs")
async def export_logs(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm="can_view_analytics")
    # AUTHZ-8: the export contains every Melonly record (notes, bans, BOLOs) -> same read gate as the Melonly page.
    if not can_access_melonly(ctx):
        raise HTTPException(status_code=403, detail="Daf\u00fcr fehlt dir die Berechtigung.")
    output = io.StringIO()
    output.write("\ufeff")  # BOM: Excel zeigt Umlaute korrekt an
    writer = csv.writer(output, delimiter=';')
    writer.writerow(["ID", "Datum", "Typ", "Ziel-Nutzer", "Roblox ID", "Moderator", "Grund"])
    for log in get_melonly_repository().iter_all():
        writer.writerow([csv_safe(log.get(k)) for k in
                         ("id", "created_at", "type", "target_user", "roblox_id", "moderator", "reason")])
    filename = f"punishment_logs_{now_de().strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


# =============================================================
# ACTIVITY CHECK: STATUS DIREKT IN DER TEAMLISTE
# =============================================================
def get_activity_today(guild_id: int):
    """Lädt den heutigen Activity Check inklusive des eingefrorenen Teilnehmer-Snapshots."""
    today = now_de().date().isoformat()
    empty = {"check_id": None, "check_date": today, "confirmed": set(), "eligible": None}
    if not os.path.exists(ACTIVITY_DB):
        return empty
    try:
        with closing(sqlite3.connect(ACTIVITY_DB)) as conn, conn:
            row = conn.execute(
                "SELECT id FROM checks WHERE guild_id=? AND check_date=?",
                (guild_id, today),
            ).fetchone()
            if not row:
                return empty

            check_id = int(row[0])
            confirmed = {
                int(x[0])
                for x in conn.execute(
                    "SELECT user_id FROM responses WHERE check_id=?",
                    (check_id,),
                ).fetchall()
            }
            snapshot_rows = conn.execute(
                "SELECT user_id FROM check_members WHERE check_id=?",
                (check_id,),
            ).fetchall()
            columns = {str(x[1]) for x in conn.execute("PRAGMA table_info(checks)").fetchall()}
            snapshot_version = 0
            if "snapshot_version" in columns:
                version_row = conn.execute(
                    "SELECT snapshot_version FROM checks WHERE id=?", (check_id,)
                ).fetchone()
                snapshot_version = int(version_row[0] or 0) if version_row else 0
            # Empty set means a new check intentionally recorded an empty snapshot;
            # None is reserved for legacy checks that predate immutable snapshots.
            eligible = (
                {int(x[0]) for x in snapshot_rows}
                if snapshot_rows
                else (set() if snapshot_version >= 1 else None)
            )
            return {
                "check_id": check_id,
                "check_date": today,
                "confirmed": confirmed,
                "eligible": eligible,
                "snapshot_version": snapshot_version,
            }
    except sqlite3.Error as exc:
        print(f"Activity-Check-Status konnte Datenbank nicht lesen: {type(exc).__name__}: {exc}")
        return empty


