"""BOT-06: HTML ticket transcripts display stored messages as escaped text."""
import unittest

from pulse_transcripts import render_html_transcript


class HtmlTranscriptTests(unittest.TestCase):
    def test_messages_are_escaped_and_not_executed_as_html(self):
        rendered = render_html_transcript(
            "T-42",
            "[21.10.2026 17:00:00] Bürger (123): <script>alert('x')</script> & \"test\"",
        )
        self.assertIn("<!doctype html>", rendered)
        self.assertIn("Ticket T-42", rendered)
        self.assertIn("&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;", rendered)
        self.assertIn("&amp;", rendered)
        self.assertNotIn("<script>alert", rendered)

    def test_ticket_id_is_escaped_too(self):
        rendered = render_html_transcript("<img src=x onerror=alert(1)>", "")
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", rendered)
        self.assertNotIn("<img src=x", rendered)
        self.assertIn("(Transkript ist leer.)", rendered)


if __name__ == "__main__":
    unittest.main()
