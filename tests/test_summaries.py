"""Local ticket and application summaries (#36, #134, #135): shared logic, Discord command, dashboard pages.

No Discord connection, no external service: fake channels, temp database and temp files only.
"""
import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord
from fastapi.testclient import TestClient

import pulse_db
import pulse_paths
import pulse_summaries as ps
import pulse_ticket_extras as tx
import webserver as ws
from tests.fakes import FakeChannel, FakeGuild, FakeInteraction, FakeMember
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

REQUESTER = "100000000000000009"


def msg(author, author_id, content, minute, **extra):
    return {"author": author, "author_id": author_id, "created_at": f"2026-11-10T10:{minute:02d}:00+00:00", "content": content, **extra}


THREAD = [
    msg("Bürger", REQUESTER, "Mein Fahrzeug wurde am Bahnhof gestohlen. Das Kennzeichen lautet BO AB 123.", 0, attachment_count=2),
    msg("Pulse Bot", "1", "Dieses Bot-Menü soll nicht als Fallinhalt erscheinen?", 1, is_bot=True),
    msg("Sina Support", "200000000000000001", "Ich habe die Kameraaufnahmen am Bahnhof geprüft. Wann genau hast du das Fahrzeug abgestellt?", 2),
    msg("Bürger", REQUESTER, "Gegen 18 Uhr am Dienstag habe ich geparkt.", 3),
    msg("Sina Support", "200000000000000001", "Danke. Kannst du uns bitte noch einen Screenshot der Garage schicken?", 4),
]


class HistoryChannel(FakeChannel):
    """A fake ticket channel whose history() yields prepared messages newest first (like Discord)."""

    def __init__(self, cid, rows, error=None):
        super().__init__(cid, "ticket")
        self.rows, self.error = rows, error

    def history(self, limit=100, oldest_first=False):
        rows, error = list(reversed(self.rows))[:limit], self.error

        async def gen():
            if error:
                raise error
            for row in rows:
                yield row
        return gen()


def discord_message(author, author_id, content, minute, bot=False):
    return SimpleNamespace(author=SimpleNamespace(display_name=author, id=int(author_id), bot=bot), content=content,
                           created_at=datetime(2026, 11, 10, 10, minute, tzinfo=timezone.utc), attachments=[])


class TicketSummaryLogicTests(unittest.TestCase):
    def summary(self, messages=THREAD, **ticket):
        base = {"id": "T-1", "user_id": REQUESTER, "status": "open", "claimed_by_id": "200000000000000001", "category": "support"}
        return ps.summarize_ticket(messages, ticket={**base, **ticket}, events=[
            {"event_type": "Ticket übernommen", "actor_name": "Sina Support", "created_at": "2026-11-10T10:01:30+00:00", "details": ""},
            {"event_type": "Tag gesetzt", "actor_name": "Sina Support", "created_at": "2026-11-10T10:02:30+00:00", "details": "diebstahl"}])

    def test_all_five_parts_are_filled_from_the_thread(self):
        s = self.summary()
        self.assertEqual((s["message_count"], s["attachment_count"]), (4, 2))  # the bot message is ignored
        self.assertTrue(s["key_points"])
        # the answered question is closed, the last one is still open
        self.assertEqual([q["content"] for q in s["open_questions"]], ["Kannst du uns bitte noch einen Screenshot der Garage schicken?"])
        steps = [x["content"] for x in s["steps_done"]]
        self.assertEqual(steps[:2], ["Ticket übernommen", "Tag gesetzt: diebstahl"])
        self.assertIn("Ich habe die Kameraaufnahmen am Bahnhof geprüft.", steps)
        self.assertEqual([(p["name"], p["role"], p["messages"]) for p in s["participants"]],
                         [("Bürger", "Ersteller", 2), ("Sina Support", "Team", 2)])
        self.assertIn("Rückmeldung des Erstellers abwarten", s["next_step"]["text"])
        self.assertIn("Sina Support", s["next_step"]["reason"])
        titles = [title for title, _ in ps.ticket_sections(s)]
        self.assertEqual(titles, ["Bisheriger Sachstand", "Offene Fragen", "Bereits durchgeführte Schritte", "Beteiligte Personen",
                                  "Nächster sinnvoller Schritt"])
        self.assertNotIn("Bot-Menü", ps.ticket_summary_text(s))

    def test_next_step_follows_the_ticket_state(self):
        self.assertIn("geschlossen", self.summary(status="closed", close_reason="gelöst")["next_step"]["text"])
        self.assertIn("gelöst", self.summary(status="closed", close_reason="gelöst")["next_step"]["reason"])
        self.assertIn("noch nicht übernommen", self.summary(claimed_by_id="", claimed_by_name="")["next_step"]["text"])
        self.assertIn("Antwort an Bürger steht aus", self.summary(THREAD[:4])["next_step"]["text"])
        self.assertIn("keine auswertbaren Nachrichten", self.summary([])["next_step"]["text"])
        done = THREAD[:4] + [msg("Sina Support", "200000000000000001", "Das Fahrzeug wurde gefunden und dir wieder eingetragen.", 5)]
        self.assertIn("Sachstand prüfen", self.summary(done)["next_step"]["text"])

    def test_summary_is_deterministic_bounded_and_never_invents_text(self):
        many = [msg("Bürger", REQUESTER, f"Nachricht Nummer {i} mit einer Frage zum Fahrzeug {i}?", i % 60) for i in range(400)]
        s = ps.summarize_ticket(many, ticket={"id": "T", "user_id": REQUESTER})
        self.assertEqual(s["message_count"], ps.MAX_MESSAGES)
        self.assertLessEqual(len(s["open_questions"]), ps.MAX_LIST)
        self.assertEqual(s, ps.summarize_ticket(many, ticket={"id": "T", "user_id": REQUESTER}))
        source_text = " ".join(m["content"] for m in THREAD)
        for part in self.summary()["key_points"] + self.summary()["open_questions"]:
            self.assertIn(part["content"], source_text)
        self.assertEqual(ps.summarize_ticket(None)["message_count"], 0)
        self.assertEqual(ps.summarize_ticket(["kaputt", {"content": 5}])["message_count"], 1)

    def test_transcript_lines_are_parsed_and_markup_is_escaped(self):
        text = ("[10.11.2026 10:00:00] Bürger#0 (100000000000000009): Mein Konto ist gesperrt <script>alert(1)</script> | Anhänge: x.png (gesichert)\n"
                "zweite Zeile der Nachricht\n"
                "[10.11.2026 10:01:00] Pulse (1): [kein Text] | Embeds: Ticket\n"
                "[10.11.2026 10:02:00] Sina (200000000000000001): Warum wurdest du gesperrt?\n"
                "--- Ticket geschlossen ---\nungültige Zeile ohne Zeitstempel davor wird angehängt")
        rows = ps.parse_transcript(text)
        self.assertEqual([(r["author"], r["author_id"]) for r in rows], [("Bürger#0", REQUESTER), ("Sina", "200000000000000001")])
        self.assertIn("zweite Zeile", rows[0]["content"])
        self.assertNotIn("Anhänge", rows[0]["content"])
        self.assertEqual(ps.parse_transcript(""), [])
        html_out = ps.ticket_summary_html(ps.summarize_ticket(rows, ticket={"user_id": REQUESTER, "claimed_by_id": "2"}, source="Archiviertes Transkript"))
        self.assertNotIn("<script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)
        self.assertIn("Archiviertes Transkript", html_out)
        self.assertIn("Warum wurdest du gesperrt?", html_out)


class ApplicationSummaryTests(unittest.TestCase):
    APP = {"user_id": "987654321", "name": "Bewerber <b>Eins</b>", "status": "pending", "career_label": "Support",
           "created_at": "01.11.2026 10:00",
           "text": "Ich spiele seit drei Jahren Roleplay und möchte neuen Spielern beim Einstieg helfen. Abends bin ich fast täglich erreichbar.",
           "test_percent": 75, "test_score": 9, "test_total": 12, "test_passed": True,
           "test_details": [{"question": "Was ist Powergaming?", "correct": False}, {"question": "Was ist RDM?", "correct": True}],
           "practical_answers": [{"title": "Konflikt im Support", "answer": ""},
                                 {"title": "Regelbruch melden", "answer": "Ich dokumentiere den Vorfall."}],
           "upvotes": ["1", "2"], "downvotes": []}

    def test_answers_are_arranged_without_rating_or_recommendation(self):
        s = ps.summarize_application(self.APP)
        self.assertEqual((s["career"], s["status"], s["text_words"] > 10), ("Support", "Eingegangen", True))
        self.assertEqual(s["test"]["wrong_questions"], ["Was ist Powergaming?"])
        self.assertEqual([(p["number"], p["empty"]) for p in s["practical"]], [(1, True), (2, False)])
        self.assertIn("Praxisaufgabe 1 „Konflikt im Support“ wurde nicht beantwortet.", s["check_notes"])
        self.assertTrue(any("sehr kurz" in n for n in s["check_notes"]))
        self.assertIn("Es liegt noch keine Prüferbewertung vor.", s["check_notes"])
        for sentence in s["text_key_sentences"]:
            self.assertIn(sentence, self.APP["text"])  # quotes only
        # no decision vocabulary and no computed score anywhere
        self.assertNotIn("score_recommendation", s)
        flat = str(s).casefold()
        for word in ("empfehl", "geeignet", "ungeeignet", "annehmen", "ablehnen", "einstellen"):
            if word == "empfehl":
                self.assertEqual(flat.count(word), 1)  # only inside the notice "keine Empfehlung"
            else:
                self.assertNotIn(word, flat)
        self.assertIn("keine Bewertung, keine Empfehlung", s["notice"])

    def test_html_is_escaped_and_broken_data_does_not_crash(self):
        out = ps.application_summary_html(self.APP)
        self.assertIn("Strukturierte Zusammenfassung für Prüfer", out)
        self.assertNotIn("<b>Eins</b>", out)
        for broken in (None, {}, {"practical_answers": ["x", None], "reviews": "kaputt", "test_details": "x"}):
            self.assertIn("Hinweise zu Vollständigkeit", ps.application_summary_html(broken))


class SummaryRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.root = root
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch.object(ws, "APPS_FILE", str(root / "applications.json")),
                        patch.object(ws, "DATA_FILE", str(root / "team_data.json")),
                        patch.object(pulse_paths, "DATA_DIR", root)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        self.tid = pulse_db.create_ticket("555", str(ws.GUILD_ID), REQUESTER, "Bürger", "support")
        self.channel = HistoryChannel(555, [discord_message(m["author"], m["author_id"], m["content"], i, bot=bool(m.get("is_bot")))
                                            for i, m in enumerate(THREAD)])
        guild = FakeGuild(gid=ws.GUILD_ID, members=[], roles=[], channels=[self.channel])
        ws.app.state.bot = SimpleNamespace(get_guild=lambda _gid: guild)
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        ws.app.state.bot = None
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def get(self, path, perms):
        with patch.object(ws, "auth", fake_auth_for(perms)):
            return self.client.get(path)

    def test_open_ticket_is_summarised_from_the_channel_for_ticket_staff_only(self):
        staff = dict(TEAMLER, can_manage_tickets=True)
        page = self.get(f"/ticket/{self.tid}/summary", staff)
        self.assertEqual(page.status_code, 200, page.text[-500:])
        for text in ("Bisheriger Sachstand", "Offene Fragen", "Screenshot der Garage", "Nächster sinnvoller Schritt",
                     "Bürger (Ersteller, 2 Nachrichten)", "Quelle: Discord-Verlauf", "keine externe KI"):
            self.assertIn(text, page.text)
        self.assertNotIn("Bot-Menü", page.text)
        self.assertIn(f"/ticket/{self.tid}/summary", self.get(f"/ticket/{self.tid}", staff).text)
        without = dict(TEAMLER, can_manage_tickets=False, can_promote=True)
        self.assertEqual(self.get(f"/ticket/{self.tid}/summary", without).status_code, 403)
        self.assertEqual(self.get("/ticket/gibt-es-nicht/summary", ADMIN).status_code, 404)

    def test_closed_ticket_uses_the_transcript_and_errors_are_explained(self):
        (self.root / "transcripts").mkdir()
        (self.root / "transcripts" / f"{self.tid}.txt").write_text(
            "[10.11.2026 10:00:00] Bürger (100000000000000009): Mein Fahrzeug wurde am Bahnhof gestohlen und ist weg.\n"
            "[10.11.2026 10:05:00] Sina (200000000000000001): Das Fahrzeug wurde wiederhergestellt und dir eingetragen.\n", encoding="utf-8")
        pulse_db.close_ticket(self.tid, "gelöst", f"{self.tid}.txt")
        page = self.get(f"/ticket/{self.tid}/summary", ADMIN)
        self.assertEqual(page.status_code, 200)
        for text in ("Quelle: Archiviertes Transkript", "Ticket ist geschlossen", "wiederhergestellt"):
            self.assertIn(text, page.text)
        # unreadable history of an open ticket: a clear message instead of an error page
        other = pulse_db.create_ticket("556", str(ws.GUILD_ID), REQUESTER, "Bürger", "support")
        broken = HistoryChannel(556, [], error=discord.HTTPException(SimpleNamespace(status=503, reason="x"), "kaputt"))
        guild = FakeGuild(gid=ws.GUILD_ID, members=[], roles=[], channels=[broken])
        ws.app.state.bot = SimpleNamespace(get_guild=lambda _gid: guild)
        page = self.get(f"/ticket/{other}/summary", ADMIN)
        self.assertEqual(page.status_code, 200)
        self.assertIn("konnte nicht gelesen werden", page.text)

    def test_application_overview_is_for_reviewers_only(self):
        ws.save_json(ws.APPS_FILE, {"app_1": dict(ApplicationSummaryTests.APP)})
        plain = self.get("/applications", TEAMLER)
        self.assertEqual(plain.status_code, 200, plain.text[-500:])
        self.assertNotIn("Strukturierte Zusammenfassung für Prüfer", plain.text)
        self.assertNotIn("Was ist Powergaming?", plain.text)
        reviewer = self.get("/applications", dict(TEAMLER, can_manage_applications=True))
        self.assertEqual(reviewer.status_code, 200, reviewer.text[-500:])
        for text in ("Strukturierte Zusammenfassung für Prüfer", "wurde nicht beantwortet", "keine Bewertung, keine Empfehlung"):
            self.assertIn(text, reviewer.text)


class TicketCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.patch.start()
        pulse_db.init_db()
        self.tid = pulse_db.create_ticket("555", "1", REQUESTER, "Bürger", "support")

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def run_command(self, channel, speichern=False, allowed=True):
        from cogs import ticket_tools
        cog = ticket_tools.TicketTools(SimpleNamespace())
        interaction = FakeInteraction(FakeMember(200000000000000001, "Sina Support"), FakeGuild(gid=1), channel)
        sent = []

        async def followup(content=None, ephemeral=False, **kwargs):
            sent.append({"content": content, "ephemeral": ephemeral, **kwargs})
        interaction.followup = SimpleNamespace(send=followup)
        with patch.object(ticket_tools, "may_manage", lambda _i: allowed):
            asyncio.run(ticket_tools.TicketTools.zusammenfassung.callback(cog, interaction, speichern))
        return interaction, sent

    def test_command_answers_privately_with_all_sections_and_real_line_breaks(self):
        channel = HistoryChannel(555, [discord_message(m["author"], m["author_id"], m["content"], i, bot=bool(m.get("is_bot")))
                                       for i, m in enumerate(THREAD)])
        _, [answer] = self.run_command(channel, speichern=True)
        self.assertTrue(answer["ephemeral"])
        embed = answer["embed"]
        self.assertIn("Bisheriger Sachstand", embed.description)
        self.assertEqual([f.name for f in embed.fields], ["Offene Fragen", "Bereits durchgeführte Schritte", "Beteiligte Personen",
                                                          "Nächster sinnvoller Schritt", "Umfang"])
        everything = embed.description + "".join(f.value for f in embed.fields)
        self.assertNotIn("\\n", everything)  # regression: doubled escapes used to show a literal backslash-n
        self.assertIn("\n", embed.description)
        self.assertIn("Screenshot der Garage", embed.fields[0].value)
        [note] = tx.internal_notes_for(self.tid, limit=5)
        self.assertIn("Nächster sinnvoller Schritt:", note["note"])
        self.assertNotIn("\\n", note["note"])

    def test_command_refuses_without_rights_and_reports_unreadable_history(self):
        channel = HistoryChannel(555, [discord_message("Bürger", REQUESTER, "Hallo, ich brauche Hilfe mit meinem Konto.", 1)])
        interaction, sent = self.run_command(channel, allowed=False)
        self.assertEqual(sent, [])
        self.assertIn("Nur das Ticket-Team", interaction.texts()[0])
        broken = HistoryChannel(555, [], error=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "nein"))
        _, [answer] = self.run_command(broken)
        self.assertIn("konnte nicht gelesen werden", answer["content"])
        self.assertEqual(tx.internal_notes_for(self.tid, limit=5), [])

    def test_no_doubled_line_break_escapes_remain_in_the_cog(self):
        source = (Path(__file__).resolve().parents[1] / "cogs" / "ticket_tools.py").read_text(encoding="utf-8")
        self.assertNotIn(chr(92) * 2 + "n", source)


if __name__ == "__main__":
    unittest.main()
