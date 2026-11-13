"""Audit #11/#12: typed case references and one event history across all moderation case files."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_case_registry as reg
import pulse_db
import pulse_mod_cases as mc
import pulse_ultimate

MOD = {"can_moderate_kicks": True}
WARN = {"can_warn": True}
LEAD = {"can_promote": True}
ADMIN = {"is_admin": True}
NONE = {"can_view_dashboard": True}


class RefTests(unittest.TestCase):
    def test_parse_typed_and_bare_references(self):
        self.assertEqual(reg.parse_ref("BM:mod-1a2b3c"), ("BM", "MOD-1A2B3C"))
        self.assertEqual(reg.parse_ref("MOD-1A2B3C"), ("BM", "MOD-1A2B3C"))
        self.assertEqual(reg.parse_ref("px-2026-0007"), ("TF", "PX-2026-0007"))
        self.assertEqual(reg.parse_ref(" TF:PX-2026-0007 "), ("TF", "PX-2026-0007"))
        self.assertEqual(reg.parse_ref("WARN_AB12CD"), ("TW", "warn_ab12cd"))
        self.assertEqual(reg.parse_ref("TW:WARN_AB12CD"), ("TW", "warn_ab12cd"))
        self.assertEqual(reg.parse_ref("ki:0123456789"), ("KI", "0123456789"))
        self.assertEqual(reg.parse_ref("`BM:MOD-1A2B3C`"), ("BM", "MOD-1A2B3C"))

    def test_invalid_and_ambiguous_references_are_rejected(self):
        for value in ("", None, "XX:1", "BM:", "BM:../../etc", "BM:a b", "x" * 80, "KI:' OR 1=1",
                      "0123456789",  # bare AI ID: format is not unique → must be written KI:…
                      "MOD-1A2B3", "PX-26-1", "warn_xyz"):
            with self.subTest(value=value):
                self.assertIsNone(reg.parse_ref(value))
        with self.assertRaises(ValueError):
            reg.make_ref("ZZ", "1")

    def test_same_native_id_in_two_case_files_gives_two_distinct_references(self):
        self.assertNotEqual(reg.make_ref("KI", "abc123"), reg.make_ref("TW", "abc123"))

    def test_rights_match_the_existing_case_file_views_and_fail_closed(self):
        self.assertEqual(reg.allowed_kinds(None), set())
        self.assertEqual(reg.allowed_kinds("is_admin"), set())
        self.assertEqual(reg.allowed_kinds(NONE), set())
        self.assertEqual(reg.allowed_kinds(MOD), {"BM", "KI"})
        self.assertEqual(reg.allowed_kinds({"can_moderate_bans": True}), {"BM", "KI"})
        self.assertEqual(reg.allowed_kinds(WARN), {"TW"})
        self.assertEqual(reg.allowed_kinds(LEAD), {"TF"})
        self.assertEqual(reg.allowed_kinds(ADMIN), {"BM", "TF", "TW", "KI"})
        # Only real booleans grant access; strings or other truthy values do not.
        self.assertEqual(reg.allowed_kinds({"is_admin": "yes", "can_warn": 1}), set())

    def test_timestamps_are_normalized_to_utc(self):
        self.assertEqual(reg._iso("2026-01-02T11:00:00"), "2026-01-02T11:00:00+00:00")  # naive = UTC
        self.assertEqual(reg._iso("2026-01-02T11:00:00Z"), "2026-01-02T11:00:00+00:00")
        self.assertEqual(reg._iso("2026-07-15T12:00:00+02:00"), "2026-07-15T10:00:00+00:00")
        self.assertEqual(reg._iso("02.01.2026 12:00"), "2026-01-02T11:00:00+00:00")  # Berlin winter time
        self.assertEqual(reg._iso("15.07.2026 12:00"), "2026-07-15T10:00:00+00:00")  # Berlin summer time
        for broken in ("", None, "N/A", "gestern", "31.02.2026 10:00"):
            self.assertEqual(reg._iso(broken), "")


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
            patch.object(pulse_ultimate, "_INIT", False),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        pulse_ultimate.setup()
        self.team = {}
        self.ai = {}
        self.team_patch = patch.object(reg, "_team_data", side_effect=lambda: self.team)
        self.ai_patch = patch.object(reg, "_ai_cases", side_effect=lambda: self.ai)
        self.team_patch.start()
        self.ai_patch.start()

    def tearDown(self):
        self.ai_patch.stop()
        self.team_patch.stop()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def seed(self):
        person = SimpleNamespace(id=42, display_name="Muster")
        moderator = SimpleNamespace(id=7, display_name="Mod")
        self.bm = mc.create_case(1, person, "warn", "Beleidigung im Chat", "", None, moderator)
        mc.mark(self.bm["id"], status="expired", closed_at="2026-01-06T09:00:00+00:00", closed_by="Pulse (Ablauf)")
        mc.create_case(2, person, "warn", "Anderer Server", "", None, moderator)  # foreign guild
        mc.create_case(1, SimpleNamespace(id=43, display_name="Andere"), "warn", "Andere Person", "", None, moderator)
        with pulse_ultimate.cx() as c:
            c.execute(
                """INSERT INTO ultimate_cases(id,case_no,title,category,priority,status,subject_type,subject_id,
                subject_name,description,assignee_id,assignee_name,created_by_id,created_by_name,created_at,updated_at,closed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("case_1", "PX-2026-0001", "Fehlverhalten im Dienst", "team", "high", "open", "team", "42",
                 "Muster", "Interna", None, "", "9", "Leitung", "2026-01-03T10:00:00+00:00",
                 "2026-01-03T10:00:00+00:00", None),
            )
            c.executemany(
                "INSERT INTO ultimate_case_events VALUES(?,?,?,?,?,?,?)",
                [("ce1", "case_1", "9", "Leitung", "created", "Interna aus der Beschreibung", "2026-01-03T10:00:00+00:00"),
                 ("ce2", "case_1", "9", "Leitung", "updated", "Interne Notiz", "2026-01-03T12:00:00+00:00")],
            )
        future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(timespec="seconds")
        self.team = {
            "42": {"warns_list": [
                {"id": "warn_aaaaaa", "reason": "Dienst nicht abgemeldet", "by": "Leitung",
                 "date": "02.01.2026 12:00", "expires_at": future, "active": True},
                {"id": "warn_bbbbbb", "reason": "Alt", "by": "Leitung", "date": "15.07.2025 12:00",
                 "revoked_at": "2025-08-01T00:00:00+00:00", "revoked_by": "Admin", "active": False},
                {"reason": "Legacy ohne ID"},
                "kaputter Eintrag",
            ]},
            "43": {"warns_list": [{"id": "warn_cccccc", "reason": "Fremd", "date": "01.01.2026 10:00"}]},
        }
        self.ai = {
            "0123456789": {"id": "0123456789", "guild_id": 1, "user_id": 42, "channel_name": "allgemein",
                           "content": "GEHEIMER NACHRICHTENTEXT", "severity": "medium", "reason": "Mögliche Beleidigung",
                           "status": "resolved", "decision": "Abgelehnt / keine Maßnahme", "reviewed_by": "Mod",
                           "created_at": "2026-01-04T08:00:00Z", "resolved_at": "2026-01-04T09:30:00+00:00"},
            "9999999999": {"id": "9999999999", "guild_id": 2, "user_id": 42, "status": "pending",
                           "created_at": "2026-01-05T08:00:00+00:00"},
            "broken": "kein Objekt",
        }

    def events(self, perms=ADMIN, **kwargs):
        return reg.person_history(42, perms, guild_id=1, **kwargs)

    def test_admin_sees_all_four_case_files_as_one_event_history(self):
        self.seed()
        history = self.events()
        self.assertEqual(history["kinds"], ["BM", "TF", "TW", "KI"])
        self.assertEqual(history["unavailable"], [])
        pairs = {(e["ref"], e["event"]) for e in history["entries"]}
        self.assertEqual(pairs, {
            (f"BM:{self.bm['id']}", "created"), (f"BM:{self.bm['id']}", "closed"),
            ("TF:PX-2026-0001", "created"), ("TF:PX-2026-0001", "updated"),
            ("TW:warn_aaaaaa", "created"), ("TW:warn_bbbbbb", "created"), ("TW:warn_bbbbbb", "revoked"),
            ("KI:0123456789", "created"), ("KI:0123456789", "decided"),
        })
        self.assertEqual(history["total"], 9)

    def test_events_are_in_chronological_order_across_sources_and_timezones(self):
        self.seed()
        times = [e["at"] for e in self.events()["entries"]]
        self.assertEqual(times, sorted(times, reverse=True))
        by_key = {(e["ref"], e["event"]): e for e in self.events()["entries"]}
        self.assertEqual(by_key[("TW:warn_aaaaaa", "created")]["at"], "2026-01-02T11:00:00+00:00")  # CET
        self.assertEqual(by_key[("TW:warn_bbbbbb", "created")]["at"], "2025-07-15T10:00:00+00:00")  # CEST
        self.assertEqual(by_key[("KI:0123456789", "created")]["at"], "2026-01-04T08:00:00+00:00")
        self.assertEqual(by_key[("TW:warn_bbbbbb", "revoked")]["event_actor"], "Admin")
        self.assertEqual(by_key[("TF:PX-2026-0001", "updated")]["url"], "/ultimate/cases/case_1")

    def test_events_without_a_readable_time_are_kept_at_the_end(self):
        self.seed()
        self.team["42"]["warns_list"].append({"id": "warn_dddddd", "reason": "Ohne Datum", "date": "N/A"})
        entries = self.events()["entries"]
        self.assertEqual((entries[-1]["ref"], entries[-1]["at"]), ("TW:warn_dddddd", ""))

    def test_each_case_file_is_only_shown_with_its_own_right(self):
        self.seed()
        for perms, expected in ((MOD, {"BM", "KI"}), (WARN, {"TW"}), (LEAD, {"TF"}), (NONE, set())):
            with self.subTest(perms=perms):
                self.assertEqual({e["kind"] for e in self.events(perms)["entries"]}, expected)

    def test_no_source_is_read_without_any_right(self):
        with patch.object(reg, "load_bm") as bm, patch.object(reg, "load_tf") as tf, \
             patch.object(reg, "load_tw") as tw, patch.object(reg, "load_ki") as ki:
            history = reg.person_history(42, NONE, guild_id=1)
        self.assertEqual(history["entries"], [])
        for loader in (bm, tf, tw, ki):
            loader.assert_not_called()

    def test_history_keeps_guild_and_person_isolation_and_hides_internal_text(self):
        self.seed()
        rendered = json.dumps(self.events(), ensure_ascii=False)
        for secret in ("Anderer Server", "Andere Person", "warn_cccccc", "9999999999", "GEHEIMER NACHRICHTENTEXT",
                       "Interna", "Interne Notiz", "Legacy ohne ID"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(reg.person_history("42; DROP", ADMIN)["entries"], [])
        self.assertEqual(reg.person_history("", ADMIN)["entries"], [])

    def test_history_is_read_only(self):
        self.seed()
        before = json.dumps(self.team, sort_keys=True)
        with patch.object(mc, "mark") as mark, patch("webserver.save_json") as save:
            self.events()
        self.assertEqual(json.dumps(self.team, sort_keys=True), before)  # legacy warn not normalized/saved
        mark.assert_not_called()
        save.assert_not_called()

    def test_unreadable_sources_are_reported_and_do_not_hide_the_others(self):
        self.seed()
        broken = reg.SourceUnavailable("kaputt")
        with patch.object(reg, "load_tf", side_effect=RuntimeError("database is locked")), \
             patch.object(reg, "_ai_cases", side_effect=broken):
            history = self.events()
        self.assertEqual(history["unavailable"], ["TF", "KI"])
        self.assertEqual({e["kind"] for e in history["entries"]}, {"BM", "TW"})

    def test_damaged_files_are_reported_as_unavailable(self):
        bad = Path(self.tmp.name) / "ai.json"
        bad.write_text("{kaputt", encoding="utf-8")
        self.ai_patch.stop()
        try:
            with patch("cogs.ai_moderation.CASES_PATH", bad):
                with self.assertRaises(reg.SourceUnavailable):
                    reg._ai_cases()
                self.assertIsNone(reg.resolve("KI:0123456789", ADMIN, guild_id=1))
            with patch("cogs.ai_moderation.CASES_PATH", Path(self.tmp.name) / "fehlt.json"):
                self.assertEqual(reg._ai_cases(), {})  # nothing stored yet is not an error
        finally:
            self.ai_patch.start()
        self.team_patch.stop()
        try:
            with patch("webserver.load_json", return_value=["kein", "dict"]):
                self.assertEqual(reg.person_history(42, WARN)["unavailable"], ["TW"])
        finally:
            self.team_patch.start()

    def test_malformed_team_entries_are_skipped(self):
        self.team = {"42": {"warns_list": "kein Array"}, "44": "kein Objekt"}
        self.assertEqual(self.events(WARN)["entries"], [])
        self.assertIsNone(reg.resolve("warn_aaaaaa", WARN))

    def test_limit_bounds_the_output_but_total_counts_all(self):
        self.seed()
        history = self.events(limit=3)
        self.assertEqual(len(history["entries"]), 3)
        self.assertEqual(history["total"], 9)

    def test_resolve_finds_each_kind_only_with_rights_and_guild(self):
        self.seed()
        self.assertEqual(reg.resolve(self.bm["id"], MOD, guild_id=1)["person_id"], "42")
        self.assertEqual(reg.resolve(f"BM:{self.bm['id'].lower()}", MOD, guild_id=1)["native_id"], self.bm["id"])
        self.assertIsNone(reg.resolve(self.bm["id"], MOD, guild_id=2))
        self.assertIsNone(reg.resolve(self.bm["id"], WARN, guild_id=1))
        self.assertEqual(reg.resolve("PX-2026-0001", LEAD)["title"], "Fehlverhalten im Dienst")
        self.assertIsNone(reg.resolve("PX-2026-0001", MOD))
        self.assertEqual(reg.resolve("warn_aaaaaa", WARN)["person_id"], "42")
        self.assertIsNone(reg.resolve("TW:warn_zzzzzz", WARN))
        self.assertEqual(reg.resolve("KI:0123456789", MOD, guild_id=1)["status"], "resolved")
        self.assertIsNone(reg.resolve("KI:9999999999", MOD, guild_id=1))
        self.assertIsNone(reg.resolve("KI:broken", MOD, guild_id=1))
        self.assertIsNone(reg.resolve("unsinn", ADMIN))

    def test_duplicate_legacy_warning_id_is_not_resolved_to_one_person(self):
        self.seed()
        self.team["43"]["warns_list"].append({"id": "warn_aaaaaa", "reason": "Dublette", "date": "01.01.2026 10:00"})
        self.assertIsNone(reg.resolve("warn_aaaaaa", WARN))
        # The history is still unambiguous because it is keyed by the person, not by the ID.
        self.assertNotIn("Dublette", json.dumps(self.events(WARN), ensure_ascii=False))

    def test_resolve_fails_closed_when_a_source_raises(self):
        with patch.object(reg, "_resolve", side_effect=RuntimeError("db")):
            self.assertIsNone(reg.resolve("PX-2026-0001", ADMIN))

    def test_appeal_and_decision_appear_as_events_of_the_citizen_case(self):
        self.seed()
        mc.submit_appeal(1, 42, self.bm["id"], "Ich halte die Verwarnung für unbegründet, bitte prüfen.")
        self.assertEqual(reg.resolve(self.bm["id"], MOD, guild_id=1)["appeal_status"], "pending")
        mc.decide_appeal(1, self.bm["id"], "rejected", "Beleg eindeutig.", 8, "Leitung")
        events = {e["event"]: e for e in self.events(MOD)["entries"] if e["kind"] == "BM"}
        self.assertIn("appeal_submitted", events)
        self.assertEqual(events["appeal_rejected"]["event_actor"], "Leitung")
        self.assertEqual(events["appeal_rejected"]["appeal_status"], "rejected")


class DashboardRouteTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import webserver
        self.webserver = webserver
        self.client = TestClient(webserver.app)

    def ctx(self, perms):
        return SimpleNamespace(user={"id": "7", "global_name": "Mod"}, perms=perms, guild=SimpleNamespace(id=1),
                               config={}, msg="")

    def render(self):
        return patch("pulse_pro.render_pro_page", side_effect=lambda ws, title, ctx, key, body: body)

    def test_viewer_without_any_case_right_is_rejected_before_reading_or_logging(self):
        with patch.object(self.webserver, "auth", return_value=self.ctx(NONE)), \
             patch.object(reg, "person_history") as history, patch.object(reg, "resolve") as resolve, \
             patch.object(self.webserver, "log_audit") as audit:
            response = self.client.get("/mod-cases/verlauf?user=42&ref=PX-2026-0001")
        self.assertEqual(response.status_code, 403)
        history.assert_not_called()
        resolve.assert_not_called()
        audit.assert_not_called()

    def test_history_page_passes_viewer_rights_escapes_and_is_audited(self):
        case = reg._case("TW", "warn_aaaaaa", title="Team-Verwarnung", status="active", detail="x")
        entries = {"entries": [reg._event(case, "created", "2026-01-02T11:00:00+00:00", detail="<script>x</script>")],
                   "kinds": ["TW"], "unavailable": ["TF"], "total": 1}
        with patch.object(self.webserver, "auth", return_value=self.ctx(WARN)), \
             patch.object(reg, "person_history", return_value=entries) as history, \
             patch.object(self.webserver, "log_audit") as audit, self.render():
            response = self.client.get("/mod-cases/verlauf?user=42abc")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(history.call_args.args[:2], ("42", WARN))
        self.assertEqual(history.call_args.kwargs["guild_id"], 1)
        self.assertIn("TW:warn_aaaaaa", response.text)
        self.assertIn("Fall angelegt", response.text)
        self.assertNotIn("<script>x</script>", response.text)
        self.assertIn("konnte gerade nicht gelesen werden", response.text)
        audit.assert_called_once()

    def test_unknown_or_forbidden_reference_gives_the_same_answer(self):
        # No ID enumeration: a lead without moderation rights cannot tell whether a BM case exists.
        with patch.object(self.webserver, "auth", return_value=self.ctx(LEAD)), \
             patch.object(reg, "_resolve") as lookup, patch.object(reg, "person_history") as history, \
             patch.object(self.webserver, "log_audit"), self.render():
            response = self.client.get("/mod-cases/verlauf?ref=BM:MOD-000000")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Keine sichtbare Akte", response.text)
        lookup.assert_not_called()
        history.assert_not_called()

    def test_found_reference_opens_the_history_of_that_person(self):
        case = reg._case("BM", "MOD-1A2B3C", title="Verwarnung", status="active", person_id="42")
        with patch.object(self.webserver, "auth", return_value=self.ctx(MOD)), \
             patch.object(reg, "resolve", return_value=case), \
             patch.object(reg, "person_history", return_value={"entries": [], "kinds": ["BM", "KI"],
                                                               "unavailable": [], "total": 0}) as history, \
             patch.object(self.webserver, "log_audit"), self.render():
            response = self.client.get("/mod-cases/verlauf?ref=MOD-1A2B3C")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Gefundene Akte", response.text)
        self.assertEqual(history.call_args.args[:2], ("42", MOD))

    def test_mod_cases_page_links_to_the_history(self):
        with patch.object(self.webserver, "auth", return_value=self.ctx(MOD)), \
             patch.object(mc, "list_cases", return_value=[]), patch.object(mc, "list_appeals", return_value=[]), \
             self.render():
            response = self.client.get("/mod-cases")
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/mod-cases/verlauf"', response.text)


class DiscordCommandTests(unittest.TestCase):
    def interaction(self):
        return SimpleNamespace(
            guild=SimpleNamespace(id=1), user=SimpleNamespace(id=7, display_name="Mod"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    def run_cmd(self, command, *args):
        from cogs.mod_cases import ModCases
        interaction = self.interaction()
        asyncio.run(getattr(ModCases, command).callback(ModCases.__new__(ModCases), interaction, *args))
        return interaction.response.send_message.await_args

    def test_history_command_requires_a_case_right(self):
        with patch("cogs.mod_cases.pulse_perms", return_value=NONE), \
             patch.object(reg, "person_history") as history, patch("webserver.log_audit") as audit:
            sent = self.run_cmd("verlauf", SimpleNamespace(id=42))
        history.assert_not_called()
        audit.assert_not_called()
        self.assertTrue(sent.kwargs["ephemeral"])

    def test_history_command_is_private_scoped_and_audited(self):
        case = reg._case("BM", "MOD-1A2B3C", title="Verwarnung", status="active", detail="Grund")
        event = reg._event(case, "created", "2026-01-02T11:00:00+00:00", actor="Mod", detail="Grund")
        with patch("cogs.mod_cases.pulse_perms", return_value=MOD), \
             patch.object(reg, "person_history", return_value={"entries": [event], "kinds": ["BM", "KI"],
                                                               "unavailable": ["KI"], "total": 1}) as history, \
             patch("webserver.log_audit") as audit:
            sent = self.run_cmd("verlauf", SimpleNamespace(id=42))
        history.assert_any_call(42, MOD, guild_id=1, limit=12)  # the repeat hint (#16) reads the same source once more
        self.assertTrue(sent.kwargs["ephemeral"])
        self.assertIn("Fall angelegt", sent.args[0])
        self.assertIn("BM:MOD-1A2B3C", sent.args[0])
        self.assertIn("KI-Moderation konnte gerade nicht gelesen werden", sent.args[0])
        self.assertLessEqual(len(sent.args[0]), 2000)
        audit.assert_called_once()

    def test_case_command_requires_a_right_and_resolves_with_viewer_rights(self):
        with patch("cogs.mod_cases.pulse_perms", return_value=NONE), patch.object(reg, "resolve") as resolve:
            sent = self.run_cmd("fall", "TF:PX-2026-0001")
        resolve.assert_not_called()
        self.assertIn("Kein Zugriff", sent.args[0])
        with patch("cogs.mod_cases.pulse_perms", return_value=MOD), \
             patch.object(reg, "resolve", return_value=None) as resolve:
            sent = self.run_cmd("fall", "TF:PX-2026-0001")
        resolve.assert_called_once_with("TF:PX-2026-0001", MOD, guild_id=1)
        self.assertIn("Keine sichtbare Akte", sent.args[0])
        self.assertTrue(sent.kwargs["ephemeral"])

    def test_existing_akte_command_is_unchanged_for_moderators_and_denied_otherwise(self):
        cases = [{"id": "MOD-1A2B3C", "action": "warn", "created_at": "2026-01-02T11:00:00+00:00",
                  "status": "active", "reason": "Grund"}]
        with patch("cogs.mod_cases.pulse_perms", return_value=MOD), \
             patch.object(mc, "list_cases", return_value=cases) as listing:
            sent = self.run_cmd("akte", SimpleNamespace(id=42, __str__=lambda self: "Muster"))
        listing.assert_called_once_with(42, limit=15)
        self.assertIn("`MOD-1A2B3C`", sent.args[0])
        self.assertTrue(sent.kwargs["ephemeral"])
        with patch("cogs.mod_cases.pulse_perms", return_value=WARN), patch.object(mc, "list_cases") as listing:
            sent = self.run_cmd("akte", SimpleNamespace(id=42))
        listing.assert_not_called()
        self.assertIn("Kein Zugriff", sent.args[0])


if __name__ == "__main__":
    unittest.main()
