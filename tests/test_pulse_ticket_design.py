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
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (Phase 9: no ./pulse.db)


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


class PanelLoadTests(unittest.TestCase):
    def test_load_line_like_galaxybot(self):
        from pulse_ticket_design import load_line
        self.assertEqual(load_line("Immobilie Beantragen", "🏠", 3, 5), "- 🟡 🏠 **Immobilie Beantragen:** Verfügbar `3/5` (60 %)")
        self.assertEqual(load_line("Verkauf", "💰", 0, 5), "- 🟢 💰 **Verkauf:** Verfügbar `0/5` (0 %)")
        self.assertEqual(load_line("Bug", "⚙️", 5, 5), "- 🔴 ⚙️ **Bug:** Ausgelastet `5/5` (100 %)")
        self.assertEqual(load_line("Bug", "⚙️", 7, 5), "- 🔴 ⚙️ **Bug:** Ausgelastet `7/5` (100 %)")
        self.assertEqual(load_line("Frei", "🎫", 2, 0), "- 🟢 🎫 **Frei:** `2` offen")

    def test_category_block(self):
        from pulse_ticket_design import category_block
        entries = [("Support", "🎫", 1, 10)]
        self.assertIn("### 📊 Ticket-Auslastung", category_block(entries, show_load=True))
        self.assertIn("Verfügbar `1/10` (10 %)", category_block(entries, show_load=True))
        self.assertEqual(category_block(entries, show_load=False), "### 📂 Verfügbare Kategorien\n- 🎫 **Support**")
        self.assertEqual(category_block(entries, show_load=False, list_categories=False), "")
        self.assertEqual(category_block([], show_load=True), "")


class ChannelNameTests(unittest.TestCase):
    def test_example_from_the_server(self):
        from pulse_ticket_design import ticket_channel_name
        self.assertEqual(ticket_channel_name("🤝", "Partnerschaft", "maxim789583574247784466788"),
                         "『🤝』𝐏𝐚𝐫𝐭𝐧𝐞𝐫𝐬𝐜𝐡𝐚𝐟𝐭-𝐓𝐢𝐜𝐤𝐞𝐭-maxim789583574247784466788")

    def test_no_double_ticket_umlauts_and_clean_user(self):
        from pulse_ticket_design import ticket_channel_name
        self.assertEqual(ticket_channel_name("💢", "Highteam Ticket", "Dawo.Jaso"), "『💢』𝐇𝐢𝐠𝐡𝐭𝐞𝐚𝐦-𝐓𝐢𝐜𝐤𝐞𝐭-dawojaso")
        self.assertEqual(ticket_channel_name("🐬", "Führungsebene", "x"), "『🐬』𝐅ü𝐡𝐫𝐮𝐧𝐠𝐬𝐞𝐛𝐞𝐧𝐞-𝐓𝐢𝐜𝐤𝐞𝐭-x")
        self.assertEqual(ticket_channel_name("🎫", "Support", "!!!"), "『🎫』𝐒𝐮𝐩𝐩𝐨𝐫𝐭-𝐓𝐢𝐜𝐤𝐞𝐭-user")

    def test_custom_emoji_falls_back_and_limit_is_respected(self):
        from pulse_ticket_design import _utf16_len, ticket_channel_name
        self.assertTrue(ticket_channel_name("<:ticket:1360768000235409559>", "Support", "a").startswith("『🎫』"))
        long_name = ticket_channel_name("🏠", "Sehr langer Kategoriename für Immobilien Beantragungen", "u" * 40, "-r1a2b")
        self.assertLessEqual(_utf16_len(long_name), 100)
        self.assertTrue(long_name.endswith("-r1a2b"))


class PanelLayoutTests(unittest.TestCase):
    def test_modern_panel_structure(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from cogs import tickets
        panel = {"id": "immo", "title": "🏠 IMMOBILIEN-CENTER", "description": "Willkommen!", "show_load": True,
                 "category_keys": ["support", "bug"], "banner_url": "https://example.com/b.png", "footer": "Immobilienverwaltung"}
        with patch.object(tickets, "category_open_count", side_effect=lambda key: {"support": 3, "bug": 5}[key]):
            layout = tickets.build_panel_layout(SimpleNamespace(icon=None), panel, tickets.panel_view(panel))
        components = layout.to_components()
        self.assertEqual([c["type"] for c in components], [17, 1])  # card, then the menu below it
        inner = components[0]["components"]
        self.assertEqual([c["type"] for c in inner], [10, 14, 10, 12, 14, 10])
        self.assertTrue(inner[0]["content"].startswith("## 🏠 IMMOBILIEN-CENTER"))
        self.assertIn("Verfügbar `3/10` (30 %)", inner[2]["content"])
        self.assertIn("Ausgelastet `5/5` (100 %)", inner[2]["content"])
        self.assertEqual(inner[-1]["content"], "-# Immobilienverwaltung")  # no timestamp on a pinned panel
        self.assertTrue(layout.is_persistent())

    def test_classic_panel_uses_same_load_lines(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from cogs import tickets
        panel = {"id": "immo", "title": "T", "show_load": True, "category_keys": ["support"]}
        with patch.object(tickets, "category_open_count", return_value=4):
            embed = tickets.build_panel_embed(SimpleNamespace(icon=None), panel)
        self.assertEqual(embed.fields[0].name, "📊 Ticket-Auslastung")
        self.assertIn("Verfügbar `4/10` (40 %)", embed.fields[0].value)


if __name__ == "__main__":
    unittest.main()
