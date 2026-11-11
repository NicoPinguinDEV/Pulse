"""Locale coverage for Discord ticket-tool replies and embeds."""
import unittest

import pulse_i18n


class TicketToolLocaleTests(unittest.TestCase):
    def test_stable_ticket_responses_are_localized(self):
        examples = {
            "Nur das Ticket-Team kann das nutzen.": "Only the ticket team can use this.",
            "Bitte in einem offenen Ticket-Kanal verwenden.": "Please use this command in an open ticket channel.",
            "Für dieses Ticket gibt es keine Verknüpfungen auf dieser Seite.": "There are no linked tickets for this ticket on this page.",
            "Der Ticketverlauf konnte nicht gelesen werden. Bitte die Kanal-Berechtigungen prüfen.": "The ticket history could not be read. Please check channel permissions.",
            "Diese Schnellantwort gibt es für die Kategorie nicht (Dashboard → Ticket-Werkzeuge).": "This quick reply is not available for this category (Dashboard → Ticket tools).",
            "Übergeben geht nur an Teammitglieder mit Ticket-Recht.": "Tickets can only be handed over to team members with ticket permissions.",
        }
        for source, expected in examples.items():
            with self.subTest(source=source):
                self.assertEqual(pulse_i18n.translate_ticket_message("en", source), expected)

    def test_german_locale_preserves_existing_copy_and_unknown_backend_errors(self):
        message = "Das Ticket ist nicht mehr offen oder der Kontext stimmt nicht."
        self.assertEqual(pulse_i18n.translate_ticket_message("de", message), message)
        backend_message = "Ticketverknüpfung ist wegen eines Datenbankfehlers nicht möglich."
        self.assertEqual(pulse_i18n.translate_ticket_message("en", backend_message), backend_message)


if __name__ == "__main__":
    unittest.main()
