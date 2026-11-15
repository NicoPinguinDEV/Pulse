"""Audit #124: Bot-Befehl /taskdone nutzt dieselbe Regel wie das Dashboard (pulse_task_actions)."""
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_db
import webserver
from cogs.pulse_pro import PulsePro


class TaskDoneWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = f"{self.tmp.name}/pulse.db"
        patcher = patch.object(pulse_db, "DB_PATH", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        pulse_db._SCHEMA_READY.discard(path)
        pulse_db.init_db()
        self.tid = pulse_db.create_task("Akte", "", "2", "Bo", "900000000000000001", "Chef")

    def _run(self, uid, perms):
        inter = SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=uid, display_name=f"U{uid}"),
                                response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(webserver, "compute_perms", return_value=(perms, object())), patch.object(webserver, "load_config", return_value={}):
            asyncio.run(PulsePro.taskdone.callback(None, inter, self.tid))
        return inter.response.send_message.await_args.args[0]

    def test_assignee_completes_own_task(self):
        self.assertIn("erledigt", self._run(2, {}))
        self.assertEqual(pulse_db.get_task(self.tid)["status"], "done")

    def test_stranger_is_refused_without_write(self):
        self.assertIn("nicht für diese Aufgabe zuständig", self._run(3, {}))
        self.assertEqual(pulse_db.get_task(self.tid)["status"], "open")

    def test_leadership_with_promote_may_complete_like_on_the_web(self):
        self.assertIn("erledigt", self._run(4, {"can_promote": True}))
        self.assertEqual(pulse_db.get_task(self.tid)["status"], "done")


if __name__ == "__main__":
    unittest.main()
