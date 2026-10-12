"""Static feature regression checks for Pulse TeamOS.

Stdlib-only; complements the compileall CI check.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def parse(name: str) -> ast.Module:
    return ast.parse(read(name), filename=name)


def assert_function_is_async(name: str, function_name: str) -> None:
    tree = parse(name)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == function_name
    ]
    assert matches, f"{name}: {function_name} fehlt"
    assert any(isinstance(node, ast.AsyncFunctionDef) for node in matches), f"{name}: {function_name} muss async sein"


def main() -> None:
    for name in ("main.py", "webserver.py", "pulse_pro.py", "cogs/pulse_pro.py", "pulse_ultimate.py"):
        parse(name)

    web = read("webserver.py")
    pro = read("pulse_pro.py")
    main = read("main.py")
    web = read("webserver.py")

    # Team-Kick must only remove team roles, never call Discord member.kick().
    assert "Team-Kick" in web
    assert 'await member.remove_roles(*team_roles' in web
    assert 'await member.kick(' not in web
    assert "Mitglied bleibt auf dem Discord-Server" not in web or "bleibt aber auf dem Discord-Server" in web
    ultimate = read("pulse_ultimate.py")

    # Pulse Next uses synchronous SQLite helpers during periodic score updates.
    # Keep workflow/profile/score maintenance off the Discord event loop.
    pulse_next_cog = read("cogs/pulse_next.py")
    parse("cogs/pulse_next.py")
    assert "await asyncio.to_thread(self._housekeeping_sync, guild)" in pulse_next_cog
    assert "def _housekeeping_sync(self, guild: discord.Guild | None) -> None:" in pulse_next_cog
    assert "pulse_next.run_workflows(self.bot)" in pulse_next_cog
    assert "pulse_next.seed_profile_for_member(member)" in pulse_next_cog
    assert "pulse_next.record_score(member)" in pulse_next_cog
    assert "await pulse_next.run_workflows(self.bot)" not in pulse_next_cog

    # Pulse Pro /member route must expose the same management actions as the
    # central dashboard action handler.
    assert "⚙ Team-Aktionen" in pro
    assert 'value="promote"' in pro
    assert 'value="demote"' in pro
    assert 'name="action" value="kick"' in pro
    assert "Pflicht: Grund für Beförderung / Degradierung" in pro
    assert "Pflicht: Grund für den Kick" in pro
    assert "ws.team_since_for(target, entry)" in pro
    assert 'placeholder="Pflicht: Rücknahmegrund"' in pro

    tree = parse("webserver.py")
    constants = {
        node.targets[0].id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    assert constants.get("TEAM_UPDATE_CHANNEL_ID") == 1531132354272170115
    assert "async def remove_warn" not in web  # action is intentionally centralized
    assert 'if action == "remove_warn":' in web
    assert "active_warns(entry)" in web
    assert "revoked_at" in web
    assert "async def reconcile_warning_roles" in web
    assert "/api/team/warn-roles" in web
    assert web.count("def normalize_warns(entry: dict) -> bool:") == 1
    assert 'SELECT user_id FROM check_members WHERE check_id=?' in web
    assert '"not_in_snapshot"' in web
    assert 'member.status' in web
    assert 'rate_limited(client_key' in web

    # Five-level team warning regression checks.
    for role_id in (
        "1489221948348043395",
        "1489222076370780232",
        "1531760107971416135",
        "1556344459422081045",
        "1556344484198088814",
    ):
        assert role_id in web
    assert "5: 1556344484198088814" in web
    assert '"count": max(0, min(int(count or 0), 5))' in web
    assert "current_count >= 5" in web
    assert "for level in sorted(ids):" in web
    assert "warning_5" in web
    assert "/3 Warnungen" not in web

    # Melonly stays internal and is editable/deletable.
    web_tree = parse("webserver.py")
    create_log_nodes = [n for n in ast.walk(web_tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_log"]
    assert create_log_nodes, "webserver.py: create_log fehlt"
    create_log_source = ast.get_source_segment(web, create_log_nodes[0]) or ""
    assert "send_team_update_embed" not in create_log_source
    assert 'Melonly-Eintrag erstellt' in create_log_source
    assert '@app.post("/log/edit")' in web
    assert '@app.post("/log/delete")' in web
    assert "edit_log_id" in web
    assert "users_map = {}" in web

    # Team rank and bulk role-management regression checks.
    assert '"/team/{user_id}/roles"' in web
    assert "async def team_role_manager_save" in web
    assert "Mehrfach-Rollenänderung" in web
    assert "⚙️ Team-Update: Rollenänderung" in web
    assert "⬆️ Befördern" in web
    assert "⬇️ Degradieren" in web
    assert "1531132354272170115" in web
    # Team list keeps member actions inside the Details/eye menu.
    assert "details-only" not in web  # no stale marker should leak into UI
    assert 'href="/member/{m["id"]}"' in web
    assert "🗓️ Team seit" in web
    assert "def team_since_for(member, entry=None)" in web
    assert 'href="/team/{member.id}/roles"' in web
    assert 'name="action_reason"' in web
    assert 'action == "kick"' in web
    assert "not guild.me.guild_permissions.manage_roles" in web
    assert "r.position >= guild.me.top_role.position" in web
    assert "def ordered_team_role_ids(guild, team_role_ids) -> list[int]:" in web
    assert "return sorted(role_ids, key=sort_key)" in web
    assert "ordered_ids = ordered_team_role_ids(guild, team_role_ids)" in web
    assert "ranked_team_role_ids = ordered_team_role_ids(guild, team_role_ids)" in web
    assert '"team_rank_idx": team_rank(member, team_role_ids)' in web
    assert "await member.remove_roles(*team_roles" in web
    assert "await member.kick(" not in web
    assert "def _record_team_since" in read("cogs/pulse_next.py")
    assert 'entry["team_since"]' in read("cogs/pulse_next.py")
    # Required reasons for personnel actions.
    assert 'name="action_reason"' in web
    assert 'action_reason: str = Form(None)' in web
    assert "Bitte einen Grund für die Maßnahme angeben." in web
    assert "Bitte einen Grund für die Rücknahme der Verwarnung angeben." in web
    assert "Bitte einen Grund für die Rollenänderung angeben." in web
    assert "Bitte einen Grund für die Bewerbungsentscheidung angeben." in web
    assert '("Grund", action_reason, False)' in web
    assert '("📝 Grund", action_reason, False)' in web

    assert "/warns" in pro
    assert "critical=sum" in pro
    assert ">=5" in pro
    assert "Warn-Rollen 1–5" in pro
    assert "range(1,6)" in pro
    assert "/settings/pro-warn-roles" in pro
    assert "/settings/pro-warn-sync" in pro
    assert "Warn-Rollen" in pro
    assert "PULSE_VERSION" in web
    assert '"/healthz"' in web

    assert "reconcile_warning_roles" in main

    assert_function_is_async("webserver.py", "sync_warn_roles")
    assert "def register(app)" in ultimate
    assert '"/ultimate"' in ultimate
    assert '"/ultimate/team"' in ultimate
    assert '"/ultimate/cases"' in ultimate
    assert '"/ultimate/roblox"' in ultimate
    assert '"/ultimate/automations"' in ultimate
    assert '"/ultimate/api/v2/team"' in ultimate
    assert 'ultimate_automation' in ultimate
    assert "frozen" in web.lower() or "eingefroren" in web.lower()
    assert "/5600" not in web
    assert "/5600" not in read("pulse_next.py")
    assert "/3600" in read("pulse_next.py")

    # Selected professional Pulse systems: confirm existing integrations remain wired.
    suite = read("cogs/pulse_suite.py")
    ai_mod = read("cogs/ai_moderation.py")
    activity_check = read("cogs/activity_check.py")
    backups = read("cogs/backups.py")
    parse("cogs/activity_check.py")
    # Activity Check must acknowledge buttons before doing DB work and then follow up.
    assert "await interaction.response.defer(ephemeral=True, thinking=True)" in activity_check
    assert "interaction.followup.send(response_text, ephemeral=True)" in activity_check
    assert "except discord.NotFound:" in activity_check
    # The AI moderation worker backs off during transient provider failures.
    assert "def _backoff_provider(self, reason: str) -> int:" in ai_mod
    assert "response.status_code >= 500" in ai_mod
    assert '_backoff_provider(f"Verbindungsfehler ({type(exc).__name__})")' in ai_mod
    # Team KI messages and stale messages after provider outages should not create
    # noisy false-positive moderation reports.
    assert 'env_bool("PULSE_AI_MODERATION_EXCLUDE_TEAM_KI", True)' in ai_mod
    assert "self.excluded_channel_ids.add(self.team_support_channel_id)" in ai_mod
    assert 'PULSE_AI_MODERATION_LOW_CONFIDENCE", "0.85"' in ai_mod
    assert 'PULSE_AI_MODERATION_MAX_QUEUE_AGE_SECONDS", "300"' in ai_mod
    assert "item_age > self.max_queue_age_seconds" in ai_mod
    assert "self.low_severity_threshold" in ai_mod
    assert "Erwähnte Rollen:" in ai_mod
    assert "Ein sachlich begründeter Rollen-/Team-Ping" in ai_mod
    for source_name in ("cogs/ai_moderation.py", "cogs/backups.py", "cogs/pulse_suite.py"):
        parse(source_name)
    assert "ModerationReviewView" in ai_mod
    assert "MODERATION_DECISION_ROLE_ID = 1510345520529412188" in ai_mod
    assert "human" not in ai_mod.lower() or "mensch" in ai_mod.lower()
    assert "backup_discord_channel_id" in backups
    assert "create_backup_file" in backups
    assert "backup_discord_channel_id" in web
    assert 'PULSE_BACKUP_DISCORD_CHANNEL_ID' in web
    assert "pre_restore_" in web
    assert 'if panel.get("logs") == {}' in web
    assert "Nicht unterstützte Backup-Version" in web
    assert "Dieses Backup gehört zu einem anderen Discord-Server" in web
    assert '"/ultimate/api/monitor"' in ultimate
    assert "automation_failures_24h" in ultimate
    assert "disk_free_mb" in ultimate
    assert "ultimate_automation_runs" in ultimate
    # Teamakten, searchable audit history and global search already live in Pulse Suite.
    assert "/suite/member/" in suite
    assert "/suite/audit" in suite
    assert "/suite/search" in suite
    assert "Legacy-Webpanel" in suite
    assert "audit_logs.json" in suite
    assert '"/ultimate/automations/{automation_id}/toggle"' in ultimate
    assert "Letzte 30 Ausführungen" in ultimate
    assert "Automation pausieren" in ultimate
    assert "Automation fortsetzen" in ultimate

    # Pulse Suite inactivity scan must keep synchronous SQLite/JSON/cache work
    # off the event loop and avoid recomputing the manager list for each member.
    assert "await asyncio.to_thread(self._inactivity_sync, g)" in suite
    assert "def _inactivity_sync(self,g):" in suite
    assert "team_managers = managers(g,web)" in suite

    # Team-list updates must batch SQLite lookups and debounce event-triggered refreshes.
    teamlist = read("cogs/teamliste.py")
    parse("cogs/teamliste.py")
    assert 'SELECT user_id, bis FROM abmeldungen' in teamlist
    assert '@tasks.loop(minutes=5)' in teamlist
    assert 'await asyncio.sleep(8)' in teamlist
    assert 'pulse-teamlist-debounced-update' in teamlist
    assert 'self.get_abmeldung_info(member.id)' not in teamlist


    # AI support: general questions, optional team channel, role restriction and answer length.
    support_assistant = read("cogs/support_assistant.py")
    support_rules = read("pulse_support_rules.py")
    parse("cogs/support_assistant.py")
    assert "TEAM_MODE_PROMPT" in support_assistant
    assert "PULSE_SUPPORT_TEAM_CHANNEL_ID" in support_assistant
    assert "PULSE_SUPPORT_TEAM_ROLE_IDS" in support_assistant
    assert "templates_status" in support_assistant
    assert "self.templates_path.is_file()" in support_assistant
    assert '"max_tokens": self.max_tokens' in support_assistant
    assert "history_key = (message.channel.id, message.author.id)" in support_assistant
    assert "Allgemeine Fragen darfst du mit deinem allgemeinen Wissen beantworten" in support_assistant
    assert "allgemeine Hilfsfragen" in support_assistant
    assert "formuliere|erklär|erkläre|zusammenfassung" in support_rules
    assert "vorlagen|textbaustein|textbausteine|partnerschaft" in support_rules
    # The Team KI must receive the separate template library only in team mode
    # and be able to return complete multi-message drafts rather than truncate them.
    assert "def _team_templates_text(self) -> str:" in support_assistant
    assert "team_ki_vorlagen.md" in support_assistant
    assert "PULSE_SUPPORT_TEMPLATES_FILE" in support_assistant
    assert "TEAM-VORLAGENBIBLIOTHEK" in support_assistant
    assert "if team_mode" in support_assistant
    assert "def split_discord_messages(content: str, limit: int = 1700)" in support_assistant
    assert "for part in answer_parts[1:]" in support_assistant
    team_templates = read("team_ki_vorlagen.md")
    for template_heading in (
        "Rückmeldung der Partnerschaftsverwaltung",
        "Partnerschaft genehmigt",
        "Änderungsantrag | Fraktion",
        "Fraktionsantrag abgelehnt",
        "Entbannungsantrag angenommen",
        "Bugreport erfasst",
        "Content-Creator-Bewerbung angenommen",
        "Ingame-Broadcasts – Vorlagen",
        "[SERVER] – Respektvoller Umgang",
        "Dienstanweisung – Zivil-Admin-Cars",
    ):
        assert template_heading in team_templates, f"Team-Vorlage fehlt: {template_heading}"

    # Office pings should mention the matching office role and delete a pending
    # notification if the request is accepted or the visitor exits the waiting room.
    buero = read("cogs/buero_system.py")
    parse("cogs/buero_system.py")
    assert "CREATE TABLE IF NOT EXISTS pending_requests" in buero
    assert "def office_role_for(guild: discord.Guild, target_voice_id: int):" in buero
    assert 'get_config(f"office_role_{target_voice_id}")' in buero
    assert "content=content" in buero
    assert "roles=[office_role] if office_role else []" in buero
    assert "name=\"buero_rolle_setzen\"" in buero
    assert "await delete_pending_ping(self.bot, member.id)" in buero
    assert "await waiting_member.move_to(target_channel" in buero
    assert 'label="Auftrag erledigt"' in buero
    assert 'custom_id="buero_auftrag_erledigt_btn"' in buero
    assert "Büro-Auftrag als erledigt markiert" in buero
    assert "Die Ping-Nachricht wurde entfernt" in buero
    assert "await message.delete()" in buero
    assert 'DELETE FROM pending_requests WHERE user_id = ? AND message_id = ?' in buero
    assert "guild_member.voice.channel.id != warteraum_id" in buero

    # Social video announcements: configurable sources, deduplication and fixed target.
    social = read("cogs/social_promo.py")
    parse("cogs/social_promo.py")
    assert "TARGET_CHANNEL_ID = 1530972281893949521" in social
    assert "SOCIAL_PING_ROLE_ID = 1516838538702753943" in social
    assert 'content=f"<@&{SOCIAL_PING_ROLE_ID}>"' in social
    assert "roles=[discord.Object(id=SOCIAL_PING_ROLE_ID)]" in social
    assert social.count('content=f"<@&{SOCIAL_PING_ROLE_ID}>"') == 2
    assert 'name="social_add"' in social
    assert 'name="social_list"' in social
    assert 'name="social_remove"' in social
    assert 'name="social_toggle"' in social
    assert 'name="social_test"' in social
    assert "@tasks.loop(minutes=POLL_INTERVAL_MINUTES)" in social
    assert "def parse_feed(xml_text: str)" in social
    assert "def _feed_candidates(self, feed_url: str, platform: str)" in social
    assert "def _fetch_feed(" in social
    assert "PULSE_RSSHUB_FALLBACK_URLS" in social
    assert "Keine RSSHub-Instanz konnte den Feed abrufen" in social
    assert 'parsed_candidate.path.rstrip("/") + "/true"' in social
    assert "optionale RSSHub-Variante mit offiziellem TikTok-iframe" in social
    assert '"initialized": not pending_feed' in social
    assert '"feed_status": "pending" if pending_feed else "active"' in social
    assert "Feed noch nicht erreichbar" in social
    assert "wird gespeichert; Feed momentan nicht erreichbar" in social
    assert "🟡 wartet auf Feed" in social
    assert "🟠 Feed gestört" in social
    assert "POLL_FAILURE_INITIAL_BACKOFF_SECONDS = 300" in social
    assert "POLL_FAILURE_MAX_BACKOFF_SECONDS = 3600" in social
    assert 'source.get("retry_after", "")' in social
    assert '"retry_after": (' in social
    assert "nächster Versuch in %d Min." in social
    assert 'source["feed_status"] = "pending" if not source.get("initialized") else "degraded"' in social
    assert '"/tiktok/user/@"' in social
    assert '"/tiktok/user/%40"' not in social
    assert "Verkürzte Teilen-Links und reine Video-Links" in social
    assert "Profil-URL mit /@benutzername" in social
    assert '"/instagram/2/user/"' in social
    assert '"/twitch/video/"' in social
    assert '"/twitter/user/"' in social
    assert "seen_ids" in social
    assert "nicht rückwirkend gepostet" in social
    assert "def _is_video_item(source: dict, item: dict[str, str]) -> bool:" in social

    print("Pulse feature smoke test: OK")


if __name__ == "__main__":
    main()
