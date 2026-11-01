"""Static feature regression checks for Pulse TeamOS.

Stdlib-only; complements the compileall CI check.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pulse_activity_utils import (
    activity_action_is_terminal,
    activity_warning_due,
    activity_warning_id,
    confirmed_by_deadline,
    leave_covers_date,
)

from pulse_intelligence import (
    build_copilot_report,
    build_findings,
    memory_state,
    simulate_scenario,
    system_health,
    ticket_is_overdue,
    validate_workflow_json,
)



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
    for name in ("main.py", "webserver.py", "pulse_db.py", "pulse_pro.py", "pulse_version.py", "pulse_nickname_utils.py", "cogs/role_nickname_prefixes.py", "cogs/honeypot.py", "cogs/server_stats.py", "cogs/voice_support.py", "pulse_voice_support_utils.py", "cogs/pulse_pro.py", "pulse_ultimate.py", "pulse_ai_resilience.py", "pulse_safety.py", "pulse_melonly.py", "pulse_shift_utils.py", "pulse_activity_utils.py", "cogs/activity_check.py", "cogs/abmeldung.py", "pulse_intelligence.py", "pulse_team_protection.py"):
        parse(name)

    web = read("webserver.py")
    pro = read("pulse_pro.py")
    main = read("main.py")
    web = read("webserver.py")
    shift_utils = read("pulse_shift_utils.py")
    # Activity Check: use the same absolute database files in the bot and web app,
    # preserve empty-vs-legacy snapshots, and issue idempotent 20:00 warnings.
    activity_utils = read("pulse_activity_utils.py")
    activity_cog = read("cogs/activity_check.py")
    activity_pro = read("pulse_pro.py")
    assert "DB_NAME = str(BASE_DIR / \"activity_check.db\")" in activity_cog
    assert "ABMELDUNGEN_DB = str(BASE_DIR / \"abmeldungen.db\")" in activity_cog
    assert 'DB_NAME = pulse_paths.data_file("abmeldungen.db")' in read("cogs/abmeldung.py")
    # Phase 13: bot and dashboard resolve the same files below the one data directory (PULSE_DATA_DIR)
    assert "BASE_DIR = pulse_paths.DATA_DIR" in activity_cog and "BASE_DIR = pulse_paths.DATA_DIR" in read("cogs/teamliste.py")
    assert "ABMELDUNGEN_DB = str(BASE_DIR / \"abmeldungen.db\")" in read("cogs/teamliste.py")
    assert "ACTIVITY_DB = str(DATA_DIR / \"activity_check.db\")" in web
    assert "sqlite3.connect(ws.ACTIVITY_DB)" in activity_pro
    assert "import sqlite3\n" in activity_pro.split("PRO_CSS", 1)[0]
    assert "Activity Check reparieren / einrichten" in activity_pro
    assert "/activity-check/setup" in activity_pro
    assert "async def activity_check_setup(" in activity_pro
    assert "ActivityCheckCog ist nicht geladen" in activity_pro
    assert "Speichern & heutigen Check erstellen" in activity_pro
    assert "snapshot_count" in activity_pro
    assert "snapshot_version INTEGER NOT NULL DEFAULT 0" in activity_cog
    assert "snapshot_version >= 1" in activity_pro
    assert "activity_warn_actions" in activity_cog
    assert "source_activity_check_id" in activity_cog
    assert "source_activity_user_id" in activity_cog
    assert "activity_warning_due(now)" in activity_cog
    # P-06: the loops carry a @tracked registry decorator; the schedule itself is checked on the real
    # tasks.Loop objects in tests/test_stability.py (ActivityScheduleTests).
    assert re.search(r"@tasks\.loop\(minutes=1\)\n(    @tracked\([^\n]*\n)?    async def activity_warning_check", activity_cog)
    assert "Activity Check Snapshot repariert" in activity_cog
    assert "leave_status is None" in activity_cog
    assert re.search(r"@tasks\.loop\(time=datetime_time\(hour=6, minute=0, tzinfo=BERLIN_TZ\)\)\n(    @tracked\([^\n]*\n)?    async def daily_activity_check", activity_cog)
    assert re.search(r"@tasks\.loop\(minutes=5\)\n(    @tracked\([^\n]*\n)?    async def activity_check_recovery", activity_cog)
    assert "self._check_creation_lock = asyncio.Lock()" in activity_cog
    assert "await self._run_daily_activity_check()" in activity_cog
    assert "or now.hour >= 20" in activity_cog
    assert "Snapshot ist leer" in activity_cog
    assert "confirmed_by_deadline(responses.get(uid), check_date)" in activity_cog
    assert "is_abgemeldet(uid, check_date)" in activity_cog
    assert "await ws.sync_warn_roles(guild, member, count, cfg)" in activity_cog
    assert "ws.apply_warn_consequences(" in activity_cog
    assert "entry=entry" in activity_cog
    assert '"duration_days": ws.DEFAULT_AUTO_WARN_DURATION_DAYS' in activity_cog
    assert "warns.append(warn)" not in activity_cog  # warning is appended to the existing warns_list
    assert "warnings.append(warn)" in activity_cog
    assert "await member.kick(" not in activity_cog
    assert "def confirmed_by_deadline(" in activity_utils
    assert "def leave_covers_date(" in activity_utils
    assert activity_warning_due(datetime(2026, 10, 15, 19, 59)) is False
    assert activity_warning_due(datetime(2026, 10, 15, 20, 0)) is True
    assert confirmed_by_deadline("2026-10-15T19:59:00+02:00", "2026-10-15")
    assert not confirmed_by_deadline("2026-10-15T20:00:01+02:00", "2026-10-15")
    assert leave_covers_date("2026-10-14", "2026-10-16", "2026-10-15")
    assert activity_warning_id(12, 34) == activity_warning_id(12, 34)
    assert activity_action_is_terminal("issued")
    assert not activity_action_is_terminal("failed")

    parse("pulse_shift_utils.py")
    assert "AUTO_SHIFT_END_HOURS = 24.0" in shift_utils
    assert "AUTO_SHIFT_END_SECONDS" in web
    assert "def process_overdue_shifts(" in web
    assert '"auto_warn_status": "pending"' in web
    assert '"source_shift_id": event_id' in web
    assert 'webserver.process_overdue_shifts(guild, housekeeping_cfg)' in read("cogs/pulse_pro.py")
    assert 'action="/shift/admin-end"' in pro
    assert "async def admin_end_shift(" in pro
    assert "Fremde Schicht beendet" in pro

    # Memory optimisation must keep features available: cap only Discord's
    # recent-message cache and fetch reply context on demand when it is absent.
    support = read("cogs/support_assistant.py")
    assert "DISCORD_MESSAGE_CACHE_SIZE" in main
    assert "max_messages=MESSAGE_CACHE_SIZE" in main
    assert "await reference_channel.fetch_message(reference.message_id)" in support
    assert "process_peak_rss_mb" in read("pulse_ultimate.py")
    assert "discord_cached_messages" in read("pulse_ultimate.py")
    assert "_ROBLOX_CACHE_MAX_ENTRIES = 300" in web
    assert "def _prune_roblox_cache" in web
    assert "def _roblox_http_client()" in web
    assert "limits=httpx.Limits(" in web

    # Honeypot: ensure the configured channel warns clearly and automatically bans authors.
    honeypot = read("cogs/honeypot.py")
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "HONEYPOT_CHANNEL_ID = 1560397792751657053" in read("pulse_ids.py")
    assert "HONEYPOT_CHANNEL_ID = _pulse_ids.HONEYPOT_CHANNEL_ID" in honeypot
    assert "HONEYPOT – HIER AUF KEINEN FALL SCHREIBEN!" in honeypot
    assert "permanenten Server-Ban" in honeypot
    assert "async def on_ready(self)" in honeypot
    assert "async def on_message(self, message: discord.Message)" in honeypot
    assert "await guild.ban(author, reason=reason)" in honeypot
    assert "message.author.id == self.bot.user.id" in honeypot
    assert "Nachrichtenverlauf anzeigen" in honeypot

    # Voice support: dashboard route, shift-synchronised duty role, cases and settings.
    voice_support = read("cogs/voice_support.py")
    voice_support_utils = read("pulse_voice_support_utils.py")
    assert "CONFIG_KEY = " in voice_support
    assert "support_open_now" in voice_support
    assert "async def duty_status_loop(self)" in voice_support
    assert "await self._reconcile_duty_roles(guild, cfg)" in voice_support
    assert "def support_open_now(" in voice_support_utils
    assert "def shift_is_online(" in voice_support_utils
    # BOT-03: dashboard and Discord panel share sync_duty_after_shift
    # (behaviour: tests/test_dienst.py::test_discord_path_checks_rights_on_every_click)
    assert "await voice_support.sync_member_duty_role(guild, str(mod_id))" in web
    assert "await voice_support._refresh_duty_status(guild)" in web
    assert "await sync_duty_after_shift(" in web
    assert '("voice-support","/voice-support","🎧","Sprach-Support"' in pro
    assert "voice_support_v5" in pro and "/voice-support/save" in pro
    assert "Start/Fortsetzen gibt die Bereitschaftsrolle" in pro
    assert "private_thread" in voice_support
    assert "ratings_enabled" in voice_support
    # Every caller waits in the configured shared room; no per-user voice rooms are created.
    assert 'case_key = f"waiting:{member.id}"' in voice_support
    assert "Bitte einen gültigen gemeinsamen Support-Warteraum einstellen." in voice_support
    assert "await guild.create_voice_channel(" not in voice_support
    assert 'waiting_in_shared_room": True' in voice_support
    assert "latest.get(\"waiting_in_shared_room\")" in voice_support
    assert "hours_" in pro
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "SUPPORT_REPORT_CHANNEL_ID = 1531443370172485703" in read("pulse_ids.py")
    assert "SUPPORT_REPORT_CHANNEL_ID = _pulse_ids.SUPPORT_REPORT_CHANNEL_ID" in voice_support
    assert "class SupportCaseView" in voice_support
    assert "class TransferUserSelect" in voice_support
    assert "async def handle_case_action" in voice_support
    assert "async def _assign_case" in voice_support
    assert "use_voice_activation=True" in voice_support
    assert "stream=True" in voice_support
    assert "await member.move_to(None" in voice_support
    assert "async def _restore_member_overwrite" in voice_support
    assert "channel.history(limit=100)" in voice_support
    assert "await duplicate.delete()" in voice_support
    assert "await send_styled(report, embed=result_embed" in voice_support
    # Board recognition works for embeds and modern containers (no duplicate status posts).
    assert "info = card_info(candidate)" in voice_support and "same_content(message, embed)" in voice_support
    assert "vorhandenen Fall fortsetzen" not in voice_support
    assert 'reason="Bürger hat den Supportkanal verlassen und erneut den Warteraum betreten"' in voice_support
    assert "async def _refresh_duty_status_locked" in voice_support
    assert "self._duty_status_lock = asyncio.Lock()" in voice_support
    assert "async with self._case_action_lock(case_id)" in voice_support

    # Server-statistics voice channels: maintain the three required non-joinable stats.
    server_stats = read("cogs/server_stats.py")
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "STATS_CATEGORY_ID = 1536812484462256179" in read("pulse_ids.py")
    assert "STATS_CATEGORY_ID = _pulse_ids.STATS_CATEGORY_ID" in server_stats
    assert '"👤・Mitglieder: ", "member_count"' in server_stats
    assert '"💎・Boosts: ", "premium_subscription_count"' in server_stats
    assert '"🤖・Bots: ", "bot_count"' in server_stats
    assert '"🛡️・Rollen: ", "role_count"' in server_stats
    assert '"role_count": len(guild.roles)' in server_stats
    assert "async def on_guild_role_create" in server_stats
    assert "async def on_guild_role_delete" in server_stats
    assert "category.create_voice_channel(" in server_stats
    assert "overwrite.connect = False" in server_stats
    assert "guild.default_role" in server_stats
    assert "async def on_member_join" in server_stats
    assert "async def on_member_remove" in server_stats
    assert "async def on_member_update" in server_stats
    assert "REFRESH_DEBOUNCE_SECONDS = 3" in server_stats
    # Discord allows two renames per channel per ten minutes; throttle instead of 429.
    assert "from pulse_channel_rename_utils import rename_wait_seconds" in server_stats
    assert "self._schedule_deferred_refresh(next_rename_wait)" in server_stats
    assert "CHANNEL_RENAME_MIN_INTERVAL_SECONDS = 300.0" in read("pulse_channel_rename_utils.py")

    # Role nickname prefixes validate, format and restore Discord-sized names.
    nickname_utils = read("pulse_nickname_utils.py")
    assert "MAX_DISCORD_NICKNAME_LENGTH = 32" in nickname_utils
    assert "MAX_ROLE_PREFIX_LENGTH = 24" in nickname_utils
    nickname_cog = read("cogs/role_nickname_prefixes.py")
    assert "async def on_member_join" in nickname_cog
    assert "async def on_member_update" in nickname_cog
    assert "start_sync_existing_members" in nickname_cog
    assert "async def on_ready" in nickname_cog
    assert "self._startup_sync_started" in nickname_cog
    assert "await guild.chunk(cache=True)" in nickname_cog
    assert "start_sync_existing_members(queue_if_running=True)" in pro
    assert "Bestehende Mitglieder werden beim Bot-Start nachgezogen" in pro
    assert "role_nickname_prefixes" in nickname_cog
    assert "await member.edit(" in nickname_cog
    assert 'action="/settings/nickname-prefix/save"' in pro
    assert 'app.post(\'/settings/nickname-prefix/sync\')(settings_nickname_prefix_sync)' in pro
    assert "Automatische Rollen-Namenspräfixe" in pro
    assert "Standardpräfix für neue und normale Mitglieder" in pro
    assert "settings_nickname_prefix_default" in pro
    assert 'nickname_default_prefix", "Bürger ✗"' in nickname_cog
    assert 'c.config.setdefault("role_nickname_prefixes",{})' in pro
    assert "select_highest_role_prefix" in nickname_utils
    assert "_TEAM_ROLE_PREFIX_ORDER" in nickname_utils
    assert "role_position" in nickname_utils
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "EX_TEAMLER_ROLE_ID = 1489912887949394082" in read("pulse_ids.py")
    assert "EX_TEAMLER_ROLE_ID = _pulse_ids.EX_TEAMLER_ROLE_ID" in nickname_utils
    assert 'EX_TEAMLER_PREFIX = "𝐄𝐗 𝐓𝐄𝐀𝐌𝐋𝐄𝐑 ✗"' in nickname_utils
    assert "automatic_role_prefix" in nickname_utils
    assert "role.id, role.position, role.name, role.is_default()" in nickname_cog
    assert "strip_legacy_rank_prefix" in nickname_cog
    assert "await member.guild.fetch_member(member.id)" in nickname_cog
    assert 'return "deferred"' in nickname_cog
    # The large existing-member sync must never block Discord with repeated synchronous JSON IO.
    assert "asyncio.to_thread(load_config)" in nickname_cog
    assert "self._member_state_cache" in nickname_cog
    assert "os.replace(temp_path, STATE_FILE)" in nickname_cog
    assert "if index % 25 == 0" in nickname_cog
    assert "load_json(STATE_FILE" not in nickname_cog
    assert "save_json(STATE_FILE" not in nickname_cog
    assert nickname_cog.count("async def _get_member_state") == 1
    assert "json.JSONDecodeError" in nickname_cog

    # Persistent moderator-abuse protection: rate-limit both manual warning interfaces,
    # preserve lockout state in SQLite/backups, and auto-remove team roles on repeat abuse.
    team_protection = read("pulse_team_protection.py")
    parse("tests/test_team_protection.py")
    parse("tests/test_team_protection_db.py")
    assert "SAME_TARGET_WARN_LIMIT = 3" in team_protection
    assert "MULTI_TARGET_WARN_LIMIT = 4" in team_protection
    assert "FIRST_LOCKOUT_SECONDS = 30 * 60" in team_protection
    assert "REPEAT_INCIDENT_WINDOW_SECONDS = 24 * 60 * 60" in team_protection
    assert "CREATE TABLE IF NOT EXISTS team_safety_warn_events" in read("pulse_db.py")
    assert "CREATE TABLE IF NOT EXISTS team_safety_actor_state" in read("pulse_db.py")
    assert '"team_safety_actor_state"' in read("pulse_db.py")
    assert "def check_team_warn_safety(" in read("pulse_db.py")
    assert "def check_team_safety_action(" in read("pulse_db.py")
    assert "async def enforce_team_warn_protection(" in web
    assert "async def enforce_high_impact_warning_approval(" in web
    assert "high_impact_warning_reason(" in web
    # BOT-05: /warn delegates to webserver.issue_team_warning, which runs both checks
    # (behaviour: tests/test_team_warning_domain.py::test_slash_path_goes_through_protection_and_approval)
    assert "web.issue_team_warning(" in read("cogs/pulse_suite.py")
    assert "await enforce_high_impact_warning_approval(" in web
    assert "async def enforce_team_safety_action_lockout(" in web
    assert "_apply_team_safety_team_kick" in web
    assert "await enforce_team_warn_protection(" in web  # reached by /warn via issue_team_warning (BOT-05)
    assert "active warning lockout" not in read("cogs/pulse_suite.py")
    assert 'if key in {"warn-remove","server-exit","team-exit","kick","team-kick","promote","demote"}:' in read("cogs/pulse_suite.py")

    # Warning lifetime, probation and automatic penalty cleanup regressions.
    assert "DEFAULT_AUTO_WARN_DURATION_DAYS = 7" in web
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "TEST_SUPPORTER_ROLE_ID = 1498397925817188518" in read("pulse_ids.py")
    assert "TEST_SUPPORTER_ROLE_ID = _pulse_ids.TEST_SUPPORTER_ROLE_ID" in web
    assert "VALID_PROBATION_DAYS = (3, 7)" in web
    assert "def warning_is_expired(" in web
    assert "def cleanup_expired_team_warnings(" in web
    assert "def cleanup_expired_warn_restrictions(" in web
    assert "def process_expired_probations(" in web
    assert "def process_probation_warning(" in web
    assert "async def restore_expired_warn_sanctions(" in web
    assert "downrank_from_role_ids" in web
    assert "team_kick_removed_role_ids" in web
    assert "downrank_reverted_at" in web
    assert "team_kick_reverted_at" in web
    assert "test_supporter_team_kick" in web
    assert "await webserver.cleanup_expired_team_warnings(guild, housekeeping_cfg)" in read("cogs/pulse_pro.py")
    assert "await webserver.cleanup_expired_warn_restrictions(guild, housekeeping_cfg)" in read("cogs/pulse_pro.py")
    assert 'name="warn_duration_days"' in web and 'name="probation_days"' in web
    assert 'name="warn_duration_days"' in pro and 'name="probation_days"' in pro

    # Dashboard-only profiles are mapped to configured Discord team roles.
    assert '"dashboard_roles"' in web
    assert "def dashboard_permission_boxes(" in web
    assert "def dashboard_discord_role_boxes(" in web
    assert 'dashboard_discord_role_ids: List[int] = Form(default=[])' in web
    assert '"save_dashboard_role": "admin"' in web
    assert '"delete_dashboard_role": "admin"' in web
    assert '"discord_role_ids": valid_role_ids' in web
    # A-03: the rule now lives in pulse_permissions (behaviour test: tests/test_permissions.py).
    assert "& member_role_ids & team_ids" in read("pulse_permissions.py")
    assert "Zugeordnete Discord-Teamrollen" in web
    assert "Dashboard-Profile dürfen nur vorhandene, konfigurierte Discord-Teamrollen verwenden." in web
    assert 'if action == "assign_dashboard_role":' not in web
    assert 'if action == "unassign_dashboard_role":' not in web


    # Dashboard Team-Kick must reset the member's roles to exactly the two
    # configured post-kick roles, and must never kick the member from Discord.
    assert "Team-Kick" in web
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "TEAM_KICK_KEEP_ROLE_IDS = (1531303733311307997, 1489912887949394082)" in read("pulse_ids.py")
    assert "TEAM_KICK_KEEP_ROLE_IDS = _pulse_ids.TEAM_KICK_KEEP_ROLE_IDS" in web
    kick_start = web.index('            if action == "team_kick":')
    kick_end = web.index('            if action == "promote":', kick_start)
    kick_block = web[kick_start:kick_end]
    assert 'action = normalize_dashboard_action(action)' in web
    assert 'return "team_kick" if action == "kick" else action' in web
    assert "await apply_team_kick_role_reset(" in kick_block
    assert "Verbleibende Rollen" in kick_block
    assert "TEAM_KICK_KEEP_ROLE_IDS" in web
    assert "async def apply_team_kick_role_reset(" in web
    assert "await member.edit(roles=keep_roles" in web
    assert "team_kick_permanent" in web
    assert 'await member.kick(' not in web
    assert 'name="action" value="team_kick"' in read("pulse_pro.py")
    assert "🚪 Aus dem Team entfernen" in read("pulse_pro.py")
    assert "Dieses Mitglied wirklich vom Discord-Server kicken" not in read("pulse_pro.py")
    ultimate = read("pulse_ultimate.py")
    assert "RAM_TREND_GROWTH_MB_PER_HOUR = 20.0" in ultimate
    assert "Verbrauch wieder im Normalbereich" in ultimate
    assert "current_level=previous" in ultimate
    assert "_RAM_SAMPLES: deque[tuple[float, float]] = deque(maxlen=180)" in ultimate
    assert "def memory_guard_level(" in ultimate
    safety = read("pulse_safety.py")
    parse("pulse_safety.py")
    assert "def rank_role_ids_after_change(" in safety
    assert 'if action_kind == "server-exit":' in safety
    assert 'if action_kind == "team-exit":' in safety
    safety_tests = read("tests/test_pulse_safety.py")
    assert "test_warning_requires_recovery_below_450" in safety_tests
    assert "test_server_kick_and_team_exit_are_not_interchangeable" in safety_tests
    suite_actions = read("cogs/pulse_suite.py")
    parse("cogs/pulse_suite.py")
    assert "await target.kick(" in suite_actions
    assert "await web.apply_team_kick_role_reset(" in suite_actions
    assert "await target.remove_roles(*team_roles" not in suite_actions
    assert "status='executing'" in suite_actions
    assert "rank_role_ids_after_change(" in suite_actions
    assert "if r.id in planned_ids and not r.is_default()" in suite_actions
    assert "Keine andere berechtigte Führungskraft" in suite_actions
    # BOT-05: warn level in the team update and validity in the slash reply now come from the shared
    # domain function (behaviour: tests/test_team_warning_domain.py::test_team_update_and_reply_show_level_and_validity)
    assert 'target=f"{member.mention}\\nWarn-Stufe: {count}/5"' in web
    assert '" Gültig bis "+w["expires_at"][:10]' in suite_actions
    assert "manager_cache=managers(g,web)" in suite_actions
    assert "task_snapshot=db.list_tasks(limit=1000,include_archived=True)" in suite_actions
    assert "ticket_snapshot=db.list_tickets(limit=2000)" in suite_actions
    assert "ctx=auth(request,user_session,manager=True);web=webserver;rows=\"\"" in suite_actions
    assert 'async def member_page(request:Request,member_id:int,user_session:str=Cookie(None)):\n        ctx=auth(request,user_session,manager=True)' in suite_actions
    assert "Interne Notiz aktualisiert" in suite_actions
    assert "🧠 RAM-Schutz" in suite_actions
    assert "🧭 Tagesprioritäten" in suite_actions
    assert "ram_trend_snapshot()" in suite_actions
    assert "Pflichtgrund für den Team-Kick" in suite_actions
    assert "Pflichtgrund für den Server-Kick" in suite_actions
    safety_policy = read("pulse_safety.py")
    assert "RAM_LIMIT_MB = 768.0" in safety_policy
    assert "RAM_WARN_MB = 500.0" in safety_policy
    assert "RAM_CRITICAL_MB = 620.0" in safety_policy
    assert "RAM_EMERGENCY_MB = 700.0" in safety_policy
    assert "async def runtime_memory_guard(bot)" in ultimate
    assert "await runtime_memory_guard(bot)" in ultimate
    assert '"ram_guard": {' in ultimate
    pulse_pro_cog = read("cogs/pulse_pro.py")
    parse("cogs/pulse_pro.py")
    assert 'name="pulse-diagnose"' in pulse_pro_cog
    assert "process_peak_rss_mb" in pulse_pro_cog
    assert "office_system.get_config(\"ping_channel_id\")" in pulse_pro_cog
    assert "office_system.get_config(\"warteraum_channel_id\")" in pulse_pro_cog
    assert 'name="RAM-Langzeittrend"' in pulse_pro_cog
    assert 'name="Bot-Berechtigungen"' in pulse_pro_cog
    assert "or not bot_permissions_ready" in pulse_pro_cog

    # Pulse Next uses synchronous SQLite helpers during periodic score updates.
    # Keep workflow/profile/score maintenance off the Discord event loop.
    pulse_next_cog = read("cogs/pulse_next.py")
    parse("cogs/pulse_next.py")
    assert "await asyncio.to_thread(self._housekeeping_sync, guild)" in pulse_next_cog
    presence_handler = pulse_next_cog.split("async def on_presence_update", 1)[1]
    assert "pulse_next.get_activity(" not in presence_handler
    assert "await asyncio.to_thread(" in presence_handler
    assert "self._presence_last_write" in presence_handler
    assert "def _team_role_ids(self) -> set[int]:" in pulse_next_cog
    assert "self._team_role_config_mtime_ns" in pulse_next_cog
    assert "def _housekeeping_sync(self, guild: discord.Guild | None) -> None:" in pulse_next_cog
    assert "asyncio.run(pulse_next.run_workflows(self.bot))" in pulse_next_cog
    assert "pulse_next.seed_profile_for_member(member)" in pulse_next_cog
    assert "pulse_next.record_score(member)" in pulse_next_cog
    assert "async def run_workflows(bot=None):" in read("pulse_next.py")
    pulse_next_source = read("pulse_next.py")
    parse("pulse_next.py")
    score_record = pulse_next_source.split("def record_score(member, context=None):", 1)[1].split("\ndef all_stats(", 1)[0]
    assert "def award_achievements(member, stats=None, conn=None):" in pulse_next_source  # Phase 10: optional shared connection
    assert "award_achievements(member, s)" in score_record
    assert score_record.index("if exists:\n        return False") < score_record.index("u.member_stats(member, context)")

    # Pulse Pro /member route must expose the same management actions as the
    # central dashboard action handler.
    assert "⚙ Team-Aktionen" in pro
    assert 'value="promote"' in pro
    assert 'value="demote"' in pro
    assert 'name="action" value="team_kick"' in pro
    assert "Pflicht: Grund für Beförderung / Degradierung" in pro
    assert "Pflicht: Grund für die Team-Entfernung" in pro
    assert "🚪 Aus dem Team entfernen" in pro
    assert "vom Discord-Server kicken" not in pro
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
    # A-09: TEAM_UPDATE_CHANNEL_ID is taken from pulse_ids.py (value unchanged)
    assert "TEAM_UPDATE_CHANNEL_ID = _pulse_ids.TEAM_UPDATE_CHANNEL_ID" in web
    assert "TEAM_UPDATE_CHANNEL_ID = 1531132354272170115" in read("pulse_ids.py")
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
        assert role_id in read("pulse_ids.py")  # A-09: IDs live in pulse_ids.py
    assert "5: 1556344484198088814" in read("pulse_ids.py")
    assert "WARN_ROLE_IDS = _pulse_ids.WARN_ROLE_IDS" in web
    assert '"count": max(0, min(int(count or 0), 5))' in web
    assert "current_count >= 5" in web
    assert "for level in sorted(ids):" in web
    assert "warning_5" in web
    assert "/3 Warnungen" not in web

    # The visible /melonly page is Pulse Pro's route, not just the legacy dashboard.
    pro_melonly = read("pulse_pro.py")
    parse("pulse_pro.py")
    version_source = read("pulse_version.py")
    assert re.search(r'(?m)^PULSE_VERSION = "\d+\.\d+\.\d+"$', version_source)
    # The command center route must also exist as an actual Discord slash command.
    pulse_pro_commands = read("cogs/pulse_pro.py")
    assert '@app_commands.command(name="ultimate"' in pulse_pro_commands
    # BOT-04: /pulse-hilfe is generated from the registered commands, so /ultimate appears automatically.
    assert "build_help(entries(self.bot.tree)" in pulse_pro_commands
    # Keep the VPS behind its HTTPS reverse proxy and enforce the intended RAM budget.
    compose_source = read("compose.yaml")
    assert 'mem_limit: 768m' in compose_source
    assert 'memswap_limit: 1024m' in compose_source
    assert '"${PULSE_PUBLISH_HOST:-127.0.0.1}:${SERVER_PORT:-25095}:${SERVER_PORT:-25095}"' in compose_source
    archive_source = read("cogs/announcement_archive.py")
    parse("cogs/announcement_archive.py")
    archive_utils = read("pulse_announcement_utils.py")
    parse("pulse_announcement_utils.py")
    assert 'name="ankuendigung"' in archive_source
    assert 'name="suchen"' in archive_source
    assert 'name="erklaeren"' in archive_source
    assert 'name="wiederholen"' in archive_source
    assert "score_announcement" in archive_utils
    assert "build_announcement_explanation" in archive_utils
    assert "from pulse_version import PULSE_VERSION" in web
    # The user-facing settings route is the Pulse Pro v5 override, so the Zwischenrollen
    # builder and its save/delete handlers must live there (not only in legacy webserver.py).
    assert "Digitale Zwischenrollen" in pro_melonly
    assert 'dashboard_role_cfg = c.config.get("dashboard_roles", {})' in pro_melonly
    assert 'name="dashboard_discord_roles"' in pro_melonly
    # A-03: profile permissions are computed in pulse_permissions (only via the member's team roles);
    # behaviour test: tests/test_permissions.py::test_profiles_only_through_team_roles_and_inheritance
    assert 'profile.get("discord_role_ids", [])' in read("pulse_permissions.py")
    assert "app.post('/settings/dashboard-profile/save')(settings_dashboard_profile_save)" in pro_melonly
    assert "app.post('/settings/dashboard-profile/delete')(settings_dashboard_profile_delete)" in pro_melonly
    assert "from pulse_version import PULSE_VERSION" in pro_melonly
    assert "VERSION = PULSE_VERSION" in pro_melonly
    assert 'pulse:scroll-restore:v1' in web
    assert 'saved.path === location.pathname' in web
    assert 'document.addEventListener' in web and 'submit' in web
    assert '</style>{SCROLL_RESTORE_SCRIPT}</head><body><main class="wrap">' in web
    assert "ws.can_manage_melonly_entry(c,x)" in pro_melonly
    assert '@app.get("/favicon.ico", include_in_schema=False)' in web
    assert "def _embed_signature(embed: discord.Embed)" in read("cogs/teamliste.py")
    assert "def _embed_signature(embed: discord.Embed)" in read("cogs/abmeldung.py")
    assert "Retry-After" in read("cogs/teamliste.py")
    assert "all_role_permissions_disabled" in pro_melonly

    # Public recruitment submissions are gated by a live Discord role check on both GET and POST.
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "APPLICATION_REQUIRED_ROLE_ID = 1531303733311307997" in read("pulse_ids.py")
    assert "APPLICATION_REQUIRED_ROLE_ID = _pulse_ids.APPLICATION_REQUIRED_ROLE_ID" in web
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "APPLICATION_ALERT_CHANNEL_ID = 1558776676731719720" in read("pulse_ids.py")
    assert "APPLICATION_ALERT_CHANNEL_ID = _pulse_ids.APPLICATION_ALERT_CHANNEL_ID" in web
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "APPLICATION_ALERT_ROLE_ID = 1531300183088824340" in read("pulse_ids.py")
    assert "APPLICATION_ALERT_ROLE_ID = _pulse_ids.APPLICATION_ALERT_ROLE_ID" in web
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "APPLICATION_ROLE_ID = 1496070682126188604" in read("pulse_ids.py")
    assert "APPLICATION_ROLE_ID = _pulse_ids.APPLICATION_ROLE_ID" in web
    active_application_statuses = web.split("active_application_statuses =", 1)[1].split("\n", 1)[0]
    assert '"pending", "interview"' in active_application_statuses
    accept_start = web.index('if decision == "accept":')
    hire_start = web.index('if decision == "hire_after_interview":', accept_start)
    accept_block = web[accept_start:hire_start]
    hire_end = web.index('if decision == "reject_after_interview"', hire_start)
    hire_block = web[hire_start:hire_end]
    assert "application_role = guild.get_role(APPLICATION_ROLE_ID)" in accept_block
    assert 'item["status"] = "interview"' in accept_block
    assert "Zum Bewerbungsgespräch eingeladen" in accept_block
    assert "await applicant.add_roles(" in accept_block
    assert 'item["status"] = "hired"' in hire_block
    assert "await applicant.edit(" in hire_block
    assert "set(team_role_ids) | {APPLICATION_ROLE_ID}" in hire_block
    assert "Zum Bewerbungsgespräch einladen" in web
    assert "async def verify_application_member(request: Request, applicant_id)" in web
    career_applications = read("career_applications.py")
    parse("career_applications.py")
    assert "answer_count = len(career_definition[\"questions\"]) if career_definition else 0" in web
    assert "[answer_1, answer_2, answer_3, answer_4, answer_5, answer_6]" not in web
    assert '"pass_percent": 75' in career_applications
    assert '"pass_percent": 82' in career_applications
    assert '"pass_percent": 90' in career_applications
    assert "APPLICATION_PRACTICAL_TASKS" in career_applications
    assert "def render_practical_tasks(" in career_applications
    assert "def grade_practical_tasks(" in career_applications
    assert "def application_module_states(" in web
    assert '@app.get("/applications/config"' in web
    assert '@app.post("/applications/config"' in web
    assert 'grade_practical_tasks(career_path or "", submitted_form)' in web
    assert '"practical_answers": practical_result["answers"]' in web
    assert "practical_review_html" in web
    assert "data-apply-wizard novalidate" in web
    assert "applicationProgressBar" in web
    assert "def save_application_review(" in web
    assert "APPLICATION_REVIEW_CRITERIA" in web
    assert '@app.post("/applications/review")' in web
    assert "Bewertungsmatrix öffnen" in web
    assert "Bewerbung bewertet" in web
    oauth_helper = web.split("def oauth_redirect_uri(", 1)[1].split("\ndef oauth_cookie_secure(", 1)[0]
    assert "(REDIRECT_URI or APPLICATION_REDIRECT_URI) if application else REDIRECT_URI" in oauth_helper
    assert 'return f"{request_origin(request)}/callback"' in oauth_helper
    oauth_callback = web.split('@app.get("/callback")', 1)[1].split("# =============================================================", 1)[0]
    assert "apply_oauth_state: str = Cookie(None)" in oauth_callback
    assert "application=application_flow" in oauth_callback
    assert 'RedirectResponse(url="/apply", status_code=303)' in oauth_callback
    public_apply_source = web.split('async def public_apply_page(', 1)[1].split('@app.get("/apply/login")', 1)[0]
    assert "await verify_application_member(" in public_apply_source
    submit_source = web.split('if action == "submit_application":', 1)[1].split("# ---------- Ab hier: Login + Recht", 1)[0]
    assert "await verify_application_member(" in submit_source
    assert "async def notify_new_application(" in web
    assert "await notify_new_application(request, app_id_new, apps[app_id_new])" in submit_source
    assert "discord.AllowedMentions(" in web
    assert "roles=[alert_role] if alert_role else []" in web
    assert 'can_decide = ctx.perms["can_manage_applications"] or ctx.perms["can_promote"] or ctx.perms["is_admin"]' in web
    assert 'action="/log/edit"' in pro_melonly
    assert 'action="/log/delete"' in pro_melonly
    assert 'name="return_to" value="/melonly"' in pro_melonly
    assert 'request.query_params.get("edit_log")' in pro_melonly
    assert "Bester Treffer" in pro_melonly
    assert "requestId!==latestSearchRequest" in pro_melonly
    assert "latestExactRequest" in pro_melonly
    assert 'document.addEventListener("DOMContentLoaded", function(){' in pro_melonly
    assert 'const name=document.getElementById("melonlyName");' in pro_melonly
    assert 'if(requestId!==latestSearchRequest || norm(name.value).toLowerCase()!==q.toLowerCase())return;' in pro_melonly
    assert 'return_to: str = Form("/dashboard")' in web

    # Melonly uses role-aware management and similarity-ranked Roblox suggestions.
    melonly_helpers = read("pulse_melonly.py")
    parse("pulse_melonly.py")
    assert "def rank_roblox_candidates(" in melonly_helpers
    assert "def roblox_similarity_score(" in melonly_helpers
    assert "def can_manage_melonly_type(" in melonly_helpers
    assert '"can_moderate_kicks"' in pro_melonly
    assert '"can_moderate_bans"' in pro_melonly
    assert '"can_moderate_bolo"' in pro_melonly
    assert "recommended_role_profile(role)" in pro_melonly
    assert "def clear_all_role_permissions(c):" in pro_melonly
    assert 'app.post(\'/settings/pro-clear-all\')(settings_clear_all_permissions)' in pro_melonly
    assert "Alle Rollenrechte entziehen" in pro_melonly
    assert "profile_version < 3" in pro_melonly
    assert 'c.config["all_role_permissions_disabled"] = True' in pro_melonly
    assert 'c.config["permissions_profile_version"] = 3' in pro_melonly
    assert 'dict(no_rights) if keep_roles_disabled else recommended_role_profile(role)' in pro_melonly
    assert "legacy_recommended_role_profile(role)" in pro_melonly
    assert "permissions_profile_version" in pro_melonly
    assert "pk_role" in pro_melonly
    assert "team_lead_role" in pro_melonly
    assert '"can_moderate_bans"' in pro_melonly
    assert '"can_moderate_bolo"' in pro_melonly
    assert "robloxSuggestionPanel" in web
    assert "Bester Treffer" in web
    assert "best_match" in web
    assert "can_manage_melonly_entry(ctx, log)" in web
    assert "can_manage_melonly_entry(ctx, entry, target_type=log_type)" in web
    assert "can_manage_melonly_entry(ctx, entry)" in web
    assert 'manager=bool(ctx.perms.get("is_admin"))' in web
    assert 'auth(request, user_session, perm="can_view_analytics")' in web

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
    # Grouped role manager keeps the same form contract for the guarded save handler.
    role_page = read("pulse_role_manager.py")
    parse("pulse_role_manager.py")
    assert "render_role_manager(esc, ctx, member, team_role_ids)" in web
    assert 'name="add_role_ids"' in role_page and 'name="remove_role_ids"' in role_page
    assert 'name="action_reason"' in role_page and 'action="/team/{member.id}/roles"' in role_page
    # Discord snowflakes are 64-bit IDs and must stay strings in browser JavaScript.
    assert '"id": str(r["id"])' in role_page
    assert '"rankIds": [str(role_id) for role_id in rank_ids]' in role_page
    assert '"currentIds": sorted(str(role_id) for role_id in current_ids)' in role_page
    assert "Number(rank.value)" not in role_page
    assert "Mehrfach-Rollenänderung" in web
    assert "⚙️ Team-Update: Rollenänderung" in web
    assert "⬆️ Befördern" in web
    assert "⬇️ Degradieren" in web
    assert "1531132354272170115" in read("pulse_ids.py")  # A-09
    # Manual team hiring: resolve ID/name safely, assign only a configured rank role,
    # require a reason, respect role hierarchy, notify the member, and log the change.
    assert 'name="target_user_id"' in web
    assert 'name="hire_role_id"' in web
    assert "➕ Teamler manuell einstellen" in web
    assert '"hire": "can_promote"' in web
    assert 'if action == "hire":' in web
    assert "guild.fetch_member(target_id)" in web
    assert "name_query in field(m)" in web
    assert "selected_rank = team_role_ids.index(role.id)" in web
    assert 'team_entry["team_since"] = now_de().isoformat(timespec="seconds")' in web
    assert '"🎉 Team-Update: Manuelle Einstellung"' in web
    assert 'log_audit(actor, actor_id, "Manuelle Einstellung"' in web

    # Team list keeps member actions inside the Details/eye menu.
    assert "details-only" not in web  # no stale marker should leak into UI
    assert 'href="/member/{m["id"]}"' in web
    assert "🗓️ Team seit" in web
    assert "def team_since_for(member, entry=None, apps=None)" in web  # Phase 10: applications.json once per list
    assert 'href="/team/{member.id}/roles"' in web
    assert 'name="action_reason"' in web
    assert 'action == "team_kick"' in web
    assert "not bot_member.guild_permissions.manage_roles" in web
    assert "role.position >= bot_member.top_role.position" in web
    assert "def ordered_team_role_ids(guild, team_role_ids) -> list[int]:" in web
    assert "return sorted(role_ids, key=sort_key)" in web
    assert "ordered_ids = ordered_team_role_ids(guild, team_role_ids)" in web
    assert "ranked_team_role_ids = ordered_team_role_ids(guild, team_role_ids)" in web
    assert '"team_rank_idx": team_rank(member, team_role_ids)' in web
    assert "await member.edit(" in web
    assert "await member.kick(" not in web
    assert "def _record_team_since" in read("cogs/pulse_next.py")
    assert 'entry["team_since"]' in read("cogs/pulse_next.py")
    # Required reasons for personnel actions.
    assert 'name="action_reason"' in web
    assert 'action_reason: str = Form(None)' in web
    assert "Bitte einen Grund für die Maßnahme angeben." in web
    assert "Bitte einen Grund für die Rücknahme der Verwarnung angeben." in web
    assert "Bitte einen Grund für die Rollenänderung angeben." in web
    assert "Bitte einen Grund bzw. eine Gesprächsnotiz angeben." in web
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
    startup_source = read("main.py")
    assert 'spawn_task(server.serve(), name="pulse-webserver", critical=True)' in startup_source
    assert 'server.should_exit = True' in startup_source
    assert 'await asyncio.wait_for(asyncio.shield(task), timeout=8.0)' in startup_source
    assert '"/healthz"' in web and 'add_api_route("/healthz"' in read("pulse_health_api.py")

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
    # Quota/auth failures share a cooldown, while transient moderation outages
    # must not silently suppress replies in either interactive support channel.
    resilience = read("pulse_ai_resilience.py")
    assert "async def provider_request_slot(" in resilience
    assert "def record_provider_failure(" in resilience
    assert "status_code == 429" in resilience
    assert "def _retry_after_seconds(value: str | None) -> int:" in resilience
    assert "_retry_after_seconds(retry_after)" in resilience
    assert "MAX_PROVIDER_STATES = 16" in resilience
    assert "hashlib.sha256(api_key.encode(\"utf-8\")).hexdigest()[:16]" in resilience
    assert "provider_request_slot(" in ai_mod
    assert "provider_pause_remaining(self.api_base, self.api_key)" in ai_mod
    assert "record_provider_failure(" in ai_mod
    assert "def _backoff_provider(" in ai_mod
    assert "if status_code == 429 or status_code in (401, 403):" in ai_mod
    assert "retry_after=response.headers.get(\"retry-after\")" in ai_mod
    assert "def _record_unreviewed_message(self, item: dict[str, Any]) -> None:" in ai_mod
    assert "def _publish_outage_notice(self) -> bool:" in ai_mod
    assert "self._outage_review_items: list[dict[str, Any]] = []" in ai_mod
    assert "def _publish_manual_outage_case(" in ai_mod
    assert 'case.get("manual_outage")' in ai_mod
    assert "Manuelle Moderationsprüfung (KI-Ausfall)" in ai_mod
    assert "view=ModerationReviewView(self, case_id, str(case.get(\"role_kind\", \"OTHER\")))" in ai_mod
    assert "self._record_unreviewed_message(item)" in ai_mod
    assert "Manuelle Moderationsentscheidung nach Prüfung" in ai_mod
    assert 'PULSE_AI_MODERATION_OUTAGE_NOTICE_INTERVAL_SECONDS", "21600"' in ai_mod
    assert "self._outage_notice_interval_seconds" in ai_mod
    assert "self._outage_notice_last_sent_at" in ai_mod
    assert "KI-Moderation pausiert – manuelle Prüfung" in ai_mod
    assert "Keine automatische Klassifikation oder Sanktion. Maßnahmen nur nach menschlicher Prüfung." in ai_mod
    assert "response.status_code >= 500" in ai_mod
    assert '_backoff_provider(f"Verbindungsfehler ({type(exc).__name__})", item=item)' in ai_mod
    # Team KI messages and stale messages after provider outages should not create
    # noisy false-positive moderation reports.
    assert 'env_bool("PULSE_AI_MODERATION_EXCLUDE_TEAM_KI", True)' in ai_mod
    assert "self.excluded_channel_ids.add(self.team_support_channel_id)" in ai_mod
    assert 'PULSE_AI_MODERATION_LOW_CONFIDENCE", "0.85"' in ai_mod
    assert 'PULSE_AI_MODERATION_MAX_QUEUE_AGE_SECONDS", "300"' in ai_mod
    assert "httpx.Limits(max_connections=2, max_keepalive_connections=1" in ai_mod
    assert "item_age > self.max_queue_age_seconds" in ai_mod
    assert "self.low_severity_threshold" in ai_mod
    assert "asyncio.Queue(maxsize=self.max_queue_size)" in ai_mod
    assert "PULSE_AI_MODERATION_QUEUE_SIZE" in ai_mod
    assert "def _drop_pending_queue(self) -> int:" in ai_mod
    assert "self._rate_limit_failures" in ai_mod
    assert "self._paused_queue_drops" in ai_mod
    assert "now < self._paused_until" in ai_mod
    assert "Erwähnte Rollen:" in ai_mod
    assert "Ein sachlich begründeter Rollen-/Team-Ping" in ai_mod
    for source_name in ("cogs/ai_moderation.py", "cogs/backups.py", "cogs/pulse_suite.py"):
        parse(source_name)
    assert "ModerationReviewView" in ai_mod
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "MODERATION_DECISION_ROLE_ID = 1510345520529412188" in read("pulse_ids.py")
    assert "MODERATION_DECISION_ROLE_ID = _pulse_ids.MODERATION_DECISION_ROLE_ID" in ai_mod
    assert "human" not in ai_mod.lower() or "mensch" in ai_mod.lower()
    assert "backup_discord_channel_id" in backups
    assert "create_backup_file" in backups
    assert "backup_discord_channel_id" in web
    assert 'PULSE_BACKUP_DISCORD_CHANNEL_ID' in web
    assert "pre_restore_" in web
    # Full Discord + Pulse backup/restore is validated, importable, and guarded by a pre-restore snapshot.
    assert '"backup_version": 3' in web
    assert "async def create_backup_file_async(guild" in web
    # D-03: Discord caches are read in the event loop, file work runs in a thread (behaviour test: tests/test_full_backup.py).
    assert "await asyncio.to_thread(add_panel_data, snapshot)" in web
    assert "snapshot = await asyncio.to_thread(build_backup, guild, guild_icon_base64)" not in web
    assert 'role_id = int(restriction.get("role_id") or 0)' in web
    assert "if not isinstance(restriction, dict)" in web
    assert "async def restore_guild_structure(guild, backup: dict, exact_restore: bool = False)" in web
    assert "def validate_backup_document(backup: dict, guild_id: int | None = None)" in web
    assert 'action="/backup/upload" method="post" enctype="multipart/form-data"' in web
    assert "MAX_BACKUP_UPLOAD_BYTES = 50 * 1024 * 1024" in web
    assert 'await create_backup_file_async(ctx.guild, "pre_restore_")' in web
    assert "member_assignments" in web
    assert "guild_settings" in web
    assert "icon_base64" in web
    assert 'await create_backup_file_async(guild, "auto_")' in web
    assert "Exakte Struktur erzwingen" in web
    assert "def resolve_backup_role_id(role_id: int, config: dict | None = None)" in web
    assert 'restored_config["backup_role_id_aliases"] = aliases' in web
    assert "if role.managed or not exact_restore" in web
    assert "MAX_BACKUP_UPLOAD_BYTES = 50 * 1024 * 1024" in web
    assert 'if panel.get("logs") == {}' in web
    assert "Nicht unterstützte Backup-Version" in web
    assert "Dieses Backup gehört zu einem anderen Discord-Server" in web
    assert '"/ultimate/api/monitor"' in ultimate
    assert "automation_failures_24h" in ultimate
    assert "disk_free_mb" in ultimate
    assert "def process_rss_mb() -> Optional[float]:" in ultimate
    assert '"process_rss_mb": process_rss_mb()' in ultimate
    assert '"paused_queue_drops"' in ultimate
    assert '"ai_support": ai_support' in ultimate
    assert "RAM des Pulse-Prozesses" in ultimate
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
    assert "def _prune_runtime_state(self, now: float) -> None:" in support_assistant
    assert "self._api_paused_until" in support_assistant
    assert "self._api_rate_limit_failures" in support_assistant
    assert "self._api_lock = asyncio.Lock()" in support_assistant
    assert "self._api_lock.locked()" not in support_assistant
    assert "request_key = (message.channel.id, message.author.id)" in support_assistant
    assert "async def _reply_status_notice(" in support_assistant
    assert "def _provider_unavailable_text(self) -> str:" in support_assistant
    assert "await self._reply_status_notice(message, self._provider_unavailable_text())" in support_assistant
    assert "self._api_transient_failures" in support_assistant
    assert "while len(self._history) > 200" in support_assistant
    assert "while len(self._user_last_request) > 500" in support_assistant
    assert "httpx.Limits(max_connections=2, max_keepalive_connections=1" in support_assistant
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
    assert "DEFAULT_OFFICE_ROLE_IDS: dict[int, tuple[int, ...]]" in buero
    assert "1541048940504555603: (1529263871003726005, 1552036094239842436)" in read("pulse_ids.py")  # A-09
    assert "DEFAULT_OFFICE_ROLE_IDS: dict[int, tuple[int, ...]] = _pulse_ids.OFFICE_ROLE_IDS" in buero
    assert "office_roles = office_roles_for(guild, target_vc_id)" in buero
    assert "roles=office_roles" in buero
    for office_id in (
        1541048940504555603, 1541038737188724996, 1508167060444942487,
        1505243897603948635, 1523012037888970894, 1535286753584218292,
        1541494047548379317, 1510377962774728815, 1505245108654571540,
        1535312722344804432, 1535392546476527738, 1559580826025599056,
    ):
        assert f"{office_id}:" in read("pulse_ids.py"), f"Büro-Ping-Zuordnung fehlt: {office_id}"  # A-09
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
    # On entering the configured/default waiting room, Pulse immediately posts
    # a persistent actionable ping; the existing DM office-selection remains.
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "OFFICE_WAITING_ROOM_ID = 1528821871057965067" in read("pulse_ids.py")
    assert "DEFAULT_WAITING_ROOM_ID = _pulse_ids.OFFICE_WAITING_ROOM_ID" in buero
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "OFFICE_PING_CHANNEL_ID = 1560211874602160218" in read("pulse_ids.py")
    assert "DEFAULT_PING_CHANNEL_ID = _pulse_ids.OFFICE_PING_CHANNEL_ID" in buero
    assert "OFFICE_CONFIG_VERSION = 3" in buero
    assert "migrate_office_config()" in buero
    assert "await self._send_arrival_ping(member, after.channel)" in buero
    assert 'content = (' in buero
    assert "arrival_role = office_role_for(member.guild, waiting_room.id)" in buero
    assert "roles=[arrival_role] if arrival_role else []" in buero
    assert 'target_voice_id=0' in buero
    assert "Die DM-Auswahl bleibt zusätzlich bestehen" in buero

    # Voice-support cases and tickets are attributed to individual team members.
    # Meeting-boundary counts remain available for the meeting history/legacy report.
    pulse_db_source = read("pulse_db.py")
    assert "def get_support_case_period_start()" in pulse_db_source
    assert "def support_case_counts(start_at=None, end_at=None)" in pulse_db_source
    assert "def finish_support_case_period(actor_id, actor_name)" in pulse_db_source
    assert "support_case_period_history" in pulse_db_source
    assert "def record_voice_support_case(" in pulse_db_source
    assert "CREATE TABLE IF NOT EXISTS voice_support_case_history" in pulse_db_source
    assert "def voice_support_case_counts(start_at=None, guild_id=None)" in pulse_db_source
    assert "def ticket_member_activity_counts(start_at=None, guild_id=None)" in pulse_db_source
    assert "support_counts = pulse_db.support_case_counts(support_period_start)" in web
    assert '"voice_cases_completed": int(voice_support_counts.get(str(member.id), {}).get("completed", 0))' in web
    assert '"ticket_cases_completed": int(ticket_counts.get(str(member.id), {}).get("completed", 0))' in web
    assert "🎧 {m['voice_cases_completed']} erledigt · {m['voice_cases_active']} offen" in web
    assert "🎫 {m['ticket_cases_completed']} erledigt · {m['ticket_cases_active']} offen" in web
    assert 'value="reset_support_case_period"' in web
    assert '"reset_support_case_period": "can_promote"' in web
    assert 'name="custom_opening_audio_file"' in pro
    assert 'name="custom_after_hours_audio_file"' in pro
    assert 'name="remove_custom_opening_audio"' in pro
    assert 'name="remove_custom_after_hours_audio"' in pro
    assert 'custom_opening_audio_path' in pro and 'custom_after_hours_audio_path' in pro
    assert 'valid_audio_signature(music_extension, waiting_music_data)' in pro

    # The native Pulse ticket flow provisions panels in the six specified channels,
    # logs opened/claimed tickets, and exposes the management page on the Pulse site.
    tickets = read("cogs/tickets.py")
    parse("cogs/tickets.py")
    for channel_id in (
        1511390378752741386, 1494338404224340179, 1494397389635719269,
        1488972966774706186, 1559242374658662602, 1488699195157254436,
    ):
        assert str(channel_id) in read("pulse_ids.py"), f"Ticketpanel-Kanal fehlt: {channel_id}"  # A-09
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "TICKET_OPEN_LOG_CHANNEL_ID = 1543914075308818503" in read("pulse_ids.py")
    assert "DEFAULT_TICKET_OPEN_LOG_CHANNEL_ID = _pulse_ids.TICKET_OPEN_LOG_CHANNEL_ID" in tickets
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "TICKET_CLAIM_LOG_CHANNEL_ID = 1535025111722098778" in read("pulse_ids.py")
    assert "DEFAULT_TICKET_CLAIM_LOG_CHANNEL_ID = _pulse_ids.TICKET_CLAIM_LOG_CHANNEL_ID" in tickets
    assert 'name="ticketpanels_setup"' in tickets
    assert "await send_ticket_log(" in tickets
    assert 'title="🎫 Neues Ticket geöffnet"' in tickets
    assert 'title="📥 Ticket übernommen"' in tickets
    assert 'async def pulse_tickets_dashboard(' in web
    assert 'filter_name = request.query_params.get("status", "all")' in web
    assert "Remove only the messages Pulse previously registered as ticket panels" in tickets
    assert 'async def pulse_tickets_dashboard_action(' in web
    assert 'auth(request, user_session, perm="can_manage_tickets")' in web
    assert "configured_panel_channel_ids()" in tickets
    assert 'configured_ticket_log_channel_id("opened")' in tickets
    assert 'configured_ticket_log_channel_id("claimed")' in tickets
    assert 'async def pulse_tickets_dashboard_settings(' in web
    assert 'async def pulse_ticket_panels_setup(' in web
    assert 'async def pulse_ticket_transcript(' in web
    assert 'async def pulse_tickets_dashboard_action(' in web
    assert 'if action == "priority":' in web
    assert 'if action == "reopen":' in web
    assert 'async def _refresh_pulse_ticket_discord_state(' in web
    assert "def reopen_ticket(" in read("pulse_db.py")
    assert '("rated", f"Bewertet ({rated_count})")' in web
    assert "ticket_open_log_channel_id" in web and "ticket_claim_log_channel_id" in web
    # Native multi-panel editor: independent panels, category registry, publishing and deletion.
    assert "async def publish_panel_for_guild" in tickets
    # Deleted ticket panel messages should self-heal and persist replacement IDs.
    assert "except discord.NotFound:" in tickets
    assert 'current_settings["panel_message_id"] = replacement.id' in tickets
    assert "wurde neu erstellt" in tickets
    assert "await self.publish_panel_for_guild(guild, str(panel[\"id\"]))" in tickets
    assert "def ticket_category_definitions()" in tickets
    assert "from pulse_version import PULSE_VERSION" in web
    assert "Panel-Builder" in web
    # The Verwaltung area (sidebar switch + command palette) exposes the standalone builder to admins.
    admin_area = read("pulse_admin.py")
    assert '("ticket-panels", "/tickets/panels", "🎛️", "Ticket-Panels"' in admin_area
    assert "from pulse_admin import ADMIN_PAGE_KEYS, admin_sections" in pro_melonly
    assert "items.extend((icon, label, href) for _, href, icon, label, _ in sections)" in pro_melonly
    assert '@app.get("/tickets/panels", response_class=HTMLResponse)' in web
    assert 'href="/tickets/panels#panel-builder"' in web
    assert 'id="panel-builder"' in web
    # The dedicated route must render a standalone builder page, not only the ticket overview.
    assert 'if request.url.path == "/tickets/panels" and not ctx.perms.get("is_admin")' in web
    assert 'Ticket-Panel-Builder' in web
    assert 'Erstelle mehrere unabhängige Ticket-Panels' in web
    assert 'document_head = page.split("<body>", 1)[0] + "<body>"' in web
    assert 'back("/tickets/panels' in web
    assert 'action="/tickets/panels/save"' in web
    assert 'action="/tickets/panels/publish"' in web
    assert 'action="/tickets/panels/delete"' in web
    assert 'action="/tickets/categories/save"' in web
    assert 'action="/tickets/categories/delete"' in web
    assert 'name="category_keys"' in web
    assert "pulse_ticket_category_registry()" in web
    assert "if not panels and not custom_panels:" in tickets

    # Social video announcements: configurable sources, deduplication and fixed target.
    social = read("cogs/social_promo.py")
    parse("cogs/social_promo.py")
    assert "MAX_FEED_BYTES = 2_000_000" in social
    assert 'self._http_client.stream("GET", url)' in social
    assert "self._http_client: httpx.AsyncClient | None = None" in social
    assert "limits=httpx.Limits(max_connections=5, max_keepalive_connections=3" in social
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "SOCIAL_TARGET_CHANNEL_ID = 1530972281893949521" in read("pulse_ids.py")
    assert "TARGET_CHANNEL_ID = _pulse_ids.SOCIAL_TARGET_CHANNEL_ID" in social
    # A-09: value lives in pulse_ids.py, the module takes it from there
    assert "SOCIAL_PING_ROLE_ID = 1516838538702753943" in read("pulse_ids.py")
    assert "SOCIAL_PING_ROLE_ID = _pulse_ids.SOCIAL_PING_ROLE_ID" in social
    # Every announcement (real and test) is built in one place and pings only the creator role.
    assert 'content = f"<@&{SOCIAL_PING_ROLE_ID}>\\n" + announcement_text(' in social
    assert social.count("roles=[discord.Object(id=SOCIAL_PING_ROLE_ID)]") == 2
    assert social.count("users=False") >= 2
    assert "content, embed, view = self.build_announcement(" in social
    assert 'name="social_link"' in social and "detect_platform(link)" in social
    assert "and is_recent(_parse_entry_date(" in social
    assert 'discord.ui.Button(label="Jetzt ansehen"' in social
    social_web = read("pulse_social_dashboard.py")
    parse("pulse_social_dashboard.py")
    assert '@app.get("/social", response_class=HTMLResponse)' in social_web
    assert 'ctx.perms.get("is_admin") or ctx.perms.get("can_manage_announcements")' in social_web
    assert "from pulse_social_dashboard import register as register_pulse_social" in web
    assert '("social", "/social", "📣", "Social Media"' in read("pulse_admin.py")
    assert 'name="social_add"' in social
    assert 'name="social_list"' in social
    assert 'name="social_remove"' in social
    assert 'name="social_toggle"' in social
    assert 'name="social_test"' in social
    assert "@tasks.loop(minutes=POLL_INTERVAL_MINUTES)" in social
    assert "def parse_feed(xml_text: str)" in social
    assert "def _feed_candidates(self, feed_url: str, platform: str)" in social
    assert "def _fetch_feed(" in social
    assert "self._host_retry_at: dict[str, float] = {}" in social
    assert "def _record_host_failure(self, host: str, exc: Exception) -> None:" in social
    assert "class FeedCooldownError(ValueError):" in social
    assert "def _retry_after_seconds(exc: Exception) -> int:" in social
    assert "retry_after_seconds=earliest" in social
    assert "_retry_after_seconds(exc)" in social
    assert "def _host_cooldown(self, host: str) -> float:" in social
    assert "if attempted == 0:" in social
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

    # The moderation digest cooldown must survive a successful probe while a provider is flapping.
    assert ai_mod.count("self._outage_notice_last_sent_at = 0.0") == 1
    assert "it must not bypass the outage-notice cooldown" in ai_mod

    # Message deletion is confirmed, bounded, and avoids individually deleting old history by default.
    messagedelete = read("cogs/messagedelete.py")
    parse("cogs/messagedelete.py")
    # BOT-01: the view now derives from PulseView (a discord.ui.View) – behaviour checked in
    # tests/test_interactions.py::test_message_delete_still_needs_confirmation
    assert "class MessageDeleteConfirmationView(" in messagedelete
    assert "app_commands.Range[int, 1, 1000]" in messagedelete
    assert "await channel.purge(" in messagedelete
    assert "after=cutoff" in messagedelete
    assert "limit=None" not in messagedelete
    assert "await asyncio.sleep(1.1)" in messagedelete
    assert "amount > 100" in messagedelete

    # Pulse Intelligence must remain deterministic, local-only, and non-mutating.
    intelligence = read("pulse_intelligence.py")
    assert "local_rules_no_external_ai" in intelligence
    assert "register_pulse_intelligence" in web
    assert '("/ultimate/copilot"' in intelligence or '"/ultimate/copilot"' in intelligence
    assert '"/ultimate/system-twin"' in intelligence
    assert '"/ultimate/sandbox"' in intelligence
    assert "execute_shell" not in intelligence
    assert memory_state(499.9)["level"] == 0
    assert memory_state(500)["level"] == 1
    assert memory_state(620)["level"] == 2
    assert memory_state(700)["level"] == 3
    test_now = datetime(2026, 10, 17, 12, 0, tzinfo=__import__("datetime").timezone.utc)
    assert ticket_is_overdue({"status": "open", "priority": "urgent", "opened_at": "2026-10-17T11:00:00+00:00"}, test_now)
    assert not ticket_is_overdue({"status": "closed", "priority": "urgent", "opened_at": "2026-10-17T10:00:00+00:00"}, test_now)
    assert system_health([{"state": "ok"}, {"state": "warning"}])["status"] == "degraded"
    intelligence_metrics = {
        "open_tickets": 7, "urgent_tickets": 2, "ticket_sla_overdue": 1,
        "overdue_tasks": 3, "open_cases": 4, "automation_failures_24h": 0,
        "active_staff": 2, "rss_mb": 215,
    }
    report = build_copilot_report("Warum sind Tickets offen?", intelligence_metrics)
    assert report["focus"] == "tickets" and report["mode"] == "local_rules_no_external_ai"
    assert any(item["topic"] == "tickets" for item in build_findings(intelligence_metrics))
    assert simulate_scenario("staff_absence", 100, intelligence_metrics)["status"] == "critical"
    assert simulate_scenario("ram_spike", 20, {**intelligence_metrics, "rss_mb": 605})["status"] == "critical"
    valid_workflow = {
        "schema_version": 1, "name": "SLA reminder", "trigger": "ticket_overdue",
        "conditions": [{"field": "priority", "operator": "in", "value": ["high", "urgent"]}],
        "actions": [{"type": "notify_assignee", "message": "Please review"}], "enabled": False,
    }
    ok, errors, _ = validate_workflow_json(__import__("json").dumps(valid_workflow))
    assert ok, errors
    valid_workflow["actions"] = [{"type": "execute_shell", "message": "must be blocked"}]
    ok, errors, _ = validate_workflow_json(__import__("json").dumps(valid_workflow))
    assert not ok and errors

    # The configured opening announcement runs once per case; selected waiting music loops afterwards.
    voice_audio_tree = parse("cogs/voice_support.py")
    voice_audio_fn = next(node for node in ast.walk(voice_audio_tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_start_waiting_audio")
    voice_audio_source = ast.get_source_segment(read("cogs/voice_support.py"), voice_audio_fn) or ""
    assert 'self._configured_audio_path(cfg, "custom_opening_audio_path")' in voice_audio_source
    assert 'self._configured_audio_path(cfg, "waiting_music_path")' in voice_audio_source
    assert 'if cfg.get("waiting_music_enabled") else None' in voice_audio_source
    assert "opening_audio_played" in voice_audio_source
    assert "mark_opening_audio = True" in voice_audio_source
    assert "after=after_playback" in voice_audio_source
    assert "async def _continue_waiting_audio(self, guild_id: int, room_id: int, previous_case_id: str):" in read("cogs/voice_support.py")
    assert "Anschließend läuft die aktivierte Wartemusik automatisch in Schleife" in read("pulse_pro.py")

    # Voice Support starts queue audio immediately, recovers idle voice clients,
    # and maintains a persistent live status embed in the notification channel.
    voice_support_source = read("cogs/voice_support.py")
    parse("cogs/voice_support.py")
    assert "async def _start_waiting_audio(self, case: dict, cfg: dict) -> bool:" in voice_support_source
    assert "await voice_client.disconnect(force=True)" in voice_support_source
    assert "await self._start_waiting_audio(saved_case, cfg)" in voice_support_source
    assert "async def _refresh_support_status(self, guild=None):" in voice_support_source
    assert "SUPPORT_STATUS_FOOTER = \"Pulse Voice-Support · Live-Status\"" in voice_support_source
    assert 'cfg.get("support_status_message_id")' in voice_support_source
    assert "await self._refresh_support_status(guild)" in voice_support_source

    # Support claiming is independent of the transient on-duty role.
    voice_claim = parse("cogs/voice_support.py")
    support_source = read("cogs/voice_support.py")
    support_method = next(
        node for node in ast.walk(voice_claim)
        if isinstance(node, ast.FunctionDef) and node.name == "_is_support_staff"
    )
    support_segment = ast.get_source_segment(support_source, support_method) or ""
    assert 'cfg.get("support_role_id")' in support_segment
    assert 'cfg.get("duty_role_id")' in support_segment
    assert 'cfg.get("duty_mode")' not in support_segment

    # User-facing menu and palette hide the three removed workflow items.
    pulse_pro_source = read("pulse_pro.py")
    sidebar_tree = ast.parse(pulse_pro_source)
    sidebar_fn = next(node for node in ast.walk(sidebar_tree) if isinstance(node, ast.FunctionDef) and node.name == "pro_sidebar_html")
    palette_fn = next(node for node in ast.walk(sidebar_tree) if isinstance(node, ast.FunctionDef) and node.name == "build_command_modal")
    sidebar_text = ast.get_source_segment(pulse_pro_source, sidebar_fn) or ""
    palette_text = ast.get_source_segment(pulse_pro_source, palette_fn) or ""
    for removed_path in ("/tasks", "/approvals", "/handover"):
        assert removed_path not in sidebar_text
        assert removed_path not in palette_text
    assert "cal-grid" in pulse_pro_source
    assert 'name="starts_at" type="datetime-local"' in pulse_pro_source
    assert "app.post('/calendar/create')(create_calendar_event_v5)" in pulse_pro_source
    assert "CREATE TABLE IF NOT EXISTS calendar_events" in read("pulse_db.py")

    # Security hardening: search results are escaped before innerHTML (stored XSS via
    # Discord names/warn reasons), deletes are never triggered by GET links, and
    # evidence uploads are read with a size cap instead of fully into RAM.
    suite_security = read("cogs/pulse_suite.py")
    assert "'+x.title+'" not in suite_security
    assert "h(x.title)" in suite_security and "h(u(x.url))" in suite_security
    ultimate_security = read("pulse_ultimate.py")
    assert '@app.post("/ultimate/calendar/{event_id}/delete")' in ultimate_security
    assert "href='/ultimate/calendar/{x['id']}/delete'" not in ultimate_security
    assert "await file.read(MAX_UPLOAD_BYTES + 1)" in ultimate_security
    assert "data = await file.read()\n" not in ultimate_security
    assert "pulse_team_export.csv'); open(path" not in pulse_pro_source

    # Phone/tablet: 16px inputs (no iOS zoom). Ultimate and Suite now render inside the
    # shared shell (scrollable drawer sidebar), so they need no separate mobile navigation.
    assert "textarea{font-size:16px!important}" in pulse_pro_source
    design = read("pulse_design.py")
    parse("pulse_design.py")
    assert "def legacy_page(" in design and ".pulse-legacy .card{" in design
    assert "return legacy_page(" in ultimate_security and "return legacy_page(" in suite_security
    assert 'f"u:{active}"' in ultimate_security and '"s:"+str(active)' in suite_security
    assert "from pulse_design import SUITE_NAV, ULTIMATE_NAV, visible_nav" in pulse_pro_source
    assert ".pulse-legacy .input,.pulse-legacy .select,.pulse-legacy .ta{font-size:16px}" in design

    # Mein Pulse: personal start page, registered after the other modules and in all navigations.
    home = read("pulse_home.py")
    parse("pulse_home.py")
    parse("pulse_home_utils.py")
    assert '@app.get("/me", response_class=HTMLResponse)' in home
    assert "ctx = ws.auth(request, user_session)" in home
    assert 'name="return_to" value="/me"' in home
    assert "from pulse_home import register as register_pulse_home" in web
    assert 'target_page = return_to if return_to in {"/dashboard", "/me"} else "/dashboard"' in web
    assert '("me","/me","★","Mein Pulse",True,False)' in pulse_pro_source

    # Leitungs-Cockpit: leadership-only, server-side authorisation and one-click shift end.
    cockpit = read("pulse_cockpit.py")
    parse("pulse_cockpit.py")
    parse("pulse_cockpit_utils.py")
    assert '@app.get("/cockpit", response_class=HTMLResponse)' in cockpit
    assert 'if not (ctx.perms.get("can_promote") or ctx.perms.get("is_admin")):' in cockpit
    assert 'action="/shift/admin-end"' in cockpit
    assert "from pulse_cockpit import register as register_pulse_cockpit" in web
    assert '("cockpit","/cockpit","◆","Leitungs-Cockpit",perms.get("can_promote") or perms.get("is_admin"),False)' in pulse_pro_source

    # Bot: Monday report covers the completed week, long shifts get a friendly self-DM with
    # the real name, /pulse-hilfe is permission-aware and a single start report is posted.
    suite_bot = read("cogs/pulse_suite.py")
    pro_cog = read("cogs/pulse_pro.py")
    assert "(ls,le),(ps,pe)=previous_full_weeks(today)" in suite_bot
    assert "name=member.display_name if member else s.get(\"mod_name\",uid)" in suite_bot
    # BOT-12: the self-DM goes through the DM queue (quiet hours, bundling; closed DMs are skipped there –
    # behaviour: tests/test_notify_prefs.py::QuietTimeTests and DrainTests)
    assert "longshift-self:" in suite_bot and "pulse_notify_prefs.queue_dm(member.id" in suite_bot
    assert '@app_commands.command(name="pulse-hilfe"' in pro_cog
    # BOT-04: the help is generated from the command tree and filtered by rights
    # (behaviour: tests/test_command_help.py::test_help_is_generated_and_filtered)
    assert "build_help(entries(self.bot.tree), perms" in pro_cog
    assert "_pulse_startup_report_sent" in pro_cog
    assert "self.pulse_failed_cogs.append(" in read("main.py")

    # Ticket-Panel-Builder v2: images, live preview, per-category welcome + forms, automation.
    builder = read("pulse_ticket_builder.py")
    parse("pulse_ticket_builder.py")
    parse("pulse_ticket_design.py")
    assert "from pulse_ticket_builder import render_ticket_builder" in web
    assert 'enctype="multipart/form-data"' in builder and 'name="builder_v2"' in builder
    assert 'action="/tickets/panels/save"' in builder and 'action="/tickets/panels/publish"' in builder
    assert 'action="/tickets/categories/save"' in builder and 'action="/tickets/automation"' in builder
    assert "Erstelle mehrere unabhängige Ticket-Panels" in builder
    assert "async def _store_ticket_panel_image(" in web and "detect=detect_image_type" in web and "save_image_upload(" in web
    assert '@app.get("/tickets/panels/media/{filename}")' in web
    assert "auth(request, user_session, admin=True)" in web.split('async def pulse_ticket_panel_media(')[1][:200]
    assert "uploads/ticket_panels/" in read(".gitignore")
    assert "def panel_files(panel" in tickets and "attachments=files" in tickets
    assert "class TicketQuestionModal(Modal)" in tickets and "send_modal(TicketQuestionModal(" in tickets
    assert 'custom_id="pulse_ticket_remind"' in tickets
    assert "async def run_automation(self)" in tickets and "await self._edit_if_changed(" in tickets
    assert '_ensure_column(db, "tickets", "automation_state"' in read("pulse_db.py")
    # Welcome message images and in-place status updates (welcome text/images survive a claim).
    assert 'image_block("welcome_banner"' in builder and 'name="welcome_use_avatar"' in builder
    assert "welcome_banner_upload: UploadFile | None = File(None)" in web
    assert 'welcome_message = await send_styled(channel, embed=embed, view=TicketControlView(), files=welcome_files' in tickets
    assert 'save_ticket_card(tid, embed' in tickets and 'load_ticket_card(ticket.get("id"), with_meta=True)' in tickets
    assert 'async def move_ticket_channel(channel, ticket: dict, *, claimed: bool)' in tickets and 'sync_permissions=False' in tickets
    # Role picker rows must beat the generic '.tb-form label{display:grid}' rule (otherwise names collapse into bars).
    assert '.tb-form label.tb-role{display:flex!important' in read('pulse_ticket_builder.py')
    assert 'STATUS_FIELDS = ("Status", "Priorität", "Bearbeiter")' in tickets
    assert "embed.set_field_at(index, name=field.name" in tickets

    # Ticket-Zentrale: triage + actions through the existing, server-authorised /tickets/action route.
    center = read("pulse_ticket_center.py")
    parse("pulse_ticket_center.py")
    parse("pulse_ticket_triage.py")
    assert "return render_ticket_center(ws, c, request, render_pro_page)" in read("pulse_pro.py")
    for action in ("claim", "unclaim", "priority", "close", "reopen"):
        assert f'name="action" value="{action}"' in center, action
    assert 'action="/tickets/action"' in center and 'name="reason" class="pulse-input" required' in center
    assert "PAGE_SIZE = 25" in center and "selected.sort(key=lambda pair: pair[1][\"sort_key\"])" in center
    assert 'auth(request, user_session, perm="can_manage_tickets")' in web
    # Mein Pulse lists own tickets with the same triage; shift end summarises open work.
    assert "from pulse_ticket_triage import triage" in read("pulse_home.py")
    assert "msg += shift_end_open_items_text(mod_id, now)" in web

    print("Pulse feature smoke test: OK")


if __name__ == "__main__":
    main()
