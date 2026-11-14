"""Audit #118/#119: inbox delivery contract and direct links."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_inbox_contract as ic
import pulse_notify_prefs as prefs

U1, U2 = "100000000000000001", "100000000000000002"


class LinkTests(unittest.TestCase):
    def test_case_ref_link_is_canonical_and_encoded(self):
        self.assertEqual(ic.link_for(case_ref="bm:mod-1a2b3c"), "/mod-cases/verlauf?ref=BM%3AMOD-1A2B3C")

    def test_ticket_link_and_fallbacks(self):
        self.assertEqual(ic.link_for(domain="ticket", target_id="ticket_ab12"), "/tickets/transcript/ticket_ab12/view")
        self.assertEqual(ic.link_for(domain="ticket", target_id="../x"), "/pulse-inbox")
        self.assertEqual(ic.link_for(domain="task", target_id="t1"), "/pulse-inbox")
        self.assertEqual(ic.link_for(case_ref="garbage"), "/pulse-inbox")
        self.assertEqual(ic.link_for(), "/pulse-inbox")

    def test_safe_path_rejects_external_and_tricky_values(self):
        for bad in ("https://evil.example", "//evil.example", "javascript:alert(1)", "/a\\b", "/a\nb", "", None, "/" + "a" * 400):
            self.assertFalse(ic.is_safe_path(bad), bad)
        self.assertTrue(ic.is_safe_path("/pulse-inbox"))

    def test_absolute_link(self):
        self.assertEqual(ic.absolute_link("/pulse-inbox", "https://pulse.example/"), "https://pulse.example/pulse-inbox")
        self.assertEqual(ic.absolute_link("//evil", "https://pulse.example"), "")
        self.assertEqual(ic.absolute_link("/x", ""), "")
        self.assertEqual(ic.absolute_link("/x", "javascript:alert(1)"), "")
        self.assertEqual(ic.absolute_link("/x", "https://a.example/path"), "")


class CategoryTests(unittest.TestCase):
    def test_every_domain_maps_to_a_known_category(self):
        for domain in ic.events.DOMAINS:
            self.assertIn(ic.category_for_domain(domain), set(prefs.CATEGORIES) | {prefs.SYSTEM}, domain)
        self.assertEqual(set(ic.DOMAIN_CATEGORY), set(ic.events.DOMAINS))

    def test_unknown_domain_rejected(self):
        with self.assertRaises(ValueError):
            ic.category_for_domain("nope")

    def test_explicit_category_beats_keyword_guess(self):
        # Title/URL contain "ticket", but the explicit category is "work": muting "tickets" must not hide it.
        muted = {"muted": ["tickets"], "dm": False}
        self.assertFalse(prefs.decide(U1, "/x", "Ticket-Aufgabe", prefs=muted)["unread"])
        self.assertTrue(prefs.decide(U1, "/x", "Ticket-Aufgabe", prefs=muted, category="work")["unread"])
        self.assertEqual(prefs.decide(U1, "/ticket", "t", prefs=muted, category="bogus")["category"], "tickets")


class DeliverTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake(uid, title, body, **kw):
            self.calls.append((uid, title, body, kw))
            return f"n_{uid[-1]}"
        self.fake = fake

    def test_delivery_uses_domain_category_link_and_dedupe(self):
        sent = ic.deliver([U1, U2, U1], "moderation", "created", "Neuer Fall", "Details", case_ref="BM:MOD-1A2B3C",
                          notifier=self.fake)
        self.assertEqual(sent, {U1: "n_1", U2: "n_2"})
        self.assertEqual(len(self.calls), 2)  # duplicate recipient collapsed
        kw = self.calls[0][3]
        self.assertEqual(kw["category"], "team")
        self.assertEqual(kw["url"], "/mod-cases/verlauf?ref=BM%3AMOD-1A2B3C")
        self.assertEqual(kw["dedupe_key"], "moderation.created:BM:MOD-1A2B3C")

    def test_invalid_input_sends_nothing(self):
        bad = [
            dict(user_ids=[], domain="ticket", action="x", title="t"),
            dict(user_ids=["abc"], domain="ticket", action="x", title="t"),
            dict(user_ids=[U1], domain="nope", action="x", title="t"),
            dict(user_ids=[U1], domain="ticket", action="X!", title="t"),
            dict(user_ids=[U1], domain="ticket", action="x", title="  "),
            dict(user_ids=[U1], domain="ticket", action="x", title="t", case_ref="garbage"),
            dict(user_ids=[str(10**9 + i) for i in range(101)], domain="ticket", action="x", title="t"),
        ]
        for kwargs in bad:
            with self.subTest(kwargs=str(kwargs)[:60]), self.assertRaises(ValueError):
                ic.deliver(notifier=self.fake, **kwargs)
        self.assertEqual(self.calls, [])

    def test_one_failing_recipient_does_not_stop_the_rest(self):
        def flaky(uid, *a, **kw):
            if uid == U1:
                raise RuntimeError("kaputt")
            return "ok"
        with self.assertLogs("pulse.inbox", level="WARNING"):
            sent = ic.deliver([U1, U2], "task", "created", "Aufgabe", notifier=flaky)
        self.assertEqual(sent, {U2: "ok"})

    def test_text_is_bounded(self):
        ic.deliver([U1], "task", "created", "t" * 500, "b" * 5000, notifier=self.fake)
        self.assertEqual(len(self.calls[0][1]), ic.MAX_TITLE)
        self.assertEqual(len(self.calls[0][2]), ic.MAX_BODY)


class DbIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "pulse.db")
        self.patch = patch.object(pulse_db, "DB_PATH", self.db_path)
        self.patch.start()
        pulse_db._SCHEMA_READY.discard(self.db_path)
        pulse_db.init_db()

    def tearDown(self):
        pulse_db._SCHEMA_READY.discard(self.db_path)
        self.patch.stop()
        self.tmp.cleanup()

    def test_end_to_end_with_real_notify_dedupe_and_mute(self):
        prefs.save(U2, {"muted": ["work"], "dm": False})
        first = ic.deliver([U1, U2], "task", "created", "Ticket-Nachfassen", "x", correlation_id="flow-1")
        again = ic.deliver([U1], "task", "created", "Ticket-Nachfassen", "x", correlation_id="flow-1")
        self.assertEqual(first[U1], again[U1])  # deduplicated
        self.assertEqual(len(pulse_db.notifications(U1)), 1)
        self.assertEqual(pulse_db.unread_count(U1), 1)
        self.assertEqual(pulse_db.unread_count(U2), 0)  # muted category "work" -> stored as read

    def test_legacy_notify_signature_still_works(self):
        nid = pulse_db.notify(U1, "Ticket offen", "b", url="/tickets")
        self.assertTrue(nid.startswith("notif_"))
        self.assertEqual(pulse_db.notifications(U1)[0]["url"], "/tickets")


if __name__ == "__main__":
    unittest.main()
