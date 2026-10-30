"""BOT-05: one domain function per warning action, used by the dashboard and the slash commands."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pulse_db
import webserver as ws
from tests.fakes import FakeGuild, FakeMember, FakeRole

TEAM, LEAD, LOCK = FakeRole(10, "Team", 5), FakeRole(11, "Leitung", 8), FakeRole(77, "Uprank-Sperre", 2)


class WarningDomainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
            patch.object(ws, "DATA_FILE", str(root / "team_data.json")),
            patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
            patch.object(ws, "enforce_team_warn_protection", AsyncMock(return_value=(True, ""))),
            patch.object(ws, "enforce_high_impact_warning_approval", AsyncMock(return_value=(True, ""))),
            patch.object(ws, "sync_warn_roles", AsyncMock(return_value={"ok": True, "role": None})),
            patch.object(ws, "apply_warn_consequences", AsyncMock(return_value={"ok": True, "actions": []})),
            patch.object(ws, "send_team_update_embed", AsyncMock()),
            patch.object(ws, "send_dm_notification", AsyncMock()),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        self.target = FakeMember(2, "Max", roles=[TEAM])
        self.lead = FakeMember(3, "Lena", roles=[LEAD])
        self.guild = FakeGuild(members=[self.target, self.lead], roles=[TEAM, LEAD, LOCK])
        self.guild.get_role = lambda rid: {10: TEAM, 11: LEAD, 77: LOCK}.get(rid)
        self.guild.me.top_role = FakeRole(1, "Pulse", 20)  # bot role above the lock role
        self.config = {"team_role_ids": [10, 11]}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def issue(self, **kw):
        args = dict(actor_member=self.lead, actor_name="Lena", actor_id="3", member=self.target, reason="Zu spät zum Dienst",
                    duration_days=14, proof="https://beweis.example/1", config=self.config, source="Slash /warn")
        args.update(kw)
        return asyncio.run(ws.issue_team_warning(self.guild, **args))

    def stored(self):
        return json.loads(Path(ws.DATA_FILE).read_text(encoding="utf-8"))["2"]["warns_list"]

    def test_slash_and_dashboard_get_duration_proof_dm_and_team_update(self):
        result = self.issue()
        self.assertTrue(result["stored"])
        warn = self.stored()[0]
        self.assertEqual((warn["duration_days"], warn["proof"]), (14, "https://beweis.example/1"))
        ws.send_dm_notification.assert_awaited()   # before: /warn sent no DM to the person
        ws.send_team_update_embed.assert_awaited()

    def test_team_update_and_reply_show_level_and_validity(self):
        result = self.issue(duration_days=30)
        update = ws.send_team_update_embed.await_args_list[0]
        self.assertIn("Warn-Stufe: 1/5", update.kwargs["target"])
        self.assertIn(("Gültig bis", result["warn"]["expires_at"], True), update.kwargs["fields"])
        self.assertIn("(1/5)", result["message"])

    def test_refusals_store_nothing(self):
        self.assertIn("zwischen 1 und 365", self.issue(duration_days=0)["message"])
        self.assertIn("selbst", self.issue(member=self.lead, actor_member=self.lead)["message"])
        low = FakeMember(4, "Neu", roles=[TEAM])
        self.assertIn("höherem Teamrang", self.issue(actor_member=low, actor_name="Neu", actor_id="4", member=self.target)["message"])
        self.assertFalse(Path(ws.DATA_FILE).exists())

    def test_five_warnings_escalate_to_leadership(self):
        with patch.object(ws, "compute_perms", lambda g, uid, c: ({"can_promote": uid == 3}, None)):
            for _ in range(5):
                self.issue()
        self.assertEqual(len(self.stored()), 5)
        self.assertTrue(any(n["title"] == "🚨 5/5 Team-Warnungen" for n in pulse_db.notifications("3")))
        self.assertIn("maximale Anzahl", self.issue()["message"])

    def test_revoke_lifts_temporary_consequence_unless_another_warning_needs_it(self):
        self.issue()
        self.issue()
        warns = json.loads(Path(ws.DATA_FILE).read_text(encoding="utf-8"))
        warns["2"]["warns_list"][0]["consequences"] = {"temporary_roles": [{"role_id": 77}]}
        Path(ws.DATA_FILE).write_text(json.dumps(warns), encoding="utf-8")
        self.target.roles.append(LOCK)
        first, second = (w["id"] for w in self.stored())
        kept = asyncio.run(ws.revoke_team_warning(self.guild, member=self.target, warn_id=first, revoke_reason="Irrtum",
                                                  actor_name="Lena", actor_id="3", config=self.config,
                                                  actor_member=self.lead))
        self.assertTrue(kept["stored"])
        self.assertIn(LOCK, self.target.roles)  # the second warning is still active
        data = json.loads(Path(ws.DATA_FILE).read_text(encoding="utf-8"))
        data["2"]["warns_list"][1]["consequences"] = {"temporary_roles": [{"role_id": 77}]}
        Path(ws.DATA_FILE).write_text(json.dumps(data), encoding="utf-8")
        asyncio.run(ws.revoke_team_warning(self.guild, member=self.target, warn_id=second, revoke_reason="Irrtum",
                                           actor_name="Lena", actor_id="3", config=self.config,
                                                  actor_member=self.lead))
        self.assertNotIn(LOCK, self.target.roles)  # before: /warn-remove left the lock role in place
        again = asyncio.run(ws.revoke_team_warning(self.guild, member=self.target, warn_id=second, revoke_reason="x",
                                                   actor_name="Lena", actor_id="3", config=self.config,
                                                  actor_member=self.lead))
        self.assertFalse(again["stored"])

    def test_two_overlapping_warnings_are_both_kept(self):
        # K-04 through the real domain function: Discord calls in between must not drop the other warning.
        async def slow_sync(*a, **k):
            await asyncio.sleep(0.01)
            return {"ok": True, "role": None}

        async def scenario():
            with patch.object(ws, "sync_warn_roles", slow_sync):
                await asyncio.gather(
                    ws.issue_team_warning(self.guild, actor_member=self.lead, actor_name="Lena", actor_id="3", member=self.target,
                                          reason="Erster Grund", duration_days=7, config=self.config),
                    ws.issue_team_warning(self.guild, actor_member=self.lead, actor_name="Lena", actor_id="3", member=self.target,
                                          reason="Zweiter Grund", duration_days=7, config=self.config),
                )
        asyncio.run(scenario())
        self.assertEqual(sorted(w["reason"] for w in self.stored()), ["Erster Grund", "Zweiter Grund"])

    def test_slash_path_goes_through_protection_and_approval(self):
        # replaces two feature_smoke literal checks: the slash command reaches both checks via the domain function
        ws.enforce_high_impact_warning_approval.return_value = (False, "Zweite Freigabe nötig.")
        result = self.issue(source="Slash /warn")
        self.assertEqual(result["message"], "Zweite Freigabe nötig.")
        self.assertEqual(ws.enforce_team_warn_protection.await_args.kwargs["source"], "Slash /warn")
        self.assertFalse(Path(ws.DATA_FILE).exists())

    def test_slash_commands_use_the_domain_functions(self):
        source = Path("cogs/pulse_suite.py").read_text(encoding="utf-8")
        self.assertIn("web.issue_team_warning(", source)
        self.assertIn("web.revoke_team_warning(", source)
        self.assertIn("dauer_tage:app_commands.Range[int,1,365]=7", source)
        self.assertNotIn('current_warn["active"]=False', source)  # the old private copy is gone


class RankChangeDomainTests(WarningDomainTests):
    """BOT-05 part 2: promotion/demotion follow-up shared by the dashboard and /promote, /demote."""

    def test_promotion_records_probation_dm_and_update(self):
        result = asyncio.run(ws.finish_promotion(self.guild, member=self.target, previous_role=TEAM, new_role=LEAD,
                                                 probation_days=3, actor_name="Lena", actor_id="3", reason="Gute Arbeit",
                                                 source="Slash /promote"))
        self.assertTrue(result["probation"])
        probation = json.loads(Path(ws.DATA_FILE).read_text(encoding="utf-8"))["2"]["probation"]
        self.assertEqual((probation["days"], probation["previous_role_id"], probation["new_role_id"]), (3, 10, 11))
        ws.send_dm_notification.assert_awaited()   # before: /promote sent no DM and set no probation
        fields = ws.send_team_update_embed.await_args.kwargs["fields"]
        self.assertIn(("Quelle", "Slash /promote", True), fields)

    def test_promotion_without_previous_rank_sets_no_probation(self):
        result = asyncio.run(ws.finish_promotion(self.guild, member=self.target, previous_role=None, new_role=TEAM,
                                                 probation_days=7, actor_name="Lena", actor_id="3", reason="Aufnahme"))
        self.assertFalse(result["probation"])
        self.assertFalse(Path(ws.DATA_FILE).exists())

    def test_demotion_messages(self):
        asyncio.run(ws.finish_demotion(self.guild, member=self.target, previous_role=LEAD, new_role=TEAM,
                                       actor_name="Lena", actor_id="3", reason="Inaktiv"))
        self.assertIn("degradiert", ws.send_dm_notification.await_args.args[1])
        asyncio.run(ws.finish_demotion(self.guild, member=self.target, previous_role=TEAM, new_role=None,
                                       actor_name="Lena", actor_id="3", reason="Inaktiv"))
        self.assertIn("entfernt", ws.send_dm_notification.await_args.args[1])

    def test_slash_promote_requires_probation_and_uses_the_shared_follow_up(self):
        source = Path("cogs/pulse_suite.py").read_text(encoding="utf-8")
        self.assertIn("probezeit:app_commands.Choice[int]", source)
        self.assertIn("web.finish_promotion(", source)
        self.assertIn("web.finish_demotion(", source)
        web = Path("webserver.py").read_text(encoding="utf-8")
        self.assertEqual(web.count('"promoted_by_id": actor_id'), 1)  # one place writes the probation entry


if __name__ == "__main__":
    unittest.main()
