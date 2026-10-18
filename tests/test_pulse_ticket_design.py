import unittest
from datetime import datetime, timedelta, timezone

from pulse_ticket_design import (
    automation_actions,
    automation_settings,
    clean_questions,
    detect_image_type,
    normalize_panel,
    render_placeholders,
    welcome_texts,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def iso(delta):
    return (NOW - delta).isoformat()


class PanelTests(unittest.TestCase):
    def test_normalize_rejects_unsafe_values(self):
        panel = normalize_panel({
            "color": "red", "thumbnail_url": "javascript:alert(1)", "banner_url": "http://x.de/a.png",
            "thumbnail_file": "../../.env", "component_style": "evil", "category_keys": "support",
        })
        self.assertEqual(panel["color"], "#5865F2")
        self.assertEqual(panel["thumbnail_url"], "")
        self.assertEqual(panel["banner_url"], "")  # only https
        self.assertEqual(panel["thumbnail_file"], "")
        self.assertEqual(panel["component_style"], "select")
        self.assertEqual(panel["category_keys"], [])

    def test_normalize_keeps_valid_values_and_defaults(self):
        panel = normalize_panel({"color": "#ff0000", "banner_file": "panel_ab_banner_1234.png", "component_style": "buttons"})
        self.assertEqual(panel["color"], "#FF0000")
        self.assertEqual(panel["banner_file"], "panel_ab_banner_1234.png")
        self.assertEqual(panel["component_style"], "buttons")
        self.assertTrue(panel["use_server_icon"])

    def test_welcome_images_are_sanitised_like_panel_images(self):
        panel = normalize_panel({
            "welcome_banner_file": "panel_x_welcome_banner_ab12.gif", "welcome_thumbnail_file": "../x.png",
            "welcome_banner_url": "ftp://x", "welcome_thumbnail_url": "https://cdn.example/t.png",
        })
        self.assertEqual(panel["welcome_banner_file"], "panel_x_welcome_banner_ab12.gif")
        self.assertEqual(panel["welcome_thumbnail_file"], "")
        self.assertEqual(panel["welcome_banner_url"], "")
        self.assertEqual(panel["welcome_thumbnail_url"], "https://cdn.example/t.png")
        self.assertTrue(panel["welcome_use_avatar"])

    def test_questions_are_limited_to_five_and_need_a_label(self):
        raw = [{"label": f"Frage {i}"} for i in range(8)] + [{"label": "  "}]
        questions = clean_questions(raw)
        self.assertEqual(len(questions), 5)
        self.assertTrue(all(q["required"] for q in questions))
        self.assertEqual(clean_questions("kaputt"), [])

    def test_welcome_priority_category_then_panel_then_default(self):
        panel = {"welcome_title": "Panel-Titel", "welcome_message": "Panel-Text"}
        self.assertEqual(welcome_texts(panel, {"welcome_title": "Kat", "opening_message": "Kat-Text"}), ("Kat", "Kat-Text"))
        self.assertEqual(welcome_texts(panel, {"name": "Bug", "emoji": "⚙️"}), ("Panel-Titel", "Panel-Text"))
        title, message = welcome_texts({}, {"name": "Bug", "emoji": "⚙️"})
        self.assertEqual(title, "⚙️ Bug")
        self.assertIn("{user}", message)

    def test_placeholders(self):
        text = render_placeholders("Hi {user} ({user_name}) auf {server}: {category} {ticket_id}",
                                   user_mention="<@1>", user_name="Max", server="Bochum", category="Bug", ticket_id="t1")
        self.assertEqual(text, "Hi <@1> (Max) auf Bochum: Bug t1")

    def test_image_signatures(self):
        self.assertEqual(detect_image_type(b"\x89PNG\r\n\x1a\n...."), "png")
        self.assertEqual(detect_image_type(b"\xff\xd8\xff\xe0"), "jpg")
        self.assertEqual(detect_image_type(b"GIF89a..."), "gif")
        self.assertEqual(detect_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "webp")
        self.assertIsNone(detect_image_type(b"<svg onload=alert(1)>"))


class AutomationTests(unittest.TestCase):
    SETTINGS = automation_settings({"unclaimed_ping_minutes": 30, "staff_reply_hours": 12,
                                    "user_reminder_hours": 24, "auto_close_hours": 24})

    def test_settings_are_clamped(self):
        settings = automation_settings({"unclaimed_ping_minutes": -5, "user_reminder_hours": "x"})
        self.assertEqual(settings["unclaimed_ping_minutes"], 0)
        self.assertEqual(settings["user_reminder_hours"], 24)

    def test_unclaimed_ping_once(self):
        ticket = {"status": "open", "opened_at": iso(timedelta(minutes=45))}
        self.assertIn("remind_unclaimed", automation_actions(ticket, self.SETTINGS, NOW))
        ticket["automation_state"] = '{"unclaimed_ping": "x"}'
        self.assertNotIn("remind_unclaimed", automation_actions(ticket, self.SETTINGS, NOW))
        claimed = {"status": "in_progress", "claimed_by_id": "5", "opened_at": iso(timedelta(hours=3))}
        self.assertNotIn("remind_unclaimed", automation_actions(claimed, self.SETTINGS, NOW))

    def test_user_reminder_then_auto_close(self):
        staff_at = NOW - timedelta(hours=30)
        ticket = {"status": "in_progress", "claimed_by_id": "5", "opened_at": iso(timedelta(days=2)),
                  "last_user_message_at": iso(timedelta(hours=40)), "last_staff_message_at": staff_at.isoformat()}
        self.assertEqual(automation_actions(ticket, self.SETTINGS, NOW), ["remind_user"])
        ticket["automation_state"] = {"unclaimed_ping": "x", "user_reminder_for": staff_at.isoformat(),
                                      "user_reminded_at": iso(timedelta(hours=25))}
        self.assertEqual(automation_actions(ticket, self.SETTINGS, NOW), ["auto_close"])

    def test_staff_reminder_when_creator_waits(self):
        ticket = {"status": "in_progress", "claimed_by_id": "5", "opened_at": iso(timedelta(days=1)),
                  "last_staff_message_at": iso(timedelta(hours=20)), "last_user_message_at": iso(timedelta(hours=13))}
        self.assertEqual(automation_actions(ticket, self.SETTINGS, NOW), ["remind_staff"])

    def test_closed_tickets_and_disabled_rules_do_nothing(self):
        self.assertEqual(automation_actions({"status": "closed"}, self.SETTINGS, NOW), [])
        off = automation_settings({k: 0 for k in self.SETTINGS})
        ticket = {"status": "open", "opened_at": iso(timedelta(days=3))}
        self.assertEqual(automation_actions(ticket, off, NOW), [])


if __name__ == "__main__":
    unittest.main()
