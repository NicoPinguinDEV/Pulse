"""Team directory, member details, duty, meetings, leave and applicant notifications."""

from pulse_web._compat import bind_legacy_namespace as _bind_legacy_namespace
_bind_legacy_namespace(globals())
del _bind_legacy_namespace

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webserver import TZ

@app.get("/team", response_class=HTMLResponse)
async def team_list_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    weekly_goal = float(ctx.config.get("weekly_goal_hours", 3.0))
    team_role_ids = ordered_team_role_ids(guild, ctx.config.get("team_role_ids", []))
    shifts_db = load_shifts()
    active = shifts_db["active_shifts"]
    loas = get_loas()
    team_db = load_json(DATA_FILE, {})
    voice_audio_cfg = pulse_db.get_setting("voice_support_config", {})
    if not isinstance(voice_audio_cfg, dict):
        voice_audio_cfg = {}
    support_period_start = pulse_db.get_support_case_period_start()
    voice_support_counts = pulse_db.voice_support_case_counts(support_period_start, guild.id)
    voice_active_counts = active_voice_support_case_counts(guild.id)
    ticket_counts = pulse_db.ticket_member_activity_counts(support_period_start, guild.id)
    support_period_start_label = format_support_case_period_start(support_period_start)
    voice_period_total = sum(int(item.get("completed", 0)) for item in voice_support_counts.values())
    ticket_period_total = sum(int(item.get("completed", 0)) for item in ticket_counts.values())
    activity = get_activity_today(guild.id)
    activity_exists = bool(activity["check_id"])
    activity_confirmed = activity["confirmed"]
    apps_cache: list = []

    def apps_once():  # Phase 10: applications.json once per page instead of once per member without team_since
        if not apps_cache:
            apps_cache.append(load_json(APPS_FILE, {}))
        return apps_cache[0]

    members = []
    for member in guild.members:
        roles = [r for r in member.roles if r.id in team_role_ids]
        if not roles:
            continue
        top = max(roles, key=lambda r: r.position)
        secs = calculate_weekly_seconds(str(member.id), shifts_db["history"], active)
        hrs = secs / 3600
        loa = loas.get(str(member.id))
        on_loa = bool(loa and loa["active"])
        members.append({
            "id": member.id, "name": member.display_name, "username": member.name,
            "avatar": member.display_avatar.url, "role": top.name, "role_id": top.id,
            "team_rank_idx": team_rank(member, team_role_ids, guild), "pos": top.position,
            "team_since": team_since_for(member, team_db.get(str(member.id)) if isinstance(team_db, dict) else None,
                                         apps_once),
            "color": role_hex(top),
            # E-3: the warning badge is only shown to the person themself and can_warn/admins.
            "warns": (len(active_warns(user_entry(team_db, str(member.id)))) if isinstance(team_db, dict) else 0)
                     if can_see_warnings(ctx.perms, ctx.user.get("id"), member.id) else 0,
            "hrs": hrs, "reached": hrs >= weekly_goal, "on_loa": on_loa,
            "voice_cases_completed": int(voice_support_counts.get(str(member.id), {}).get("completed", 0)),
            "voice_cases_active": voice_active_counts.get(str(member.id), 0),
            "ticket_cases_completed": int(ticket_counts.get(str(member.id), {}).get("completed", 0)),
            "ticket_cases_active": int(ticket_counts.get(str(member.id), {}).get("active", 0)),
            "loa_until": fmt_date(loa["bis"]) if on_loa else "",
            "duty": active.get(str(member.id), {}).get("status"),
            "activity": (
                "confirmed"
                if activity_exists and (activity["eligible"] is None or member.id in activity["eligible"]) and member.id in activity_confirmed
                else "open"
                if activity_exists and (activity["eligible"] is None or member.id in activity["eligible"])
                else "not_in_snapshot"
                if activity_exists
                else "none"
            ),
            "discord_status": str(member.status),
            "discord_activity": next(
                (
                    getattr(a, "name", None) or getattr(a, "state", None)
                    for a in (member.activities or [])
                    if getattr(a, "name", None) or getattr(a, "state", None)
                ),
                None,
            ),
        })
    members.sort(key=lambda m: (-m["pos"], m["name"].lower()))

    below = len([m for m in members if not m["reached"] and not m["on_loa"]])
    activity_confirmed_count = sum(1 for m in members if m["activity"] == "confirmed")
    activity_open_count = sum(1 for m in members if m["activity"] == "open")
    activity_outside_snapshot_count = sum(1 for m in members if m["activity"] == "not_in_snapshot")
    activity_summary = (
        f"✅ {activity_confirmed_count} bestätigt · ⏳ {activity_open_count} offen"
        + (f" · ⚪ {activity_outside_snapshot_count} nicht im Check" if activity_outside_snapshot_count else "")
        if activity_exists else "⚪ Heute noch kein Activity Check"
    )
    summary = (f"{len(members)} Mitglieder · ✅ {len([m for m in members if m['reached']])} Ziel erreicht · "
               f"⚠️ {below} unter Ziel · 🟢 {len([m for m in members if m['duty']])} im Dienst · 🌴 {len([m for m in members if m['on_loa']])} abgemeldet · Activity: {activity_summary} · "
               f"🎧 {voice_period_total} Voice-Supportfälle · 🎫 {ticket_period_total} Tickets abgeschlossen seit {support_period_start_label}")

    confirmed_names = [m["name"] for m in members if m["activity"] == "confirmed"]
    open_names = [m["name"] for m in members if m["activity"] == "open"]
    not_in_snapshot_names = [m["name"] for m in members if m["activity"] == "not_in_snapshot"]
    activity_panel = f"""
    <section id="activity" class="{CARD} p-5 mb-5">
        <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
            <div><h2 class="text-base font-bold text-slate-900 dark:text-white">✅ Activity Check – Teamübersicht</h2>
            <p class="text-[11px] text-slate-500 dark:text-slate-400">{activity["check_date"]} · direkt aus dem Bot-Activity-Check</p></div>
            <div class="flex gap-2 text-[11px] font-semibold"><span class="px-3 py-1.5 rounded-xl border {BADGE_OK}">✅ {activity_confirmed_count} bestätigt</span><span class="px-3 py-1.5 rounded-xl border {BADGE_BAD}">⏳ {activity_open_count} offen</span></div>
        </div>
        <div class="grid md:grid-cols-2 gap-4 mt-4">
            <div class="rounded-xl bg-emerald-500/5 border border-emerald-500/20 p-3"><div class="text-[10px] font-bold uppercase tracking-wider text-emerald-600 mb-2">Bestätigt</div><div class="text-xs leading-6">{esc(", ".join(confirmed_names) or "Noch niemand bestätigt.")}</div></div>
            <div class="rounded-xl bg-rose-500/5 border border-rose-500/20 p-3"><div class="text-[10px] font-bold uppercase tracking-wider text-rose-600 mb-2">Noch offen</div><div class="text-xs leading-6">{esc(", ".join(open_names) or ("Niemand offen." if activity_exists else "Heute wurde noch kein Check gesendet."))}</div></div>
            <div class="rounded-xl bg-slate-500/5 border border-slate-500/20 p-3 md:col-span-2"><div class="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-2">Nicht Teil dieses Checks</div><div class="text-xs leading-6">{esc(", ".join(not_in_snapshot_names) or "Niemand.")}</div></div>
        </div>
    </section>"""

    rows_html = ""
    actor_team_rank = team_rank(ctx.member, team_role_ids, guild) if ctx.member else -1
    for m in members:
        style = BADGE_WARN if m["on_loa"] else (BADGE_OK if m["reached"] else BADGE_BAD)
        can_manage_target = bool(
            not ctx.perms.get("is_admin")
            and ctx.member
            and team_rank(ctx.member, team_role_ids, guild) > m["team_rank_idx"]
        ) or bool(ctx.perms.get("is_admin"))
        can_promote_target = bool(
            can_manage_target
            and m["team_rank_idx"] >= 0
            and m["team_rank_idx"] + 1 < len(team_role_ids)
            and (
                ctx.perms.get("is_admin")
                or (ctx.member and team_rank(ctx.member, team_role_ids, guild) > m["team_rank_idx"] + 1)
            )
        )
        can_demote_target = bool(can_manage_target and m["team_rank_idx"] >= 0)
        role_actions = ""
        if can_manage_target:
            promote_form = f'''
                <form action="/action" method="post" class="inline" onsubmit="return fillActionReason(this, 'Beförderung');">
                    <input type="hidden" name="action" value="promote">
                    <input type="hidden" name="user_id" value="{m['id']}">
                    <input type="hidden" name="redirect_to_member" value="">
                    <input type="hidden" name="action_reason" value="">
                    <select name="probation_days" required class="{INPUT} text-xs"><option value="">Probezeit wählen</option><option value="3">3 Tage Probezeit</option><option value="7">7 Tage Probezeit</option></select>
                    <button class="w-full text-left px-3 py-2 rounded-lg text-xs hover:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" {'disabled title="Höchster Rang erreicht"' if not can_promote_target else ''}>⬆️ Befördern</button>
                </form>''' if can_promote_target else ""
            demote_form = f'''
                <form action="/action" method="post" class="inline" onsubmit="return fillActionReason(this, 'Degradierung');">
                    <input type="hidden" name="action" value="demote">
                    <input type="hidden" name="user_id" value="{m['id']}">
                    <input type="hidden" name="redirect_to_member" value="">
                    <input type="hidden" name="action_reason" value="">
                    <button class="w-full text-left px-3 py-2 rounded-lg text-xs hover:bg-amber-500/10 text-amber-600 dark:text-amber-400">⬇️ Degradieren</button>
                </form>''' if can_demote_target else ""
            kick_form = f'''
                <form action="/action" method="post" class="inline" onsubmit="return fillActionReason(this, 'Kick') && confirm('Dieses Mitglied wirklich vom Discord-Server kicken?');">
                    <input type="hidden" name="action" value="kick">
                    <input type="hidden" name="user_id" value="{m['id']}">
                    <input type="hidden" name="redirect_to_member" value="">
                    <input type="hidden" name="action_reason" value="">
                    <button class="w-full text-left px-3 py-2 rounded-lg text-xs hover:bg-rose-500/10 text-rose-600 dark:text-rose-400">🚪 Team-Kick</button>
                </form>'''
            role_actions = f'''
                <details class="relative">
                    <summary class="list-none cursor-pointer select-none inline-flex items-center gap-2 px-3.5 py-2 bg-indigo-500/10 hover:bg-indigo-600 hover:text-white rounded-xl text-indigo-600 dark:text-indigo-400 text-xs transition font-semibold shadow-sm">
                        👁️ Aktionen
                    </summary>
                    <div class="absolute right-0 top-full mt-2 z-30 w-52 p-1.5 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-[#141824] shadow-2xl">
                        <a href="/member/{m['id']}" class="block px-3 py-2 rounded-lg text-xs hover:bg-slate-100 dark:hover:bg-slate-800">👤 Teamakte öffnen</a>
                        {promote_form}
                        {demote_form}
                        <a href="/team/{m['id']}/roles" class="block px-3 py-2 rounded-lg text-xs hover:bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">⚙️ Rollen ändern</a>
                        <div class="my-1 border-t border-slate-100 dark:border-slate-800"></div>
                        {kick_form}
                    </div>
                </details>'''
        else:
            role_actions = f'<a href="/member/{m["id"]}" class="px-3.5 py-2 bg-indigo-500/10 hover:bg-indigo-600 hover:text-white rounded-xl text-indigo-600 dark:text-indigo-400 text-xs transition font-semibold shadow-sm">👁️ Details</a>'
        bar_color = "bg-amber-500" if m["on_loa"] else ("bg-emerald-500" if m["reached"] else "bg-rose-500")
        pct = min(100, int(m["hrs"] / weekly_goal * 100)) if weekly_goal > 0 else 100
        loa_badge = (f'<span class="border {BADGE_WARN} text-[10px] px-2.5 py-0.5 rounded-full font-semibold">Abgemeldet bis {esc(m["loa_until"])}</span>'
                     if m["on_loa"] else "")
        duty_badge = ""
        if m["duty"]:
            duty_badge = (f'<span class="border {BADGE_WARN if m["duty"] == "break" else BADGE_OK} text-[10px] px-2.5 py-0.5 rounded-full font-semibold">'
                          f'{"☕ Pause" if m["duty"] == "break" else "🟢 Im Dienst"}</span>')
        presence_labels = {
            "online": "🟢 Online",
            "idle": "🟡 Abwesend",
            "dnd": "🔴 Bitte nicht stören",
            "offline": "⚪ Offline",
        }
        presence_text = presence_labels.get(m["discord_status"], "⚪ Offline")
        presence_badge = f'<span class="text-[10px] px-2 py-0.5 rounded-full font-semibold border bg-slate-50 dark:bg-slate-900/30 text-slate-600 dark:text-slate-300 border-slate-200 dark:border-slate-700">{presence_text}</span>'
        activity_badge_class = (
            BADGE_OK if m["activity"] == "confirmed"
            else BADGE_BAD if m["activity"] == "open"
            else "bg-slate-100 dark:bg-slate-800 text-slate-500 border-slate-200 dark:border-slate-700"
        )
        activity_badge_text = (
            "✅ Aktiv bestätigt" if m["activity"] == "confirmed"
            else "⏳ Nicht bestätigt" if m["activity"] == "open"
            else "⚪ Nicht Teil dieses Checks" if m["activity"] == "not_in_snapshot"
            else "⚪ Kein Check"
        )
        activity_name = f'<span class="text-[10px] text-slate-400 truncate max-w-[220px]" title="{esc(m["discord_activity"])}">🎮 {esc(m["discord_activity"])}</span>' if m["discord_activity"] else ""
        warn_badge = (f'<span class="border border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400 text-[10px] px-2 py-0.5 rounded-full font-semibold">🚨 {m["warns"]}/5 Warnungen</span>' if m["warns"] >= 5 else
                      f'<span class="border border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400 text-[10px] px-2 py-0.5 rounded-full font-semibold">⚠ {m["warns"]}/5 Warnungen</span>' if m["warns"] else '')
        rows_html += f"""
        <div class="team-row {CARD} hover:bg-slate-50 dark:hover:bg-[#1a2030] transition px-5 py-4 flex flex-col md:flex-row md:items-center justify-between gap-3"
             data-search="{esc((m['name'] + ' ' + m['username'] + ' ' + m['role']).lower())}" data-below="{1 if (not m['reached'] and not m['on_loa']) else 0}">
            <div class="flex items-center gap-3.5 md:w-1/5 min-w-0">
                <img src="{esc(m['avatar'])}" alt="" class="w-11 h-11 rounded-full border border-slate-200 dark:border-slate-700 shadow-sm">
                <div class="truncate">
                    <div class="font-semibold text-sm text-slate-900 dark:text-white flex items-center gap-2 flex-wrap"><span>{esc(m['name'])}</span>{presence_badge}{loa_badge}{duty_badge}{warn_badge}<span class="text-[10px] px-2 py-0.5 rounded-full font-semibold border {activity_badge_class}">{activity_badge_text}</span>{activity_name}</div>
                    <div class="text-xs text-slate-400 font-mono">@{esc(m['username'])}</div>
                </div>
            </div>
            <div class="md:w-1/5 space-y-1.5">
                <div class="flex items-center gap-3 flex-wrap">
                    <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border" style="background-color:{m['color']}15;color:{m['color']};border-color:{m['color']}40;">
                        <span class="w-1.5 h-1.5 rounded-full" style="background-color:{m['color']}"></span>{esc(m['role'])}
                    </span>
                    <span class="text-xs font-mono font-bold px-2.5 py-1 rounded-lg border {style}" title="Soll-Ziel: {weekly_goal}h/Woche">⏱️ {m['hrs']:.1f}h / {weekly_goal:g}h</span>
                    <span class="text-xs font-mono font-bold px-2.5 py-1 rounded-lg border border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400" title="Abgeschlossene Voice-Supportfälle seit dem letzten Teammeeting; offen = aktuell zugewiesene Fälle">🎧 {m['voice_cases_completed']} erledigt · {m['voice_cases_active']} offen</span>
                    <span class="text-xs font-mono font-bold px-2.5 py-1 rounded-lg border border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400" title="Tickets seit dem letzten Teammeeting; offen = aktuell übernommene Tickets">🎫 {m['ticket_cases_completed']} erledigt · {m['ticket_cases_active']} offen</span>
                </div>
                <div class="text-[10px] text-slate-400">🗓️ Team seit <span class="font-semibold text-slate-500 dark:text-slate-300">{esc(m['team_since'])}</span></div>
                <div class="h-1.5 rounded-full bg-slate-200 dark:bg-slate-800 overflow-hidden"><div class="h-full {bar_color}" style="width:{pct}%"></div></div>
            </div>
            <div class="md:w-2/5 flex flex-wrap md:justify-end gap-2 items-center">
                {role_actions}
            </div>
        </div>"""

    head = """<style>
        details > summary::-webkit-details-marker { display: none; }
        details[open] > summary { background: rgba(79,70,229,.14); }
        .team-row details[open] { z-index: 40; }
    </style>
    <script>
        function fillActionReason(form, label) {
            const reason = prompt("Bitte gib den Grund für " + label + " ein:");
            if (reason === null) return false;
            const clean = reason.trim().slice(0, 500);
            if (!clean) {
                alert("Ein Grund ist Pflicht.");
                return false;
            }
            const field = form.querySelector('[name="action_reason"]');
            if (field) field.value = clean;
            return true;
        }
        function filterTeam() {
            const q = document.getElementById('searchInput').value.toLowerCase();
            const onlyBelow = document.getElementById('onlyBelow').checked;
            document.querySelectorAll('.team-row').forEach(r => {
                const ok = r.dataset.search.includes(q) && (!onlyBelow || r.dataset.below === '1');
                r.style.display = ok ? '' : 'none';
            });
        }
    </script>"""
    hire_panel = ""
    if ctx.perms.get("can_promote") or ctx.perms.get("is_admin"):
        hire_roles = [
            guild.get_role(rid) for rid in team_role_ids
            if guild.get_role(rid) and not guild.get_role(rid).managed and not guild.get_role(rid).is_default()
        ]
        if hire_roles:
            hire_role_options = "".join(
                f'<option value="{role.id}">{esc(role.name)}</option>'
                for role in hire_roles
            )
            hire_panel = f"""
            <section class="{CARD} p-5 mb-6">
                <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-3 mb-4">
                    <div>
                        <h2 class="text-base font-bold text-slate-900 dark:text-white">➕ Teamler manuell einstellen</h2>
                        <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">Discord-ID, Benutzername oder Anzeigename eingeben und die Teamrolle festlegen. Die Person muss bereits auf diesem Discord-Server sein.</p>
                    </div>
                    <span class="text-[10px] font-semibold px-3 py-1 rounded-full border {BADGE_OK}">Führung</span>
                </div>
                <form action="/action" method="post" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                    <input type="hidden" name="action" value="hire">
                    <label class="text-[11px] font-semibold text-slate-600 dark:text-slate-300">Discord-Nutzer
                        <input name="target_user_id" required maxlength="100" autocomplete="off" class="mt-1 w-full bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 rounded-xl px-3 py-2.5 text-xs" placeholder="ID, @Benutzername oder Name">
                    </label>
                    <label class="text-[11px] font-semibold text-slate-600 dark:text-slate-300">Teamrolle
                        <select name="hire_role_id" required class="mt-1 w-full bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 rounded-xl px-3 py-2.5 text-xs">
                            <option value="">Teamrolle auswählen…</option>
                            {hire_role_options}
                        </select>
                    </label>
                    <label class="text-[11px] font-semibold text-slate-600 dark:text-slate-300">Pflichtgrund
                        <input name="action_reason" required maxlength="500" class="mt-1 w-full bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 rounded-xl px-3 py-2.5 text-xs" placeholder="Grund für die Einstellung">
                    </label>
                    <div class="flex items-end">
                        <button class="w-full px-4 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold shadow-sm" onclick="return confirm('Diese Person wirklich mit der ausgewählten Teamrolle einstellen?')">🎉 Teamler einstellen</button>
                    </div>
                </form>
            </section>"""
        else:
            hire_panel = f'<section class="{CARD} p-4 mb-6 text-sm text-amber-600">⚠️ Es sind keine gültigen Teamrollen konfiguriert. Bitte zuerst die Teamrollen in den Einstellungen festlegen.</section>'


    voice_audio_editor = ""
    if ctx.perms.get("can_promote") or ctx.perms.get("is_admin"):
        def audio_status(config_key, fallback_text):
            raw_path = str(voice_audio_cfg.get(config_key) or "").strip()
            if not raw_path:
                if config_key == "custom_opening_audio_path" and voice_audio_cfg.get("waiting_music_enabled") and voice_audio_cfg.get("waiting_music_path"):
                    return "ℹ️ Keine eigene Datei. Die bisher konfigurierte Wartemusik bleibt als Rückfalloption aktiv."
                return fallback_text
            try:
                candidate = Path(raw_path)
                if not candidate.is_absolute():
                    candidate = DATA_DIR / candidate
                candidate = candidate.resolve()
                if candidate.is_file():
                    return f"✅ Eigene Audiodatei aktiv: {esc(candidate.name)} · {round(candidate.stat().st_size / 1024, 1)} KB"
            except (OSError, RuntimeError):
                pass
            return "⚠️ Der gespeicherte Audiopfad ist nicht verfügbar. Bitte lade die Datei erneut hoch."

        def audio_remove_form(audio_type, config_key):
            if not str(voice_audio_cfg.get(config_key) or "").strip():
                return ""
            return f"""
                <form action="/team/voice-support/audio" method="post" onsubmit="return confirm('Eigene Audio wirklich entfernen?')">
                    <input type="hidden" name="audio_type" value="{audio_type}">
                    <input type="hidden" name="audio_action" value="remove">
                    <button class="px-3 py-2 rounded-xl text-xs font-semibold border border-rose-500/30 bg-rose-500/5 text-rose-600 dark:text-rose-400 hover:bg-rose-500/10">Eigene Audio entfernen</button>
                </form>"""

        opening_audio_status = audio_status(
            "custom_opening_audio_path",
            "⚪ Keine eigene Audio eingerichtet. Während der Supportzeiten wird dann keine eigene Wartemeldung abgespielt."
        )
        after_hours_audio_status = audio_status(
            "custom_after_hours_audio_path",
            "✅ Eingebaute deutsche Standardansage aktiv."
        )
        voice_audio_editor = f"""
        <section id="voice-audio" class="{CARD} p-5 md:p-6 mb-6 space-y-4">
            <div>
                <h2 class="text-base font-bold text-slate-900 dark:text-white">🔊 Voice-Support-Audios</h2>
                <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">Eigene Audiodateien direkt hier verwalten. Änderungen werden gespeichert und vom Voice-Support ohne Änderung an den Öffnungszeiten verwendet. Erlaubt: MP3, WAV, OGG, FLAC oder M4A · maximal {MAX_VOICE_SUPPORT_AUDIO_BYTES // (1024 * 1024)} MB.</p>
            </div>
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div class="rounded-2xl border border-slate-200 dark:border-slate-700 p-4 space-y-3">
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white">🟢 Während der Supportzeiten</h3>
                    <p class="text-xs text-slate-500">Wird im privaten Support-Sprachraum wiederholt, solange der Bürger auf einen Supporter wartet. Sobald ein Teamler übernimmt, stoppt die Wiedergabe.</p>
                    <p class="text-xs font-semibold">{opening_audio_status}</p>
                    <form action="/team/voice-support/audio" method="post" enctype="multipart/form-data" class="space-y-3">
                        <input type="hidden" name="audio_type" value="opening">
                        <input type="hidden" name="audio_action" value="upload">
                        <input type="file" name="audio_file" aria-label="Audiodatei wählen" accept=".mp3,.wav,.ogg,.flac,.m4a,audio/*" required class="block w-full text-xs text-slate-600 dark:text-slate-300">
                        <button class="px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold">Audio während Supportzeiten speichern</button>
                    </form>
                    {audio_remove_form("opening", "custom_opening_audio_path")}
                </div>
                <div class="rounded-2xl border border-slate-200 dark:border-slate-700 p-4 space-y-3">
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white">🌙 Außerhalb der Supportzeiten</h3>
                    <p class="text-xs text-slate-500">Wird einmal im gemeinsamen Warteraum abgespielt, wenn ein neuer Supportfall außerhalb der Öffnungszeiten aufgenommen wird. Ohne eigene Datei bleibt die deutsche Standardansage aktiv.</p>
                    <p class="text-xs font-semibold">{after_hours_audio_status}</p>
                    <form action="/team/voice-support/audio" method="post" enctype="multipart/form-data" class="space-y-3">
                        <input type="hidden" name="audio_type" value="after_hours">
                        <input type="hidden" name="audio_action" value="upload">
                        <input type="file" name="audio_file" aria-label="Audiodatei wählen" accept=".mp3,.wav,.ogg,.flac,.m4a,audio/*" required class="block w-full text-xs text-slate-600 dark:text-slate-300">
                        <button class="px-4 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-bold">Audio außerhalb Supportzeiten speichern</button>
                    </form>
                    {audio_remove_form("after_hours", "custom_after_hours_audio_path")}
                </div>
            </div>
        </section>"""

    body = f"""
    <div class="flex flex-col md:flex-row md:justify-between md:items-center gap-4 mb-6">
        <div>
            <div class="text-xs text-slate-400 mb-1">Team / <span class="text-indigo-600 dark:text-indigo-400 font-medium">Teamliste</span></div>
            <h1 class="text-2xl font-bold text-slate-900 dark:text-white">Teamliste & Wochenziel ({weekly_goal:g}h)</h1>
            <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">{summary}</p>
            <div class="mt-2 inline-flex flex-wrap items-center gap-2 text-[11px] font-semibold">
                <span class="px-3 py-1.5 rounded-xl border {BADGE_OK if activity_exists else "bg-slate-100 dark:bg-slate-800 text-slate-500 border-slate-200 dark:border-slate-700"}">{"✅ Activity Check ausgewertet" if activity_exists else "⚪ Kein Activity Check heute"}</span>
                <a href="/team" class="px-3 py-1.5 rounded-xl bg-slate-100 dark:bg-slate-800 hover:bg-indigo-600 hover:text-white transition">🔄 Aktualisieren</a>
            </div>
        </div>
        <div class="flex items-center gap-3">
            <label class="text-xs flex items-center gap-1.5 cursor-pointer text-slate-600 dark:text-slate-300"><input type="checkbox" id="onlyBelow" onchange="filterTeam()" class="rounded"> Nur unter Ziel</label>
            <input type="text" id="searchInput" oninput="filterTeam()" placeholder="Teammitglied suchen..." class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-700 text-xs text-slate-900 dark:text-white rounded-xl px-4 py-2.5 w-56 focus:outline-none focus:border-indigo-500 shadow-sm">
        </div>
    </div>
    {hire_panel}
    {voice_audio_editor}
    <div class="space-y-3">{rows_html or f"<div class='text-center py-12 text-slate-400 text-sm {CARD}'>Keine Teammitglieder gefunden. Team-Rollen kannst du unter Einstellungen festlegen.</div>"}</div>"""
    return render_page("Teamliste", ctx, "team", body, head)


@app.post("/team/voice-support/audio")
async def manage_voice_support_audio(
    request: Request,
    audio_type: str = Form(...),
    audio_action: str = Form("upload"),
    audio_file: UploadFile | None = File(None),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, perm="can_promote")
    def audio_redirect(message, ok=True):
        response = back("/team", message, ok)
        response.headers["location"] = response.headers.get("location", "/team") + "#voice-audio"
        return response

    if audio_type not in {"opening", "after_hours"}:
        return audio_redirect( "Ungültiger Audio-Typ.", False)
    if audio_action not in {"upload", "remove"}:
        return audio_redirect( "Ungültige Audio-Aktion.", False)

    config_key = "custom_opening_audio_path" if audio_type == "opening" else "custom_after_hours_audio_path"
    expected_prefix = f"{ctx.guild.id}_support_{audio_type}_"
    audio_dir = VOICE_SUPPORT_AUDIO_DIR.resolve()

    def remove_owned_audio(raw_path):
        raw = str(raw_path or "").strip()
        if not raw:
            return
        try:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = DATA_DIR / candidate
            candidate = candidate.resolve()
            if candidate.parent == audio_dir and candidate.name.startswith(expected_prefix) and candidate.is_file():
                candidate.unlink()
        except (OSError, RuntimeError):
            pass

    config = pulse_db.get_setting("voice_support_config", {})
    if not isinstance(config, dict):
        config = {}
    old_path = str(config.get(config_key) or "").strip()

    if audio_action == "remove":
        config.pop(config_key, None)
        pulse_db.set_setting("voice_support_config", config)
        remove_owned_audio(old_path)
        actor_name = ctx.member.display_name if ctx.member else str(ctx.user.get("global_name") or "Teamleitung")
        log_audit(actor_name, str(ctx.user["id"]), "Voice-Support Audio entfernt",
                  f"{'Während der Supportzeiten' if audio_type == 'opening' else 'Außerhalb der Supportzeiten'} · eigene Datei entfernt")
        return audio_redirect( "Eigene Audio entfernt. Die Standardkonfiguration wird wieder verwendet.")

    if not audio_file or not audio_file.filename:
        return audio_redirect( "Bitte zuerst eine Audiodatei auswählen.", False)
    extension = Path(audio_file.filename).suffix.lower()
    if extension not in VOICE_SUPPORT_AUDIO_EXTENSIONS:
        return audio_redirect( "Dateiformat nicht unterstützt. Erlaubt sind MP3, WAV, OGG, FLAC und M4A.", False)

    try:
        raw = await audio_file.read(MAX_VOICE_SUPPORT_AUDIO_BYTES + 1)
    except Exception:
        return audio_redirect( "Die Audiodatei konnte nicht gelesen werden.", False)
    finally:
        try:
            await audio_file.close()
        except Exception:
            pass

    if len(raw) > MAX_VOICE_SUPPORT_AUDIO_BYTES:
        return audio_redirect( f"Die Audiodatei ist zu groß. Maximum: {MAX_VOICE_SUPPORT_AUDIO_BYTES // (1024 * 1024)} MB.", False)
    if len(raw) < 100:
        return audio_redirect( "Die Datei ist leer oder zu kurz, um eine gültige Audiodatei zu sein.", False)

    def valid_audio_signature(ext, data):
        if ext == ".mp3":
            return data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
        if ext == ".wav":
            return data[:4] in {b"RIFF", b"RF64"} and data[8:12] == b"WAVE"
        if ext == ".ogg":
            return data.startswith(b"OggS")
        if ext == ".flac":
            return data.startswith(b"fLaC")
        if ext == ".m4a":
            return len(data) >= 12 and data[4:8] == b"ftyp"
        return False

    if not valid_audio_signature(extension, raw):
        return audio_redirect( "Die Dateiendung passt nicht zum Audioinhalt oder die Datei ist beschädigt.", False)

    try:
        VOICE_SUPPORT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        target = VOICE_SUPPORT_AUDIO_DIR / f"{ctx.guild.id}_support_{audio_type}_{uuid.uuid4().hex[:8]}{extension}"
        temporary = target.with_name(target.name + ".uploading")
        temporary.write_bytes(raw)
        os.replace(temporary, target)
        relative_path = target.resolve().relative_to(DATA_DIR.resolve()).as_posix()
        config[config_key] = relative_path
        pulse_db.set_setting("voice_support_config", config)
        if old_path and old_path != relative_path:
            remove_owned_audio(old_path)
        actor_name = ctx.member.display_name if ctx.member else str(ctx.user.get("global_name") or "Teamleitung")
        human_type = "Während der Supportzeiten" if audio_type == "opening" else "Außerhalb der Supportzeiten"
        log_audit(actor_name, str(ctx.user["id"]), "Voice-Support Audio gespeichert",
                  f"{human_type} · {target.name} · {len(raw)} Bytes")
        return audio_redirect( f"{human_type}: Audiodatei erfolgreich gespeichert.")
    except (OSError, ValueError, RuntimeError) as exc:
        try:
            if "temporary" in locals() and temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        print(f"⚠️ Voice-Support Audio-Upload fehlgeschlagen: {type(exc).__name__}: {exc}")
        return audio_redirect( "Audio konnte nicht gespeichert werden. Prüfe den verfügbaren Speicherplatz.", False)


# =============================================================
# ROUTE: MITGLIEDER-DETAILSEITE
# =============================================================
@app.get("/team/{user_id}/roles", response_class=HTMLResponse)
async def team_role_manager(request: Request, user_id: int, user_session: str = Cookie(None)):
    ctx = auth(request, user_session, perm="can_promote")
    guild, config = ctx.guild, ctx.config
    member = guild.get_member(user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden.")
    if member.bot or member.id == guild.owner_id or member.id == int(ctx.user["id"]):
        raise HTTPException(status_code=403, detail="Dieses Mitglied kann nicht über die Rollenverwaltung bearbeitet werden.")

    team_role_ids = ordered_team_role_ids(guild, config.get("team_role_ids", []))
    was_team_member = bool(team_role_ids and any(r.id in team_role_ids for r in member.roles))
    actor_idx = team_rank(ctx.member, team_role_ids, guild) if ctx.member else -1
    target_idx = team_rank(member, team_role_ids, guild)
    if not ctx.perms.get("is_admin") and actor_idx <= target_idx:
        raise HTTPException(status_code=403, detail="Du kannst nur Mitglieder mit niedrigerem Rang bearbeiten.")

    # Grouped, searchable manager; posts the same fields to the unchanged save handler below.
    from pulse_role_manager import ROLE_CSS, render_role_manager
    return render_page(
        f"Rollenverwaltung · {member.display_name}", ctx, "team",
        render_role_manager(esc, ctx, member, team_role_ids), ROLE_CSS,
    )


# SEC-08: rights that let the holder take over or sanction beyond the Pulse model. Deliberately not the
# everyday ones (manage_messages, manage_nicknames, …) that ordinary department roles carry.
ESCALATING_ROLE_PERMISSIONS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
                               "ban_members", "kick_members")


@app.post("/team/{user_id}/roles")
async def team_role_manager_save(
    request: Request,
    user_id: int,
    add_role_ids: List[int] = Form(default=[]),
    remove_role_ids: List[int] = Form(default=[]),
    action_reason: str = Form(...),
    user_session: str = Cookie(None),
):
    ctx = auth(request, user_session, perm="can_promote")
    guild, config = ctx.guild, ctx.config
    member = guild.get_member(user_id)
    if not member:
        return back("/team", "Mitglied nicht gefunden.", False)
    if member.bot or member.id == guild.owner_id or member.id == int(ctx.user["id"]):
        return back(f"/team/{user_id}/roles", "Dieses Mitglied kann nicht bearbeitet werden.", False)

    add_set, remove_set = set(add_role_ids or []), set(remove_role_ids or [])
    team_role_ids = ordered_team_role_ids(guild, config.get("team_role_ids", []))
    was_team_member = bool(team_role_ids and any(r.id in team_role_ids for r in member.roles))
    conflict = add_set & remove_set
    if conflict:
        return back(f"/team/{user_id}/roles", "Eine Rolle darf nicht gleichzeitig hinzugefügt und entfernt werden.", False)

    team_role_ids = ordered_team_role_ids(guild, config.get("team_role_ids", []))
    actor_idx = team_rank(ctx.member, team_role_ids, guild) if ctx.member else -1
    target_idx = team_rank(member, team_role_ids, guild)
    if not ctx.perms.get("is_admin") and actor_idx <= target_idx:
        return back(f"/team/{user_id}/roles", "Du kannst nur Mitglieder mit niedrigerem Rang bearbeiten.", False)

    # AUTHZ-5: the role manager is an alternative rank-change path – an active Teamler-Schutz lockout applies here too.
    safety_ok, safety_message = await enforce_team_safety_action_lockout(
        guild, ctx.member, member, "promote", config, source="Rollenmanager",
    )
    if not safety_ok:
        return back(f"/team/{user_id}/roles", safety_message, False)

    action_reason = (action_reason or "").strip()[:500]
    if not action_reason:
        return back(f"/team/{user_id}/roles", "Bitte einen Grund für die Rollenänderung angeben.", False)

    all_ids = add_set | remove_set
    if not all_ids:
        return back(f"/team/{user_id}/roles", "Keine Rollenänderung ausgewählt.", False)

    selected = []
    for rid in sorted(all_ids):
        role = guild.get_role(rid)
        if not role or role.is_default() or role.managed:
            return back(f"/team/{user_id}/roles", f"Rolle {rid} ist nicht verwaltbar.", False)
        if guild.me and role.position >= guild.me.top_role.position:
            return back(f"/team/{user_id}/roles", f"Die Rolle **{role.name}** steht über der Bot-Rolle.", False)
        if not ctx.perms.get("is_admin"):
            if ctx.member is None or role.position >= ctx.member.top_role.position:
                return back(f"/team/{user_id}/roles", f"Die Rolle **{role.name}** steht auf oder über deinem Rang.", False)
            # SEC-08: a role below the own top role can still carry server-wide rights (e.g. a forgotten
            # admin or moderation role). Only admins hand those out; configured team roles keep their
            # rank rule (their moderation rights are intended), Administrator is never delegable.
            role_perms = role.permissions
            risky = [name for name in ESCALATING_ROLE_PERMISSIONS if getattr(role_perms, name, False)]
            if rid in add_set and (role_perms.administrator or (risky and role.id not in team_role_ids)):
                return back(f"/team/{user_id}/roles",
                            f"Die Rolle **{role.name}** hat weitreichende Serverrechte ({', '.join(risky[:4])}) – "
                            "nur Administratoren dürfen sie vergeben.", False)
        selected.append(role)

    old_roles = [r for r in member.roles if r.id in all_ids]
    to_add = [r for r in selected if r.id in add_set and r not in member.roles]
    to_remove = [r for r in selected if r.id in remove_set and r in member.roles]

    try:
        if to_add:
            await member.add_roles(*to_add, reason=f"Rollenänderung durch {ctx.user.get('global_name') or ctx.user.get('username') or 'Team'}: {action_reason}")
        if to_remove:
            await member.remove_roles(*to_remove, reason=f"Rollenänderung durch {ctx.user.get('global_name') or ctx.user.get('username') or 'Team'}: {action_reason}")

        resulting_role_ids = ({r.id for r in member.roles} | {r.id for r in to_add}) - {r.id for r in to_remove}
        is_team_member = bool(team_role_ids and any(rid in team_role_ids for rid in resulting_role_ids))
        actor = ctx.user.get("global_name") or ctx.user.get("username") or "Team"
        add_text = ", ".join(r.mention for r in to_add) or "Keine"
        remove_text = ", ".join(r.mention for r in to_remove) or "Keine"
        audit_ref = log_audit(
            actor,
            ctx.user["id"],
            "Rollenänderung",
            f"{member.display_name}: +{[r.name for r in to_add]} -{[r.name for r in to_remove]} · Grund: {action_reason}",
        )
        if not was_team_member and is_team_member:
            hired_at = now_de().isoformat(timespec="seconds")
            team_db = load_json(DATA_FILE, {})
            team_entry = user_entry(team_db, str(member.id))
            team_entry["team_since"] = hired_at
            store_employment_event(team_entry, build_employment_event(
                user_id=member.id,
                hired_at=hired_at,
                actor_id=ctx.user["id"],
                source="team_role_assignment",
                audit_ref=audit_ref,
                career_path=team_entry.get("career_path"),
            ))
            save_json(DATA_FILE, team_db)
        await send_team_update_embed(
            guild,
            "⚙️ Team-Update: Rollenänderung",
            f"{member.mention} wurde über die Pulse-Rollenverwaltung geändert.",
            _pulse_color("info"),
            target=member.mention,
            action="Mehrfach-Rollenänderung",
            actor=actor,
            fields=[
                ("➕ Hinzugefügt", add_text, False),
                ("➖ Entfernt", remove_text, False),
                ("📝 Grund", action_reason, False),
            ],
            thumbnail=member.display_avatar.url,
        )
        return back(f"/team/{user_id}/roles", "Rollenänderung erfolgreich gespeichert.")
    except discord.Forbidden:
        return back(f"/team/{user_id}/roles", "Discord verweigert die Rollenänderung. Prüfe Bot-Rolle und Rollenrechte.", False)
    except Exception as exc:
        print(f"Rollenverwaltung fehlgeschlagen: {exc}")
        return back(f"/team/{user_id}/roles", "Rollenänderung fehlgeschlagen.", False)

@app.get("/member/{user_id}", response_class=HTMLResponse)
async def member_detail(request: Request, user_id: int, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    user_id = int(user_id)
    guild = ctx.guild
    member = guild.get_member(user_id)
    if not member:
        raise HTTPException(status_code=404, detail=f"Mitglied mit ID {user_id} wurde nicht gefunden.")

    team_role_ids = ordered_team_role_ids(guild, ctx.config.get("team_role_ids", []))
    team_db = load_json(DATA_FILE, {})
    shifts_db = load_shifts()
    info = team_db.get(str(user_id), {"warns_list": [], "notes": [], "ticket_cases": 0, "support_cases": 0})
    if normalize_warns(info):
        team_db[str(user_id)] = info
        save_json(DATA_FILE, team_db)
    normalize_warns(info)
    warns = info.get("warns_list", [])
    active_warn_count = len(active_warns(info))
    weekly = calculate_weekly_seconds(str(user_id), shifts_db["history"], shifts_db["active_shifts"]) / 3600
    support_period_start = pulse_db.get_support_case_period_start()
    voice_case_stats = pulse_db.voice_support_case_counts(support_period_start, guild.id)
    voice_active_counts = active_voice_support_case_counts(guild.id)
    ticket_stats = pulse_db.ticket_member_activity_counts(support_period_start, guild.id)
    voice_case_completed = int(voice_case_stats.get(str(user_id), {}).get("completed", 0))
    voice_case_active = int(voice_active_counts.get(str(user_id), 0))
    ticket_case_stats = ticket_stats.get(str(user_id), {})
    ticket_case_completed = int(ticket_case_stats.get("completed", 0))
    ticket_case_active = int(ticket_case_stats.get("active", 0))
    support_period_start_label = format_support_case_period_start(support_period_start)

    roles = [r for r in member.roles if r.id in team_role_ids]
    top = max(roles, key=lambda r: r.position) if roles else member.top_role
    top_color = role_hex(top)
    is_self = str(user_id) == ctx.user["id"]

    warns_html = ""
    for w in warns:
        proof = safe_url(w.get("proof"))
        proof_btn = f'<a href="{esc(proof)}" target="_blank" rel="noopener noreferrer" class="text-indigo-600 dark:text-indigo-400 hover:underline ml-2 font-medium">🔗 Beweis</a>' if proof else ""
        remove = f"""
                <form action="/action" method="post" onsubmit="return confirm('Diesen Warn wirklich zurückziehen?');">
                    <input type="hidden" name="action" value="remove_warn"><input type="hidden" name="user_id" value="{user_id}">
                    <input type="hidden" name="warn_id" value="{esc(w.get('id'))}"><input type="hidden" name="redirect_to_member" value="1">
                    <input type="text" name="warn_revoke_reason" maxlength="500" placeholder="Grund für die Rücknahme..." required class="{INPUT} py-1.5 w-56">
                    <button class="text-rose-500 hover:underline font-semibold">🗑️ Zurückziehen</button>
                </form>""" if ctx.perms["can_warn"] else ""
        warns_html += f"""
        <div class="bg-slate-50 dark:bg-[#0b0e14] p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs space-y-2">
            <div class="flex justify-between items-center text-slate-400 font-mono text-[10px]">
                <span>Datum: {esc(w.get('date', 'N/A'))} | Von: {esc(w.get('by', 'System'))}</span>{remove}
            </div>
            <div class="text-slate-400">Gültigkeit: {esc((w.get("duration_days") or "Dauerhaft") if not w.get("expires_at") else str(w.get("duration_days") or "") + " Tage · bis " + str(w.get("expires_at")) )}</div>
        <div class="text-slate-700 dark:text-slate-200"><strong>Grund:</strong> {esc(w.get('reason', 'Kein Grund'))}{proof_btn}</div>
        </div>"""

    notes_html = "".join(f"<div class='text-xs bg-slate-50 dark:bg-[#0b0e14] p-3 rounded-xl border border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-300'>• {esc(n)}</div>"
                         for n in info.get("notes", []))

    history = [h for h in shifts_db["history"] if str(h.get("mod_id")) == str(user_id)][-8:]
    shifts_html = "".join(f"""
        <div class="flex justify-between text-xs bg-slate-50 dark:bg-[#0b0e14] px-3.5 py-2 rounded-xl border border-slate-200 dark:border-slate-800">
            <span class="text-slate-500">{esc(fmt_date(h.get('date')))}{(' · automatisch beendet · unbestätigt (0 h)' if needs_duration_confirmation(h) else ' · automatisch beendet') if h.get('auto_closed') else ''}</span>
            <span class="font-mono font-semibold text-slate-800 dark:text-slate-200">{fmt_duration(credited_shift_seconds(h))}</span>
        </div>""" for h in reversed(history))

    actions_html = ""
    if ctx.perms["can_promote"] and not is_self:
        can_manage_target = ctx.perms.get("is_admin") or (
            team_role_ids and team_rank(ctx.member, team_role_ids, guild) > team_rank(member, team_role_ids, guild)
        )
        if can_manage_target:
            actions_html = f"""
            <div class="rounded-2xl border border-slate-200 dark:border-slate-800 bg-slate-50/70 dark:bg-[#0b0e14]/50 p-4 space-y-4">
                <div>
                    <div class="text-xs font-bold text-slate-900 dark:text-white mb-2">Team-Rang ändern</div>
                    <form action="/action" method="post" class="flex flex-wrap gap-2 items-center">
                        <input type="hidden" name="user_id" value="{member.id}"><input type="hidden" name="redirect_to_member" value="1">
                        <input type="text" name="action_reason" maxlength="500" placeholder="Pflicht: Grund für Beförderung / Degradierung..." required class="{INPUT} min-w-64">
                        <select name="probation_days" class="{INPUT}" aria-label="Probezeit (nur bei Beförderung)"><option value="">Probezeit wählen</option><option value="3">3 Tage Probezeit</option><option value="7">7 Tage Probezeit</option></select>
                        <button name="action" value="promote" onclick="this.form.querySelector('[name=probation_days]').required=true; return confirm('Wirklich befördern?')" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⬆️ Befördern</button>
                        <button name="action" value="demote" onclick="this.form.querySelector('[name=probation_days]').required=false; return confirm('Wirklich degradieren?')" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⬇️ Degradieren</button>
                    </form>
                </div>
                <div class="flex flex-wrap gap-2">
                    <a href="/team/{member.id}/roles" class="bg-indigo-500/10 hover:bg-indigo-600 hover:text-white text-indigo-600 dark:text-indigo-400 border border-indigo-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">⚙️ Rollen verwalten</a>
                    <form action="/action" method="post" class="flex flex-wrap gap-2 items-center" onsubmit="return confirm('Dieses Mitglied wirklich aus dem Team entfernen? Es bleibt auf dem Discord-Server.');">
                        <input type="hidden" name="action" value="kick"><input type="hidden" name="user_id" value="{member.id}">
                        <input type="text" name="action_reason" maxlength="500" placeholder="Pflicht: Grund für den Kick..." required class="{INPUT} w-64">
                        <button class="bg-rose-500/10 hover:bg-rose-600 hover:text-white text-rose-600 dark:text-rose-400 border border-rose-500/30 px-3.5 py-2 rounded-xl text-xs font-semibold transition">🚪 Kicken</button>
                    </form>
                </div>
            </div>
            <hr class="border-slate-100 dark:border-slate-800 my-4">"""
    warn_form = f"""
        <h3 class="text-sm font-bold text-slate-900 dark:text-white">Verwarnung ausstellen</h3>
        <form action="/action" method="post" class="space-y-2.5 text-xs">
            <input type="hidden" name="user_id" value="{member.id}"><input type="hidden" name="action" value="warn_with_proof"><input type="hidden" name="redirect_to_member" value="1">
            <input type="text" name="warn_reason" maxlength="500" placeholder="Grund für die Verwarnung..." required class="{INPUT}">
            <label class="block text-xs font-semibold">Gültigkeit der Warnung (Tage)<input type="number" name="warn_duration_days" min="1" max="365" value="7" required class="{INPUT} w-full mt-1"><span class="text-slate-400 font-normal">Nach Ablauf wird die Warn automatisch deaktiviert.</span></label>
            <input type="url" name="warn_proof" placeholder="Beweis-Link (Screenshot / Video URL)..." class="{INPUT}">
            <button class="bg-amber-500 hover:bg-amber-600 px-4 py-2 rounded-xl font-semibold text-white shadow-sm transition">⚠️ Verwarnung eintragen</button>
        </form>
        <hr class="border-slate-100 dark:border-slate-800 my-4">""" if ctx.perms["can_warn"] else ""

    note_form = f"""
        <form action="/action" method="post" class="flex gap-2 pt-1 text-xs">
            <input type="hidden" name="user_id" value="{member.id}"><input type="hidden" name="action" value="add_note"><input type="hidden" name="redirect_to_member" value="1">
            <input type="text" name="note_text" maxlength="500" placeholder="Neue Notiz..." required class="{INPUT}">
            <button class="{BTN} px-4">Hinzufügen</button>
        </form>""" if ctx.perms["can_add_notes"] else ""

    warn_color = "text-rose-500" if active_warn_count >= 5 else "text-amber-500"
    body = f"""
    <div class="flex items-center gap-4 mb-8">
        <a href="/team" class="bg-white dark:bg-[#141824] border border-slate-200 dark:border-slate-800 hover:bg-slate-100 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-300 p-2.5 rounded-xl transition shadow-sm">←</a>
        <img src="{esc(member.display_avatar.url)}" alt="" class="w-12 h-12 rounded-full border border-slate-200 dark:border-slate-700 shadow-sm">
        <div>
            <h1 class="text-xl font-bold text-slate-900 dark:text-white leading-tight">{esc(member.display_name)}</h1>
            <p class="text-xs text-slate-400 font-mono">@{esc(member.name)}</p>
        </div>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div class="lg:col-span-2 space-y-6">
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Voice-Support erledigt</div><div class="text-2xl font-bold text-sky-600 dark:text-sky-400">{voice_case_completed}</div><div class="text-[10px] text-slate-400 mt-1">Seit {esc(support_period_start_label)}</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Aktive Supportfälle</div><div class="text-2xl font-bold">{voice_case_active}</div><div class="text-[10px] text-slate-400 mt-1">Aktuell zugewiesen</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Tickets erledigt</div><div class="text-2xl font-bold text-violet-600 dark:text-violet-400">{ticket_case_completed}</div><div class="text-[10px] text-slate-400 mt-1">Seit {esc(support_period_start_label)}</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Aktive Tickets</div><div class="text-2xl font-bold">{ticket_case_active}</div><div class="text-[10px] text-slate-400 mt-1">Aktuell übernommen</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Wochenstunden</div><div class="text-2xl font-bold text-indigo-600 dark:text-indigo-400">{weekly:.1f}h</div></div>
                <div class="{CARD} p-5"><div class="text-xs text-slate-400 font-semibold mb-1">Verwarnungen</div><div class="text-2xl font-bold {warn_color}">{len(warns)}/5</div></div>
            </div>
            <div class="{CARD} p-6 space-y-4">
                {actions_html}{warn_form}
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">Notizen</h3>
                <div class="space-y-2 max-h-36 overflow-y-auto">{notes_html or "<p class='text-xs text-slate-400 italic'>Keine Notizen hinterlegt.</p>"}</div>
                {note_form}
            </div>
            <div class="{CARD} p-6 space-y-3">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">Verwarnungs-Historie ({active_warn_count} aktiv / {len(warns)} gesamt)</h3>
                <div class="space-y-2 max-h-64 overflow-y-auto">{warns_html or "<p class='text-xs text-slate-400 italic'>Keine Verwarnungen vorhanden.</p>"}</div>
            </div>
        </div>
        <div class="space-y-4">
            <div class="text-right text-xl font-extrabold uppercase tracking-widest opacity-90 break-words" style="color:{top_color};">» {esc(ctx.guild_name)} ✕ {esc(top.name)}</div>
            <div class="{CARD} p-6 space-y-4 text-xs">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white border-b border-slate-100 dark:border-slate-800 pb-2">Information</h3>
                <div><div class="text-slate-400 mb-0.5">Nutzername</div><div class="font-medium">[{esc(top.name)}] {esc(member.display_name)}</div></div>
                <div><div class="text-slate-400 mb-0.5">ID</div><div class="text-slate-600 dark:text-slate-300 font-mono">{member.id}</div></div>
                <div><div class="text-slate-400 mb-0.5">Team seit</div><div>{esc(team_since_for(member, info))}</div></div>
                <div><div class="text-slate-400 mb-0.5">Auf dem Server seit</div><div>{member.joined_at.strftime('%d.%m.%Y') if member.joined_at else 'unbekannt'}</div></div>
            </div>
            <div class="{CARD} p-6 space-y-2">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">Letzte Schichten</h3>
                {shifts_html or "<p class='text-xs text-slate-400 italic'>Noch keine Schichten.</p>"}
            </div>
        </div>
    </div>"""
    return render_page(member.display_name, ctx, "team", body)

# =============================================================
# ROUTE: TEAM-BESPRECHUNGS-TOOL
# =============================================================
@app.get("/meetings", response_class=HTMLResponse)
async def meetings_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    meetings = load_meetings()
    support_period_start = pulse_db.get_support_case_period_start()
    support_counts = pulse_db.support_case_counts(support_period_start)
    support_period_total = sum(support_counts.values())
    support_period_start_label = format_support_case_period_start(support_period_start)
    uid = ctx.user["id"]
    rsvps = meetings["rsvps"]
    user_status = rsvps.get(uid, {}).get("status", "none")
    is_manager = ctx.perms["can_promote"] or ctx.perms["is_admin"]

    accepted = [v.get("name") for v in rsvps.values() if v.get("status") == "accepted"]
    declined = [v.get("name") for v in rsvps.values() if v.get("status") == "declined"]

    # Wer hat noch nicht geantwortet?
    team_role_ids = ctx.config.get("team_role_ids", [])
    pending = sorted(m.display_name for m in guild.members
                     if any(r.id in team_role_ids for r in m.roles) and str(m.id) not in rsvps)

    def li(names, color):
        return "".join(f"<li class='{color}'>• {esc(n)}</li>" for n in names)

    topics_html = ""
    for t in meetings["topics"]:
        can_del = is_manager or t.get("by_id") == uid
        del_btn = f"""
            <form action="/action" method="post" onsubmit="return confirm('Thema entfernen?');">
                <input type="hidden" name="action" value="delete_meeting_topic"><input type="hidden" name="topic_id" value="{esc(t.get('id'))}">
                <button class="text-rose-500 hover:underline">🗑️</button>
            </form>""" if can_del else ""
        topics_html += f"""
        <div class="bg-slate-50 dark:bg-[#0b0e14] p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 text-xs space-y-1">
            <div class="flex justify-between items-center font-semibold text-slate-900 dark:text-white gap-2">
                <span>📌 {esc(t.get('title'))}</span>
                <span class="flex items-center gap-2 text-[10px] text-slate-400 font-mono">Von: {esc(t.get('by'))}{del_btn}</span>
            </div>
            <p class="text-slate-600 dark:text-slate-300 break-words">{esc(t.get('details'))}</p>
        </div>"""

    def rsvp_btn(value, label, active_cls, idle_cls):
        return f'<button name="rsvp_status" value="{value}" class="{active_cls if user_status == value else idle_cls} px-4 py-2 rounded-xl text-xs transition">{label}</button>'

    support_period_action = f"""
        <form action="/action" method="post" onsubmit="return confirm('Teammeeting als abgeschlossen markieren und einen neuen Supportfall-Zeitraum starten? Die bisherige Zählung wird archiviert.');">
            <input type="hidden" name="action" value="reset_support_case_period">
            <button class="{BTN} px-4 py-2.5">✅ Teammeeting abgeschlossen · neue Support-Woche starten</button>
        </form>""" if is_manager else '<p class="text-[11px] text-slate-500">Die nächste Support-Woche kann nach dem Teammeeting durch die Teamleitung gestartet werden.</p>'
    support_period_card = f"""
        <div class="{CARD} p-5 space-y-3">
            <div class="flex items-start justify-between gap-3">
                <div>
                    <h3 class="text-sm font-bold text-slate-900 dark:text-white">🎧 Supportfälle zwischen Teammeetings</h3>
                    <p class="text-xs text-slate-500 dark:text-slate-400 mt-1">Gezählt werden abgeschlossene Pulse-Tickets, die einem Teammitglied zugeordnet sind.</p>
                    <p class="text-[11px] text-slate-500 dark:text-slate-400 mt-2">Aktueller Zeitraum seit <strong>{esc(support_period_start_label)}</strong></p>
                </div>
                <div class="text-3xl font-extrabold text-sky-600 dark:text-sky-400">{support_period_total}</div>
            </div>
            {support_period_action}
        </div>"""

    admin_card = f"""
    <div class="{CARD} p-6 space-y-3 text-xs">
        <h3 class="text-sm font-bold text-slate-900 dark:text-white">⚙️ Besprechung ansetzen</h3>
        <form action="/action" method="post" class="space-y-3">
            <input type="hidden" name="action" value="set_meeting_info">
            <input type="text" name="meeting_title" maxlength="100" placeholder="Titel..." required class="{INPUT}">
            <input type="text" name="meeting_datetime" maxlength="100" placeholder="z. B. Sonntag, 18:00 Uhr" required class="{INPUT}">
            <textarea name="meeting_desc" maxlength="500" placeholder="Kurze Beschreibung..." class="{INPUT} h-16"></textarea>
            <label class="flex items-center gap-2"><input type="checkbox" name="reset_rsvps" checked class="rounded"> Zu-/Absagen zurücksetzen</label>
            <label class="flex items-center gap-2"><input type="checkbox" name="clear_topics" class="rounded"> Themenliste leeren</label>
            <label class="flex items-center gap-2"><input type="checkbox" name="announce" checked class="rounded"> Im Team-Update-Kanal ankündigen</label>
            <button class="w-full {BTN} py-2.5">Besprechung speichern</button>
        </form>
    </div>""" if is_manager else ""

    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">🎙️ Team-Besprechungs-Tool</h1>
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div class="lg:col-span-2 space-y-6">
            <div class="{CARD} p-6 space-y-4">
                <div class="flex justify-between items-start border-b border-slate-100 dark:border-slate-800 pb-4 gap-3">
                    <div>
                        <h2 class="text-lg font-bold text-slate-900 dark:text-white">{esc(meetings['title'])}</h2>
                        <p class="text-xs text-indigo-600 dark:text-indigo-400 font-mono mt-1">📅 {esc(meetings['date_time'])}</p>
                    </div>
                    <span class="bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border border-indigo-500/20 text-xs px-3 py-1 rounded-full font-semibold shrink-0">Anstehend</span>
                </div>
                <p class="text-xs text-slate-600 dark:text-slate-300 break-words">{esc(meetings['description'])}</p>
                <div class="pt-2">
                    <label class="block text-xs font-semibold text-slate-700 dark:text-slate-300 mb-2">Dein Status für die Besprechung:</label>
                    <form action="/action" method="post" class="flex gap-3">
                        <input type="hidden" name="action" value="meeting_rsvp">
                        {rsvp_btn("accepted", "✅ Zusage", "bg-emerald-600 text-white font-bold", "bg-emerald-500/10 text-emerald-600 border border-emerald-500/30")}
                        {rsvp_btn("declined", "❌ Absage", "bg-rose-600 text-white font-bold", "bg-rose-500/10 text-rose-600 border border-rose-500/30")}
                    </form>
                </div>
            </div>
            {support_period_card}
            <div class="{CARD} p-6 space-y-4">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white">💡 Themenvorschläge einreichen</h3>
                <form action="/action" method="post" class="space-y-3 text-xs">
                    <input type="hidden" name="action" value="add_meeting_topic">
                    <input type="text" name="topic_title" maxlength="100" placeholder="Thema / Titel..." required class="{INPUT}">
                    <textarea name="topic_details" maxlength="600" placeholder="Beschreibung / Details zum Thema..." required class="{INPUT} h-20"></textarea>
                    <button class="{BTN} px-4 py-2.5">Thema auf Tagesordnung setzen</button>
                </form>
                <div class="pt-4 border-t border-slate-100 dark:border-slate-800 space-y-2">
                    <h4 class="text-xs font-bold text-slate-900 dark:text-white">Eingereichte Themenvorschläge ({len(meetings['topics'])})</h4>
                    <div class="space-y-2 max-h-72 overflow-y-auto">{topics_html or "<p class='text-xs text-slate-400 italic'>Noch keine Themenvorschläge eingereicht.</p>"}</div>
                </div>
            </div>
        </div>
        <div class="space-y-6">
            <div class="{CARD} p-6 space-y-4 text-xs">
                <h3 class="text-sm font-bold text-slate-900 dark:text-white border-b border-slate-100 dark:border-slate-800 pb-2">Teilnehmer-Übersicht</h3>
                <div><div class="font-bold text-emerald-600 dark:text-emerald-400 mb-1">Zugesagt ({len(accepted)})</div>
                    <ul class="space-y-1">{li(accepted, 'text-emerald-600 dark:text-emerald-400') or "<li class='text-slate-400 italic'>Niemand</li>"}</ul></div>
                <hr class="border-slate-100 dark:border-slate-800">
                <div><div class="font-bold text-rose-600 dark:text-rose-400 mb-1">Abgesagt ({len(declined)})</div>
                    <ul class="space-y-1">{li(declined, 'text-rose-600 dark:text-rose-400') or "<li class='text-slate-400 italic'>Niemand</li>"}</ul></div>
                <hr class="border-slate-100 dark:border-slate-800">
                <div><div class="font-bold text-slate-500 mb-1">Noch keine Antwort ({len(pending)})</div>
                    <ul class="space-y-1">{li(pending, 'text-slate-500 dark:text-slate-400') or "<li class='text-slate-400 italic'>Alle haben geantwortet 🎉</li>"}</ul></div>
            </div>
            {admin_card}
        </div>
    </div>"""
    return render_page("Teambesprechung", ctx, "meetings", body)


# =============================================================
# ROUTE: ABWESENHEITEN (LOA)
# =============================================================
@app.get("/loa", response_class=HTMLResponse)
async def loa_page(request: Request, user_session: str = Cookie(None)):
    ctx = auth(request, user_session)
    guild = ctx.guild
    is_manager = ctx.perms["can_promote"] or ctx.perms["is_admin"]
    uid = ctx.user["id"]
    team_role_ids = ctx.config.get("team_role_ids", [])

    entries = sorted(get_loas().values(), key=lambda l: (not l["active"], str(l["bis"])))
    entries_html = ""
    for l in entries:
        member = guild.get_member(l["user_id"])
        name = member.display_name if member else l["name"]
        can_cancel = is_manager or str(l["user_id"]) == uid
        state = (f'<span class="border {BADGE_WARN} text-[10px] px-2 py-0.5 rounded-full font-semibold">Aktiv</span>' if l["active"]
                 else '<span class="border border-slate-300 dark:border-slate-700 text-slate-400 text-[10px] px-2 py-0.5 rounded-full font-semibold">Abgelaufen</span>')
        btn = f"""
            <form action="/action" method="post" onsubmit="return confirm('Abmeldung wirklich beenden?');">
                <input type="hidden" name="action" value="cancel_loa"><input type="hidden" name="target_user_id" value="{l['user_id']}">
                <button class="bg-rose-500/10 hover:bg-rose-500/20 text-rose-600 dark:text-rose-400 border border-rose-500/30 text-xs px-3.5 py-2 rounded-xl font-medium transition">{'Beenden' if l['active'] else 'Entfernen'}</button>
            </form>""" if can_cancel else ""
        entries_html += f"""
        <div class="{CARD} p-4 flex justify-between items-center gap-3 {'' if l['active'] else 'opacity-60'}">
            <div class="min-w-0">
                <div class="font-bold text-slate-900 dark:text-white text-sm flex items-center gap-2">{esc(name)} {state}</div>
                <div class="text-xs text-slate-400 mt-0.5">📅 {esc(fmt_date(l['von']))} bis {esc(fmt_date(l['bis']))}</div>
                <div class="text-xs text-slate-600 dark:text-slate-300 mt-1.5 break-words"><strong>Grund:</strong> {esc(l['grund'])}</div>
            </div>{btn}
        </div>"""

    # Auswahl: Manager sehen alle Teammitglieder, alle anderen nur sich selbst
    if is_manager:
        options = sorted(((m.display_name, m.id) for m in guild.members if any(r.id in team_role_ids for r in m.roles)),
                         key=lambda x: x[0].lower())
        if int(uid) not in [o[1] for o in options]:
            options.insert(0, (ctx.user.get("global_name", "Ich"), int(uid)))
    else:
        options = [(ctx.user.get("global_name", "Ich"), int(uid))]
    options_html = "".join(f'<option value="{i}" {"selected" if str(i) == uid else ""}>{esc(n)}</option>' for n, i in options)
    today = now_de().strftime("%Y-%m-%d")

    body = f"""
    <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-6">Abwesenheiten (LOA)</h1>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
        <div class="{CARD} p-6 space-y-4">
            <h2 class="text-base font-bold text-slate-900 dark:text-white">Neue Abmeldung eintragen</h2>
            <form action="/action" method="post" class="space-y-3.5 text-xs">
                <input type="hidden" name="action" value="submit_loa">
                <div><label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Mitglied</label>
                    <select name="user_id" required class="{INPUT}">{options_html}</select></div>
                <div class="grid grid-cols-2 gap-3">
                    <div><label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Startdatum</label>
                        <input type="date" name="loa_start" value="{today}" min="{today}" required class="{INPUT}"></div>
                    <div><label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Enddatum</label>
                        <input type="date" name="loa_end" min="{today}" required class="{INPUT}"></div>
                </div>
                <div><label class="block text-slate-500 dark:text-slate-400 mb-1 font-medium">Grund</label>
                    <textarea name="loa_reason" maxlength="300" placeholder="Grund für die Abmeldung..." required class="{INPUT} h-24"></textarea></div>
                <button class="w-full {BTN} py-3">Abmeldung speichern</button>
            </form>
        </div>
        <div class="space-y-4">
            <h2 class="text-base font-bold text-slate-900 dark:text-white">Abmeldungen</h2>
            <div class="space-y-3">{entries_html or f"<div class='text-xs text-slate-400 italic {CARD} p-6'>Keine Abmeldungen vorhanden.</div>"}</div>
        </div>
    </div>"""
    return render_page("Abmeldungen (LOA)", ctx, "loa", body)


async def verify_application_member(request: Request, applicant_id) -> tuple[bool, int, str, str]:
    """Fail-closed eligibility check for the public recruitment form."""
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        return False, 503, "", "Pulse ist noch nicht bereit. Bitte versuche es gleich erneut."

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return False, 503, "", "Der Discord-Server konnte gerade nicht geprüft werden."

    eligible_role = guild.get_role(APPLICATION_REQUIRED_ROLE_ID)
    if eligible_role is None:
        return False, 503, "", "Die für Bewerbungen erforderliche Discord-Rolle wurde nicht gefunden. Bitte informiere die Serverleitung."

    try:
        uid = int(applicant_id)
    except (TypeError, ValueError):
        return False, 403, eligible_role.name, "Bitte verbinde zuerst deinen Discord-Account."

    member = guild.get_member(uid)
    if member is None:
        try:
            member = await guild.fetch_member(uid)
        except discord.NotFound:
            return False, 403, eligible_role.name, "Du musst dem Discord-Server beitreten und die erforderliche Mitgliedsrolle besitzen."
        except (discord.Forbidden, discord.HTTPException):
            return False, 503, eligible_role.name, "Deine Mitgliedschaft konnte gerade nicht sicher geprüft werden. Bitte versuche es erneut."

    if member.bot:
        return False, 403, eligible_role.name, "Bot-Accounts können keine Bewerbung einreichen."
    if not any(role.id == APPLICATION_REQUIRED_ROLE_ID for role in member.roles):
        return False, 403, eligible_role.name, f"Dieses Bewerbungsformular ist nur für Mitglieder mit der Rolle „{eligible_role.name}“ freigeschaltet."

    return True, 200, eligible_role.name, ""


async def dm_applicant(request: Request, user_id, template: str, actor=None, **values) -> bool:
    """BOT-10: DM to an applicant from a template; applicants may not be server members yet.

    #100: every attempt - sent or failed - is written to the applicant communication journal
    (``actor`` = (id, name) of the person who caused it, default: the system)."""
    import pulse_app_comms
    import pulse_app_journal
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot is not None else None
    resolved = pulse_app_comms.resolve_for_guild(
        template, getattr(guild, "id", None), server=getattr(guild, "name", "dem Server"), **values)
    text, template_ref = resolved["text"], resolved["template_ref"]
    actor_id, actor_name = actor or ("0", "System")

    def journal(ok: bool, reason: str = "") -> bool:
        pulse_app_journal.record_safe(values.get("id"), template, "sent" if ok else "failed", text, detail="" if ok else reason,
                                      actor_id=actor_id, actor_name=actor_name, template_ref=template_ref)
        return ok
    if bot is None:
        return journal(False, "bot_unavailable")
    if not str(user_id or "").isdigit():
        return journal(False, "no_discord_id")
    try:
        user = (guild.get_member(int(user_id)) if guild else None) or bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
    except (discord.NotFound, discord.HTTPException):
        return journal(False, "user_not_found")
    return journal(bool(await send_dm_notification(user, text)), "dm_not_delivered")


async def send_applicant_notice(user, text: str, *, app_id, kind: str, actor_id="0", actor_name="System", template_ref=None) -> bool:
    """#100: DM to an applicant that is already a Discord member object (or ``None``) - sent and journaled."""
    import pulse_app_journal
    ok, reason = False, "user_not_found" if user is None else "dm_not_delivered"
    if user is not None:
        try:
            ok = bool(await send_dm_notification(user, text))
        except Exception:  # noqa: BLE001 - recorded as failed, the decision flow continues as before
            ok, reason = False, "error"
    pulse_app_journal.record_safe(app_id, kind, "sent" if ok else "failed", text, detail="" if ok else reason,
                                  actor_id=str(actor_id), actor_name=str(actor_name), template_ref=str(template_ref or f"legacy:{kind}"))
    return ok


async def notify_new_application(request: Request, application_id: str, application: dict) -> bool:
    """Notify the configured team channel, pinging only the recruitment alert role."""
    bot = getattr(request.app.state, "bot", None)
    guild = bot.get_guild(GUILD_ID) if bot else None
    if guild is None:
        print("⚠️ Neue Bewerbung gespeichert, aber Team-Benachrichtigung übersprungen: Discord-Server nicht verfügbar.")
        return False

    channel = guild.get_channel(APPLICATION_ALERT_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(APPLICATION_ALERT_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Neue Bewerbung gespeichert, aber Bewerbungs-Kanal nicht erreichbar: {type(exc).__name__}.")
            return False

    channel_guild = getattr(channel, "guild", None)
    if channel_guild is not None and channel_guild.id != GUILD_ID:
        print("⚠️ Bewerbungs-Benachrichtigung abgebrochen: Der konfigurierte Kanal liegt auf einem anderen Server.")
        return False
    if not callable(getattr(channel, "send", None)):
        print("⚠️ Bewerbungs-Benachrichtigung abgebrochen: Der konfigurierte Kanal kann keine Nachrichten empfangen.")
        return False

    alert_role = guild.get_role(APPLICATION_ALERT_ROLE_ID)
    applicant_id = str(application.get("user_id") or "")
    applicant_name = str(application.get("name") or "Unbekannt")[:80]
    career_label = str(application.get("career_label") or "Nicht angegeben")[:100]
    score = int(application.get("test_score") or 0)
    total = max(0, int(application.get("test_total") or 0))
    percent = max(0, min(100, int(application.get("test_percent") or 0)))
    result_label = "Richtwert erreicht" if application.get("test_passed") else "Richtwert nicht erreicht"

    embed = discord.Embed(
        title="📝 Neue Bewerbung eingegangen",
        description="Eine neue Bewerbung wurde über das externe Pulse-Bewerbungsformular eingereicht.",
        color=_pulse_color("info"),
        timestamp=now_de(),
    )
    # SEC-04: the name comes from the public form – Discord markdown (masked links, mentions) is escaped,
    # so nobody can put a phishing link into the team alert. The ID is digits only.
    embed.add_field(name="Bewerber", value=f"{discord_text(applicant_name)}\nDiscord-ID: `{re.sub(r'[^0-9]', '', applicant_id)}`",
                    inline=True)
    embed.add_field(name="Laufbahn", value=discord_text(career_label), inline=True)
    embed.add_field(name="Online-Test", value=f"{score}/{total} richtig ({percent} %)\n{result_label}", inline=True)
    embed.add_field(
        name="Bewerbung öffnen",
        value=f"[In Pulse zur Bewerbungsübersicht]({request_origin(request)}/applications)",
        inline=False,
    )
    embed.set_footer(text=f"Pulse Recruiting · Bewerbungs-ID {application_id}")

    content = alert_role.mention if alert_role else f"<@&{APPLICATION_ALERT_ROLE_ID}>"
    try:
        await send_styled(
            channel,
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                roles=[alert_role] if alert_role else [],
                users=False,
                everyone=False,
                replied_user=False,
            ),
        )
        if alert_role is None:
            print(f"⚠️ Neue Bewerbung wurde gemeldet, aber Ping-Rolle {APPLICATION_ALERT_ROLE_ID} wurde im Server nicht gefunden.")
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(f"⚠️ Bewerbung {application_id} wurde gespeichert, aber Team-Benachrichtigung fehlgeschlagen: {type(exc).__name__}.")
        return False


# =============================================================
# ROUTE: ÖFFENTLICHES BEWERBUNGSFORMULAR MIT LAUFBAHN-TEST
# =============================================================
