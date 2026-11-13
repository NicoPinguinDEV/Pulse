"""Audit #14/#16: unified case evidence and the explainable repeat-offence hint."""
import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_case_evidence as ev
import pulse_case_repeat as repeat
import pulse_db
import pulse_mod_cases as mc
from tests import test_case_registry as base

ADMIN, LEAD, MOD, NONE, WARN = base.ADMIN, base.LEAD, base.MOD, base.NONE, base.WARN


class Fixture(unittest.TestCase):
    setUp = base.HistoryTests.setUp
    tearDown = base.HistoryTests.tearDown
    seed = base.HistoryTests.seed

    def count(self, where="1=1"):
        with pulse_db.connect() as db:
            ev._table(db)
            return db.execute(f"SELECT COUNT(*) FROM case_evidence WHERE {where}").fetchone()[0]


class EvidenceTests(Fixture):
    def add(self, ref, perms=MOD, guild=1, kind="link", value="https://example.org/clip", note=""):
        return ev.add_evidence(ref, perms, guild_id=guild, kind=kind, value=value, note=note, actor_id="7", actor_name="Mod")

    def test_link_validation_rejects_unsafe_values_without_writing(self):
        self.seed()
        ref = "BM:" + self.bm["id"]
        for bad in ("javascript:alert(1)", "ftp://x.y/z", "https://user:pw@example.org/", "https://exa mple.org",
                    "not a link", "", "https://" + "a" * 600 + ".de"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                self.add(ref, value=bad)
        with self.assertRaises(ValueError):
            self.add(ref, kind="text", value="ab")
        with self.assertRaises(ValueError):
            self.add(ref, kind="file", value="x")
        self.assertEqual(self.count(), 0)

    def test_add_list_and_legacy_items_for_every_case_file(self):
        self.seed()
        item = self.add("MOD-" + self.bm["id"][4:], note="Clip")  # bare reference form
        self.assertEqual(item["case_ref"], "BM:" + self.bm["id"])
        data = ev.evidence_for("BM:" + self.bm["id"], MOD, guild_id=1)
        self.assertEqual([m["value"] for m in data["managed"]], ["https://example.org/clip"])
        self.assertEqual(data["active_count"], 1)
        tf = self.add("PX-2026-0001", perms=LEAD, kind="text", value="Chatprotokoll liegt im Ticket")
        self.assertEqual(tf["case_ref"], "TF:PX-2026-0001")
        self.assertEqual(ev.evidence_for("TW:warn_aaaaaa", WARN, guild_id=1)["managed"], [])

    def test_legacy_proof_of_citizen_cases_and_team_warnings_is_shown_read_only(self):
        self.seed()
        person = SimpleNamespace(id=50, display_name="P")
        case = mc.create_case(1, person, "warn", "Grund fuer Test", "https://example.org/proof", None, SimpleNamespace(id=7, display_name="M"))
        legacy = ev.evidence_for("BM:" + case["id"], MOD, guild_id=1)["legacy"]
        self.assertEqual((legacy[0]["kind"], legacy[0]["value"]), ("link", "https://example.org/proof"))
        self.team["42"]["warns_list"][0]["proof"] = "Screenshot im Archiv"
        legacy = ev.evidence_for("TW:warn_aaaaaa", WARN, guild_id=1)["legacy"]
        self.assertEqual((legacy[0]["kind"], legacy[0]["value"]), ("text", "Screenshot im Archiv"))

    def test_team_case_attachments_appear_with_download_link(self):
        self.seed()
        import pulse_ultimate
        with pulse_ultimate.cx() as c:
            c.execute("INSERT INTO ultimate_attachments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      ("att1", "case", "case_1", "beweis.png", "stored", "image/png", 10, "", "9", "Leitung",
                       "2026-01-03T11:00:00+00:00"))
        legacy = ev.evidence_for("TF:PX-2026-0001", LEAD, guild_id=1)["legacy"]
        self.assertEqual((legacy[0]["kind"], legacy[0]["value"], legacy[0]["url"]),
                         ("file", "beweis.png", "/ultimate/cases/case_1/attachments/att1"))

    def test_rights_and_guild_isolation_fail_closed_and_write_nothing(self):
        self.seed()
        ref = "BM:" + self.bm["id"]
        for perms in (NONE, WARN, LEAD, None, {}):
            with self.subTest(perms=perms), self.assertRaises(PermissionError):
                self.add(ref, perms=perms)
            self.assertIsNone(ev.evidence_for(ref, perms, guild_id=1))
        with self.assertRaises(PermissionError):
            self.add(ref, guild=2)  # case belongs to guild 1
        with self.assertRaises(PermissionError):
            self.add("BM:MOD-FFFFFF")
        with self.assertRaises(PermissionError):
            self.add("nonsense")
        self.assertEqual(self.count(), 0)

    def test_limit_of_active_items_per_case(self):
        self.seed()
        ref = "BM:" + self.bm["id"]
        for number in range(ev.MAX_ACTIVE):
            self.add(ref, kind="text", value=f"Notiz {number:02d}")
        with self.assertRaises(ValueError):
            self.add(ref, kind="text", value="eine zu viel")
        self.assertEqual(self.count(), ev.MAX_ACTIVE)

    def test_removal_is_soft_needs_reason_right_and_guild(self):
        self.seed()
        item = self.add("BM:" + self.bm["id"])
        kwargs = dict(guild_id=1, actor_id="8", actor_name="Zweite Person")
        with self.assertRaises(ValueError):
            ev.remove_evidence(item["id"], MOD, reason="x", **kwargs)
        for perms, guild in ((NONE, 1), (WARN, 1), (MOD, 2)):
            with self.assertRaises(PermissionError):
                ev.remove_evidence(item["id"], perms, reason="falscher Link", **{**kwargs, "guild_id": guild})
        self.assertEqual(self.count("removed_at IS NULL"), 1)
        ev.remove_evidence(item["id"], MOD, reason="falscher Link", **kwargs)
        row = ev.evidence_for("BM:" + self.bm["id"], MOD, guild_id=1)["managed"][0]
        self.assertTrue(row["removed"])
        self.assertEqual((row["removed_by_name"], row["remove_reason"], row["value"]), ("Zweite Person", "falscher Link", item["value"]))
        self.assertEqual(ev.evidence_for("BM:" + self.bm["id"], MOD, guild_id=1)["active_count"], 0)
        with self.assertRaises(ValueError):
            ev.remove_evidence(item["id"], MOD, reason="nochmal", **kwargs)

    def test_evidence_table_is_part_of_the_backup_roundtrip(self):
        self.seed()
        item = self.add("BM:" + self.bm["id"])
        state = pulse_db.export_state()
        self.assertEqual([r["id"] for r in state["case_evidence"]], [item["id"]])
        pulse_db.import_state(state)
        self.assertEqual(self.count(), 1)


class RepeatHintTests(Fixture):
    JAN = datetime(2026, 1, 20, tzinfo=timezone.utc)

    def test_levels_are_derived_from_counted_cases_only(self):
        self.assertEqual([repeat.level_for(n) for n in (0, 1, 2, 3, 9)], ["none", "none", "repeat", "multiple", "multiple"])

    def test_counts_cases_across_case_files_and_skips_revoked_ones(self):
        self.seed()
        hint = repeat.repeat_hint(42, ADMIN, guild_id=1, now=self.JAN)
        self.assertIn("TF:PX-2026-0001", hint["refs"])
        self.assertIn("TW:warn_aaaaaa", hint["refs"])
        self.assertNotIn("TW:warn_bbbbbb", hint["refs"])  # revoked
        self.assertGreaterEqual(hint["count"], 2)
        self.assertEqual(hint["level"], repeat.level_for(hint["count"]))
        self.assertEqual(hint["limits"], [])
        self.assertIn("keine automatische Maßnahme", hint["rule"])

    def test_window_and_visibility_limit_the_count_and_are_disclosed(self):
        self.seed()
        outside = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(repeat.repeat_hint(42, ADMIN, guild_id=1, now=outside)["count"], 0)
        hint = repeat.repeat_hint(42, WARN, guild_id=1, now=self.JAN)
        self.assertEqual(hint["refs"], ["TW:warn_aaaaaa"])
        self.assertEqual(hint["level"], "none")
        self.assertTrue(hint["limits"])
        none = repeat.repeat_hint(42, NONE, guild_id=1, now=self.JAN)
        self.assertEqual((none["count"], none["level"]), (0, "none"))

    def test_unreadable_source_is_reported_not_hidden(self):
        self.seed()
        with patch("pulse_case_registry.load_tf", side_effect=RuntimeError("locked")):
            hint = repeat.repeat_hint(42, ADMIN, guild_id=1, now=self.JAN)
        self.assertTrue(any("Team-/Spielerfall" in text for text in hint["limits"]))
        self.assertNotIn("TF:PX-2026-0001", hint["refs"])

    def test_bad_input_is_harmless(self):
        self.seed()
        for user in (None, "", "abc", "42; DROP TABLE"):
            self.assertEqual(repeat.repeat_hint(user, ADMIN, guild_id=1, now=self.JAN)["count"], 0)
        self.assertEqual(repeat.repeat_hint(42, ADMIN, guild_id=1, now=self.JAN, window_days="x")["window_days"], 90)

    def test_failed_and_new_citizen_cases_count_by_status_and_time(self):
        self.seed()
        person = SimpleNamespace(id=60, display_name="P")
        actor = SimpleNamespace(id=7, display_name="M")
        for _ in range(3):
            mc.create_case(1, person, "warn", "Wiederholt regelwidrig", "", None, actor)
        self.assertEqual(repeat.repeat_hint(60, MOD, guild_id=1)["level"], "multiple")
        first = mc.list_cases(target_id="60")[0]
        mc.mark(first["id"], status="failed")
        self.assertEqual(repeat.repeat_hint(60, MOD, guild_id=1)["level"], "repeat")
        self.assertEqual(repeat.repeat_hint(60, MOD, guild_id=2)["count"], 0)  # other guild


class EvidenceRouteTests(Fixture):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        import webserver
        self.ws = webserver
        self.client = TestClient(webserver.app, raise_server_exceptions=False)

    def ctx(self, perms, guild=1):
        return SimpleNamespace(user={"id": "7", "global_name": "Mod"}, perms=perms, guild=SimpleNamespace(id=guild), config={}, msg="")

    def call(self, method, path, perms, data=None, guild=1):
        with patch.object(self.ws, "auth", return_value=self.ctx(perms, guild)), patch.object(self.ws, "log_audit") as audit,                 patch("pulse_pro.render_pro_page", side_effect=lambda ws, title, ctx, key, body: body):
            response = self.client.request(method, path, data=data, follow_redirects=False)
        return response, audit

    def test_page_shows_legacy_and_managed_evidence_and_hides_unauthorised_cases(self):
        self.seed()
        ref = "BM:" + self.bm["id"]
        ev.add_evidence(ref, MOD, guild_id=1, kind="text", value="Zeuge sah den Vorfall", note="", actor_id="7", actor_name="Mod")
        page, _ = self.call("GET", f"/mod-cases/beweise?ref={ref}", MOD)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Zeuge sah den Vorfall", page.text)
        hidden, _ = self.call("GET", f"/mod-cases/beweise?ref={ref}", WARN)
        self.assertNotIn("Zeuge sah den Vorfall", hidden.text)
        self.assertIn("Keine sichtbare Akte", hidden.text)

    def test_add_and_remove_are_authorised_audited_and_validated(self):
        self.seed()
        ref = "BM:" + self.bm["id"]
        denied, audit = self.call("POST", "/mod-cases/beweise/add", WARN,
                                  {"ref": ref, "kind": "link", "value": "https://example.org/a"})
        self.assertEqual(denied.status_code, 403)
        audit.assert_not_called()
        invalid, _ = self.call("POST", "/mod-cases/beweise/add", MOD, {"ref": ref, "kind": "link", "value": "javascript:x"})
        self.assertEqual(invalid.status_code, 303)
        self.assertEqual(self.count(), 0)
        added, audit = self.call("POST", "/mod-cases/beweise/add", MOD, {"ref": ref, "kind": "link", "value": "https://example.org/a"})
        self.assertEqual(added.status_code, 303)
        audit.assert_called_once()
        evidence_id = ev.evidence_for(ref, MOD, guild_id=1)["managed"][0]["id"]
        foreign, _ = self.call("POST", "/mod-cases/beweise/remove", MOD, {"ref": ref, "evidence_id": evidence_id, "reason": "falsch"}, guild=2)
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(self.count("removed_at IS NULL"), 1)
        removed, audit = self.call("POST", "/mod-cases/beweise/remove", MOD, {"ref": ref, "evidence_id": evidence_id, "reason": "falsch"})
        self.assertEqual(removed.status_code, 303)
        audit.assert_called_once()
        self.assertEqual(self.count("removed_at IS NULL"), 0)
        self.assertEqual(self.count(), 1)

    def test_history_page_shows_the_repeat_hint(self):
        self.seed()
        page, _ = self.call("GET", "/mod-cases/verlauf?user=42", ADMIN)
        self.assertEqual(page.status_code, 200)
        self.assertIn("keine automatische Maßnahme", page.text)


class DiscordCommandTests(Fixture):
    def run_cmd(self, command, perms, *args):
        from cogs.mod_cases import ModCases
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=7, display_name="Mod"),
                                      response=SimpleNamespace(send_message=AsyncMock()))
        with patch("cogs.mod_cases.pulse_perms", return_value=perms), patch("webserver.log_audit") as audit:
            asyncio.run(getattr(ModCases, command).callback(ModCases.__new__(ModCases), interaction, *args))
        call = interaction.response.send_message.await_args
        return call.args[0], call.kwargs, audit

    def test_add_and_list_commands_respect_rights_and_are_private(self):
        self.seed()
        ref = "BM:" + self.bm["id"]
        choice = SimpleNamespace(value="link")
        text, kwargs, audit = self.run_cmd("beweis_hinzufuegen", WARN, ref, choice, "https://example.org/x")
        self.assertIn("Keine sichtbare Akte", text)
        audit.assert_not_called()
        self.assertEqual(self.count(), 0)
        text, kwargs, audit = self.run_cmd("beweis_hinzufuegen", MOD, ref, choice, "javascript:x")
        self.assertIn("❌", text)
        text, kwargs, audit = self.run_cmd("beweis_hinzufuegen", MOD, ref, choice, "https://example.org/x")
        self.assertIn("gespeichert", text)
        self.assertTrue(kwargs["ephemeral"])
        audit.assert_called_once()
        text, kwargs, _ = self.run_cmd("beweise", MOD, ref)
        self.assertIn("https://example.org/x", text)
        text, kwargs, _ = self.run_cmd("beweise", NONE, ref)
        self.assertIn("Keine sichtbare Akte", text)

    def test_fall_and_verlauf_show_the_repeat_hint(self):
        self.seed()
        text, kwargs, _ = self.run_cmd("fall", ADMIN, "TF:PX-2026-0001")
        self.assertIn("keine automatische Maßnahme", text)
        text, kwargs, _ = self.run_cmd("verlauf", ADMIN, SimpleNamespace(id=42))
        self.assertIn("keine automatische Maßnahme", text)


if __name__ == "__main__":
    unittest.main()
