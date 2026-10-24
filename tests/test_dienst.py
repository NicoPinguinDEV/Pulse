"""BOT-03: shift actions in Discord use exactly the dashboard logic."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_db
import webserver as ws


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, content=None, **kw):
        self.sent.append(content)

    def is_done(self):
        return bool(self.sent)


def interaction(uid=7):
    return SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=uid, display_name="Max"),
                           response=FakeResponse(), client=SimpleNamespace())


class DutyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(ws, "SHIFTS_FILE", str(root / "shifts.json")),
                        patch.object(ws, "DATA_FILE", str(root / "team_data.json")),
                        patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "get_loas", lambda: {}),
                        patch.object(ws, "sync_duty_after_shift", AsyncMock())]
        for p in self.patches:
            p.start()
        pulse_db.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_full_cycle_with_the_shared_logic(self):
        self.assertEqual(ws.apply_shift_action("7", "start"), (True, "Schicht gestartet – viel Erfolg!"))
        self.assertFalse(ws.apply_shift_action("7", "start")[0])          # already running
        self.assertTrue(ws.apply_shift_action("7", "break")[0])
        self.assertFalse(ws.apply_shift_action("7", "break")[0])          # already paused
        self.assertTrue(ws.apply_shift_action("7", "resume")[0])
        ok, msg = ws.apply_shift_action("7", "end")
        self.assertTrue(ok)
        self.assertIn("gutgeschrieben", msg)
        self.assertEqual(len(ws.load_shifts()["history"]), 1)
        self.assertIn("nicht im Dienst", ws.shift_status_text("7"))

    def test_start_during_leave_keeps_the_leave(self):
        with patch.object(ws, "get_loas", lambda: {"7": {"active": True, "bis": "2026-12-24"}}):
            ok, msg = ws.apply_shift_action("7", "start")
        self.assertTrue(ok)
        self.assertIn("Deine Abmeldung bleibt bis", msg)
        self.assertIn("7", ws.load_shifts()["active_shifts"])

    def test_five_warnings_block_the_start_like_in_the_dashboard(self):
        ws.save_json(ws.DATA_FILE, {"7": {"warns_list": [{"id": str(n), "active": True} for n in range(5)]}})
        with patch.object(ws, "active_warns", lambda e: e.get("warns_list", [])):
            self.assertEqual(ws.apply_shift_action("7", "start")[1], "Schicht-Start gesperrt: Du hast bereits 5 aktive Verwarnungen!")

    def test_discord_path_checks_rights_on_every_click(self):
        from cogs import dienst
        outsider = interaction(uid=9)
        with patch.object(dienst, "may_use_duty", lambda g, uid: uid == 7):
            asyncio.run(dienst.run_duty_action(outsider, "start"))
            self.assertIn("nur Teammitglieder", outsider.response.sent[0])
            member = interaction()
            with patch("pulse_presence.update", AsyncMock()):
                asyncio.run(dienst.run_duty_action(member, "start"))
            self.assertEqual(member.response.sent[0], "✅ Schicht gestartet – viel Erfolg!")
            ws.sync_duty_after_shift.assert_awaited()                     # voice-support duty follows
            status = interaction()
            asyncio.run(dienst.run_duty_action(status, "status"))
            self.assertIn("im Dienst seit", status.response.sent[0])

    def test_panel_buttons_are_persistent(self):
        from cogs import dienst
        view = dienst.DutyPanelView()
        self.assertIsNone(view.timeout)
        self.assertEqual(sorted(c.custom_id for c in view.children),
                         ["pulse_dienst:break", "pulse_dienst:end", "pulse_dienst:resume", "pulse_dienst:start"])

    def test_dashboard_route_uses_the_shared_function(self):
        source = Path(ws.__file__).read_text(encoding="utf-8")
        route = source[source.index('@app.post("/shift/action")'):]
        route = route[:route.index("\n\n\n")]
        self.assertIn("apply_shift_action(", route)


if __name__ == "__main__":
    unittest.main()
