"""F8 / Audit #123: ticket quick replies are read from the template library, with the legacy store as fallback."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_eventbus as bus
import pulse_events as ev
import pulse_templates as tpl
import pulse_ticket_extras as tx
import pulse_ticket_replies as tr
import webserver as ws
from cogs import ticket_tools
from tests.test_pulse_admin_area import ADMIN, GUILD, TEAMLER, fake_auth_for

G, G2 = "1", "2"
MGR = {"can_manage_tickets": True}
LEAD_ONLY = {"can_view_dashboard": True}
ADM = {"is_admin": True}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch.object(ws, "BASE_DIR", root), patch.object(ws, "DATA_DIR", root)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        self.tid = pulse_db.create_ticket("555", G, "9", "Bürger", "support")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def titles(self, perms=MGR, guild=G, category="support"):
        return [o["title"] for o in tr.offers(perms, guild, category)]

    def make(self, title="Danke", body="Hallo {user}, Ticket {ticket}.", scope="*", guild=G):
        return tpl.create(MGR, guild, "ticket_reply", scope, title, body, "7", "Anna")


class OffersAndResolveTests(Base):
    def test_library_template_is_used_with_version_and_placeholders(self):
        t = self.make()
        values = {"user": "Max", "ticket": self.tid, "category": "support", "staff": "Lena"}
        reply = tr.resolve(MGR, G, "support", "Danke", values)
        self.assertEqual((reply["source"], reply["template_id"], reply["version"]), ("library", t["id"], 1))
        self.assertEqual(reply["text"], f"Hallo Max, Ticket {self.tid}.")
        tpl.update(MGR, G, t["id"], "Danke", "Neu {staff}", "7", "Anna")
        again = tr.resolve(MGR, G, "support", "Danke", values)
        self.assertEqual((again["version"], again["text"]), (2, "Neu Lena"))
        self.assertEqual(tr.resolve(MGR, G, "support", "Danke", values)["template_id"], t["id"])

    def test_category_scope_and_all_scope_like_before(self):
        self.make("Alle", "A", scope="*")
        self.make("Support", "S", scope="support")
        self.make("Anderes", "X", scope="other")
        self.assertEqual(sorted(self.titles()), ["Alle", "Support"])
        self.assertEqual(self.titles(category="other"), ["Anderes", "Alle"])

    def test_missing_template_is_none(self):
        self.assertIsNone(tr.resolve(MGR, G, "support", "Gibt es nicht", {}))
        self.assertIsNone(tr.resolve(MGR, G, "support", "", {}))

    def test_archived_template_is_not_offered_and_not_resurrected_from_legacy(self):
        tx.save_replies({"*": "Danke | Legacy-Text"})
        t = self.make("Danke", "Bibliothek")
        self.assertEqual(tr.resolve(MGR, G, "support", "Danke", {})["text"], "Bibliothek")
        tpl.archive(MGR, G, t["id"], "7", "Anna")
        self.assertNotIn("Danke", self.titles())
        self.assertIsNone(tr.resolve(MGR, G, "support", "Danke", {}))

    def test_foreign_guild_templates_are_invisible(self):
        self.make("Intern", "geheim", guild=G2)
        self.assertNotIn("Intern", self.titles(guild=G))
        self.assertIsNone(tr.resolve(MGR, G, "support", "Intern", {}))
        self.assertIn("Intern", self.titles(guild=G2))

    def test_missing_permission_offers_and_resolves_nothing(self):
        self.make()
        tx.save_replies({"*": "Alt | Legacy"})
        for perms in (LEAD_ONLY, {}, None, {"can_manage_applications": True}):
            self.assertEqual(tr.offers(perms, G, "support"), [])
            self.assertIsNone(tr.resolve(perms, G, "support", "Danke", {}))
            self.assertIsNone(tr.resolve(perms, G, "support", "Alt", {}))
        self.assertIn("Danke", self.titles(perms=ADM))

    def test_mentions_in_text_and_inserted_values_are_neutralised(self):
        self.make("Ping", "{user} @everyone <@&123456789012345678> {staff}")
        text = tr.resolve(MGR, G, "support", "Ping", {"user": "@here", "staff": "@everyone"})["text"]
        for bad in ("@everyone", "@here", "<@&1"):
            self.assertNotIn(bad, text)

    def test_invalid_template_text_in_library_cannot_exist(self):
        for body in ("Hi {user.name}", "Hi {unbekannt}", "Hi {user"):
            with self.assertRaises(ValueError):
                self.make("Kaputt", body)


class LegacyFallbackTests(Base):
    def test_empty_library_serves_the_legacy_store_verbatim(self):
        tx.save_replies({"*": "Gruß | Hallo {user}, danke!", "support": "Logs | Schick Logs."})
        offers = tr.offers(MGR, G, "support")
        self.assertEqual([(o["title"], o["source"]) for o in offers], [("Logs", "legacy"), ("Gruß", "legacy")])
        reply = tr.resolve(MGR, G, "support", "Gruß", {"user": "Max"})
        self.assertEqual((reply["text"], reply["source"], reply["version"]), ("Hallo {user}, danke!", "legacy", 0))

    def test_partial_library_mixes_with_legacy(self):
        tx.save_replies({"*": "A | Legacy A\nB | Legacy B"})
        self.make("A", "Bibliothek A")
        by_title = {o["title"]: o["source"] for o in tr.offers(MGR, G, "support")}
        self.assertEqual(by_title, {"A": "library", "B": "legacy"})

    def test_broken_library_falls_back_to_legacy(self):
        tx.save_replies({"*": "Gruß | Hallo"})
        self.make("Gruß", "Bibliothek")
        with patch.object(tpl, "lookup", side_effect=RuntimeError("db kaputt")), self.assertLogs("pulse.ticket_replies", "WARNING"):
            reply = tr.resolve(MGR, G, "support", "Gruß", {})
        self.assertEqual((reply["source"], reply["text"]), ("legacy", "Hallo"))

    def test_render_failure_is_controlled(self):
        self.make("Gruß", "Hi")
        with patch.object(tpl, "render", side_effect=ValueError("archiviert")), self.assertLogs("pulse.ticket_replies", "WARNING"):
            self.assertIsNone(tr.resolve(MGR, G, "support", "Gruß", {}))

    def test_old_and_damaged_legacy_formats(self):
        for raw in (None, [], "text", 5, {"*": "nope"}, {"*": [None, 3, {"title": "x"}, {"title": "", "text": "y"}]},
                    {"*": [{"title": "Ok", "text": "Fein"}]}):
            pulse_db.set_setting("ticket_quick_replies", raw)
            offers = tr.offers(MGR, G, "support")
            self.assertEqual([o["title"] for o in offers], ["Ok"] if raw == {"*": [{"title": "Ok", "text": "Fein"}]} else [], raw)

    def test_offers_are_capped_like_discord_autocomplete(self):
        tx.save_replies({"*": "\n".join(f"T{i:02d} | Text {i}" for i in range(40))})
        self.assertEqual(len(tr.offers(MGR, G, "support")), tx.MAX_REPLIES)


class SyncTests(Base):
    def test_sync_creates_updates_archives_and_keeps_history(self):
        tx.save_replies({"*": "Danke | v1 {user}\nEntf | weg"})
        r1 = tr.sync_from_legacy(MGR, G, "7", "Anna")
        self.assertEqual((r1["created"], r1["updated"], r1["archived"]), (2, 0, 0))
        tx.save_replies({"*": "Danke | v2 {user}"})
        r2 = tr.sync_from_legacy(MGR, G, "7", "Anna")
        self.assertEqual((r2["updated"], r2["archived"], r2["created"]), (1, 1, 0))
        danke = [t for t in tpl.lookup(MGR, G, "ticket_reply") if t["title"] == "Danke"][0]
        self.assertEqual((danke["current_version"], danke["body"]), (2, "v2 {user}"))
        self.assertEqual([v["body"] for v in tpl.versions(MGR, G, danke["id"])], ["v2 {user}", "v1 {user}"])
        self.assertNotIn("Entf", self.titles())  # archived title is not resurrected from anywhere
        self.assertEqual(tr.sync_from_legacy(MGR, G, "7", "Anna")["unchanged"], 1)

    def test_invalid_placeholder_text_archives_the_outdated_version_and_legacy_takes_over(self):
        tx.save_replies({"*": "Danke | gut {user}"})
        tr.sync_from_legacy(MGR, G, "7", "Anna")
        tx.save_replies({"*": "Danke | jetzt {kaputt.x}"})
        result = tr.sync_from_legacy(MGR, G, "7", "Anna")
        self.assertEqual(result["archived"], 1)
        self.assertEqual(len(result["skipped"]), 1)
        reply = tr.resolve(MGR, G, "support", "Danke", {})
        # the outdated library version must not shadow the admin's newer (legacy) text
        self.assertEqual((reply["source"], reply["text"]), ("legacy", "jetzt {kaputt.x}"))

    def test_concurrent_change_is_reported_and_does_not_raise(self):
        tx.save_replies({"*": "Danke | eins"})
        tr.sync_from_legacy(MGR, G, "7", "Anna")
        tx.save_replies({"*": "Danke | zwei"})
        t = [x for x in tpl.lookup(MGR, G, "ticket_reply")][0]
        tpl.update(MGR, G, t["id"], "Danke", "dazwischen", "8", "Ben")  # someone else edits meanwhile
        with patch.object(tpl, "lookup", return_value=[{**t, "current_version": 1, "body": "eins"}]):
            result = tr.sync_from_legacy(MGR, G, "7", "Anna")
        self.assertEqual(result["updated"], 0)
        self.assertTrue(any("geändert" in s for s in result["skipped"]))

    def test_sync_needs_the_ticket_right_and_never_raises(self):
        tx.save_replies({"*": "Danke | eins"})
        result = tr.sync_from_legacy(LEAD_ONLY, G, "7", "Anna")
        self.assertIn("error", result)
        self.assertEqual(tpl.lookup(ADM, G, "ticket_reply"), [])


def interaction_for(channel_id="555", user_id=42, name="Lena"):
    sent = []

    async def send_message(text, **kw):
        sent.append((text, kw))
    inter = SimpleNamespace(guild=SimpleNamespace(id=int(G)), user=SimpleNamespace(id=user_id, display_name=name),
                            channel=SimpleNamespace(id=int(channel_id)), response=SimpleNamespace(send_message=send_message),
                            locale=None)
    return inter, sent


class CommandTests(Base):
    def run_antwort(self, perms, title, **kw):
        inter, sent = interaction_for(**kw)
        cog = ticket_tools.TicketTools.__new__(ticket_tools.TicketTools)
        with patch.object(ticket_tools, "may_manage", lambda i: bool(perms.get("can_manage_tickets") or perms.get("is_admin"))), \
                patch.object(ticket_tools, "_perms", lambda i: perms):
            asyncio.run(ticket_tools.TicketTools.antwort.callback(cog, inter, title))
        return sent

    def test_antwort_sends_library_text_with_mention_protection_and_records_the_version(self):
        t = self.make("Gruß", "Hallo {user} von {staff} zu {ticket}")
        cursor = bus.poll(ADM)["cursor"]
        with patch.object(pulse_db, "get_ticket", lambda **k: {"id": self.tid, "category": "support", "user_name": "@everyone", "status": "open"}):
            sent = self.run_antwort(MGR, "Gruß")
        text, kw = sent[0]
        self.assertEqual(text, f"Hallo @​everyone von Lena zu {self.tid}")
        self.assertFalse(kw["allowed_mentions"].everyone)
        self.assertFalse(kw["allowed_mentions"].roles)
        events = [e for e in bus.poll(ADM, cursor)["events"] if e["action"] == "quick_reply_sent"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["correlation_id"], f"ticket:{self.tid}")
        hist = ev.history(correlation_id=f"ticket:{self.tid}", perms=MGR)
        self.assertEqual(hist[0]["payload"]["template_id"], t["id"])
        self.assertEqual(hist[0]["payload"]["version"], 1)

    def test_antwort_legacy_text_is_sent_verbatim_and_recorded_as_legacy(self):
        tx.save_replies({"*": "Gruß | Hallo {user}!"})
        sent = self.run_antwort(MGR, "Gruß")
        self.assertEqual(sent[0][0], "Hallo {user}!")
        hist = ev.history(correlation_id=f"ticket:{self.tid}", perms=MGR)
        self.assertEqual((hist[0]["payload"]["source"], hist[0]["payload"]["version"]), ("legacy", 0))

    def test_antwort_missing_archived_foreign_and_unauthorised_are_refused_without_event(self):
        t = self.make("Weg", "x")
        tpl.archive(MGR, G, t["id"], "7", "Anna")
        self.make("Fremd", "x", guild=G2)
        for perms, title in ((MGR, "Gibt es nicht"), (MGR, "Weg"), (MGR, "Fremd"), (LEAD_ONLY, "Weg")):
            sent = self.run_antwort(perms, title)
            self.assertTrue(sent[0][0].startswith("❌"), (perms, title))
        self.assertEqual([e for e in ev.history(correlation_id=f"ticket:{self.tid}", perms=ADM) if "quick_reply" in e["action"]], [])

    def test_autocomplete_uses_the_library_and_respects_rights(self):
        self.make("Gruß", "x")
        self.make("Archiv", "x")
        tpl.archive(MGR, G, [r for r in tpl.lookup(MGR, G, "ticket_reply") if r["title"] == "Archiv"][0]["id"], "7", "Anna")
        inter, _ = interaction_for()
        cog = ticket_tools.TicketTools.__new__(ticket_tools.TicketTools)

        def complete(perms, current=""):
            with patch.object(ticket_tools, "may_manage", lambda i: bool(perms.get("can_manage_tickets"))), \
                    patch.object(ticket_tools, "_perms", lambda i: perms):
                return asyncio.run(ticket_tools.TicketTools.antwort_autocomplete(cog, inter, current))
        self.assertEqual([c.value for c in complete(MGR)], ["Gruß"])
        self.assertEqual([c.value for c in complete(MGR, "zzz")], [])
        self.assertEqual(complete(LEAD_ONLY), [])


class ToolsPageSaveTests(Base):
    def test_admin_save_mirrors_into_the_library_as_versions(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            for text in ("Hallo | Version eins {user}", "Hallo | Version zwei {user}"):
                res = client.post("/tickets/tools/replies", data={"replies_*": text}, follow_redirects=False)
                self.assertIn("t=ok", res.headers["location"])
        guild = str(GUILD.id)
        rows = tpl.lookup(ADM, guild, "ticket_reply")
        self.assertEqual([(r["title"], r["current_version"], r["body"]) for r in rows], [("Hallo", 2, "Version zwei {user}")])
        self.assertEqual(tx.replies_for("x")[0]["text"], "Version zwei {user}")  # legacy editor store unchanged in format

    def test_team_member_cannot_save_and_nothing_is_mirrored(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(client.post("/tickets/tools/replies", data={"replies_*": "A | B"}).status_code, 403)
        with pulse_db.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM message_templates").fetchone()[0], 0)

    def test_library_failure_never_blocks_saving(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", fake_auth_for(ADMIN)), patch.object(tpl, "sync_kind", side_effect=RuntimeError("kaputt")), \
                self.assertLogs("pulse.ticket_replies", "WARNING"):
            res = client.post("/tickets/tools/replies", data={"replies_*": "Hallo | Willkommen"}, follow_redirects=False)
        self.assertIn("t=ok", res.headers["location"])
        self.assertEqual(tx.replies_for("x")[0]["title"], "Hallo")


if __name__ == "__main__":
    unittest.main()
