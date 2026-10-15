import hashlib
import hmac
import json
import unittest

from pulse_galaxybot import (
    GALAXYBOT_WEBHOOK_PATH,
    TICKET_CLAIM_LOG_CHANNEL_ID,
    TICKET_OPEN_LOG_CHANNEL_ID,
    TICKET_SOURCE_CHANNEL_IDS,
    collect_numeric_ids,
    find_channel_ids,
    ticket_display_fields,
    verify_webhook_signature,
)


class GalaxyBotWebhookTests(unittest.TestCase):
    def test_configured_ticket_channels_and_log_destinations(self):
        self.assertEqual(GALAXYBOT_WEBHOOK_PATH, "/api/galaxybot/webhook")
        self.assertEqual(TICKET_OPEN_LOG_CHANNEL_ID, 1543914075308818503)
        self.assertEqual(TICKET_CLAIM_LOG_CHANNEL_ID, 1535025111722098778)
        self.assertEqual(len(TICKET_SOURCE_CHANNEL_IDS), 6)

    def test_signature_requires_valid_hmac_and_fresh_timestamp(self):
        secret = "whsec_test-secret"
        timestamp = "1700000000"
        body = b'{"event":"ticket.created","data":{"ticketId":"t-1"}}'
        expected = "sha256=" + hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        self.assertTrue(verify_webhook_signature(secret, timestamp, expected, body, now=1700000000))
        self.assertFalse(verify_webhook_signature(secret, timestamp, expected, body + b" ", now=1700000000))
        self.assertFalse(verify_webhook_signature(secret, timestamp, expected, body, now=1700001000))
        self.assertFalse(verify_webhook_signature("", timestamp, expected, body, now=1700000000))
        self.assertFalse(verify_webhook_signature(secret, "bad", expected, body, now=1700000000))

    def test_nested_source_ids_and_channel_ids_are_found(self):
        data = {
            "ticket": {
                "id": "ticket-1",
                "channel": {"id": "1511390378752741386"},
                "categoryID": "1488972966774706186",
            }
        }
        self.assertIn(1511390378752741386, collect_numeric_ids(data))
        self.assertIn(1488972966774706186, find_channel_ids(data))

    def test_ticket_fields_tolerate_common_galaxybot_payload_shapes(self):
        data = {
            "ticket": {
                "id": "T-123",
                "channelId": "1494338404224340179",
                "category": {"name": "Content Creator"},
                "creator": {"username": "TestUser"},
                "claimedBy": {"displayName": "Teamler"},
                "title": "Bewerbung",
            }
        }
        fields = ticket_display_fields(data)
        self.assertEqual(fields["ticket_id"], "T-123")
        self.assertEqual(fields["channel_id"], "1494338404224340179")
        self.assertEqual(fields["category"], "Content Creator")
        self.assertEqual(fields["creator"], "TestUser")
        self.assertEqual(fields["claimed_by"], "Teamler")
        self.assertEqual(fields["title"], "Bewerbung")


if __name__ == "__main__":
    unittest.main()
