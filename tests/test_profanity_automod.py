"""Schimpfwortfilter im AutoMod: Eskalation, Persistenz, Ausnahmen, Modi, Fehlerfälle und Dashboard (lokale Fakes)."""
import asyncio
import os

os.environ["PULSE_ALLOWED_GUILD_IDS"] = "*"  # DISC-8: these tests use fake guild ids (explicit operator opt-out)
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from fastapi.testclient import TestClient

import pulse_db
import pulse_profanity_strikes as strikes
import webserver as ws
from cogs.automod import PulseAutoMod
from tests.fakes import FakeMember
from tests.test_guard_admin import AUTOMOD_FORM, ROLE, auth_with
from tests.test_pulse_admin_area import ADMIN, TEAMLER

BAD = "Du bist ein Arschloch"


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="test"), "test")


def make_guild(guild_id=1, me=None):
    return SimpleNamespace(id=guild_id, owner_id=999, name="BOCHUM RP", get_channel=lambda _cid: None, me=me)


def make_member(uid=123, roles=()):
    member = FakeMember(uid, "Testmitglied", roles=roles)
    member.guild_permissions.manage_messages = False
    member.timeout = AsyncMock()
    member.send = AsyncMock()
    return member


def make_message(guild, member, mid, text=BAD, channel=None):
    return SimpleNamespace(
        id=mid, guild=guild, author=member, webhook_id=None, channel=channel or SimpleNamespace(id=456),
        clean_content=text, mentions=[], role_mentions=[], mention_everyone=False, delete=AsyncMock(),
    )


def make_cog(**config):
    cog = PulseAutoMod(SimpleNamespace(user=SimpleNamespace(id=1)))
    base = {"enabled": True, "mode": "enforce", "rules": ["profanity"]}
    cog._config = lambda: {**base, **config}
    return cog


class ProfanityCogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
            patch("cogs.automod.discord.Member", FakeMember),
            patch("builtins.print"),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def run_async(self, coro):
        return asyncio.run(coro)

    # ---- 9–12: Eskalation ------------------------------------------------------------------------
    def test_escalation_warns_twice_then_times_out_for_exactly_60_minutes_every_time(self):
        guild, member, cog = make_guild(), make_member(), make_cog()

        async def scenario():
            titles = []
            for number in range(1, 6):
                message = make_message(guild, member, number)
                await cog.on_message(message)
                self.assertTrue(message.delete.awaited)
                titles.append(member.send.await_args.kwargs["embed"].title)
                if number < 3:
                    member.timeout.assert_not_awaited()
                else:
                    self.assertEqual(member.timeout.await_count, number - 2)
                    self.assertEqual(member.timeout.await_args.args[0].total_seconds(), 3600)
            return titles

        titles = self.run_async(scenario())
        self.assertEqual(titles[0], "⚠️ Pulse AutoMod – Erste Verwarnung")
        self.assertEqual(titles[1], "⚠️ Pulse AutoMod – Zweite Verwarnung")
        self.assertEqual(set(titles[2:]), {"🔇 Pulse AutoMod – 60 Minuten Timeout"})
        rows = list(reversed(strikes.history(1, 123)))
        self.assertEqual([r["strike_number"] for r in rows], [1, 2, 3, 4, 5])
        self.assertEqual([r["timeout_minutes"] for r in rows], [0, 0, 60, 60, 60])
        self.assertTrue(all(r["outcome"] == "ok" and r["event_id"] and r["category"] == "de" for r in rows))
        events = pulse_db.events(event_type="AutoMod-Maßnahme")
        self.assertEqual(len(events), 5)
        payloads = [json.loads(e["payload_json"]) for e in events]
        self.assertTrue(all(p["rule"] == "profanity" and "content" not in p for p in payloads))
        self.assertNotIn("arschloch", json.dumps(payloads).casefold())  # Nachrichteninhalt wird nie gespeichert
        self.assertNotIn("arschloch", json.dumps(rows).casefold())

    # ---- 13: andere Regeln zählen nicht ------------------------------------------------------------
    def test_other_rules_do_not_feed_the_profanity_counter(self):
        guild, member = make_guild(), make_member()
        cog = make_cog(rules=["profanity", "keywords"], blocked_terms=["verbotenes wort"])

        async def scenario():
            for mid in (1, 2, 3):
                await cog.on_message(make_message(guild, member, mid, "ein verbotenes wort"))
            # unveränderte alte Eskalation: Warnung, 10 Min., dann verdoppelt 20 Min. – nichts davon ist ein Schimpfwort-Verstoß
            self.assertEqual(member.timeout.await_args.args[0].total_seconds(), 1200)
            member.timeout.reset_mock()
            await cog.on_message(make_message(guild, member, 4))

        self.run_async(scenario())
        self.assertEqual([r["strike_number"] for r in strikes.history(1, 123)], [1])
        member.timeout.assert_not_awaited()  # der erste Schimpfwort-Verstoß ist nur eine Verwarnung

    # ---- 14: Server getrennt, 15: Neustart, 16: Zeitfenster ---------------------------------------------
    def test_counters_are_separate_per_server(self):
        member, cog = make_member(), make_cog()

        async def scenario():
            for mid, gid in ((1, 1), (2, 1), (3, 2)):
                await cog.on_message(make_message(make_guild(gid), member, mid))

        self.run_async(scenario())
        self.assertEqual(strikes.count_in_window(1, 123, 60), 2)
        self.assertEqual(strikes.count_in_window(2, 123, 60), 1)
        member.timeout.assert_not_awaited()

    def test_restart_does_not_reset_the_counter(self):
        guild, member = make_guild(), make_member()

        async def scenario():
            await make_cog().on_message(make_message(guild, member, 1))
            await make_cog().on_message(make_message(guild, member, 2))  # neue Cog-Instanz = Neustart
            await make_cog().on_message(make_message(guild, member, 3))

        self.run_async(scenario())
        self.assertEqual(member.timeout.await_count, 1)
        self.assertEqual(member.timeout.await_args.args[0].total_seconds(), 3600)

    def test_strike_window_is_respected_and_history_is_kept(self):
        guild, member = make_guild(), make_member()
        cog = make_cog(strike_window_minutes=30)
        clock = {"now": 1_000_000.0}
        fake_time = SimpleNamespace(time=lambda: clock["now"])

        async def scenario():
            with patch.object(strikes, "time", fake_time):
                await cog.on_message(make_message(guild, member, 1))
                clock["now"] += 20 * 60
                await cog.on_message(make_message(guild, member, 2))
                clock["now"] += 31 * 60  # beide älteren Verstöße sind jetzt außerhalb von 30 Minuten
                await cog.on_message(make_message(guild, member, 3))

        self.run_async(scenario())
        member.timeout.assert_not_awaited()
        rows = list(reversed(strikes.history(1, 123)))
        self.assertEqual([r["strike_number"] for r in rows], [1, 2, 1])
        self.assertEqual(len(rows), 3)

    # ---- 17–18: Fehlerfälle -------------------------------------------------------------------------
    def test_closed_dms_do_not_prevent_the_sanction(self):
        guild, member, cog = make_guild(), make_member(), make_cog()
        member.send = AsyncMock(side_effect=http_error(discord.Forbidden, 403))

        async def scenario():
            for mid in (1, 2, 3):
                message = make_message(guild, member, mid)
                await cog.on_message(message)
                self.assertTrue(message.delete.awaited)

        self.run_async(scenario())
        member.timeout.assert_awaited_once()
        rows = list(reversed(strikes.history(1, 123)))
        self.assertEqual([r["strike_number"] for r in rows], [1, 2, 3])
        self.assertEqual(rows[2]["timeout_minutes"], 60)
        self.assertEqual(rows[2]["outcome"], "partial")
        self.assertIn("DM geschlossen", rows[2]["action"])

    def test_missing_permissions_are_handled_without_counting_or_crashing(self):
        # a) Nachricht kann nicht gelöscht werden -> kein Verstoß gezählt, Fehler protokolliert
        guild, member, cog = make_guild(), make_member(), make_cog()
        message = make_message(guild, member, 1)
        message.delete = AsyncMock(side_effect=http_error(discord.Forbidden, 403))
        self.run_async(cog.on_message(message))
        self.assertEqual(strikes.history(1, 123), [])
        member.send.assert_not_awaited()
        payload = json.loads(pulse_db.events(event_type="AutoMod-Maßnahme")[0]["payload_json"])
        self.assertEqual(payload["outcome"], "error")

        # b) Bot ohne "Nachrichten verwalten" im Kanal
        channel = SimpleNamespace(id=456, permissions_for=lambda _me: SimpleNamespace(manage_messages=False))
        blocked = make_message(make_guild(me=SimpleNamespace()), member, 2, channel=channel)
        self.run_async(cog.on_message(blocked))
        blocked.delete.assert_not_awaited()
        self.assertEqual(strikes.history(1, 123), [])

    def test_timeout_failures_are_logged_and_never_undo_the_count(self):
        # Rollenhierarchie: Bot-Rolle <= Rolle des Mitglieds -> kein API-Aufruf, Verstoß bleibt gezählt
        class Role:
            def __init__(self, position):
                self.position = position

            def __le__(self, other):
                return self.position <= other.position

        me = SimpleNamespace(guild_permissions=SimpleNamespace(moderate_members=True), top_role=Role(5))
        guild, member, cog = make_guild(me=me), make_member(), make_cog()
        member.top_role = Role(9)

        async def scenario():
            for mid in (1, 2, 3):
                await cog.on_message(make_message(guild, member, mid))

        self.run_async(scenario())
        member.timeout.assert_not_awaited()
        third = strikes.history(1, 123)[0]
        self.assertEqual((third["strike_number"], third["outcome"], third["timeout_minutes"]), (3, "error", 0))
        self.assertIn("Rollenhierarchie", third["action"])

        # Discord lehnt den Timeout ab (403)
        other = make_member(124)
        other.timeout = AsyncMock(side_effect=http_error(discord.Forbidden, 403))
        cog2 = make_cog()

        async def scenario2():
            for mid in (11, 12, 13):
                await cog2.on_message(make_message(make_guild(), other, mid))

        self.run_async(scenario2())
        self.assertEqual(strikes.history(1, 124)[0]["outcome"], "error")

    # ---- 19–20: Ausnahmen ---------------------------------------------------------------------------
    def test_exempt_roles_and_channels_are_not_sanctioned(self):
        guild = make_guild()
        exempt_role = SimpleNamespace(id=77)
        member = make_member(roles=[exempt_role])
        message = make_message(guild, member, 1)
        self.run_async(make_cog(exempt_role_ids=[77]).on_message(message))
        message.delete.assert_not_awaited()

        channel_msg = make_message(guild, make_member(124), 2, channel=SimpleNamespace(id=900))
        self.run_async(make_cog(exempt_channel_ids=[900]).on_message(channel_msg))
        channel_msg.delete.assert_not_awaited()

        scoped = make_message(guild, make_member(125, roles=[exempt_role]), 3)
        cog = make_cog(rule_exemptions={"profanity": {"role_ids": [77], "channel_ids": []}})
        self.run_async(cog.on_message(scoped))
        scoped.delete.assert_not_awaited()

        owner = make_member(999)
        owner_msg = make_message(guild, owner, 4)
        self.run_async(make_cog().on_message(owner_msg))
        owner_msg.delete.assert_not_awaited()
        self.assertEqual(strikes.history(1), [])

    def test_a_rule_exemption_for_another_rule_does_not_exempt_profanity(self):
        guild, role = make_guild(), SimpleNamespace(id=77)
        member = make_member(roles=[role])
        message = make_message(guild, member, 1)
        cog = make_cog(rule_exemptions={"spam": {"role_ids": [77], "channel_ids": []}})
        self.run_async(cog.on_message(message))
        self.assertTrue(message.delete.awaited)

    # ---- 21–22: Bearbeitungen und Parallelität -------------------------------------------------------
    def test_edited_messages_are_checked(self):
        guild, member, cog = make_guild(), make_member(), make_cog()
        before = make_message(guild, member, 1, "Hallo zusammen")
        after = make_message(guild, member, 1, "Hallo du Arschloch")
        after.content, before.content = after.clean_content, before.clean_content
        self.run_async(cog.on_message_edit(before, after))
        after.delete.assert_awaited_once()
        self.assertEqual(len(strikes.history(1, 123)), 1)

        harmless = make_message(guild, member, 2, "Hallo")
        harmless_after = make_message(guild, member, 2, "Hallo Welt")
        harmless.content, harmless_after.content = "Hallo", "Hallo Welt"
        self.run_async(cog.on_message_edit(harmless, harmless_after))
        harmless_after.delete.assert_not_awaited()

    def test_parallel_events_for_one_message_create_one_strike(self):
        guild, member, cog = make_guild(), make_member(), make_cog()
        message = make_message(guild, member, 42)

        async def scenario():
            await asyncio.gather(cog.on_message(message), cog.on_message(message), cog.on_message(message))
            edited = make_message(guild, member, 42, "noch ein Arschloch")
            edited.content, message.content = "noch ein Arschloch", "x"
            await cog.on_message_edit(message, edited)

        self.run_async(scenario())
        self.assertEqual(len(strikes.history(1, 123)), 1)
        self.assertEqual(member.send.await_count, 1)
        member.timeout.assert_not_awaited()

    # ---- 23–24: Test- und Beobachtungsmodus -------------------------------------------------------------
    def test_test_and_observe_modes_never_sanction_or_count(self):
        for mode in ("test", "observe"):
            with self.subTest(mode=mode):
                guild, member, cog = make_guild(), make_member(), make_cog(mode=mode)

                async def scenario():
                    for mid in range(1, 5):
                        message = make_message(guild, member, mid)
                        await cog.on_message(message)
                        message.delete.assert_not_awaited()

                self.run_async(scenario())
                member.send.assert_not_awaited()
                member.timeout.assert_not_awaited()
                self.assertEqual(strikes.history(1, 123), [])
        types = {e["event_type"] for e in pulse_db.events(limit=50)}
        self.assertIn("AutoMod-Testtreffer", types)

    def test_disabled_automod_and_disabled_rule_do_nothing(self):
        guild, member = make_guild(), make_member()
        for cog in (make_cog(enabled=False), make_cog(rules=["spam"])):
            message = make_message(guild, member, 1)
            self.run_async(cog.on_message(message))
            message.delete.assert_not_awaited()
        self.assertEqual(strikes.history(1), [])

    # ---- 25: bestehende Regeln -------------------------------------------------------------------------
    def test_existing_rules_keep_their_behaviour(self):
        guild, member = make_guild(), make_member()
        cog = make_cog(rules=["invites", "mentions"], blocked_terms=[])
        invite = make_message(guild, member, 1, "komm auf discord.gg/abcdef")
        self.run_async(cog.on_message(invite))
        self.assertTrue(invite.delete.awaited)
        member.send.assert_awaited_once()
        self.assertEqual(member.send.await_args.kwargs, {})  # alte Warn-DM bleibt reiner Text (kein Schimpfwort-Embed)
        self.assertEqual(strikes.history(1), [])


class ProfanityDashboardTests(unittest.TestCase):
    PROFANITY_FORM = {
        "profanity_enabled": "1", "profanity_cat_de": "1", "profanity_cat_en": "1", "profanity_cat_discrimination": "1",
        "custom_terms": "Schnuffelwort\nschnuffelwort\nBlödbatz", "removed_terms": "idiot",
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "config.json"
        self.patches = [
            patch.object(ws, "CONFIG_FILE", str(self.config_path)),
            patch.object(ws, "auth", auth_with(ADMIN)),
            patch.object(ws, "log_audit", lambda *a, **k: None),
            patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def saved(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))["pulse_automod"]

    def test_route_is_admin_only(self):
        with patch.object(ws, "auth", auth_with(TEAMLER)):
            self.assertEqual(self.client.post("/admin/security/profanity", data=self.PROFANITY_FORM).status_code, 403)
        self.assertFalse(self.config_path.exists())

    def test_page_shows_the_profanity_section_and_the_fixed_escalation(self):
        page = self.client.get("/admin/security")
        self.assertEqual(page.status_code, 200)
        for text in ("Schimpfwortfilter", "60 Minuten", "Verwarnungszeitfenster", "Eigene Wörter", "Standardlisten ansehen",
                     'name="profanity_cat_de"', "3. Verstoß"):
            self.assertIn(text, page.text)

    def test_enabling_never_changes_global_enable_or_mode(self):
        self.client.post("/admin/security/automod", data={**AUTOMOD_FORM, "automod_enabled": "", "mode": "observe"})
        before = self.saved()
        self.assertFalse(before["enabled"])
        response = self.client.post("/admin/security/profanity", data=self.PROFANITY_FORM, follow_redirects=False)
        self.assertIn("t=ok", response.headers["location"])
        after = self.saved()
        self.assertIn("profanity", after["rules"])
        self.assertFalse(after["enabled"])
        self.assertEqual(after["mode"], "observe")
        self.assertEqual(after["blocked_terms"], before["blocked_terms"])
        self.assertEqual(after["profanity"]["custom_terms"], ["schnuffelwort", "blödbatz"])
        self.assertEqual(after["profanity"]["removed_terms"], ["idiot"])
        self.assertFalse(after["profanity"]["categories"]["gaming"])

    def test_fresh_install_keeps_automod_off_and_defaults_are_not_copied_into_the_config(self):
        self.client.post("/admin/security/profanity", data=self.PROFANITY_FORM)
        cfg = self.saved()
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["mode"], "enforce")
        self.assertNotIn("arschloch", json.dumps(cfg).casefold())  # Standardliste bleibt im Code, nie in der Konfiguration

    def test_disabling_removes_only_the_rule_and_keeps_the_word_lists(self):
        self.client.post("/admin/security/profanity", data=self.PROFANITY_FORM)
        self.client.post("/admin/security/profanity", data={"custom_terms": "Schnuffelwort", "profanity_cat_de": "1"})
        cfg = self.saved()
        self.assertNotIn("profanity", cfg["rules"])
        self.assertEqual(cfg["profanity"]["custom_terms"], ["schnuffelwort"])

    def test_main_automod_form_does_not_reset_the_profanity_settings(self):
        self.client.post("/admin/security/profanity", data=self.PROFANITY_FORM)
        self.client.post("/admin/security/automod", data={**AUTOMOD_FORM, "automod_rules": ["keywords", "profanity"]})
        cfg = self.saved()
        self.assertEqual(cfg["profanity"]["custom_terms"], ["schnuffelwort", "blödbatz"])
        self.assertIn("profanity", cfg["rules"])
        self.assertEqual(cfg, __import__("pulse_moderation").normalize_automod(cfg))

    def test_legacy_compatibility_form_keeps_the_profanity_rule_and_words(self):
        self.client.post("/admin/security/profanity", data=self.PROFANITY_FORM)
        saved = self.saved()
        legacy = {"automod_enabled": "on", "automod_rules": ["spam"], "mention_limit": "5",
                  "antinuke_enabled": "on", "antinuke_events": ["ban"], "action_limit": "3", "window_seconds": "10"}
        with patch.object(ws, "auth", auth_with(ADMIN, config={"pulse_automod": saved})):
            self.client.post("/security", data=legacy, follow_redirects=False)
        cfg = self.saved()
        self.assertIn("profanity", cfg["rules"])
        self.assertEqual(cfg["profanity"], saved["profanity"])

    def test_invalid_input_is_rejected_without_saving(self):
        response = self.client.post("/admin/security/profanity", data={**self.PROFANITY_FORM, "custom_terms": "***\nok"},
                                    follow_redirects=False)
        self.assertNotIn("t=ok", response.headers["location"])
        self.assertFalse(self.config_path.exists())
        empty = self.client.post("/admin/security/profanity", data={"profanity_enabled": "1"}, follow_redirects=False)
        self.assertNotIn("t=ok", empty.headers["location"])
        self.assertFalse(self.config_path.exists())

    def test_tester_reports_profanity_without_echoing_the_stored_word(self):
        self.client.post("/admin/security/automod", data={**AUTOMOD_FORM, "automod_rules": ["profanity"], "mode": "enforce"})
        result = self.client.post("/admin/security/test", data={"sample": "du A r s c h l o c h"})
        self.assertIn("Schimpfwort / Beleidigung", result.text)
        clean = self.client.post("/admin/security/test", data={"sample": "Guten Morgen zusammen"})
        self.assertIn("Keine Regel würde auslösen", clean.text)


if __name__ == "__main__":
    unittest.main()
