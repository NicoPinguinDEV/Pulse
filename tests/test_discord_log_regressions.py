"""Regression tests for Discord interaction acknowledgement and API request deduplication."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def get_method(source: str, name: str) -> str:
    tree = ast.parse(source)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    ]
    if not matches:
        raise AssertionError(f"Method {name!r} missing")
    return ast.get_source_segment(source, matches[0]) or ""


class DiscordLogRegressionTests(unittest.TestCase):
    def test_activity_check_defers_before_database_access_and_uses_followup(self):
        method = get_method(read_source("cogs/activity_check.py"), "confirm")
        self.assertIn("interaction.response.defer(ephemeral=True, thinking=True)", method)
        self.assertLess(
            method.index("interaction.response.defer"),
            method.index("self.cog.current_check_id"),
        )
        self.assertIn("interaction.followup.send", method)
        self.assertIn("except discord.NotFound", method)
        self.assertIn("except discord.HTTPException", method)

    def test_team_member_can_start_shift_while_leave_remains_active(self):
        shift_handler = get_method(read_source("webserver.py"), "handle_shift_action")
        self.assertIn("leave_active = bool(loa and loa.get(\"active\"))", shift_handler)
        self.assertIn("Schicht gestartet. Deine Abmeldung bleibt bis", shift_handler)
        self.assertNotIn("beende erst deine Abmeldung", shift_handler)
        self.assertIn("save_json(SHIFTS_FILE, shifts_db)", shift_handler)

        teamlist_method = get_method(read_source("cogs/teamliste.py"), "create_team_embed")
        self.assertIn("active_shifts = self.get_active_shift_map()", teamlist_method)
        self.assertIn("Im Dienst (Abmeldung bis", teamlist_method)
        self.assertIn("Abgemeldet (bis", teamlist_method)

    def test_activity_check_is_still_confirmable_during_active_leave(self):
        activity_source = read_source("cogs/activity_check.py")
        confirm = get_method(activity_source, "confirm")
        self.assertIn("INSERT OR REPLACE INTO responses", confirm)
        self.assertNotIn("is_abgemeldet(", confirm)
        self.assertIn(
            "Auch mit aktiver Abmeldung kannst du bestätigen, wenn du heute arbeitest.",
            activity_source,
        )
        warning_method = get_method(activity_source, "process_daily_warnings")
        self.assertIn('if leave_status:', warning_method)
        self.assertIn('set_warn_action(check_id, uid, "on_leave")', warning_method)

    def test_voice_support_staff_can_claim_while_off_duty(self):
        source = read_source("cogs/voice_support.py")
        eligibility = get_method(source, "_is_support_staff")
        claim = get_method(source, "_assign_case_locked")
        self.assertIn('cfg.get("support_role_id")', eligibility)
        self.assertIn('cfg.get("duty_role_id")', eligibility)
        self.assertNotIn('cfg.get("duty_mode")', eligibility)
        self.assertIn("Nur Teamler mit Support- oder Bereitschaftsrolle", claim)
        self.assertIn("Tritt zuerst einem normalen Sprachkanal bei", claim)

    def test_voice_support_waiting_audio_recovers_stale_idle_connection(self):
        source = read_source("cogs/voice_support.py")
        audio = get_method(source, "_start_waiting_audio")
        open_case = get_method(source, "_open_case")
        loop = get_method(source, "music_loop")
        self.assertIn("await voice_client.disconnect(force=True)", audio)
        self.assertIn("if voice_client.is_playing():", audio)
        self.assertIn("await channel.connect(self_deaf=True)", audio)
        self.assertIn("voice_client.play(", audio)
        self.assertIn("discord.FFmpegPCMAudio(str(audio_path)", audio)
        self.assertIn("await self._start_waiting_audio(saved_case, cfg)", open_case)
        self.assertIn("await self._start_waiting_audio(case, cfg)", loop)
        self.assertIn("case.get(\"outside_opening_hours\")", audio)

    def test_shared_waiting_room_plays_intro_once_then_loops_selected_music(self):
        source = read_source("cogs/voice_support.py")
        audio = get_method(source, "_start_waiting_audio")
        continuation = get_method(source, "_continue_waiting_audio")
        self.assertIn('self._configured_audio_path(cfg, "custom_opening_audio_path")', audio)
        self.assertIn('self._configured_audio_path(cfg, "waiting_music_path")', audio)
        self.assertIn('if cfg.get("waiting_music_enabled") else None', audio)
        self.assertIn("opening_audio_played", audio)
        self.assertIn("mark_opening_audio = True", audio)
        self.assertIn("after=after_playback", audio)
        self.assertIn("self._continue_waiting_audio", audio)
        self.assertIn("self._start_waiting_audio(item, cfg)", continuation)
        self.assertIn('self._configured_audio_path(cfg, "custom_after_hours_audio_path")', audio)

    def test_voice_support_publishes_one_persistent_live_status(self):
        source = read_source("cogs/voice_support.py")
        status = get_method(source, "_refresh_support_status")
        open_case = get_method(source, "_open_case")
        close_case = get_method(source, "_close_case")
        status_loop = get_method(source, "duty_status_loop")
        self.assertIn("🎧 Voice-Support · Aktueller Status", status)
        self.assertIn('cfg.get("support_status_message_id")', status)
        self.assertIn("channel.get_partial_message(saved_id)", status)
        self.assertIn("saved_partial.edit", status)
        self.assertIn("⏳ Wartende Fälle", status)
        self.assertIn("🎧 Laufende Gespräche", status)
        self.assertIn("await self._refresh_support_status(guild)", open_case)
        self.assertIn("await self._refresh_support_status", close_case)
        self.assertIn("await self._refresh_support_status(guild)", status_loop)

    def test_requested_workflow_items_are_removed_from_normal_navigation(self):
        source = read_source("pulse_pro.py")
        sidebar = get_method(source, "patched_sidebar")
        palette = get_method(source, "build_command_modal")
        for path in ("/tasks", "/approvals", "/handover"):
            self.assertNotIn(path, sidebar)
            self.assertNotIn(path, palette)
        self.assertIn('("calendar","/calendar","□","Kalender",True,False)', sidebar)

    def test_calendar_is_a_month_grid_with_persistent_event_creation(self):
        source = read_source("pulse_pro.py")
        calendar_page = get_method(source, "calendar_v5")
        event_route = get_method(source, "create_calendar_event_v5")
        database = read_source("pulse_db.py")
        self.assertIn("range(42)", calendar_page)
        self.assertIn("cal-grid", calendar_page)
        self.assertIn('name="starts_at" type="datetime-local"', calendar_page)
        self.assertIn("db.list_calendar_events", calendar_page)
        self.assertIn("db.create_calendar_event", event_route)
        self.assertIn("Das Ende muss nach dem Beginn liegen", event_route)
        self.assertIn("CREATE TABLE IF NOT EXISTS calendar_events", database)
        self.assertIn("app.post('/calendar/create')(create_calendar_event_v5)", source)

    def test_abmeldung_preflights_nickname_permissions_and_suppresses_retries(self):
        source = read_source("cogs/abmeldung.py")
        helper = get_method(source, "_can_edit_member_nickname")
        apply = get_method(source, "apply_user_status")
        reset = get_method(source, "reset_user_status")
        self.assertIn("manage_nicknames", helper)
        self.assertIn("member.top_role.position >= bot_member.top_role.position", helper)
        self.assertIn("self._nickname_forbidden", helper)
        self.assertIn("suffix not in member.display_name", apply)
        self.assertIn("self._nickname_forbidden.add(key)", apply)
        self.assertIn("self._nickname_forbidden.add(key)", reset)
        self.assertNotIn('print(f"⚠️ Keine Rechte, um den Namen von {member} zu ändern.")', source)
        self.assertIn("async def on_guild_role_update", source)

    def test_abmeldung_live_list_skips_identical_embed_patch(self):
        method = get_method(read_source("cogs/abmeldung.py"), "update_live_list")
        self.assertIn("len(message.embeds) == len(embeds)", method)
        self.assertIn("_embed_signature(old) == _embed_signature(new)", method)
        self.assertLess(
            method.index("_embed_signature(old) == _embed_signature(new)"),
            method.index("await message.edit(embeds=embeds"),
        )

    def test_teamlist_compares_complete_embed_before_patching(self):
        source = read_source("cogs/teamliste.py")
        method = get_method(source, "update_teamlist")
        self.assertIn("_embed_signature(msg.embeds[0]) == _embed_signature(embed)", method)
        self.assertLess(
            method.index("_embed_signature(msg.embeds[0]) == _embed_signature(embed)"),
            method.index("await msg.edit(embed=embed)"),
        )
        self.assertIn("_retry_after_seconds(e)", method)
        self.assertIn("Retry-After", source)

    def test_favicon_route_prevents_browser_404_noise(self):
        source = read_source("webserver.py")
        self.assertIn('@app.get("/favicon.ico", include_in_schema=False)', source)
        self.assertIn('media_type="image/svg+xml"', source)

    def test_application_version_has_one_source_of_truth(self):
        version = read_source("pulse_version.py")
        self.assertIn('PULSE_VERSION = "7.9.5"', version)
        self.assertIn("from pulse_version import PULSE_VERSION", read_source("webserver.py"))
        self.assertIn("from pulse_version import PULSE_VERSION", read_source("pulse_pro.py"))


if __name__ == "__main__":
    unittest.main()
