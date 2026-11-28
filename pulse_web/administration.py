"""Setup, security and administrative settings/actions."""

from pulse_web._compat import bind_legacy_namespace as _bind_legacy_namespace
_bind_legacy_namespace(globals())
del _bind_legacy_namespace

@app.get("/setup", response_class=HTMLResponse)
async def setup_wizard(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    import pulse_system_checks
    checks = {
        name: (ok, detail)
        for name, ok, detail in pulse_system_checks.run(
            getattr(request.app.state, "bot", None), ctx.guild, ctx.config
        )
    }

    def check_ok(*names: str) -> bool:
        return all(checks.get(name, (False, "Prüfung fehlt"))[0] for name in names)

    def details(*names: str) -> str:
        return " · ".join(checks.get(name, (False, "Prüfung fehlt"))[1] for name in names)

    bot_ready = bool(getattr(getattr(request.app.state, "bot", None), "is_ready", lambda: False)())
    has_roles = bool(ctx.config.get("team_role_ids"))
    automod = ctx.config.get("pulse_automod", {})
    antinuke = ctx.config.get("pulse_antinuke", {})
    automod = automod if isinstance(automod, dict) else {}
    antinuke = antinuke if isinstance(antinuke, dict) else {}
    moderation_check_names = {
        "AutoMod · Nachrichteninhalt", "AutoMod · Nachrichten löschen",
        "Anti-Nuke · Audit-Log", "Anti-Nuke · Sperren",
    }
    moderation_checks = {name: value for name, value in checks.items() if name in moderation_check_names}

    def enabled_checks_ready(prefix: str) -> bool:
        relevant = [ok for name, (ok, _) in moderation_checks.items() if name.startswith(prefix)]
        return bool(relevant) and all(relevant)

    auto_ready = (not automod.get("enabled")) or enabled_checks_ready("AutoMod")
    nuke_ready = (not antinuke.get("enabled")) or enabled_checks_ready("Anti-Nuke")
    backup = full_backup_status()
    restore_test = backup.get("restore_test") or {}
    backup_ready = bool(backup.get("latest") and backup.get("total_ok") and restore_test.get("ok"))

    steps = [
        ("Discord & Anmeldung", check_ok(".env-Pflichtwerte") and bot_ready,
         details(".env-Pflichtwerte", "Gateway-Latenz"), "/system",
         "Discord-Token, OAuth-Werte und Bot-Verbindung prüfen."),
        ("Teamrollen & Dashboard-Rechte", has_roles and check_ok("Rollenhierarchie"),
         "Teamrollen sind konfiguriert." if has_roles else "Noch keine Teamrollen ausgewählt.",
         "/settings", "Teamrollen zuordnen und die Bot-Rolle über den verwalteten Rollen platzieren."),
        ("AutoMod", auto_ready,
         "Aktiv und Bot-Berechtigungen vorhanden." if automod.get("enabled") else "Optional: noch nicht aktiviert.",
         "/admin/security", "Regeln, Ausnahmen und Nachrichtenverwaltungsrechte prüfen."),
        ("Anti-Nuke", nuke_ready,
         "Aktiv und Audit-/Sperrberechtigungen vorhanden." if antinuke.get("enabled") else "Optional: noch nicht aktiviert.",
         "/admin/security", "Ereignisse, Vertrauensrollen, Schwelle und Bot-Berechtigungen prüfen."),
        ("Backups & Wiederherstellung", backup_ready,
         f"{backup.get('count', 0)} Sicherungen · letzter Restore-Test {'bestanden' if restore_test.get('ok') else 'noch ausstehend'}",
         "/backups", "Erstes Komplett-Backup und den automatischen Restore-Test sicherstellen."),
    ]
    completed = sum(1 for _, ready, _, _, _ in steps if ready)
    rows = "".join(
        f'<a href="{href}" class="block rounded-2xl border border-slate-200 dark:border-slate-800 p-4 hover:border-indigo-400 focus-visible:ring-2">'
        f'<div class="flex flex-wrap items-center justify-between gap-2"><h2 class="font-bold">{esc(name)}</h2>'
        f'<span class="text-xs font-semibold {"text-emerald-600" if ready else "text-amber-600"}">'
        f'{"Erledigt" if ready else ("Optional" if name in {"AutoMod", "Anti-Nuke"} and not (automod.get("enabled") if name == "AutoMod" else antinuke.get("enabled")) else "Nächster Schritt")}</span></div>'
        f'<p class="mt-2 text-sm text-slate-500">{esc(detail)}</p><p class="mt-2 text-xs">{esc(action)}</p></a>'
        for name, ready, detail, href, action in steps
    )
    body = f"""
    <div class="pulse-topbar">
        <div><div class="pulse-section-title">🧭 Pulse Einrichtungsassistent</div>
        <div class="pulse-section-sub">Sichere, schrittweise Inbetriebnahme – ohne Einstellungen automatisch zu verändern.</div></div>
        <a href="/system" class="{BTN}">Vollständige Diagnose</a>
    </div>
    <div class="{CARD} mb-5 p-5">
        <div class="flex items-center justify-between gap-4"><div><div class="font-bold">Einrichtungsfortschritt</div>
        <p class="mt-1 text-sm text-slate-500">{completed} von {len(steps)} Schritten bereit (optionale Schutzfunktionen sind nicht erforderlich).</p></div>
        <span class="text-2xl font-bold text-indigo-600">{round(completed / len(steps) * 100)}%</span></div>
        <div class="mt-4 h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700" role="progressbar" aria-label="Einrichtungsfortschritt" aria-valuemin="0" aria-valuemax="{len(steps)}" aria-valuenow="{completed}">
            <div class="h-full rounded-full bg-indigo-600" style="width:{completed / len(steps) * 100:.0f}%"></div>
        </div>
    </div>
    <div class="grid gap-3 md:grid-cols-2">{rows}</div>
    """
    return HTMLResponse(render_page("Einrichtungsassistent", ctx, "setup", body))


@app.get("/security")
async def security_page(request: Request, user_session: str = Cookie(None)):
    # The full AutoMod/Anti-Nuke management lives in Verwaltung → Sicherheit (pulse_guard_admin.py).
    auth(request, user_session, perm=None, admin=True)
    return RedirectResponse(url="/admin/security", status_code=303)


@app.post("/security")
async def save_security_settings(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    form = await request.form()

    def parse_ids(field: str, label: str) -> list[int]:
        values = [value for value in re.split(r"[\s,;]+", str(form.get(field) or "").strip()) if value]
        if len(values) > 50 or any(not value.isdigit() or int(value) <= 0 for value in values):
            raise ValueError(f"{label}: Bitte ausschließlich bis zu 50 gültige positive IDs angeben.")
        return list(dict.fromkeys(int(value) for value in values))

    def parse_lines(field: str, label: str, *, domains: bool = False) -> list[str]:
        values = list(dict.fromkeys(line.strip().casefold() for line in str(form.get(field) or "").splitlines() if line.strip()))
        if len(values) > 100 or any(len(value) > 100 for value in values):
            raise ValueError(f"{label}: Maximal 100 Einträge mit höchstens 100 Zeichen sind erlaubt.")
        if domains:
            from pulse_moderation import valid_domain
            if any(not valid_domain(value) for value in values):
                raise ValueError(f"{label}: Bitte gültige Hostnamen wie example.com angeben, keine URLs oder Wildcards.")
        return values

    try:
        rules = sorted(set(form.getlist("automod_rules")) & {"spam", "mentions", "invites", "keywords", "domains"})
        events = sorted(set(form.getlist("antinuke_events")) & {
            "ban", "kick", "role_delete", "role_update", "channel_delete", "channel_update",
            "webhook_create", "webhook_delete", "webhook_update", "bot_add",
        })
        mention_limit = int(form.get("mention_limit", 5))
        action_limit = int(form.get("action_limit", 3))
        window_seconds = int(form.get("window_seconds", 10))
        if not 3 <= mention_limit <= 30:
            raise ValueError("Die Erwähnungsgrenze muss zwischen 3 und 30 liegen.")
        if not 2 <= action_limit <= 10:
            raise ValueError("Die Anti-Nuke-Schwelle muss zwischen 2 und 10 Aktionen liegen.")
        if not 5 <= window_seconds <= 60:
            raise ValueError("Das Anti-Nuke-Zeitfenster muss zwischen 5 und 60 Sekunden liegen.")
        alert_raw = str(form.get("alert_channel_id") or "").strip()
        alert_channel_id = int(alert_raw) if alert_raw.isdigit() else 0
        if alert_raw and not alert_channel_id:
            raise ValueError("Die Alarmkanal-ID muss eine positive Discord-ID sein.")
        if alert_channel_id and ctx.guild.get_channel(alert_channel_id) is None:
            raise ValueError("Der Alarmkanal wurde auf diesem Discord-Server nicht gefunden.")
        automod_enabled = bool(form.get("automod_enabled"))
        antinuke_enabled = bool(form.get("antinuke_enabled"))
        if automod_enabled and not rules:
            raise ValueError("Aktiviere mindestens eine AutoMod-Regel oder deaktiviere AutoMod.")
        if antinuke_enabled and not events:
            raise ValueError("Wähle mindestens ein Anti-Nuke-Ereignis oder deaktiviere Anti-Nuke.")
        automod = {
            "enabled": automod_enabled,
            "rules": rules,
            "mention_limit": mention_limit,
            "blocked_terms": parse_lines("blocked_terms", "Wortfilter"),
            "blocked_domains": parse_lines("blocked_domains", "Linkfilter", domains=True),
            "exempt_role_ids": parse_ids("automod_exempt_role_ids", "AutoMod-Ausnahmen"),
            "exempt_channel_ids": parse_ids("automod_exempt_channel_ids", "AutoMod-Ausnahmen"),
            "alert_channel_id": alert_channel_id,
        }
        antinuke = {
            "enabled": antinuke_enabled,
            "events": events,
            "action_limit": action_limit,
            "window_seconds": window_seconds,
            "trusted_role_ids": parse_ids("trusted_role_ids", "Vertrauensrollen"),
            "trusted_user_ids": parse_ids("trusted_user_ids", "Freigestellte Nutzer"),
            "alert_channel_id": alert_channel_id,
        }
    except (TypeError, ValueError) as exc:
        return back("/admin/security", str(exc), False)

    # Compatibility form: it only knows the original fields – keep everything else set at /admin/security
    # (mode, response, escalation, …) instead of silently resetting it to the defaults.
    from pulse_moderation import normalize_antinuke, normalize_automod
    previous_automod = ctx.config.get("pulse_automod") if isinstance(ctx.config.get("pulse_automod"), dict) else {}
    previous_antinuke = ctx.config.get("pulse_antinuke") if isinstance(ctx.config.get("pulse_antinuke"), dict) else {}
    ctx.config["pulse_automod"] = normalize_automod({**previous_automod, **automod})
    if "profanity" in (previous_automod.get("rules") or []):  # the compatibility form cannot edit it – never switch it off silently
        ctx.config["pulse_automod"]["rules"] = sorted({*ctx.config["pulse_automod"]["rules"], "profanity"})
    ctx.config["pulse_antinuke"] = normalize_antinuke({**previous_antinuke, **antinuke})
    if isinstance(previous_antinuke.get("rules"), list):  # keep individual rules exactly as stored (also invalid ones)
        ctx.config["pulse_antinuke"]["rules"] = previous_antinuke["rules"]
    ctx.config["pulse_antinuke"]["revision"] = ctx.config["pulse_antinuke"]["revision"] + 1
    save_json(CONFIG_FILE, ctx.config)
    log_audit(
        ctx.user.get("global_name") or ctx.user.get("username"),
        ctx.user["id"],
        "AutoMod/Anti-Nuke Konfiguration geändert",
        f"AutoMod={'aktiv' if automod_enabled else 'aus'} · Anti-Nuke={'aktiv' if antinuke_enabled else 'aus'}",
    )
    return back("/admin/security", "AutoMod- und Anti-Nuke-Einstellungen gespeichert.")


# =============================================================
# ROUTE: EINSTELLUNGEN, RECHTE & AUDIT-LOG (nur Admins)
# =============================================================
@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm=None, admin=True)
    guild, config = ctx.guild, ctx.config
    team_role_ids = config.get("team_role_ids", [])
    perms_cfg = config.get("permissions", {})
    weekly_goal = float(config.get("weekly_goal_hours", 3.0))

    role_boxes = "".join(f"""
        <label class="flex items-center gap-2 cursor-pointer text-xs py-1">
            <input type="checkbox" name="team_roles" value="{r.id}" {'checked' if r.id in team_role_ids else ''} class="rounded">
            <span class="font-semibold" style="color:{role_hex(r)};">{esc(r.name)}</span>
        </label>""" for r in sorted(guild.roles, key=lambda r: -r.position) if not r.is_default() and not r.managed)

    perm_cards = ""
    for rid in team_role_ids:
        role = guild.get_role(rid)
        if not role:
            continue
        rp = perms_cfg.get(str(rid), {"can_view_dashboard": True})
        def cb(name, label):
            return (f'<label class="flex items-center gap-2 cursor-pointer text-slate-700 dark:text-slate-300">'
                    f'<input type="checkbox" name="{name}" {"checked" if rp.get(name) else ""} class="rounded"><span>{label}</span></label>')
        perm_cards += f"""
        <div class="{CARD} p-5 space-y-4">
            <div class="flex justify-between items-center border-b border-slate-100 dark:border-slate-800 pb-2.5">
                <span class="font-bold text-sm" style="color:{role_hex(role)};">{esc(role.name)}</span>
                <span class="text-[10px] text-slate-400 font-mono">ID: {role.id}</span>
            </div>
            <form action="/action" method="post" class="grid grid-cols-2 gap-3 text-xs">
                <input type="hidden" name="action" value="save_role_permissions"><input type="hidden" name="role_id" value="{role.id}">
                {cb('can_view_dashboard', 'Dashboard sehen')}{cb('can_warn', 'Verwarnen')}
                {cb('can_promote', 'Befördern/Degradieren/Kicken')}{cb('can_add_notes', 'Notizen erstellen')}
                {cb('can_manage_tickets', 'Tickets verwalten')}{cb('can_manage_applications', 'Bewerbungen verwalten')}
                {cb('can_manage_tasks', 'Aufgaben verwalten')}{cb('can_manage_training', 'Schulungen verwalten')}
                {cb('can_manage_wiki', 'Wiki verwalten')}{cb('can_view_analytics', 'Statistiken sehen')}
                <button class="col-span-2 mt-2 {BTN} py-2.5">Rechte Speichern</button>
            </form>
        </div>"""

    # Interne Dashboard-Profile werden echten Discord-Teamrollen zugeordnet.
    dashboard_role_cfg = config.get("dashboard_roles", {})

    def dashboard_permission_boxes(profile_permissions, default_dashboard=False):
        return "".join(
            f'<label class="flex items-start gap-2 text-xs text-slate-700 dark:text-slate-300">'
            f'<input type="checkbox" name="{key}" {"checked" if (profile_permissions.get(key) or (default_dashboard and key == "can_view_dashboard")) else ""} class="rounded mt-0.5">'
            f'<span>{esc(label)}</span></label>'
            for key, label in DASHBOARD_PERMISSION_LABELS
        )

    def dashboard_discord_role_boxes(selected_role_ids):
        selected = {int(value) for value in selected_role_ids if str(value).isdigit()}
        role_items = []
        for rid in team_role_ids:
            role = guild.get_role(int(rid))
            if not role or role.is_default() or role.managed:
                continue
            role_items.append(
                f'<label class="flex items-start gap-2 text-xs text-slate-700 dark:text-slate-300">'
                f'<input type="checkbox" name="dashboard_discord_role_ids" value="{role.id}" '
                f'{"checked" if role.id in selected else ""} class="rounded mt-0.5">'
                f'<span><span class="font-semibold">{esc(role.name)}</span> '
                f'<span class="text-slate-400">({role.id})</span></span></label>'
            )
        return "".join(role_items) or "<p class='text-xs text-slate-400'>Zuerst unter Team-Rollen mindestens eine Discord-Teamrolle konfigurieren.</p>"

    dashboard_role_cards = ""
    for dashboard_role_key, profile in sorted(dashboard_role_cfg.items(), key=lambda item: str(item[1].get("name", item[0]) if isinstance(item[1], dict) else item[0]).casefold()):
        if not isinstance(profile, dict):
            continue
        profile_permissions = profile.get("permissions", {})
        dashboard_role_name = str(profile.get("name") or dashboard_role_key)
        mapped_role_ids = profile.get("discord_role_ids", [])
        mapped_role_names = [
            guild.get_role(int(rid)).name
            for rid in mapped_role_ids
            if str(rid).isdigit() and guild.get_role(int(rid))
        ]
        dashboard_role_cards += f"""
        <div class="{CARD} p-5 space-y-4">
            <form action="/action" method="post" class="space-y-3">
                <input type="hidden" name="action" value="save_dashboard_role">
                <input type="hidden" name="dashboard_role_id" value="{esc(dashboard_role_key)}">
                <label class="block text-xs font-semibold">Name des digitalen Profils
                    <input required maxlength="40" name="dashboard_role_name" value="{esc(dashboard_role_name)}" class="{INPUT} w-full mt-1">
                </label>
                <div class="space-y-2">
                    <p class="text-xs font-bold">Discord-Teamrollen, die dieses Profil erhalten</p>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{dashboard_discord_role_boxes(mapped_role_ids)}</div>
                </div>
                <div class="space-y-2">
                    <p class="text-xs font-bold">Dashboard-Berechtigungen</p>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{dashboard_permission_boxes(profile_permissions)}</div>
                </div>
                <button class="{BTN} w-full py-2.5">Profil & Zuordnung speichern</button>
            </form>
            <p class="text-[11px] text-slate-500">Aktuell zugeordnet: {esc(", ".join(mapped_role_names) if mapped_role_names else "Keine Discord-Teamrolle")}</p>
            <form action="/action" method="post" onsubmit="return confirm('Dieses digitale Dashboard-Profil wirklich löschen?')">
                <input type="hidden" name="action" value="delete_dashboard_role">
                <input type="hidden" name="dashboard_role_id" value="{esc(dashboard_role_key)}">
                <button class="w-full rounded-xl border border-rose-300 dark:border-rose-900 text-rose-600 dark:text-rose-400 px-3 py-2 text-xs font-semibold">Digitales Profil löschen</button>
            </form>
        </div>"""

    dashboard_new_role_form = f"""
    <div class="{CARD} p-5 space-y-4">
        <h3 class="font-bold text-sm">➕ Neues digitales Dashboard-Profil</h3>
        <form action="/action" method="post" class="space-y-3">
            <input type="hidden" name="action" value="save_dashboard_role">
            <label class="block text-xs font-semibold">Profilname
                <input required maxlength="40" name="dashboard_role_name" placeholder="z. B. Support, Personalverwaltung, Leitung" class="{INPUT} w-full mt-1">
            </label>
            <div class="space-y-2">
                <p class="text-xs font-bold">Zugeordnete Discord-Teamrollen</p>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{dashboard_discord_role_boxes([])}</div>
            </div>
            <div class="space-y-2">
                <p class="text-xs font-bold">Dashboard-Berechtigungen</p>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">{dashboard_permission_boxes({}, default_dashboard=True)}</div>
            </div>
            <p class="text-[11px] text-slate-500">Speichern legt eine digitale Rolle in Pulse an. Es wird keine neue Discord-Rolle erstellt oder verändert.</p>
            <button class="{BTN} w-full py-2.5">Digitales Profil erstellen</button>
        </form>
    </div>"""

    dashboard_role_section = f"""
    <section class="{CARD} p-6 space-y-5">
        <div>
            <h2 class="text-lg font-bold text-slate-900 dark:text-white">🧩 Digitale Dashboard-Rollen</h2>
            <p class="text-xs text-slate-500 mt-1">Diese Rollen existieren nur in Pulse. Du erstellst ein Profil, wählst seine Rechte und ordnest ihm eine oder mehrere bereits konfigurierte Discord-Teamrollen zu. Alle Mitglieder dieser Discord-Rollen erhalten das Profil automatisch. Discord-Rollen, Serverrechte und Rollenhierarchie werden nicht verändert.</p>
        </div>
        {dashboard_new_role_form}
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">{dashboard_role_cards or "<p class='text-xs text-slate-400 italic'>Noch keine digitalen Dashboard-Profile angelegt.</p>"}</div>
    </section>"""

    audit = load_json(AUDIT_FILE, [])
    audit_html = "".join(f"""
        <div class="audit-row bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-800 rounded-xl p-3 text-xs flex flex-col sm:flex-row sm:justify-between sm:items-center gap-1" data-search="{esc((str(e.get('actor')) + ' ' + str(e.get('action')) + ' ' + str(e.get('details'))).lower())}">
            <div class="break-words"><span class="font-bold text-slate-900 dark:text-white">{esc(e.get('actor'))}</span>
                <span class="text-indigo-600 dark:text-indigo-400 font-semibold px-2">[{esc(e.get('action'))}]</span>
                <span class="text-slate-600 dark:text-slate-300">{esc(e.get('details'))}</span></div>
            <span class="text-[10px] text-slate-400 font-mono shrink-0">{esc(e.get('timestamp'))}</span>
        </div>""" for e in reversed(audit[-100:]))

    head = """<script>function filterAudit(){const q=document.getElementById('auditSearch').value.toLowerCase();
        document.querySelectorAll('.audit-row').forEach(r=>r.style.display=r.dataset.search.includes(q)?'':'none');}</script>"""
    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-2">Einstellungen & Panel-Audit-Log</h1>
    <p class="text-xs text-slate-500 dark:text-slate-400 mb-6">Server-ID: <code class="text-indigo-600 dark:text-indigo-400 font-mono">{GUILD_ID}</code></p>
    <div class="space-y-8">
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div class="{CARD} p-6 space-y-3">
                <h2 class="text-base font-bold text-slate-900 dark:text-white">🎯 Wochenziel</h2>
                <form action="/action" method="post" class="flex gap-2 items-center text-xs">
                    <input type="hidden" name="action" value="set_weekly_goal">
                    <input type="number" step="0.5" min="0.5" max="100" name="weekly_goal" aria-label="Wochenziel in Stunden" value="{weekly_goal:g}" class="{INPUT} w-28"> <span>Stunden pro Woche</span>
                    <button class="{BTN} px-4 py-3">Speichern</button>
                </form>
            </div>
            <div class="{CARD} p-6 space-y-3">
                <h2 class="text-base font-bold text-slate-900 dark:text-white">👥 Team-Rollen</h2>
                <p class="text-[11px] text-slate-500">Die Reihenfolge der Beförderungen richtet sich nach der Rollen-Position im Server (niedrig → hoch).</p>
                <form action="/action" method="post" class="space-y-2">
                    <input type="hidden" name="action" value="save_team_roles">
                    <div class="max-h-48 overflow-y-auto grid grid-cols-1 sm:grid-cols-2">{role_boxes}</div>
                    <button class="{BTN} px-4 py-2.5 text-xs">Team-Rollen speichern</button>
                </form>
            </div>
        </div>
        <div>
            <h2 class="text-lg font-bold text-slate-900 dark:text-white mb-1">Rollen-Berechtigungen</h2>
            <p class="text-[11px] text-slate-500 mb-4">Admins/Server-Owner haben immer alle Rechte. Neue Team-Rollen dürfen standardmäßig nur das Dashboard sehen.</p>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">{perm_cards or "<p class='text-xs text-slate-400 italic'>Keine Team-Rollen konfiguriert.</p>"}</div>
        </div>
        {dashboard_role_section}
        <div class="{CARD} p-6 space-y-4">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <h2 class="text-lg font-bold text-slate-900 dark:text-white">📜 Panel-Audit-Log (letzte 100)</h2>
                <input type="text" id="auditSearch" oninput="filterAudit()" placeholder="🔎 Filtern..." class="bg-slate-50 dark:bg-[#0b0e14] border border-slate-200 dark:border-slate-700 rounded-xl px-3 py-2 text-xs w-full sm:w-56">
            </div>
            <div class="space-y-2 max-h-96 overflow-y-auto pr-1">{audit_html or "<p class='text-xs text-slate-400 italic'>Keine Audit-Einträge vorhanden.</p>"}</div>
        </div>
    </div>"""
    return render_page("Einstellungen & Rechte", ctx, "settings", body, head)


def normalize_dashboard_action(action: str) -> str:
    """Map legacy dashboard ``kick`` forms to the safe, team-only action."""
    return "team_kick" if action == "kick" else action


# =============================================================
# ZENTRALER ACTION-HANDLER (jede Aktion mit Rechteprüfung)
# =============================================================
ACTION_PERMS = {
    "promote": "can_promote", "demote": "can_promote", "team_kick": "can_promote", "hire": "can_promote",
    "warn_with_proof": "can_manage_team_warnings", "remove_warn": "can_manage_team_warnings", "add_note": "can_add_notes",
    "submit_loa": "can_view_dashboard", "cancel_loa": "can_view_dashboard",
    "vote_app": "can_view_dashboard", "decide_app": "can_manage_applications",
    "meeting_rsvp": "can_view_dashboard", "add_meeting_topic": "can_view_dashboard",
    "delete_meeting_topic": "can_view_dashboard", "set_meeting_info": "can_promote",
    "reset_support_case_period": "can_promote",
    "save_role_permissions": "admin", "set_weekly_goal": "admin", "save_team_roles": "admin",
    "save_dashboard_role": "admin", "delete_dashboard_role": "admin",
}



def user_entry(team_db: dict, key: str) -> dict:
    entry = team_db.setdefault(key, {})
    for k, v in (("warns_list", []), ("notes", []), ("ticket_cases", 0), ("support_cases", 0)):
        entry.setdefault(k, v)
    normalize_warns(entry)
    return entry


def can_see_warnings(perms: dict, viewer_id, target_id) -> bool:
    """E-3 / D-08: team warnings (incl. count and reason) only for the person themself, can_warn and admins."""
    perms = perms or {}
    return str(viewer_id) == str(target_id) or bool(perms.get("can_warn") or perms.get("can_manage_team_warnings") or perms.get("is_admin"))


def can_see_notes(perms: dict) -> bool:
    """E-3 / D-08: internal team notes only for can_add_notes, leadership (can_promote) and admins."""
    perms = perms or {}
    return bool(perms.get("can_add_notes") or perms.get("can_promote") or perms.get("is_admin"))


def commit_member_entry(uid, before: dict, ours: dict) -> dict:
    """K-04: merge our changes of one member into the *current* team_data.json (no await inside)."""
    from pulse_store import merge_entry
    with _io_lock:
        team_db = load_json(DATA_FILE, {})
        if not isinstance(team_db, dict):
            team_db = {}
        merged = merge_entry(before, ours, user_entry(team_db, str(uid)))
        team_db[str(uid)] = merged
        save_json(DATA_FILE, team_db)
        return merged


def track_member_entry(uid, entry: dict):
    """Snapshot of a member entry; `.commit()` writes only our changes back (see pulse_store)."""
    from pulse_store import EntryTracker
    return EntryTracker(uid, entry, commit_member_entry)


def commit_shift_event(event: dict) -> None:
    """K-04: store one changed history event without writing back a stale copy of all shifts."""
    event_id = event.get("shift_event_id")
    identity = (event.get("mod_id"), event.get("started_at_iso"), event.get("ended_at_iso"))
    with _io_lock:
        shifts_db = load_json(SHIFTS_FILE, {})
        history = shifts_db.setdefault("history", [])
        for pos, item in enumerate(history):
            if not isinstance(item, dict):
                continue
            same = (item.get("shift_event_id") == event_id) if event_id else (
                (item.get("mod_id"), item.get("started_at_iso"), item.get("ended_at_iso")) == identity)
            if same:
                history[pos] = dict(event)
                break
        else:
            return
        save_json(SHIFTS_FILE, shifts_db)


def team_since_for(member, entry=None, apps=None) -> str:
    """Resolve team entry date without depending on complete application records.

    Priority: explicit team_since, independent employment event, legacy accepted/hired
    application timestamps, then Discord joined_at. Lists can pass a preloaded apps snapshot.
    """
    entry = entry if isinstance(entry, dict) else {}
    raw = str(entry.get("team_since") or "").strip()
    if raw:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%d.%m.%Y %H:%M"):
            try:
                return datetime.strptime(raw, fmt).strftime("%d.%m.%Y")
            except Exception:
                pass

    event_hired_at = employment_hired_at(entry, member.id)
    if event_hired_at:
        try:
            event_timestamp = parse_timestamp(event_hired_at)
            if event_timestamp is not None:
                return display(event_timestamp, with_time=False)
        except Exception:
            pass

    try:
        if callable(apps):
            apps = apps()
        if apps is None:
            apps = load_json(APPS_FILE, {})
        candidates = []
        for item in apps.values() if isinstance(apps, dict) else []:
            if str(item.get("user_id")) != str(member.id) or str(item.get("status")) not in {"hired", "accepted"}:
                continue
            decided = parse_timestamp(item.get("decided_at"))  # D-09: every stored format
            if decided is not None:
                candidates.append(decided)
        if candidates:
            return display(min(candidates), with_time=False)  # Berlin date
    except Exception:
        pass

    return member.joined_at.strftime("%d.%m.%Y") if member.joined_at else "unbekannt"


def migrate_warning_data():
    """Normalisiert bestehende Warn-Datensätze einmalig beim Start."""
    team_db = load_json(DATA_FILE, {})
    if not isinstance(team_db, dict):
        return
    changed = False
    for entry in team_db.values():
        if isinstance(entry, dict):
            changed = normalize_warns(entry) or changed
    if changed:
        save_json(DATA_FILE, team_db)


migrate_warning_data()


# ---------- BOT-05: what follows a rank change (dashboard and slash commands) ----------
async def finish_promotion(guild, *, member, previous_role, new_role, probation_days: int, actor_name: str,
                           actor_id: str, reason: str, source: str = "Dashboard") -> dict:
    """After the roles were changed: probation entry (3 or 7 days, kept history), DM, team update, audit.
    Without a previous team role there is nothing to fall back to, so no probation is recorded."""
    probation_days = int(probation_days)
    probation_started = now_de()
    probation_expires = probation_started + timedelta(days=probation_days)
    if previous_role is not None:
        team_db = load_json(DATA_FILE, {})
        team_entry = user_entry(team_db, str(member.id))
        tracker = track_member_entry(member.id, team_entry)  # K-04
        previous_probation = team_entry.get("probation")
        if isinstance(previous_probation, dict) and previous_probation.get("active"):
            history = team_entry.setdefault("probation_history", [])
            history.append(dict(previous_probation))
            team_entry["probation_history"] = history[-10:]
        team_entry["probation"] = {
            "active": True,
            "status": "active",
            "days": probation_days,
            "previous_role_id": previous_role.id,
            "new_role_id": new_role.id,
            "started_at": probation_started.isoformat(timespec="seconds"),
            "expires_at": probation_expires.isoformat(timespec="seconds"),
            "promoted_by": actor_name,
            "promoted_by_id": actor_id,
            "reason": reason,
        }
        tracker.commit()
    until = probation_expires.strftime("%d.%m.%Y %H:%M")
    probation_text = f"{probation_days} Tage · bis {until}" if previous_role is not None else "keine (vorher keine Teamrolle)"
    await send_dm_notification(
        member,
        f"🎉 **Herzlichen Glückwunsch!** Du wurdest auf **{guild.name}** zum **{new_role.name}** befördert. "
        + (f"Deine Probezeit beträgt **{probation_days} Tage** und endet am **{until}**." if previous_role is not None else ""),
    )
    await send_team_update_embed(
        guild,
        "⬆️ Team-Update: Beförderung",
        f"{member.mention} wurde befördert.",
        _pulse_color("success"),
        target=member.mention,
        action="Beförderung",
        actor=actor_name,
        fields=[("Vorherige Rolle", previous_role.mention if previous_role is not None else "Keine", True),
                ("Neue Rolle", new_role.mention, True), ("Probezeit", probation_text, True), ("Grund", reason, False),
                ("Quelle", source, True)],
        thumbnail=member.display_avatar.url,
    )
    log_audit(actor_name, actor_id, "Beförderung",
              f"{member.display_name} -> {new_role.name} · Probezeit: {probation_text} · Grund: {reason}")
    return {"probation": previous_role is not None, "probation_text": probation_text}


async def finish_demotion(guild, *, member, previous_role, new_role, actor_name: str, actor_id: str, reason: str,
                          source: str = "Dashboard") -> None:
    """After the roles were changed: DM, team update and audit for a demotion (new_role None = no team rank left)."""
    if new_role is not None:
        await send_dm_notification(member, f"⚠️ Du wurdest auf **{guild.name}** auf die Rolle **{new_role.name}** degradiert.")
        await send_team_update_embed(
            guild,
            "⬇️ Team-Update: Degradierung",
            f"{member.mention} wurde degradiert.",
            _pulse_color("warning"),
            target=member.mention,
            action="Degradierung",
            actor=actor_name,
            fields=[("Vorherige Rolle", previous_role.mention if previous_role is not None else "Unbekannt", True),
                    ("Neue Rolle", new_role.mention, True), ("Grund", reason, False), ("Quelle", source, True)],
            thumbnail=member.display_avatar.url,
        )
        log_audit(actor_name, actor_id, "Degradierung", f"{member.display_name} -> {new_role.name} · Grund: {reason}")
        return
    await send_dm_notification(member, f"⚠️ Du wurdest aus dem Team-Rollenrang auf **{guild.name}** entfernt.")
    await send_team_update_embed(
        guild,
        "⬇️ Team-Update: Teamrolle entfernt",
        f"{member.mention} hat keine Teamrolle mehr.",
        _pulse_color("danger"),
        target=member.mention,
        action="Teamrolle entfernt",
        actor=actor_name,
        fields=[("Grund", reason, False), ("Quelle", source, True)],
        thumbnail=member.display_avatar.url,
    )
    log_audit(actor_name, actor_id, "Degradierung", f"{member.display_name} -> Keine Teamrolle · Grund: {reason}")


# ---------- BOT-05: one logic for team warnings (dashboard and slash commands) ----------
WARN_MIN_DAYS, WARN_MAX_DAYS = 1, 365


async def issue_team_warning(guild, *, actor_member, actor_name: str, actor_id: str, member, reason: str,
                             duration_days: int, proof: str = "", config: dict | None = None,
                             actor_is_admin: bool = False, source: str = "Dashboard") -> dict:
    """Checks, stores (K-04 merge), sets warn roles and consequences, posts the team update, DMs the
    person and escalates 5/5 to leadership. Returns {"ok", "message", "warn", "count", "role_report",
    "consequence_report"}; ok=False before anything was stored means the warning was refused."""
    config = config if config is not None else load_config()
    team_role_ids = config.get("team_role_ids", [])
    reason = (reason or "").strip()[:1000]  # /warn allowed 1000 before BOT-05 (review fix); the dashboard field stays at 500
    refused = lambda message: {"ok": False, "stored": False, "message": message}
    if not reason:
        return refused("Bitte einen Grund angeben.")
    try:
        duration_days = int(duration_days)
    except (TypeError, ValueError):
        duration_days = 0
    if not WARN_MIN_DAYS <= duration_days <= WARN_MAX_DAYS:
        return refused(f"Die Warn-Gültigkeit muss zwischen {WARN_MIN_DAYS} und {WARN_MAX_DAYS} Tagen liegen.")
    if member is None:
        return refused("Mitglied nicht gefunden.")
    if member.bot:
        return refused("Bots können keine Team-Verwarnung erhalten.")
    if str(member.id) == str(actor_id):
        return refused("Du kannst dir selbst keine Team-Verwarnung geben.")
    configured_team_ids = {int(value) for value in team_role_ids if str(value).isdigit()}
    if not configured_team_ids or not any(getattr(role, "id", None) in configured_team_ids for role in getattr(member, "roles", ()) or ()):
        return refused("Team-Verwarnungen können nur an Teammitglieder mit einer konfigurierten Teamrolle vergeben werden.")
    actor_rank = team_rank(actor_member, team_role_ids, guild)
    target_rank = team_rank(member, team_role_ids, guild)
    admin_actor = bool(
        actor_is_admin
        or getattr(actor_member, "id", None) == getattr(guild, "owner_id", None)
        or getattr(getattr(actor_member, "guild_permissions", None), "administrator", False)
    )
    if actor_rank < 0 and admin_actor:
        actor_rank = len(ordered_team_role_ids(guild, team_role_ids))
    if actor_rank < 0:
        return refused("Dein Teamrang konnte nicht ermittelt werden. Prüfe deine konfigurierte Teamrolle.")
    if target_rank < 0:
        return refused("Der Teamrang des Zielmitglieds konnte nicht sicher ermittelt werden. Die Verwarnung wurde nicht vergeben.")
    if not can_manage_team_warnings(guild, actor_member, team_role_ids):
        return refused("Team-Verwarnungen dürfen erst ab Rang Teamleitung vergeben werden.")
    if target_rank >= actor_rank:
        return refused("Du darfst keine Team-Warnung an ein Mitglied mit gleichem oder höherem Teamrang vergeben.")

    team_db = load_json(DATA_FILE, {})
    entry = user_entry(team_db, str(member.id))
    tracker = track_member_entry(member.id, entry)  # K-04: merge-on-save after awaits
    current_count = len(active_warns(entry))
    if current_count >= 5:
        return refused("Dieses Teammitglied hat bereits die maximale Anzahl von 5 aktiven Verwarnungen.")
    allowed_warn, protection_message = await enforce_team_warn_protection(guild, actor_member, member, config, source=source)
    if not allowed_warn:
        return refused(protection_message)
    allowed_impact, impact_message = await enforce_high_impact_warning_approval(
        guild, actor_member, member, entry, current_count, config, source=source,
    )
    if not allowed_impact:
        return refused(impact_message)

    proof_url = safe_url(proof)
    warn = {
        "id": f"warn_{uuid.uuid4().hex[:6]}", "reason": reason, "proof": proof_url,
        "by": actor_name, "date": now_de().strftime("%d.%m.%Y %H:%M"),
        "duration_days": duration_days,
        "expires_at": (now_de() + timedelta(days=duration_days)).isoformat(timespec="seconds"),
        "active": True, "revoked_at": None, "revoked_by": None, "revoked_reason": "",
    }
    entry["warns_list"].append(warn)
    count = len(active_warns(entry))
    tracker.commit()
    log_audit(actor_name, actor_id, "Verwarnung", f"User-ID {member.id} ({count}/5): {reason}")
    role_report = await sync_warn_roles(guild, member, count, config)
    consequence_report = await apply_warn_consequences(guild, member, warn, count, config, entry=entry)
    tracker.commit()
    await send_team_update_embed(
        guild,
        "⚠️ Team-Update: Verwarnung",
        f"{member.mention} hat eine neue Verwarnung erhalten.",
        _pulse_color("warning"),
        target=f"{member.mention}\nWarn-Stufe: {count}/5",
        action=f"Verwarnung {count}/5",
        actor=actor_name,
        fields=[
            ("Grund", reason, False),
            ("Beweis", proof_url or "Kein Beweis-Link", False),
            ("Gültig bis", warn["expires_at"], True),
            ("Quelle", source, True),
            ("Discord-Warnrolle", f"✅ {role_report.get('role').mention}" if role_report.get("ok") and role_report.get("role") else (f"✅ keine Warnrolle bei 0" if count == 0 else f"❌ {role_report.get('message')}"), False),
        ],
        thumbnail=member.display_avatar.url,
    )
    consequence_text = "; ".join(x for x in consequence_report.get("actions", []) if x)
    dm_suffix = f"\n**Automatische Folge:** {consequence_text}" if consequence_text else ""
    await send_dm_notification(member, f"⚠️ Du hast eine Verwarnung erhalten ({count}/5)!\n**Grund:** {reason}\n**Von:** {actor_name}{dm_suffix}")
    if count >= 5:
        # Führungskräfte erhalten zusätzlich eine Pulse-Inbox-Meldung.
        for manager in guild.members:
            if manager.bot or manager.id == member.id:
                continue
            manager_perms, _ = compute_perms(guild, manager.id, config)
            if manager_perms.get("can_promote") or manager_perms.get("is_admin"):
                try:
                    pulse_db.notify(manager.id, "🚨 5/5 Team-Warnungen",
                                    f"{member.display_name} hat 5 aktive Verwarnungen. Bitte Fall prüfen.",
                                    "warning", "/warns", f"warn-escalation:{member.id}:{count}", 86400)
                except Exception as exc:
                    print(f"Warn-Eskalationsbenachrichtigung fehlgeschlagen: {exc}")
        await send_team_update_embed(
            guild,
            "🚨 Team-Update: 5 Verwarnungen",
            f"**Mitglied:** {member.mention} ({member.display_name})\n**Status:** Automatisch aus dem Team entfernt.",
            _pulse_color("danger"),
            target=member.mention,
            action="Warn-Schwelle 5/5",
            actor=actor_name,
            fields=[("Warnrollen-Sync", "✅ Erfolgreich" if role_report.get("ok") else f"❌ {role_report.get('message')}")],
            thumbnail=member.display_avatar.url,
        )
    ok = bool(role_report.get("ok") and consequence_report.get("ok"))
    if ok:
        message = f"Verwarnung eingetragen ({count}/5). Automatische Folge ausgeführt."
    else:
        problems = []
        if not role_report.get("ok"):
            problems.append(f"Warnrolle: {role_report.get('message')}")
        if not consequence_report.get("ok"):
            problems.extend(consequence_report.get("actions", []))
        suffix = " · ".join(x for x in problems if x) or "Eine automatische Folge konnte nicht vollständig ausgeführt werden."
        message = f"Verwarnung eingetragen ({count}/5), aber: {suffix}"
    return {"ok": ok, "stored": True, "message": message, "warn": warn, "count": count,
            "role_report": role_report, "consequence_report": consequence_report}


async def revoke_team_warning(guild, *, member, warn_id: str, revoke_reason: str, actor_name: str, actor_id: str,
                              config: dict | None = None, source: str = "Team-Dashboard",
                              actor_member=None, actor_is_admin: bool = False) -> dict:
    """Withdraws one warning: marks it, lifts its temporary consequences (unless another active warning
    still justifies them), syncs warn roles, posts the team update and writes the audit."""
    config = config if config is not None else load_config()
    revoke_reason = (revoke_reason or "").strip()[:500]
    if member is None:
        return {"ok": False, "stored": False, "message": "Mitglied nicht gefunden."}
    # SEC-09: self-revocation is forbidden for everyone, including Discord administrators.
    if str(member.id) == str(actor_id):
        return {"ok": False, "stored": False, "message": "Eigene Team-Verwarnungen kannst du nicht zurückziehen."}
    team_role_ids = config.get("team_role_ids", [])
    target_rank = team_rank(member, team_role_ids, guild)
    actor_rank = team_rank(actor_member, team_role_ids, guild) if actor_member is not None else -1
    admin_actor = bool(
        actor_is_admin
        or getattr(actor_member, "id", None) == getattr(guild, "owner_id", None)
        or getattr(getattr(actor_member, "guild_permissions", None), "administrator", False)
    )
    if actor_rank < 0 and admin_actor:
        actor_rank = len(ordered_team_role_ids(guild, team_role_ids))
    # Fail closed: an unrecognised target/actor rank must never bypass the hierarchy guard.
    if actor_rank < 0:
        return {"ok": False, "stored": False,
                "message": "Dein Teamrang konnte nicht ermittelt werden. Prüfe deine konfigurierte Teamrolle."}
    if target_rank < 0:
        return {"ok": False, "stored": False,
                "message": "Für dieses Mitglied wurde keine konfigurierte Teamrolle gefunden. Die Verwarnung wurde nicht verändert."}
    if not can_manage_team_warnings(guild, actor_member, team_role_ids):
        return {"ok": False, "stored": False,
                "message": "Team-Verwarnungen dürfen erst ab Rang Teamleitung verwaltet werden."}
    if target_rank >= actor_rank:
        return {"ok": False, "stored": False,
                "message": "Du darfst keine Verwarnung eines Mitglieds mit gleichem oder höherem Teamrang zurückziehen."}
    if not revoke_reason:
        return {"ok": False, "stored": False, "message": "Bitte einen Grund für die Rücknahme der Verwarnung angeben."}
    team_db = load_json(DATA_FILE, {})
    entry = user_entry(team_db, str(member.id))
    tracker = track_member_entry(member.id, entry)  # K-04: merge-on-save after awaits
    target = next((w for w in entry["warns_list"] if str(w.get("id")) == str(warn_id)), None)
    if target is None or not target.get("active", True) or target.get("revoked_at"):
        return {"ok": False, "stored": False,
                "message": "Diese Verwarnung wurde bereits zurückgezogen oder ist nicht mehr vorhanden. Bitte die Seite aktualisieren."}
    target["active"] = False
    target["revoked_at"] = now_de().strftime("%d.%m.%Y %H:%M")
    target["revoked_by"] = actor_name
    target["revoked_reason"] = revoke_reason
    count = len(active_warns(entry))
    tracker.commit()

    original_reason = str(target.get("reason") or "Kein Grund")
    original_by = str(target.get("by") or "System")
    original_date = str(target.get("date") or "N/A")
    # Temporäre Folgen dieser konkreten Warnung beim Zurückziehen sofort entfernen.
    temp_roles = (target.get("consequences") or {}).get("temporary_roles", [])
    temp_remove_failed = False
    temp_roles_to_keep = []
    for restriction in temp_roles:
        role_id = int(restriction.get("role_id", 0) or 0)
        role = guild.get_role(role_id)
        if not role:
            continue
        # A different active warning keeps temporary punishments in place,
        # even if the warning that originally granted the role was revoked.
        other_active_warning = any(
            other is not target
            and isinstance(other, dict)
            and other.get("active", True)
            and not other.get("revoked_at")
            and not warning_is_expired(other)
            for other in entry.get("warns_list", [])
        )
        if other_active_warning:
            temp_roles_to_keep.append(restriction)
            continue
        try:
            if role in member.roles:
                if guild.me and (not guild.me.guild_permissions.manage_roles or role.position >= guild.me.top_role.position):
                    temp_remove_failed = True
                    temp_roles_to_keep.append(restriction)
                    continue
                await member.remove_roles(role, reason=f"Warn zurückgezogen · {revoke_reason}")
        except discord.Forbidden:
            temp_remove_failed = True
            temp_roles_to_keep.append(restriction)
        except Exception:
            temp_remove_failed = True
            temp_roles_to_keep.append(restriction)
    target.setdefault("consequences", {})["temporary_roles"] = temp_roles if temp_remove_failed else temp_roles_to_keep
    tracker.commit()
    role_report = await sync_warn_roles(guild, member, count, config)

    await send_team_update_embed(
        guild,
        "✅ Team-Update: Verwarnung zurückgezogen",
        f"Die Verwarnung von {member.mention} wurde durch {source} zurückgezogen.",
        _pulse_color("success"),
        target=f"{member.mention}\nAktive Warnungen: {count}/5",
        action="Warn zurückgezogen",
        actor=actor_name,
        fields=[
            ("Ursprünglicher Grund", original_reason, False),
            ("Ausgestellt von", original_by, True),
            ("Ausgestellt am", original_date, True),
            ("Rücknahmegrund", revoke_reason, False),
            ("Warn-ID", warn_id, True),
            ("Discord-Warnrolle", "✅ Synchronisiert" if role_report.get("ok") else f"❌ {role_report.get('message')}"),
        ],
        thumbnail=member.display_avatar.url,
    )
    log_audit(actor_name, actor_id, "Warn Zurückgezogen",
              f"User-ID {member.id}, Warn-ID {warn_id}, Grund: {original_reason}, Rücknahme: {revoke_reason}")
    if role_report.get("ok") and not temp_remove_failed:
        message = f"Verwarnung zurückgezogen ({count}/5)."
    elif not role_report.get("ok"):
        message = f"Verwarnung zurückgezogen ({count}/5), aber Discord-Warnrolle konnte nicht synchronisiert werden: {role_report.get('message')}"
    else:
        message = f"Verwarnung zurückgezogen ({count}/5), aber eine temporäre Sperrrolle konnte nicht entfernt werden – Rollenhierarchie prüfen."
    return {"ok": bool(role_report.get("ok")) and not temp_remove_failed, "stored": True, "message": message,
            "count": count, "role_report": role_report}


@app.post("/action")
async def handle_action(
    request: Request,
    action: str = Form(...),
    user_id: int = Form(None),
    role_id: int = Form(None),
    redirect_to_member: str = Form(None),
    warn_reason: str = Form(None),
    warn_proof: str = Form(None),
    warn_duration_days: int = Form(7),
    probation_days: int = Form(None),
    warn_id: str = Form(None),
    warn_revoke_reason: str = Form(None),
    action_reason: str = Form(None),
    note_text: str = Form(None),
    loa_start: str = Form(None),
    loa_end: str = Form(None),
    loa_reason: str = Form(None),
    target_user_id: str = Form(None),
    applicant_id: int = Form(None),
    applicant_name: str = Form(None),
    applicant_text: str = Form(None),
    career_path: str = Form(None),
    app_id: str = Form(None),
    vote: str = Form(None),
    decision: str = Form(None),
    return_to: str = Form(None),
    expected_status: str = Form(None),
    hire_role_id: int = Form(None),
    rsvp_status: str = Form(None),
    topic_title: str = Form(None),
    topic_details: str = Form(None),
    topic_id: str = Form(None),
    meeting_title: str = Form(None),
    meeting_datetime: str = Form(None),
    meeting_desc: str = Form(None),
    reset_rsvps: bool = Form(False),
    clear_topics: bool = Form(False),
    announce: bool = Form(False),
    weekly_goal: float = Form(None),
    team_roles: List[int] = Form(default=[]),
    dashboard_role_id: str = Form(None),
    dashboard_role_name: str = Form(None),
    dashboard_discord_role_ids: List[int] = Form(default=[]),
    can_view_dashboard: bool = Form(False),
    can_warn: bool = Form(False),
    can_promote: bool = Form(False),
    can_add_notes: bool = Form(False),
    can_manage_tickets: bool = Form(False),
    can_manage_applications: bool = Form(False),
    can_manage_tasks: bool = Form(False),
    can_manage_training: bool = Form(False),
    can_manage_wiki: bool = Form(False),
    can_view_analytics: bool = Form(False),
    user_session: str = Cookie(None),
    apply_session: str = Cookie(None)
):
    # Old forms may still submit action=kick; it is never a server-membership operation.
    action = normalize_dashboard_action(action)
    # The shared dashboard action route must not bypass the public recruitment release gate.
    if action == "submit_application" and not pulse_public_site.public_access_enabled():
        raise HTTPException(status_code=404, headers={"X-Robots-Tag": "noindex, nofollow"})
    # ---------- Öffentliche Bewerbung inklusive serverseitig bewerteten Laufbahn-Test ----------
    if action == "submit_application":
        ip = request.client.host if request.client else "unknown"
        # K-01: every error page leads back to the same career form; the browser draft refills it.
        return_career = known_career(career_path)
        form_url = f"/apply?career={quote(return_career)}" if return_career else "/apply"
        back_link = (f'<a href="{form_url}" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">'
                     'Zurück zum Formular – deine Antworten sind noch da</a>')
        login_link = (f'<a href="/apply/login?career={quote(return_career)}" class="px-4 py-2 rounded-xl bg-indigo-600 text-white font-semibold">'
                      'Neu mit Discord verbinden – deine Antworten bleiben erhalten</a>')
        if rate_limited(f"apply:{ip}"):
            return HTMLResponse(simple_page("⏳", "Zu viele Bewerbungen", "Bitte versuche es später erneut.", back_link), status_code=429)
        if career_path not in APPLICATION_CAREERS or not application_module_states(load_config()).get(career_path, False):
            return HTMLResponse(simple_page("⏸️", "Bewerbungsmodul geschlossen", "Für diese Laufbahn werden aktuell keine Bewerbungen angenommen.", back_link), status_code=403)
        apply_user = verify_session_token(apply_session, TOKEN_APPLY)
        if not apply_user or str(apply_user.get("id")) != str(applicant_id):
            return HTMLResponse(simple_page("🔒", "Discord-Verknüpfung fehlt",
                                            "Deine Sitzung ist abgelaufen oder fehlt. Deine Antworten sind in diesem Browser gespeichert – "
                                            "nach dem erneuten Verbinden mit Discord ist alles wieder da.", login_link), status_code=403)
        eligible, eligibility_status, eligibility_role, eligibility_message = await verify_application_member(
            request, apply_user.get("id")
        )
        if not eligible:
            title = "Bewerbung nicht freigegeben" if eligibility_status == 403 else "Mitgliedschaft gerade nicht prüfbar"
            return HTMLResponse(
                simple_page("🔒", title, eligibility_message, back_link),
                status_code=eligibility_status,
            )
        name = (apply_user.get("global_name") or apply_user.get("username") or applicant_name or "").strip()[:80]
        text = (applicant_text or "").strip()[:2000]
        if not applicant_id or not name or not text:
            return HTMLResponse(simple_page("⚠️", "Angaben fehlen", "Bitte fülle die Motivation und alle Pflichtfelder aus.", back_link), status_code=400)
        try:
            # Die Laufbahnen haben unterschiedliche Testlängen (12/22/30).
            # Antworten anhand der serverseitig definierten Fragezahl aus dem Formular
            # lesen; zusätzliche oder manipulierte Felder können den Score nicht erhöhen.
            submitted_form = await request.form()
            career_definition = APPLICATION_CAREERS.get(career_path or "")
            answer_count = len(career_definition["questions"]) if career_definition else 0
            submitted_answers = [
                str(submitted_form.get(f"answer_{index}", ""))
                for index in range(1, answer_count + 1)
            ]
            test_result = grade_career_test(career_path or "", submitted_answers)
            practical_result = grade_practical_tasks(career_path or "", submitted_form)
        except ValueError as exc:
            return HTMLResponse(simple_page("⚠️", "Online-Test unvollständig", str(exc), back_link), status_code=400)

        # K-04: duplicate check + insert must be one critical section; two concurrent
        # submissions by the same applicant must not create two active applications.
        with _io_lock:
            apps = load_json(APPS_FILE, {})
            if any(a.get("user_id") == str(applicant_id) and a.get("status") in {"pending", "interview"}
                   for a in apps.values()):
                return HTMLResponse(simple_page("ℹ️", "Bewerbung liegt bereits vor",
                                                "Deine Bewerbung wird gerade geprüft. Ein weiterer Antrag ist währenddessen nicht möglich.",
                                                back_link))
            app_id_new = f"app_{uuid.uuid4().hex[:10]}"
            apps[app_id_new] = {
                "user_id": str(applicant_id),
                "name": name,
                "text": text,
                "status": "pending",
                "career_path": test_result["career_path"],
                "career_label": test_result["career_label"],
                "test_score": test_result["score"],
                "test_total": test_result["total"],
                "test_percent": test_result["percent"],
                "test_pass_percent": test_result["pass_percent"],
                "test_passed": test_result["passed"],
                "test_details": test_result["details"],
                "test_version": 3,
                "practical_answers": practical_result["answers"],
                "practical_review_status": "pending",
                "practical_version": 1,
                "upvotes": [],
                "downvotes": [],
                "created_at": now_de().strftime("%d.%m.%Y %H:%M"),
            }
            save_json(APPS_FILE, apps)
        await notify_new_application(request, app_id_new, apps[app_id_new])
        await dm_applicant(request, applicant_id, "received", id=app_id_new)  # BOT-10
        result_status = "Bestanden" if test_result["passed"] else "noch nicht bestanden"
        result_text = (
            f"Deine Bewerbung wurde mit der Laufbahn {esc(test_result['career_label'])} gespeichert. "
            f"Online-Test: {test_result['score']}/{test_result['total']} richtig ({test_result['percent']} %), "
            f"Status: {result_status}. Deine Praxisaufgaben wurden ebenfalls gespeichert und werden vom Team manuell geprüft."
        )
        clear_draft = APPLY_DRAFT_CLEAR_SCRIPT.replace("__KEY__", json.dumps(apply_draft_key(applicant_id, career_path)))
        return HTMLResponse(simple_page("✅", "Bewerbung erfolgreich abgesendet!", result_text,
                                        '<a href="/apply" class="px-4 py-2 rounded-xl bg-slate-100 dark:bg-slate-800">Zurück</a>' + clear_draft))

    # ---------- Ab hier: Login + Recht je nach Aktion ----------
    required = ACTION_PERMS.get(action)
    if required is None:
        raise HTTPException(status_code=400, detail="Unbekannte Aktion.")
    ctx = auth(request, user_session, perm=None if required == "admin" else required, admin=(required == "admin"))
    guild, config = ctx.guild, ctx.config
    actor, actor_id = ctx.user.get("global_name"), ctx.user["id"]
    is_manager = ctx.perms["can_promote"] or ctx.perms["is_admin"]
    team_role_ids = ordered_team_role_ids(guild, config.get("team_role_ids", []))
    if action == "decide_app" and not (ctx.perms.get("can_manage_applications") or ctx.perms.get("can_promote") or ctx.perms.get("is_admin")):
        raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
    member_url = f"/member/{user_id}" if redirect_to_member and user_id else "/dashboard"

    # Active safety lockouts also block alternate high-impact dashboard actions.
    if action in {"promote", "demote", "team_kick", "hire", "remove_warn"}:
        actor_member = ctx.member or guild.get_member(int(actor_id)) if str(actor_id).isdigit() else ctx.member
        guard_target = guild.get_member(user_id) if user_id else None
        allowed_safety_action, safety_message = await enforce_team_safety_action_lockout(
            guild, actor_member, guard_target, action, config, source="Dashboard",
        )
        if not allowed_safety_action:
            return back(member_url, safety_message, False)

    # ---------- Manuelle Einstellung über Discord-ID oder Namen ----------
    if action == "hire":
        query = (target_user_id or "").strip()
        action_reason = (action_reason or "").strip()[:500]
        if not query:
            return back("/team", "Bitte gib eine Discord-Nutzer-ID oder einen Discord-Namen ein.", False)
        if not action_reason:
            return back("/team", "Bitte gib einen Pflichtgrund für die Einstellung an.", False)
        if not hire_role_id:
            return back("/team", "Bitte wähle eine Teamrolle aus.", False)

        role = guild.get_role(hire_role_id)
        configured_team_role_ids = set(team_role_ids)
        if role is None or role.id not in configured_team_role_ids or role.managed or role.is_default():
            return back("/team", "Die ausgewählte Rolle ist keine gültige, konfigurierte Teamrolle.", False)
        if not guild.me:
            return back("/team", "Der Bot ist auf diesem Discord-Server noch nicht vollständig verfügbar.", False)
        if not guild.me.guild_permissions.manage_roles:
            return back("/team", "Dem Bot fehlt die Berechtigung 'Rollen verwalten'.", False)
        if role.position >= guild.me.top_role.position:
            return back("/team", "Der Bot muss in der Rollenliste über der ausgewählten Teamrolle stehen.", False)

        lookup = query
        if lookup.startswith("<@") and lookup.endswith(">"):
            lookup = lookup[2:-1].lstrip("!").strip()
        target = None
        if lookup.isdigit():
            target_id = int(lookup)
            target = guild.get_member(target_id)
            if target is None:
                try:
                    target = await guild.fetch_member(target_id)
                except discord.NotFound:
                    return back("/team", "Diese Discord-ID gehört zu keinem Mitglied dieses Servers. Bitte prüfe die ID und lade die Person zuerst auf den Server ein.", False)
                except discord.Forbidden:
                    return back("/team", "Discord verweigert das Abrufen dieses Mitglieds. Bitte prüfe die Bot-Berechtigungen.", False)
                except discord.HTTPException:
                    return back("/team", "Das Discord-Mitglied konnte gerade nicht geladen werden. Bitte versuche es erneut.", False)
        else:
            name_query = lookup.lstrip("@").strip().casefold()
            if not name_query:
                return back("/team", "Bitte gib einen gültigen Discord-Namen ein.", False)
            try:
                if not getattr(guild, "chunked", True):
                    await guild.chunk(cache=True)
            except (discord.Forbidden, discord.HTTPException):
                pass
            pool = [m for m in list(getattr(guild, "members", []) or []) if not m.bot]
            lookup_fields = (
                lambda m: str(getattr(m, "name", "") or "").casefold(),
                lambda m: str(getattr(m, "global_name", "") or "").casefold(),
                lambda m: str(getattr(m, "display_name", "") or "").casefold(),
            )
            for field in lookup_fields:
                exact = [m for m in pool if field(m) == name_query]
                if exact:
                    target = exact[0] if len(exact) == 1 else None
                    if len(exact) > 1:
                        examples = ", ".join(f"{m.display_name} (ID {m.id})" for m in exact[:5])
                        return back("/team", f"Der Name ist nicht eindeutig. Bitte verwende die Discord-ID. Treffer: {examples}", False)
                    break
            if target is None:
                partial = [
                    m for m in pool
                    if any(name_query in field(m) for field in lookup_fields)
                ]
                unique_partial = {m.id: m for m in partial}
                if len(unique_partial) == 1:
                    target = next(iter(unique_partial.values()))
                elif len(unique_partial) > 1:
                    examples = ", ".join(f"{m.display_name} (ID {m.id})" for m in list(unique_partial.values())[:5])
                    return back("/team", f"Mehrere Discord-Mitglieder passen zu dieser Eingabe. Bitte verwende die ID. Treffer: {examples}", False)

        if target is None:
            return back("/team", "Kein passendes Discord-Mitglied gefunden. Nutze am besten die Nutzer-ID oder prüfe, ob die Person bereits auf dem Server ist.", False)
        if target.bot or target.id == guild.owner_id or str(target.id) == str(actor_id):
            return back("/team", "Bots, der Serverinhaber und du selbst können nicht über diese Funktion eingestellt werden.", False)
        if target.top_role.position >= guild.me.top_role.position:
            return back("/team", "Der Bot kann dieses Mitglied wegen der Discord-Rollenhierarchie nicht verwalten. Die Bot-Rolle muss über der höchsten Rolle der Person stehen.", False)
        if any(r.id in configured_team_role_ids for r in target.roles):
            return back("/team", f"{target.display_name} besitzt bereits eine Teamrolle. Verwende stattdessen die Rollenverwaltung.", False)

        selected_rank = team_role_ids.index(role.id)
        actor_rank = team_rank(ctx.member, team_role_ids, guild) if ctx.member else -1
        if not ctx.perms.get("is_admin") and actor_rank <= selected_rank:
            return back("/team", "Du darfst niemanden auf deinen eigenen Rang oder einen höheren Rang einstellen.", False)

        # The selected rank stays the only rank role. The other roles are a deterministic
        # membership/level bundle and are deliberately not added to team_role_ids.
        from pulse_hire_roles import manual_hire_role_ids

        automatic_role_ids = manual_hire_role_ids(role.name)
        if not automatic_role_ids:
            return back("/team", "Für diese Teamrolle ist keine sichere Ebenen-Zuordnung hinterlegt. Bitte die Teamrollen-Zuordnung prüfen.", False)

        automatic_roles = []
        for automatic_role_id in automatic_role_ids:
            automatic_role = guild.get_role(automatic_role_id)
            if automatic_role is None or automatic_role.managed or automatic_role.is_default():
                return back(
                    "/team",
                    f"Eine benötigte Team-/Ebenenrolle ({automatic_role_id}) fehlt oder kann nicht vergeben werden. Es wurde nichts geändert.",
                    False,
                )
            if automatic_role.position >= guild.me.top_role.position:
                return back(
                    "/team",
                    f"Die Bot-Rolle muss über der zusätzlichen Team-/Ebenenrolle {automatic_role.name} stehen. Es wurde nichts geändert.",
                    False,
                )
            if (
                not ctx.perms.get("is_admin")
                and getattr(getattr(automatic_role, "permissions", None), "administrator", False)
            ):
                return back(
                    "/team",
                    f"Die zusätzliche Rolle {automatic_role.name} besitzt Administratorrechte und darf nicht automatisch vergeben werden.",
                    False,
                )
            if automatic_role.id != role.id:
                automatic_roles.append(automatic_role)

        hire_role_bundle = [role, *automatic_roles]
        existing_role_ids = {member_role.id for member_role in target.roles}
        roles_to_add = [hire_role for hire_role in hire_role_bundle if hire_role.id not in existing_role_ids]
        assigned_role_names = ", ".join(hire_role.name for hire_role in hire_role_bundle)

        async def rollback_partial_hire_roles():
            current_role_ids = {member_role.id for member_role in target.roles}
            partial = [
                hire_role for hire_role in roles_to_add
                if hire_role.id not in existing_role_ids and hire_role.id in current_role_ids
            ]
            if not partial:
                return True
            try:
                await target.remove_roles(
                    *partial,
                    reason=f"Rücknahme unvollständiger Teameinstellung durch {actor}: {action_reason}",
                )
                return True
            except (discord.Forbidden, discord.HTTPException):
                return False

        try:
            if roles_to_add:
                await target.add_roles(
                    *roles_to_add,
                    reason=f"Manuelle Teameinstellung durch {actor}: {action_reason}",
                )
        except discord.Forbidden:
            rolled_back = await rollback_partial_hire_roles()
            message = (
                "Discord hat die Rollenänderung verweigert. Teilweise gesetzte Rollen wurden zurückgenommen."
                if rolled_back else
                "Discord hat die Rollenänderung verweigert; eine teilweise Vergabe konnte nicht vollständig zurückgenommen werden. Bitte Mitgliedsrollen prüfen."
            )
            return back("/team", message, False)
        except discord.HTTPException:
            rolled_back = await rollback_partial_hire_roles()
            message = (
                "Die Teamrollen konnten gerade nicht vollständig vergeben werden. Teilweise gesetzte Rollen wurden zurückgenommen."
                if rolled_back else
                "Die Rollenvergabe ist fehlgeschlagen und eine teilweise Vergabe konnte nicht vollständig zurückgenommen werden. Bitte Mitgliedsrollen prüfen."
            )
            return back("/team", message, False)

        hired_at = now_de().isoformat(timespec="seconds")
        audit_ref = log_audit(
            actor, actor_id, "Manuelle Einstellung",
            f"{target.display_name} ({target.id}) -> {assigned_role_names} · Grund: {action_reason}",
        )
        team_db = load_json(DATA_FILE, {})
        team_entry = user_entry(team_db, str(target.id))
        team_entry["team_since"] = hired_at
        team_entry["manual_hire_by"] = str(actor)
        team_entry["manual_hire_reason"] = action_reason
        store_employment_event(team_entry, build_employment_event(
            user_id=target.id,
            hired_at=hired_at,
            actor_id=actor_id,
            source="manual_hire",
            audit_ref=audit_ref,
            career_path=team_entry.get("career_path"),
        ))
        save_json(DATA_FILE, team_db)

        await send_dm_notification(
            target,
            f"🎉 Du wurdest von **{guild.name}** in das Team aufgenommen. Deine neue Teamrolle: **{role.name}**. "
            f"Zugehörige Team- und Ebenenrollen: **{', '.join(r.name for r in automatic_roles)}**. Bei Fragen wende dich bitte an die Führungsebene."
        )
        await send_team_update_embed(
            guild,
            "🎉 Team-Update: Manuelle Einstellung",
            f"{target.mention} wurde manuell in das Team aufgenommen.",
            _pulse_color("success"),
            target=target.mention,
            action="Manuelle Einstellung",
            actor=actor,
            fields=[
                ("Teamrolle", role.mention, True),
                ("Zugehörige Team-/Ebenenrollen", ", ".join(r.mention for r in automatic_roles), False),
                ("Discord-ID", str(target.id), True),
                ("Grund", action_reason, False),
                ("Zeitpunkt", now_de().strftime("%d.%m.%Y %H:%M"), True),
            ],
            thumbnail=target.display_avatar.url,
        )
        return back(
            "/team",
            f"{target.display_name} wurde mit {role.name} und {len(automatic_roles)} zugehörigen Team-/Ebenenrollen erfolgreich eingestellt.",
        )

    # ---------- Befördern / Degradieren / Kicken ----------
    if action in ("promote", "demote", "team_kick"):
        member = guild.get_member(user_id) if user_id else None
        if not member:
            return back("/team", "Mitglied nicht gefunden.", False)
        if str(member.id) == actor_id:
            return back(member_url, "Du kannst diese Aktion nicht bei dir selbst ausführen.", False)
        if member.id == guild.owner_id or member.bot:
            return back(member_url, "Dieses Mitglied kann nicht bearbeitet werden.", False)
        actor_idx = team_rank(ctx.member, team_role_ids, guild) if ctx.member else -1
        target_idx = team_rank(member, team_role_ids, guild)
        if not ctx.perms["is_admin"] and actor_idx <= target_idx:
            return back(member_url, "Du kannst nur Mitglieder mit niedrigerem Rang bearbeiten.", False)

        action_reason = (action_reason or "").strip()[:500]
        if action in ("promote", "demote", "team_kick") and not action_reason:
            return back(member_url, "Bitte einen Grund für die Maßnahme angeben.", False)
        if action == "promote" and probation_days not in VALID_PROBATION_DAYS:
            return back(member_url, "Für jede Beförderung muss eine Probezeit von 3 oder 7 Tagen ausgewählt werden.", False)

        # DATA-3: one rank action per member at a time (per-target lock, no global lock) and no replay of the same
        # action right after it ran – a double click or repeated POST must not promote/demote twice, because the rank
        # is derived from the (possibly stale) member cache. Check + acquire have no await in between (atomic).
        rank_lock = _rank_target_lock(guild.id, member.id)
        if rank_lock.locked():
            return back(member_url, "Für dieses Mitglied läuft gerade eine Rang-Aktion. Bitte kurz warten.", False)
        if _rank_action_replayed(guild.id, member.id, action):
            return back(member_url, "Diese Aktion wurde für dieses Mitglied gerade erst ausgeführt. "
                                    "Zur Sicherheit bitte einen Moment warten und das Ergebnis prüfen.", False)
        await rank_lock.acquire()
        try:
            if action == "team_kick":
                if not guild.me:
                    return back(member_url, "Der Bot ist auf diesem Server noch nicht verfügbar.", False)

                team_roles = [role for role in member.roles if role.id in team_role_ids and not role.is_default()]
                if not team_roles:
                    return back(member_url, "Dieses Mitglied hat keine Teamrolle mehr.", False)

                # One shared, fail-closed implementation performs every role and hierarchy check
                # before making the single role update. It never calls Member.kick().
                kick_result = await apply_team_kick_role_reset(
                    guild, member, config, f"Dashboard durch {actor}: {action_reason}",
                    actor=ctx.member, actor_is_admin=bool(ctx.perms.get("is_admin")),  # AUTHZ-6
                )
                if not kick_result.get("ok"):
                    return back(
                        member_url,
                        str(kick_result.get("message") or "Team-Entfernung wurde sicherheitshalber abgebrochen."),
                        False,
                    )

                keep_roles = kick_result.get("keep_roles", [])
                removed_role_names = kick_result.get("removed_roles", [])
                keep_role_names = ", ".join(role.name for role in keep_roles)
                await send_dm_notification(
                    member,
                    f"❌ Du wurdest aus dem Team von **{guild.name}** entfernt. "
                    f"Du bleibst auf dem Discord-Server und behältst {keep_role_names}. Grund: {action_reason}",
                )
                await send_team_update_embed(
                    guild,
                    "🚪 Team-Update: Team-Kick",
                    f"{member.mention} wurde aus dem BOCHUM-RP-Team entfernt. "
                    "Die Person bleibt weiterhin auf dem Discord-Server.",
                    _pulse_color("danger"),
                    target=member.mention,
                    action="Team-Kick",
                    actor=actor,
                    fields=[
                        ("Entfernte Rollen", ", ".join(removed_role_names) if removed_role_names else "Keine zusätzlichen Rollen", False),
                        ("Verbleibende Rollen", ", ".join(role.mention for role in keep_roles), False),
                        ("Grund", action_reason, False),
                    ],
                    thumbnail=member.display_avatar.url,
                )
                log_audit(
                    actor,
                    actor_id,
                    "Team-Kick",
                    f"{member.display_name}: entfernte Rollen {', '.join(removed_role_names)} · "
                    f"behalten {keep_role_names} · Grund: {action_reason}",
                )
                return back(
                    "/team",
                    f"{member.display_name} wurde aus dem BOCHUM-RP-Team entfernt. "
                    "Das Mitglied bleibt auf dem Discord-Server.",
                )

            if action == "promote":
                new_idx = target_idx + 1
                if new_idx >= len(team_role_ids):
                    return back(member_url, "Höchster Rang bereits erreicht.", False)
                if not ctx.perms["is_admin"] and new_idx >= actor_idx:
                    return back(member_url, "Du kannst nicht auf deinen eigenen Rang oder höher befördern.", False)
                new_role = guild.get_role(team_role_ids[new_idx])
                if not new_role:
                    return back(member_url, "Die Zielrolle existiert nicht mehr (Team-Rollen in den Einstellungen prüfen).", False)
                old = [r for r in member.roles if r.id in team_role_ids and r.id != new_role.id]
                previous_role = max(old, key=lambda role: role.position) if old else None
                if previous_role is None:
                    return back(member_url, "Für eine Beförderung wurde keine vorherige Teamrolle gefunden. Bitte Teamrollen prüfen.", False)
                await member.add_roles(new_role, reason=f"Beförderung durch {actor}: {action_reason}")
                if old:
                    await member.remove_roles(*old, reason=f"Beförderung durch {actor}: {action_reason}")

                await finish_promotion(  # BOT-05: same follow-up as /promote
                    guild, member=member, previous_role=previous_role, new_role=new_role, probation_days=probation_days,
                    actor_name=actor, actor_id=actor_id, reason=action_reason, source="Dashboard",
                )
                return back(member_url, f"{member.display_name} wurde zum {new_role.name} befördert; Probezeit: {probation_days} Tage.")

            # demote
            if target_idx < 0:
                return back(member_url, "Dieses Mitglied hat keine Team-Rolle.", False)
            old = [r for r in member.roles if r.id in team_role_ids]
            new_idx = target_idx - 1
            if new_idx >= 0:
                new_role = guild.get_role(team_role_ids[new_idx])
                if not new_role:
                    return back(member_url, "Die Zielrolle existiert nicht mehr (Team-Rollen in den Einstellungen prüfen).", False)
                await member.add_roles(new_role, reason=f"Degradierung durch {actor}: {action_reason}")
                await member.remove_roles(*[r for r in old if r.id != new_role.id], reason=f"Degradierung durch {actor}: {action_reason}")
                await finish_demotion(  # BOT-05: same follow-up as /demote
                    guild, member=member, previous_role=old[0] if old else None, new_role=new_role,
                    actor_name=actor, actor_id=actor_id, reason=action_reason, source="Dashboard",
                )
                return back(member_url, f"{member.display_name} wurde zum {new_role.name} degradiert.")
            await member.remove_roles(*old, reason=f"Degradierung durch {actor}")
            await finish_demotion(guild, member=member, previous_role=old[0] if old else None, new_role=None,
                                  actor_name=actor, actor_id=actor_id, reason=action_reason, source="Dashboard")
            return back(member_url, f"{member.display_name} wurde aus dem Team-Rang entfernt.")
        except discord.Forbidden:
            return back(member_url, "Dem Bot fehlen Rechte – seine Rolle muss über den Team-Rollen stehen.", False)
        except Exception as e:
            print(f"Rang-Aktion fehlgeschlagen: {e}")
            return back(member_url, "Aktion fehlgeschlagen (Details in der Bot-Konsole).", False)
        finally:
            rank_lock.release()

    # ---------- Verwarnungen ----------
    if action == "warn_with_proof":
        if not user_id or not (warn_reason or "").strip():
            return back(member_url, "Bitte einen Grund angeben.", False)
        result = await issue_team_warning(  # BOT-05: same logic as /warn
            guild, actor_member=ctx.member or guild.get_member(int(actor_id)), actor_name=actor, actor_id=actor_id,
            member=guild.get_member(user_id), reason=warn_reason, duration_days=warn_duration_days, proof=warn_proof,
            config=config, actor_is_admin=bool(ctx.perms.get("is_admin")), source="Dashboard",
        )
        return back(member_url, result["message"], result["ok"])

    if action == "remove_warn":
        if not user_id or not warn_id:
            return back(member_url, "Ungültige Anfrage.", False)
        result = await revoke_team_warning(  # BOT-05: same logic as /warn-remove
            guild, member=guild.get_member(user_id), warn_id=warn_id, revoke_reason=warn_revoke_reason,
            actor_name=actor, actor_id=actor_id, config=config,
            actor_member=ctx.member or guild.get_member(int(actor_id)), actor_is_admin=bool(ctx.perms.get("is_admin")),
        )
        return back(member_url, result["message"], result["ok"])


    if action == "add_note":
        note = (note_text or "").strip()[:500]
        if not user_id or not note:
            return back(member_url, "Notiz ist leer.", False)

        # Resolve the submitted ID against this guild before touching the personnel file.
        target = guild.get_member(int(user_id))
        if target is None or getattr(target, "bot", False):
            return back("/team", "Teammitglied wurde nicht gefunden.", False)
        configured_role_ids = set(team_role_ids)
        if (
            configured_role_ids
            and str(target.id) != str(actor_id)
            and not any(
                getattr(role, "id", None) in configured_role_ids
                for role in (getattr(target, "roles", ()) or ())
            )
        ):
            return back("/team", "Interne Notizen können nur für Teammitglieder erstellt werden.", False)

        team_db = load_json(DATA_FILE, {})
        if not isinstance(team_db, dict):
            return back(member_url, "Die Teamakte konnte nicht geladen werden.", False)
        entry = user_entry(team_db, str(target.id))
        tracker = track_member_entry(target.id, entry)
        entry["notes"].append(f"[{now_de().strftime('%d.%m.%Y')}] {note} (von {actor})")
        # K-04: merge this note into the latest entry so concurrent updates are not overwritten.
        tracker.commit()
        log_audit(actor, actor_id, "Notiz Erstellt", f"User-ID {target.id}: {note}")
        return back(member_url, "Notiz gespeichert.")

    # ---------- Abmeldungen ----------
    if action == "submit_loa":
        target = user_id or int(actor_id)
        if str(target) != actor_id and not is_manager:
            return back("/loa", "Du kannst nur dich selbst abmelden.", False)
        if str(target) != actor_id and _loa_rank_problem(ctx, guild, team_role_ids, target):
            return back("/loa", "Du darfst nur Mitglieder unterhalb deines eigenen Teamrangs abmelden.", False)
        try:
            d_start = datetime.strptime(loa_start or "", "%Y-%m-%d").date()
            d_end = datetime.strptime(loa_end or "", "%Y-%m-%d").date()
        except ValueError:
            return back("/loa", "Ungültiges Datum.", False)
        if d_end < d_start:
            return back("/loa", "Das Enddatum liegt vor dem Startdatum.", False)
        if d_end < now_de().date():
            return back("/loa", "Das Enddatum liegt in der Vergangenheit.", False)
        m = guild.get_member(target)
        display_n = m.display_name if m else f"User-{target}"
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        # Named columns: migrated databases have 'von' appended at the end, so a
        # positional insert stored the end date as original_nick (nickname became a date).
        # original_nick is the real Discord nickname, like the slash command stores it.
        conn.execute(
            "INSERT OR REPLACE INTO abmeldungen(user_id,user_name,grund,von,bis,original_nick,guild_id) VALUES (?,?,?,?,?,?,?)",
            (target, display_n, (loa_reason or "Kein Grund").strip()[:300], loa_start, loa_end,
             (m.nick if m else None), GUILD_ID),
        )
        conn.commit()
        conn.close()
        log_audit(actor, actor_id, "LOA Eingetragen", f"{display_n} bis {loa_end}")
        return back("/loa", f"Abmeldung für {display_n} gespeichert.")

    if action == "cancel_loa":
        try:
            target = int(target_user_id)
        except (TypeError, ValueError):
            return back("/loa", "Ungültige Anfrage.", False)
        if str(target) != actor_id and not is_manager:
            return back("/loa", "Du kannst nur deine eigene Abmeldung beenden.", False)
        if str(target) != actor_id and _loa_rank_problem(ctx, guild, team_role_ids, target):
            return back("/loa", "Du darfst nur Abmeldungen von Mitgliedern unterhalb deines eigenen Teamrangs beenden.", False)
        conn = sqlite3.connect(DB_ABMELDUNGEN)
        conn.execute("DELETE FROM abmeldungen WHERE user_id = ?", (target,))
        conn.commit()
        conn.close()
        log_audit(actor, actor_id, "LOA Storniert", f"User-ID {target}")
        return back("/loa", "Abmeldung beendet.")

    # ---------- Bewerbungen ----------
    if action == "vote_app":
        apps = load_json(APPS_FILE, {})
        item = apps.get(app_id or "")
        if not item or item.get("status") != "pending" or vote not in ("up", "down"):
            return back("/applications", "Bewerbung nicht mehr offen.", False)
        for key in ("upvotes", "downvotes"):
            item[key] = [u for u in item.get(key, []) if u != actor_id]
        item["upvotes" if vote == "up" else "downvotes"].append(actor_id)
        save_json(APPS_FILE, apps)
        return back("/applications")

    if action == "decide_app":
        import pulse_board
        app_return = pulse_board.safe_return(return_to)  # the recruiting board may be the return target, nothing else
        async with APPLICATION_DECISION_LOCK:
            apps = load_json(APPS_FILE, {})
            item = apps.get(app_id or "")
            if not item or (item.get("guild_id") not in (None, "") and str(item.get("guild_id")) != str(guild.id)):
                return back(app_return, "Bewerbung nicht gefunden.", False)

            original_item = copy.deepcopy(item)
            current_status = str(item.get("status") or "")
            stale_message = pulse_board.stale_reason(item, expected_status)
            if stale_message:
                return back(app_return, stale_message, False)
            if decision not in pulse_board.allowed_decisions(current_status):
                return back(app_return, "Diese Entscheidung passt nicht zum aktuellen Bewerbungsstatus.", False)
            import pulse_application_review as app_review
            gate_message = app_review.decision_gate(item, app_review.normalize_policy(get_setting_safe(app_review.POLICY_KEY)))
            if gate_message:
                return back(app_return, gate_message, False)

            def store_item() -> None:
                # Merge only fields changed by this decision. Concurrent reviews, votes,
                # reminder flags and history entries on the same application stay intact.
                from pulse_applications_repository import ApplicationRepositoryConflict

                try:
                    get_applications_repository().update_entry(
                        app_id,
                        expected_status=current_status,
                        before=original_item,
                        updated=item,
                    )
                except ApplicationRepositoryConflict as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="Die Bewerbung wurde während der Discord-Aktion verändert. Bitte Status und Teamrolle prüfen und die Seite neu laden.",
                    ) from exc

            applicant = None
            try:
                applicant = guild.get_member(int(item.get("user_id")))
            except Exception:
                applicant = None

            action_reason = (action_reason or "").strip()[:500]
            if not action_reason:
                return back(app_return, "Bitte einen Grund bzw. eine Gesprächsnotiz angeben.", False)

            if decision == "accept":
                # Erste Annahme = Einladung zum Bewerbungsgespräch. Das ist ausdrücklich
                # KEINE Einstellung; die Team-Einstiegsrolle wird erst nach dem Gespräch vergeben.
                if not applicant:
                    return back(app_return, "Der Bewerber ist nicht mehr auf dem Discord-Server.", False)
                application_role = guild.get_role(APPLICATION_ROLE_ID)
                if application_role is None:
                    return back(
                        "/applications",
                        f"Die Bewerberolle {APPLICATION_ROLE_ID} wurde auf Discord nicht gefunden. Bitte die Rolle prüfen.",
                        False,
                    )
                if application_role.is_default() or application_role.managed:
                    return back(app_return, "Die Bewerberolle ist nicht verwaltbar.", False)
                if guild.me and (
                    not guild.me.guild_permissions.manage_roles
                    or application_role.position >= guild.me.top_role.position
                ):
                    return back(
                        "/applications",
                        "Der Bot benötigt „Rollen verwalten“ und muss in der Rollen-Hierarchie über der Bewerberolle stehen.",
                        False,
                    )
                try:
                    if application_role not in applicant.roles:
                        await applicant.add_roles(
                            application_role,
                            reason=f"Zum Bewerbungsgespräch eingeladen durch {actor}: {action_reason}",
                        )
                except discord.Forbidden:
                    return back(app_return, "Discord hat die Bewerberolle verweigert. Bitte Bot-Rechte und Rollen-Hierarchie prüfen.", False)
                except discord.HTTPException as exc:
                    print(f"Bewerberolle für {applicant.id} konnte nicht vergeben werden: {exc}")
                    return back(app_return, "Die Bewerberolle konnte wegen eines Discord-Fehlers nicht vergeben werden.", False)

                invited_at = now_de().strftime("%d.%m.%Y %H:%M")
                item["status"] = "interview"
                item["application_role_id"] = application_role.id
                item["application_role"] = application_role.name
                item["interview_invited_by"] = actor
                item["interview_invited_at"] = invited_at
                item["interview_invitation_reason"] = action_reason
                store_item()
                import pulse_app_comms  # BOT-10: resolve from the central versioned library, legacy fallback
                resolved = pulse_app_comms.resolve_for_guild("interview", str(guild.id),
                                                               server=guild.name, rolle=application_role.name, id=app_id)
                await send_applicant_notice(
                    applicant, resolved["text"], app_id=app_id, kind="interview", actor_id=actor_id, actor_name=actor,
                    template_ref=resolved["template_ref"],
                )
                await send_team_update_embed(
                    guild,
                    "🎙️ Bewerbungsgespräch erforderlich",
                    f"{applicant.mention} wurde zum verpflichtenden Bewerbungsgespräch eingeladen. Eine Einstellung ist noch nicht erfolgt.",
                    _pulse_color("info"),
                    target=applicant.mention,
                    action="Zum Bewerbungsgespräch eingeladen",
                    actor=actor,
                    fields=[
                        ("Bewerberolle", application_role.mention, True),
                        ("Laufbahn", item.get("career_label", "Nicht angegeben"), True),
                        ("Bewerbung", app_id or "—", True),
                        ("Grund / Hinweis", action_reason, False),
                        ("Eingeladen am", invited_at, True),
                    ],
                    thumbnail=applicant.display_avatar.url,
                )
                log_audit(
                    actor,
                    actor_id,
                    "Bewerbungsgespräch eingeladen",
                    f"{applicant.display_name} ({applicant.id}) erhielt Bewerberolle {application_role.id}; noch keine Einstellung. Grund: {action_reason}",
                )
                return back(app_return, f"{applicant.display_name} wurde zum Bewerbungsgespräch eingeladen. Noch keine Teamrolle vergeben.")

            if decision == "hire_after_interview":
                if current_status != "interview" or not item.get("interview_invited_at"):
                    return back(app_return, "Vor der Einstellung muss die Einladung zum Bewerbungsgespräch dokumentiert sein.", False)
                if not applicant:
                    return back(app_return, "Der Bewerber ist nicht mehr auf dem Discord-Server.", False)
                configured_roles = [
                    guild.get_role(rid) for rid in team_role_ids
                    if str(rid) != str(APPLICATION_ROLE_ID)
                ]
                configured_roles = [r for r in configured_roles if r and not r.managed]
                role = guild.get_role(hire_role_id) if hire_role_id else (
                    min(configured_roles, key=lambda r: r.position) if configured_roles else None
                )
                if role is None or role.id not in team_role_ids or role.id == APPLICATION_ROLE_ID:
                    return back(app_return, "Bitte nach dem Bewerbungsgespräch eine gültige Team-Einstiegsrolle auswählen.", False)
                # SEC-07: hire_role_id comes from the form – same rank rule as action=hire: nobody (but admins)
                # hires onto the own rank or higher, and nobody hires themselves.
                if str(applicant.id) == str(actor_id):
                    return back(app_return, "Du kannst dich nicht selbst einstellen.", False)
                ordered_ids = ordered_team_role_ids(guild, team_role_ids)
                selected_rank = ordered_ids.index(role.id) if role.id in ordered_ids else len(ordered_ids)
                actor_rank = team_rank(ctx.member, team_role_ids, guild) if ctx.member else -1
                if not ctx.perms.get("is_admin") and actor_rank <= selected_rank:
                    return back(app_return, "Du darfst niemanden auf deinen eigenen Rang oder einen höheren Rang einstellen.", False)
                application_role = guild.get_role(APPLICATION_ROLE_ID)
                if guild.me and (
                    not guild.me.guild_permissions.manage_roles
                    or role.position >= guild.me.top_role.position
                    or (application_role and application_role.position >= guild.me.top_role.position)
                ):
                    return back(
                        "/applications",
                        "Der Bot benötigt „Rollen verwalten“ und muss über der Team-Einstiegsrolle und der Bewerberolle stehen.",
                        False,
                    )
                # Ein Rollen-Edit ersetzt die Teamrangrolle und entfernt die Bewerberolle
                # in einem Discord-Aufruf, während alle anderen Rollen erhalten bleiben.
                remove_role_ids = set(team_role_ids) | {APPLICATION_ROLE_ID}
                roles_to_keep = [r for r in applicant.roles if r.id not in remove_role_ids]
                roles_to_keep.append(role)
                try:
                    await applicant.edit(
                        roles=roles_to_keep,
                        reason=f"Einstellung nach Bewerbungsgespräch durch {actor}: {action_reason}",
                    )
                except discord.Forbidden:
                    return back(app_return, "Discord hat die Einstellung verweigert. Bitte Bot-Rechte und Rollen-Hierarchie prüfen.", False)
                except discord.HTTPException as exc:
                    print(f"Einstellung nach Bewerbungsgespräch für {applicant.id} fehlgeschlagen: {exc}")
                    return back(app_return, "Die Einstellung konnte wegen eines Discord-Fehlers nicht durchgeführt werden.", False)

                hired_time = now_de()
                hired_at = hired_time.isoformat(timespec="seconds")
                decided_at = hired_time.strftime("%d.%m.%Y %H:%M")
                item["status"] = "hired"
                item["hired_role_id"] = role.id
                item["hired_role"] = role.name
                item["application_role_id"] = APPLICATION_ROLE_ID
                item["interview_completed_by"] = actor
                item["interview_completed_at"] = decided_at
                item["decision_reason"] = action_reason
                item["decided_by"] = actor
                item["decided_at"] = decided_at
                audit_ref = log_audit(
                    actor, actor_id, "Einstellung nach Bewerbungsgespräch",
                    f"{applicant.display_name} -> {role.name}; Bewerberolle {APPLICATION_ROLE_ID} entfernt. Grund: {action_reason}",
                )
                team_db = load_json(DATA_FILE, {})
                team_entry = user_entry(team_db, str(applicant.id))
                team_entry["team_since"] = hired_at
                if item.get("career_path"):
                    team_entry["career_path"] = item.get("career_path")
                    team_entry["career_label"] = item.get("career_label")
                    team_entry["application_test_percent"] = item.get("test_percent")
                    team_entry["application_test_passed"] = item.get("test_passed")
                store_employment_event(team_entry, build_employment_event(
                    user_id=applicant.id,
                    hired_at=hired_at,
                    actor_id=actor_id,
                    source="application_interview",
                    audit_ref=audit_ref,
                    career_path=item.get("career_path"),
                ))
                save_json(DATA_FILE, team_db)
                store_item()
                import pulse_app_comms  # BOT-10
                resolved = pulse_app_comms.resolve_for_guild("hired", str(guild.id), server=guild.name, rolle=role.name, id=app_id)
                await send_applicant_notice(
                    applicant, resolved["text"], app_id=app_id, kind="hired", actor_id=actor_id, actor_name=actor,
                    template_ref=resolved["template_ref"],
                )
                await send_team_update_embed(
                    guild,
                    "🎉 Team-Update: Einstellung nach Bewerbungsgespräch",
                    f"{applicant.mention} wurde nach dem verpflichtenden Bewerbungsgespräch eingestellt.",
                    _pulse_color("success"),
                    target=applicant.mention,
                    action="Einstellung nach Bewerbungsgespräch",
                    actor=actor,
                    fields=[
                        ("Einstiegsrolle", role.mention, True),
                        ("Bewerberolle", f"Entfernt (<@&{APPLICATION_ROLE_ID}>)", True),
                        ("Bewerbung", app_id or "—", True),
                        ("Gesprächsnotiz / Grund", action_reason, False),
                        ("Zeitpunkt", decided_at, True),
                    ],
                    thumbnail=applicant.display_avatar.url,
                )
                return back(app_return, f"{applicant.display_name} wurde nach dem Bewerbungsgespräch als {role.name} eingestellt.")

            if decision == "reject_after_interview" and applicant:
                application_role = guild.get_role(APPLICATION_ROLE_ID)
                if application_role and application_role in applicant.roles:
                    try:
                        await applicant.remove_roles(application_role, reason=f"Bewerbung nach Gespräch abgelehnt durch {actor}: {action_reason}")
                    except discord.Forbidden:
                        return back(app_return, "Die Ablehnung kann nicht abgeschlossen werden, weil Discord das Entfernen der Bewerberolle verweigert.", False)
                    except discord.HTTPException as exc:
                        print(f"Bewerberolle nach Ablehnung für {applicant.id} konnte nicht entfernt werden: {exc}")
                        return back(app_return, "Die Bewerberolle konnte nicht entfernt werden. Bitte Discord-Rechte prüfen.", False)

            item["status"] = "rejected"
            item["decided_by"] = actor
            item["decided_at"] = now_de().strftime("%d.%m.%Y %H:%M")
            item["decision_reason"] = action_reason
            store_item()
            import pulse_app_comms  # BOT-10
            reject_kind = "rejected_after_interview" if decision == "reject_after_interview" else "rejected"
            resolved = pulse_app_comms.resolve_for_guild(reject_kind, str(guild.id), server=guild.name, id=app_id)
            # #100: also journals the case that the applicant is no longer reachable (no DM is possible then)
            await send_applicant_notice(applicant, resolved["text"], app_id=app_id, kind=reject_kind, actor_id=actor_id,
                                        actor_name=actor, template_ref=resolved["template_ref"])
            await send_team_update_embed(
                guild,
                "📄 Team-Update: Bewerbung abgelehnt",
                f"Die Bewerbung von **{item.get('name','Unbekannt')}** wurde abgelehnt.",
                _pulse_color("danger"),
                target=item.get("name","Unbekannt"),
                action="Bewerbung nach Gespräch abgelehnt" if decision == "reject_after_interview" else "Bewerbung abgelehnt",
                actor=actor,
                fields=[("Bewerbung", app_id or "—", True), ("Grund", action_reason, False), ("Zeitpunkt", now_de().strftime("%d.%m.%Y %H:%M"), True)],
            )
            log_audit(actor, actor_id, "Bewerbung Entschieden", f"{item.get('name')}: abgelehnt · Grund: {action_reason}")
            return back(app_return, f"Bewerbung von {item.get('name')} abgelehnt.")
    # ---------- Meetings ----------
    if action == "meeting_rsvp":
        if rsvp_status not in ("accepted", "declined"):
            return back("/meetings", "Ungültiger Status.", False)
        meetings = load_meetings()
        meetings["rsvps"][actor_id] = {"name": actor, "status": rsvp_status}
        save_json(MEETINGS_FILE, meetings)
        return back("/meetings", "Zusage gespeichert." if rsvp_status == "accepted" else "Absage gespeichert.")

    if action == "add_meeting_topic":
        title, details = (topic_title or "").strip()[:100], (topic_details or "").strip()[:600]
        if not title or not details:
            return back("/meetings", "Titel und Beschreibung sind Pflicht.", False)
        meetings = load_meetings()
        meetings["topics"].append({"id": f"topic_{uuid.uuid4().hex[:6]}", "title": title, "details": details,
                                   "by": actor, "by_id": actor_id})
        save_json(MEETINGS_FILE, meetings)
        return back("/meetings", "Thema hinzugefügt.")

    if action == "delete_meeting_topic":
        meetings = load_meetings()
        topic = next((t for t in meetings["topics"] if t.get("id") == topic_id), None)
        if not topic:
            return back("/meetings", "Thema nicht gefunden.", False)
        if not (is_manager or topic.get("by_id") == actor_id):
            return back("/meetings", "Du darfst nur eigene Themen entfernen.", False)
        meetings["topics"] = [t for t in meetings["topics"] if t.get("id") != topic_id]
        save_json(MEETINGS_FILE, meetings)
        return back("/meetings", "Thema entfernt.")

    if action == "set_meeting_info":
        title, when = (meeting_title or "").strip()[:100], (meeting_datetime or "").strip()[:100]
        if not title or not when:
            return back("/meetings", "Titel und Zeitpunkt sind Pflicht.", False)
        meetings = load_meetings()
        # Vorherige Besprechung automatisch archivieren, sobald eine neue angesetzt wird.
        try:
            previous_title = meetings.get("title")
            previous_when = meetings.get("date_time")
            if previous_title and previous_when and previous_when != "Noch nicht angesetzt":
                pulse_db.save_meeting_history(previous_title, previous_when, meetings.get("description", ""), "", meetings.get("rsvps", {}))
        except Exception as e:
            print(f"Meeting-Historie konnte nicht gespeichert werden: {e}")
        meetings.update({"title": title, "date_time": when, "description": (meeting_desc or "").strip()[:500]})
        if reset_rsvps:
            meetings["rsvps"] = {}
        if clear_topics:
            meetings["topics"] = []
        save_json(MEETINGS_FILE, meetings)
        log_audit(actor, actor_id, "Meeting Aktualisiert", title)
        if announce:
            await send_team_update_embed(guild, "🎙️ Teambesprechung angesetzt",
                f"**{title}**\n📅 {when}\n{meetings['description']}\n\nZu-/Absagen im Dashboard unter *Teambesprechung*.", _pulse_color("info"))
        return back("/meetings", "Besprechung gespeichert.")

    if action == "reset_support_case_period":
        try:
            completed_period = pulse_db.finish_support_case_period(actor_id, actor)
            log_audit(
                actor, actor_id, "Supportfall-Zeitraum abgeschlossen",
                f"{completed_period['total']} abgeschlossene Pulse-Tickets · "
                f"{completed_period['start_at']} bis {completed_period['end_at']}",
            )
            return back(
                "/meetings",
                f"Teammeeting-Zeitraum archiviert: {completed_period['total']} abgeschlossene Supportfälle. "
                "Die neue Zählung läuft ab jetzt.",
            )
        except Exception as exc:
            print(f"Supportfall-Zeitraum konnte nicht zurückgesetzt werden: {type(exc).__name__}: {exc}")
            return back("/meetings", "Der Supportfall-Zeitraum konnte nicht gespeichert werden. Bitte erneut versuchen.", False)

    # ---------- Dashboard-only Rollenprofile (keine Discord-Rollenänderung) ----------
    if action == "save_dashboard_role":
        name = (dashboard_role_name or "").strip()[:40]
        if not name:
            return back("/settings", "Bitte gib einen Namen für das Dashboard-Profil an.", False)

        profiles = config.setdefault("dashboard_roles", {})
        profile_key = (dashboard_role_id or "").strip()
        if profile_key and profile_key not in profiles:
            return back("/settings", "Dieses Dashboard-Profil wurde nicht gefunden. Bitte lade die Einstellungen neu.", False)
        if any(
            str(existing_key) != profile_key and isinstance(existing, dict)
            and str(existing.get("name", "")).casefold() == name.casefold()
            for existing_key, existing in profiles.items()
        ):
            return back("/settings", "Es gibt bereits ein Dashboard-Profil mit diesem Namen.", False)
        if not profile_key:
            if len(profiles) >= 50:
                return back("/settings", "Es können maximal 50 Dashboard-Profile angelegt werden.", False)
            profile_key = f"dash_{uuid.uuid4().hex[:10]}"

        allowed_role_ids = {int(value) for value in config.get("team_role_ids", []) if str(value).isdigit()}
        selected_ids = {int(value) for value in dashboard_discord_role_ids}
        if selected_ids - allowed_role_ids:
            return back("/settings", "Dashboard-Profile dürfen nur vorhandene, konfigurierte Discord-Teamrollen verwenden.", False)
        valid_role_ids = []
        for selected_id in sorted(selected_ids):
            role = guild.get_role(selected_id)
            if role and not role.managed and not role.is_default():
                valid_role_ids.append(role.id)

        profile_permissions = {
            "can_view_dashboard": can_view_dashboard, "can_warn": can_warn,
            "can_promote": can_promote, "can_add_notes": can_add_notes,
            "can_manage_tickets": can_manage_tickets, "can_manage_applications": can_manage_applications,
            "can_manage_tasks": can_manage_tasks, "can_manage_training": can_manage_training,
            "can_manage_wiki": can_manage_wiki, "can_view_analytics": can_view_analytics,
        }
        profiles[profile_key] = {
            "name": name,
            "permissions": profile_permissions,
            "discord_role_ids": valid_role_ids,
        }
        save_json(CONFIG_FILE, config)
        enabled = ", ".join(label for key, label in DASHBOARD_PERMISSION_LABELS if profile_permissions.get(key)) or "keine Rechte"
        log_audit(actor, actor_id, "Dashboard-Profil gespeichert", f"{name} ({profile_key}) · {enabled}")
        return back("/settings", f"Dashboard-Profil „{name}“ gespeichert.")

    if action == "delete_dashboard_role":
        profile_key = (dashboard_role_id or "").strip()
        profiles = config.setdefault("dashboard_roles", {})
        if not profile_key or profile_key not in profiles:
            return back("/settings", "Dieses Dashboard-Profil wurde nicht gefunden.", False)
        profile_value = profiles[profile_key]
        profile_name = str(profile_value.get("name") or profile_key) if isinstance(profile_value, dict) else profile_key
        del profiles[profile_key]
        save_json(CONFIG_FILE, config)
        log_audit(actor, actor_id, "Dashboard-Profil gelöscht", f"{profile_name} ({profile_key}); Discord-Teamrollen-Zuordnungen entfernt")
        return back("/settings", f"Digitales Dashboard-Profil „{profile_name}“ wurde gelöscht.")

    # ---------- Einstellungen (nur Admins) ----------
    if action == "save_role_permissions":
        if role_id not in team_role_ids:
            return back("/settings", "Diese Rolle ist keine Team-Rolle.", False)
        config["permissions"][str(role_id)] = {
            "can_view_dashboard": can_view_dashboard, "can_warn": can_warn,
            "can_promote": can_promote, "can_add_notes": can_add_notes,
            "can_manage_tickets": can_manage_tickets, "can_manage_applications": can_manage_applications,
            "can_manage_tasks": can_manage_tasks, "can_manage_training": can_manage_training,
            "can_manage_wiki": can_manage_wiki, "can_view_analytics": can_view_analytics,
        }
        save_json(CONFIG_FILE, config)
        log_audit(actor, actor_id, "Rechte Gespeichert", f"Rolle {role_id}")
        return back("/settings", "Rechte gespeichert.")

    if action == "set_weekly_goal":
        if weekly_goal is None or not (0 < weekly_goal <= 100):
            return back("/settings", "Bitte einen Wert zwischen 0,5 und 100 angeben.", False)
        config["weekly_goal_hours"] = round(weekly_goal, 1)
        save_json(CONFIG_FILE, config)
        log_audit(actor, actor_id, "Wochenziel Geändert", f"{weekly_goal:g}h")
        return back("/settings", "Wochenziel gespeichert.")

    if action == "save_team_roles":
        valid = [guild.get_role(r) for r in team_roles]
        valid = sorted((r for r in valid if r), key=lambda r: r.position)
        config["team_role_ids"] = [r.id for r in valid]
        save_json(CONFIG_FILE, config)
        log_audit(actor, actor_id, "Team-Rollen Geändert", ", ".join(r.name for r in valid) or "keine")
        return back("/settings", f"{len(valid)} Team-Rollen gespeichert.")

    return back("/dashboard")

# =============================================================
# PULSE v4 – ERWEITERTE TEAMOS MODULE
# =============================================================
try:
    from pulse_features import register as register_pulse_features
    register_pulse_features(app)
except Exception as _feature_error:
    print(f"❌ Pulse-v4 Module konnten nicht registriert werden: {_feature_error}")

try:  # Audit #175: Abschlussbericht und interne Notizen für Teamfälle
    from pulse_case_report import register as register_case_report
    register_case_report(app)
except Exception as _case_report_error:
    print(f"❌ Fall-Abschlussbericht konnte nicht registriert werden: {_case_report_error}")

try:  # Audit #106/#176: Academy-Übersicht
    from pulse_academy import register as register_academy
    register_academy(app)
except Exception as _academy_error:
    print(f"❌ Academy konnte nicht registriert werden: {_academy_error}")

try:  # Audit #111/#113: Wiki-Suche und Pflichtlektüre
    from pulse_wiki_pages import register as register_wiki_pages
    register_wiki_pages(app)
except Exception as _wiki_error:
    print(f"❌ Wiki-Zusatz konnte nicht registriert werden: {_wiki_error}")

try:  # Audit #123: zentrale Vorlagenbibliothek
    from pulse_templates_pages import register as register_templates_pages
    register_templates_pages(app)
except Exception as _templates_error:
    print(f"❌ Vorlagenbibliothek konnte nicht registriert werden: {_templates_error}")

try:  # Audit #108: Quizverwaltung
    from pulse_quiz_admin import register as register_quiz_admin
    register_quiz_admin(app)
except Exception as _quiz_error:
    print(f"❌ Quizverwaltung konnte nicht registriert werden: {_quiz_error}")

try:  # Audit #51: Besprechungsmodus
    from pulse_meetings_pages import register as register_meetings_pages
    register_meetings_pages(app)
except Exception as _meetings_error:
    print(f"❌ Besprechungsmodus konnte nicht registriert werden: {_meetings_error}")

try:  # Audit #53: einheitliche Lesebestätigung
    from pulse_ack_pages import register as register_ack_pages
    register_ack_pages(app)
except Exception as _ack_error:
    print(f"❌ Lesebestätigungen konnten nicht registriert werden: {_ack_error}")

try:  # Audit #168: Testserver-Modus
    from pulse_testmode import register as register_testmode
    register_testmode(app)
except Exception as _testmode_error:
    print(f"❌ Testmodus konnte nicht registriert werden: {_testmode_error}")

try:  # Audit #47/#133: Regelwerk-Assistent mit Quellenangaben
    from pulse_rules_lookup import register as register_rules_lookup
    register_rules_lookup(app)
except Exception as _rules_error:
    print(f"❌ Regelwerk-Assistent konnte nicht registriert werden: {_rules_error}")

try:  # Audit #71-#94: Teamplanung, 360°-Teamakte, Entwicklungsziele
    from pulse_teamakte_pages import register as register_teamakte_pages
    register_teamakte_pages(app)
except Exception as _teamakte_error:
    print(f"❌ Teamakte-Module konnten nicht registriert werden: {_teamakte_error}")
try:  # Audit #58/#70: Live-Status und Aktivitätsfeed (Polling)
    from pulse_live import register as register_pulse_live
    register_pulse_live(app)
except Exception as _live_error:
    print(f"❌ Live-Modul konnte nicht registriert werden: {_live_error}")
try:  # Audit #153-#172: Betriebskonsole (nur Administratoren, lesend)
    from pulse_ops_pages import register as register_ops_pages
    register_ops_pages(app)
except Exception as _ops_error:
    print(f"❌ Betriebskonsole konnte nicht registriert werden: {_ops_error}")


try:
    from pulse_pro import register as register_pulse_pro
    register_pulse_pro(app)
except Exception as _pro_error:
    print(f"❌ Pulse Pro Module konnten nicht registriert werden: {_pro_error}")


try:
    from pulse_ultimate import register as register_pulse_ultimate
    register_pulse_ultimate(app)
except Exception as _ultimate_error:
    print(f"❌ Pulse Ultimate Module konnten nicht registriert werden: {_ultimate_error}")

try:
    from pulse_next import register as register_pulse_next
    register_pulse_next(app)
except Exception as _next_error:
    print(f"❌ Pulse TeamOS 2.0 Module konnten nicht registriert werden: {_next_error}")

try:
    from pulse_intelligence import register as register_pulse_intelligence
    register_pulse_intelligence(app)
except Exception as _intelligence_error:
    print(f"❌ Pulse Intelligence Module konnten nicht registriert werden: {_intelligence_error}")

try:
    from pulse_home import register as register_pulse_home
    register_pulse_home(app)
except Exception as _home_error:
    print(f"❌ Mein Pulse konnte nicht registriert werden: {_home_error}")

try:
    import pulse_views
    pulse_views.register(app)
except Exception as _views_error:
    print(f"❌ Gespeicherte Ansichten konnten nicht registriert werden: {_views_error}")

try:
    import pulse_privacy_requests_web
    pulse_privacy_requests_web.register(app)
except Exception as _privacy_requests_error:
    print(f"❌ Datenschutzanfragen konnten nicht registriert werden: {_privacy_requests_error}")

try:
    from pulse_cockpit import register as register_pulse_cockpit
    register_pulse_cockpit(app)
except Exception as _cockpit_error:
    print(f"❌ Leitungs-Cockpit konnte nicht registriert werden: {_cockpit_error}")

try:
    from pulse_social_dashboard import register as register_pulse_social
    register_pulse_social(app)
except Exception as _social_error:
    print(f"❌ Social-Media-Seite konnte nicht registriert werden: {_social_error}")

try:
    from pulse_admin import register as register_pulse_admin
    register_pulse_admin(app)
except Exception as _admin_error:
    print(f"❌ Verwaltungsbereich konnte nicht registriert werden: {_admin_error}")

try:
    from pulse_phase7 import register as register_pulse_phase7
    register_pulse_phase7(app)
except Exception as _phase7_error:
    print(f"❌ Neue Funktionen (Phase 7) konnten nicht registriert werden: {_phase7_error}")

try:
    from pulse_server_control import register as register_pulse_server_control
    register_pulse_server_control(app)
except Exception as _server_control_error:
    print(f"❌ Server Control Center konnte nicht registriert werden: {_server_control_error}")

try:
    from pulse_guard_admin import register as register_pulse_guard_admin
    register_pulse_guard_admin(app)
except Exception as _guard_error:
    print(f"❌ Sicherheitsbereich (AutoMod & Anti-Nuke) konnte nicht registriert werden: {_guard_error}")

try:
    from pulse_factions_dashboard import register as register_pulse_factions
    register_pulse_factions(app)
except Exception as _factions_error:
    print(f"❌ Fraktionsverwaltung konnte nicht registriert werden: {_factions_error}")


