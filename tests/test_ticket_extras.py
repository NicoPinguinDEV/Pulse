"""BOT-06: quick replies, handover, ticket ban, tags, rating report, transcript view, ticket tools page."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_ticket_extras as tx
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch.object(ws, "BASE_DIR", root), patch.object(ws, "DATA_DIR", root)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        self.root = root
        self.tid = pulse_db.create_ticket("555", "1", "9", "Bürger", "support")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class ExtrasTests(TempDb):
    def test_quick_replies_per_category_plus_all(self):
        tx.save_replies({"*": "Danke | Danke für deine Geduld!", "support": "Logs | Bitte schick uns deine Logs.\\nDanke.\nkaputte Zeile"})
        titles = [r["title"] for r in tx.replies_for("support")]
        self.assertEqual(titles, ["Logs", "Danke"])
        self.assertEqual(tx.replies_for("support")[0]["text"], "Bitte schick uns deine Logs.\nDanke.")
        self.assertEqual([r["title"] for r in tx.replies_for("anderes")], ["Danke"])
        self.assertIn("Logs | ", tx.replies_as_text("support"))

    def test_handover_requires_a_note_and_logs_it(self):
        self.assertFalse(tx.handover(self.tid, 7, "Lena", 3, "Max", "kurz")[0])
        ok, _ = tx.handover(self.tid, 7, "Lena", 3, "Max", "Bürger wartet auf Rückerstattung")
        self.assertTrue(ok)
        ticket = pulse_db.get_ticket(ticket_id=self.tid)
        self.assertEqual((ticket["claimed_by_id"], ticket["claimed_by_name"]), ("7", "Lena"))
        events = pulse_db.ticket_events(self.tid)
        self.assertTrue(any(e["event_type"] == "handover" and "Rückerstattung" in e["details"] for e in events))
        self.assertTrue(any(n["title"] == "🎫 Ticket übergeben" for n in pulse_db.notifications("7")))
        self.assertFalse(tx.handover(self.tid, 7, "Lena", 3, "Max", "nochmal dieselbe Person")[0])

    def test_ticket_ban_with_expiry(self):
        with self.assertRaises(ValueError):
            tx.block(9, 3, "kurz", "Max")
        entry = tx.block(9, 3, "Spam-Tickets", "Max")
        self.assertTrue(tx.block_for(9))
        self.assertIn("Spam-Tickets", tx.block_message(entry))
        self.assertIsNone(tx.block_for(9, now=datetime.now(timezone.utc) + timedelta(days=4)))  # expired
        self.assertTrue(tx.unblock(9))
        self.assertIsNone(tx.block_for(9))

    def test_tags(self):
        self.assertEqual(tx.add_tag(self.tid, "#Rückerstattung", "Max"), "rückerstattung")
        tx.add_tag(self.tid, "bug")
        with self.assertRaises(ValueError):
            tx.add_tag(self.tid, "x")
        self.assertEqual(tx.tags_for([self.tid]), {self.tid: ["bug", "rückerstattung"]})
        self.assertTrue(tx.remove_tag(self.tid, "bug"))
        self.assertEqual(tx.tags_for([]), {})

    def test_ratings_report(self):
        tickets = [{"category": "support", "claimed_by_name": "Lena", "rating": 5},
                   {"category": "support", "claimed_by_name": "Max", "rating": 3},
                   {"category": "bewerbung", "claimed_by_name": "Lena", "rating": None}]
        report = tx.ratings_report(tickets)
        self.assertEqual((report["count"], report["average"]), (2, 4.0))
        self.assertEqual(report["by_category"], [("support", 4.0, 2)])
        self.assertIn(("Lena", 5.0, 1), report["by_staff"])

    def test_transcript_html_escapes(self):
        out = tx.transcript_html("[2026-10-21 12:00] Max (9): <script>alert(1)</script>\n--- Ticket geschlossen ---")
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("<b>Max (9)</b>", out)
        self.assertIn("tr-line", out)


class BlockedOpenTests(TempDb):
    def test_banned_person_cannot_open_a_ticket(self):
        from cogs import tickets
        tx.block(42, 2, "Missbrauch der Tickets", "Max")
        sent = []
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1), user=SimpleNamespace(id=42),
            response=SimpleNamespace(send_message=lambda *a, **k: _record(sent, a)),
            followup=SimpleNamespace(send=lambda *a, **k: _record(sent, a)))
        asyncio.run(tickets.create_ticket_channel(interaction, "support", None, []))
        self.assertIn("keine neuen Tickets", sent[0])


class SlashBanTargetTests(TempDb):
    def test_team_members_bots_and_self_cannot_be_ticket_banned(self):
        from cogs import ticket_tools
        replies = []

        async def send_message(text, **kw):
            replies.append(text)
        actor = SimpleNamespace(id=1, display_name="Lena")
        teammate = SimpleNamespace(id=2, bot=False, display_name="Max", roles=[SimpleNamespace(id=5)])
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1), user=actor, response=SimpleNamespace(send_message=send_message))
        cog = ticket_tools.TicketTools.__new__(ticket_tools.TicketTools)
        with patch.object(ticket_tools, "may_manage", lambda i: True), patch.object(ws, "load_config", lambda: {"team_role_ids": [5]}):
            asyncio.run(ticket_tools.TicketTools.sperre.callback(cog, interaction, teammate, 3, "Missbrauch der Tickets"))
            myself = SimpleNamespace(id=1, bot=False, display_name="Lena", roles=[])
            asyncio.run(ticket_tools.TicketTools.sperre.callback(cog, interaction, myself, 3, "Selbstversuch xx"))
        self.assertTrue(all("nur für Bürger" in r for r in replies))
        self.assertIsNone(tx.block_for(2))


async def _record(sent, args):
    sent.append(args[0])


class PageTests(TempDb):
    def test_tools_page_rights_and_save(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):  # can_manage_tickets, no admin
            page = client.get("/tickets/tools")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Bearbeiten können nur Admins", page.text)
            self.assertEqual(client.post("/tickets/tools/replies", data={"replies_*": "A | B"}).status_code, 403)
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            saved = client.post("/tickets/tools/replies", data={"replies_*": "Hallo | Willkommen im Support"}, follow_redirects=False)
            self.assertIn("t=ok", saved.headers["location"])
        self.assertEqual(tx.replies_for("x")[0]["title"], "Hallo")

    def test_transcript_view(self):
        (self.root / "transcripts").mkdir()
        (self.root / "transcripts" / f"{self.tid}.txt").write_text("[t] Max (9): Hallo <b>", encoding="utf-8")
        pulse_db.close_ticket(self.tid, "erledigt", f"transcripts/{self.tid}.txt")
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            page = client.get(f"/tickets/transcript/{self.tid}/view")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Hallo &lt;b&gt;", page.text)


if __name__ == "__main__":
    unittest.main()
