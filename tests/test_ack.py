"""Audit #53: einheitliche Lesebestätigung (Modul, Routen, Rechte, Bot-Befehl; temp DB, keine Discord-Verbindung)."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_ack as ack
import pulse_db
from tests.test_wiki_plus import Base as WikiBase

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
MANAGER = {"can_view_dashboard": True, "can_manage_announcements": True}
TEAM = {"can_view_dashboard": True}
ID1, ID2, ID3 = "900000000000000001", "900000000000000002", "900000000000000003"


class ModuleTests(WikiBase):
    def test_require_is_idempotent_and_validates(self):
        self.assertTrue(ack.require("announcement", "a1", "Neue Regel", actor_id="9", actor_name="Lead", now=NOW))
        self.assertFalse(ack.require("announcement", "a1", "Neue Regel"))
        for bad in (("unbekannt", "a2", "x"), ("announcement", "mit leer", "x"), ("announcement", "", "x"), ("announcement", "a3", "  ")):
            self.assertFalse(ack.require(*bad), bad)
        self.assertTrue(ack.require("team_news", "n1", "T", url="https://evil.example"))   # nicht abgelehnt, die URL wird ersetzt
        self.assertTrue(ack.is_required("team_news", "n1"))

    def test_external_urls_are_replaced(self):
        ack.require("instruction", "i1", "Titel", url="//evil.example/x")
        with pulse_db.connect() as db:
            self.assertEqual(db.execute("SELECT url FROM ack_requirements WHERE subject_id='i1'").fetchone()[0], ack.LIST_URL)

    def test_acknowledge_only_for_required_posts_and_only_once(self):
        self.assertFalse(ack.acknowledge("announcement", "nope", ID1))
        ack.require("announcement", "a1", "Regel")
        self.assertTrue(ack.acknowledge("announcement", "a1", ID1, "Ann", NOW))
        self.assertTrue(ack.acknowledge("announcement", "a1", ID1, "Ann", NOW + timedelta(hours=1)))   # idempotent
        with pulse_db.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ack_receipts").fetchone()[0], 1)
        self.assertFalse(ack.acknowledge("announcement", "a1", "", "x"))

    def test_pending_overdue_first_and_coverage(self):
        ack.require("announcement", "old", "Alt", due_days=1, now=NOW - timedelta(days=5))
        ack.require("team_news", "new", "Neu", now=NOW)
        self.assertEqual([p["subject_id"] for p in ack.pending_for(ID1, NOW)], ["old", "new"])
        self.assertTrue(ack.pending_for(ID1, NOW)[0]["overdue"])
        ack.acknowledge("announcement", "old", ID1)
        self.assertEqual([p["subject_id"] for p in ack.pending_for(ID1, NOW)], ["new"])
        cov = ack.coverage("announcement", "old", [(ID1, "Ann"), (ID2, "Bo"), (ID3, "Cy")])
        self.assertEqual(([c[1] for c in cov["confirmed"]], [m[1] for m in cov["missing"]], cov["total"]), (["Ann"], ["Bo", "Cy"], 3))
        self.assertEqual(len(ack.overview([(ID1, "Ann")])), 2)

    def test_due_days_are_bounded(self):
        ack.require("instruction", "i2", "T", due_days=99999, now=NOW)
        with pulse_db.connect() as db:
            due = db.execute("SELECT due_at FROM ack_requirements WHERE subject_id='i2'").fetchone()[0]
        self.assertEqual(due[:10], (NOW + timedelta(days=ack.MAX_DAYS)).date().isoformat())

    def test_tables_are_part_of_the_backup(self):
        self.assertTrue({"ack_requirements", "ack_receipts"} <= set(pulse_db.TABLES))


class RouteTests(WikiBase):
    def setUp(self):
        super().setUp()
        from tests.fakes import FakeMember
        self.members += [FakeMember(int(ID1), "Ann", roles=[self.role]), FakeMember(int(ID2), "Bo", roles=[self.role])]

    def test_page_lists_own_pending_and_confirming_works(self):
        ack.require("announcement", "a1", "Neue <b>Regel</b>", actor_name="Lead")
        with self.as_user(TEAM, ID1):
            html = self.client.get("/bestaetigungen").text
            self.assertIn("Neue &lt;b&gt;Regel&lt;/b&gt;", html)
            self.assertNotIn("Abdeckung (Führung)", html)
            resp = self.client.post("/bestaetigungen/announcement/a1/bestaetigen", follow_redirects=False)
            self.assertEqual(resp.status_code, 303)
            self.assertIn("Nichts zu bestätigen", self.client.get("/bestaetigungen").text)
            self.assertEqual(self.client.post("/bestaetigungen/announcement/gibtesnicht/bestaetigen").status_code, 404)
        self.assertEqual(len(ack.pending_for(ID2)), 1)            # die Bestätigung gilt nur für die eigene Person

    def test_manager_sees_coverage_and_can_publish_an_instruction(self):
        with self.as_user(MANAGER, ID1):
            self.assertEqual(self.client.post("/bestaetigungen/neu", data={"title": "Funkdisziplin", "content": "Kurz funken.", "frist": "3"}, follow_redirects=False).status_code, 303)
            html = self.client.get("/bestaetigungen").text
        self.assertIn("Abdeckung (Führung)", html)
        self.assertIn("Funkdisziplin", html)
        [req] = ack.overview([])
        self.assertEqual((req["kind"], req["due_at"] != ""), ("instruction", True))
        self.assertTrue(any(a["title"] == "Funkdisziplin" for a in pulse_db.announcements(10)))

    def test_team_member_cannot_publish_and_nothing_is_written(self):
        with self.as_user(TEAM, ID1):
            self.assertEqual(self.client.post("/bestaetigungen/neu", data={"title": "X", "content": "Y"}).status_code, 403)
        self.assertEqual(ack.overview([]), [])
        self.assertEqual(pulse_db.announcements(10), [])

    def test_require_and_notify_informs_the_team_once_but_not_the_author(self):
        import pulse_ack_pages as pages
        guild = SimpleNamespace()
        with patch.object(pages, "team_pairs", return_value=[(ID1, "Ann"), (ID2, "Bo"), (ID3, "Cy")]):
            self.assertTrue(pages.require_and_notify(guild, {}, "announcement", "a9", "Regel", actor_id=ID1))
            self.assertFalse(pages.require_and_notify(guild, {}, "announcement", "a9", "Regel", actor_id=ID1))      # bereits verlangt
        self.assertEqual(len(pulse_db.notifications(ID1)), 0)
        self.assertEqual(len(pulse_db.notifications(ID2)), 1)
        self.assertEqual(pulse_db.notifications(ID2)[0]["url"], ack.LIST_URL)


class BotCommandTests(WikiBase):
    def test_command_lists_pending_and_confirms_selected_posts(self):
        from cogs.pulse_pro import PulsePro
        ack.require("announcement", "a1", "Regel A")
        ack.require("team_news", "n1", "News B")
        inter = SimpleNamespace(user=SimpleNamespace(id=int(ID1), display_name="Ann"), response=SimpleNamespace(send_message=AsyncMock()))
        asyncio.run(PulsePro.bestaetigungen.callback(None, inter))
        args, kwargs = inter.response.send_message.await_args
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("Offene Bestätigungen (2)", args[0])
        select = kwargs["view"].children[0]
        select._values = ["announcement|a1"]           # Auswahl simulieren
        confirm_inter = SimpleNamespace(user=SimpleNamespace(id=int(ID1), display_name="Ann"), response=SimpleNamespace(edit_message=AsyncMock()))
        asyncio.run(select.callback(confirm_inter))
        self.assertIn("1 Beitrag", confirm_inter.response.edit_message.await_args.kwargs["content"])
        self.assertEqual([p["subject_id"] for p in ack.pending_for(ID1)], ["n1"])

    def test_nothing_pending(self):
        from cogs.pulse_pro import PulsePro
        inter = SimpleNamespace(user=SimpleNamespace(id=int(ID1), display_name="Ann"), response=SimpleNamespace(send_message=AsyncMock()))
        asyncio.run(PulsePro.bestaetigungen.callback(None, inter))
        self.assertIn("nichts zu bestätigen", inter.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
