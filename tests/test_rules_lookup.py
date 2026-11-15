"""Audit #47/#133: Regelwerk-Assistent mit Quellen (Parser, Suche, Route, Bot-Befehl; ohne KI, ohne Discord)."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_rules_lookup as rules
from tests.test_teamakte_pages import TEAM, Base

SAMPLE = """# Wissensbasis
Stand der Regelwerke: egal.

# A. INGAME / RP – REGELWERK
Stand: Juli 2026

## §12 Safezones
- §12.1 In Safezones sind Straftaten verboten.
- §12.2 Waffen bleiben im Holster.

# B. DISCORD – REGELWERK
Stand: September 2026

## §7 Sanktionskatalog
- §7.3 Wiederholte Verstöße können zu einem Timeout führen.

# C. Häufige Antworten
## Wo melde ich Beweise?
- Im Support-Ticket mit Video.
"""


class ParserTests(unittest.TestCase):
    def test_parse_assigns_document_stand_and_reference(self):
        items = rules.parse(SAMPLE)
        by_ref = {i["ref"]: i for i in items if i["ref"]}
        self.assertEqual((by_ref["§12.1"]["doc"], by_ref["§12.1"]["stand"]), ("ingame", "Juli 2026"))
        self.assertEqual((by_ref["§7.3"]["doc"], by_ref["§7.3"]["stand"]), ("discord", "September 2026"))
        self.assertTrue(any(i["doc"] == "faq" and "Support-Ticket" in i["text"] for i in items))

    def test_empty_or_garbage_input(self):
        self.assertEqual(rules.parse(""), [])
        self.assertEqual(rules.parse(None), [])
        self.assertEqual(rules.parse("- lose Zeile ohne Dokument"), [])


class SearchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "wissen.md"
        path.write_text(SAMPLE, encoding="utf-8")
        patcher = patch.dict("os.environ", {"SUPPORT_KNOWLEDGE_FILE": str(path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        rules._cache.clear()

    def test_results_carry_source_and_version(self):
        data = rules.search("safezones")
        self.assertEqual(len(data["results"]), 2)
        self.assertIn("Ingame/RP-Regelwerk §12.1, Stand Juli 2026", data["results"][0]["cite"])
        self.assertEqual(len(data["version"]), 8)

    def test_exact_paragraph_number_wins_and_document_filter_works(self):
        self.assertEqual(rules.search("7.3")["results"][0]["ref"], "§7.3")
        self.assertEqual(rules.search("verboten", doc="discord")["results"], [])
        self.assertEqual(len(rules.search("verboten", doc="ingame")["results"]), 1)

    def test_short_empty_and_unknown_queries_return_nothing(self):
        for q in ("", " ", "a", None, "gibtesnicht"):
            self.assertEqual(rules.search(q)["results"], [])

    def test_missing_file_is_handled(self):
        with patch.dict("os.environ", {"SUPPORT_KNOWLEDGE_FILE": "/nicht/da.md"}):
            rules._cache.clear()
            self.assertEqual(rules.search("safezone"), {"results": [], "version": "", "query": "safezone"})


class RouteAndCommandTests(Base):
    def test_page_renders_escaped_and_requires_dashboard_access(self):
        with self.as_user(TEAM):
            html = self.client.get("/regelwerk-suche?q=<script>x</script>").text
            self.assertNotIn("<script>x</script>", html)
            self.assertIn("Regelwerk-Assistent", html)
            self.assertEqual(self.client.get("/regelwerk-suche?q=safezone&doc=ingame").status_code, 200)
        with self.as_user({}):
            self.assertEqual(self.client.get("/regelwerk-suche").status_code, 403)

    def test_bot_command_answers_privately_with_source(self):
        from cogs.pulse_pro import PulsePro
        inter = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        asyncio.run(PulsePro.regelwerk.callback(None, inter, "Safezone", None))
        args, kwargs = inter.response.send_message.await_args
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("Regelwerk", args[0])
        inter2 = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
        asyncio.run(PulsePro.regelwerk.callback(None, inter2, "zzzqqq", None))
        self.assertIn("keine Stelle", inter2.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()


class SourcesTests(SearchTests):
    """#133: Quellenangabe unter KI-Antworten (lexikalisch, nachvollziehbar, begrenzt)."""

    def test_best_sources_need_at_least_two_matching_terms_and_name_the_passage(self):
        data = rules.best_sources("Darf ich in einer Safezone Waffen ziehen oder Straftaten begehen?")
        refs = [s["ref"] for s in data["sources"]]
        self.assertTrue(refs and refs[0].startswith("§12"))
        self.assertIn("Stand Juli 2026", data["sources"][0]["cite"])
        self.assertEqual(len(data["version"]), 8)
        self.assertEqual(rules.best_sources("Wie wird das Wetter morgen in Berlin?")["sources"], [])
        self.assertEqual(rules.best_sources("Safezone")["sources"], [])               # ein einzelner Begriff reicht nicht
        self.assertEqual(rules.best_sources("")["sources"], [])
        self.assertEqual(rules.best_sources(None)["sources"], [])

    def test_one_source_per_paragraph_and_a_limit(self):
        data = rules.best_sources("Safezone Waffen Straftaten Holster verboten", limit=4)
        paragraphs = [s["ref"].split(".")[0] for s in data["sources"]]
        self.assertEqual(len(paragraphs), len(set(paragraphs)))

    def test_sources_line_format_and_failure_tolerance(self):
        line = rules.sources_line("Darf ich in einer Safezone Waffen ziehen?")
        self.assertTrue(line.startswith("-# 📎 Passende Regelwerkstellen"))
        self.assertIn("keine Auslegung", line)
        self.assertEqual(rules.sources_line("Völlig anderes Thema ohne Bezug"), "")
        with patch.object(rules, "best_sources", side_effect=RuntimeError("x")):
            self.assertEqual(rules.sources_line("Safezone Waffen"), "")

    def test_assistant_answers_carry_the_source_line_within_the_discord_limit(self):
        import asyncio
        import os
        from tests import test_support_assistant_cog as cog_tests
        long_text = "\n".join(f"Schritt {i}: " + "x" * 80 for i in range(30))
        cog = cog_tests.make_cog([cog_tests.ok("Kurz: In Safezones sind Straftaten verboten." ), cog_tests.ok(long_text)])
        short = cog_tests.FakeMessage("Darf ich in einer Safezone Waffen ziehen oder Straftaten begehen?")
        asyncio.run(cog.on_message(short))
        self.assertIn("Passende Regelwerkstellen", short.replies[0])
        self.assertIn("KI-Support von Pulse", short.replies[0])
        cog._user_last_request.clear()
        longer = cog_tests.FakeMessage("Darf ich in einer Safezone Waffen ziehen oder Straftaten begehen?")
        asyncio.run(cog.on_message(longer))
        for part in [longer.replies[0], *longer.channel.sent]:
            self.assertLessEqual(len(part), 2000)
        del os
