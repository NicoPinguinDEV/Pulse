"""BOT-14: panel overview – reads every panel's location read-only, shows its state, republishes where possible."""
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord
from fastapi.testclient import TestClient

import pulse_db
import pulse_panels as pp
import webserver as ws
from tests.fakes import FakeChannel
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")), patch.object(pp, "BASE_DIR", root),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json"))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        self.root = root
        self.channel = FakeChannel(555, "dienst")
        self.guild = SimpleNamespace(id=1, name="G", get_channel=lambda cid: self.channel if cid == 555 else None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_collect_reads_cog_databases_without_creating_them(self):
        items = pp.collect(1)
        self.assertEqual(list(self.root.glob("*.db")), [pulse_db.DB_PATH and Path(pulse_db.DB_PATH)])  # only pulse.db
        keys = {i["key"] for i in items}
        self.assertTrue({"tickets", "verification", "duty", "abmeldung", "rp", "buero", "activity", "teamliste", "quiz", "honeypot"} <= keys)
        con = sqlite3.connect(self.root / "teamliste.db")
        con.execute("CREATE TABLE config (key TEXT, value INTEGER)")
        con.execute("INSERT INTO config VALUES ('teamlist_channel_id', 555)")
        con.commit()
        con.close()
        team = next(i for i in pp.collect(1) if i["key"] == "teamliste")
        self.assertEqual(team["channel_id"], 555)

    def test_states(self):
        items = [pp._entry("a", "A"), pp._entry("b", "B", 999), pp._entry("c", "C", 555, 4242), pp._entry("d", "D", 555)]
        checked = {i["key"]: i["state"] for i in asyncio.run(pp.check(self.guild, items))}
        self.assertEqual(checked, {"a": "not_set_up", "b": "missing_channel", "c": "missing_message", "d": "ok"})

    def test_duty_panel_republish(self):
        pulse_db.set_setting("duty_panel", {"channel_id": 555, "message_id": 1})
        text = asyncio.run(pp.republish(SimpleNamespace(cogs={}), self.guild, "duty"))
        self.assertEqual(text, "Dienst-Panel wurde neu veröffentlicht.")   # old message was gone
        self.assertEqual(len(self.channel.sent), 1)
        stored = pulse_db.get_setting("duty_panel")
        self.assertEqual(stored["message_id"], self.channel.sent[0].id)
        with self.assertRaises(ValueError):
            asyncio.run(pp.republish(SimpleNamespace(cogs={}), self.guild, "quiz"))

    def test_page_and_rights(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        guild = SimpleNamespace(id=1, name="G", get_channel=lambda cid: None, members=[], roles=[])
        admin = fake_auth_for(ADMIN)

        def admin_ctx(*a, **k):
            ctx = admin(*a, **k)
            ctx.guild = guild
            return ctx
        with patch.object(ws, "auth", admin_ctx):
            page = client.get("/admin/panels")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Dienst-Panel", page.text)
            self.assertIn("Neu veröffentlichen", page.text)
            refused = client.post("/admin/panels/republish", data={"kind": "quiz"}, follow_redirects=False)
            self.assertIn("t=err", refused.headers["location"])
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(client.get("/admin/panels").status_code, 403)

    def test_discord_error_is_reported(self):
        async def broken(*a, **k):
            raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no")
        with patch.object(pp, "republish", broken), patch.object(ws, "auth", fake_auth_for(ADMIN)):
            previous = getattr(ws.app.state, "bot", None)
            ws.app.state.bot = SimpleNamespace()
            try:
                response = TestClient(ws.app, raise_server_exceptions=False).post(
                    "/admin/panels/republish", data={"kind": "duty"}, follow_redirects=False)
            finally:
                ws.app.state.bot = previous
        self.assertIn("t=err", response.headers["location"])
        self.assertIn("Forbidden", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
