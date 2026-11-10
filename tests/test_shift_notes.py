"""Audit #90: private, shift-bound notes and their data lifecycle."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import webserver as ws
from tests.test_pulse_admin_area import GUILD, TEAMLER, fake_auth_for


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


def fake_interaction(user_id=7, guild_id=1, name="Max"):
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild_id),
        user=SimpleNamespace(id=user_id, display_name=name, name=name),
        response=FakeResponse(),
    )


class ShiftNoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = str(Path(self.tmp.name) / "pulse.db")
        self.db_patch = patch.object(pulse_db, "DB_PATH", self.db_path)
        self.db_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_notes_are_validated_and_limited_per_shift(self):
        with self.assertRaises(ValueError):
            pulse_db.create_shift_note("shift-1", "1", "7", "Max", "   ")
        with self.assertRaises(ValueError):
            pulse_db.create_shift_note("shift-1", "1", "7", "Max", "x" * 1001)
        for index in range(50):
            pulse_db.create_shift_note("shift-1", "1", "7", "Max", f"Notiz {index}")
        with self.assertRaisesRegex(ValueError, "50 Notizen"):
            pulse_db.create_shift_note("shift-1", "1", "7", "Max", "Noch eine")
        self.assertEqual(len(pulse_db.list_shift_notes("1", "7", limit=500)), 50)
        self.assertEqual(len(pulse_db.list_shift_notes("1", "7", limit=1)), 1)

    def test_notes_are_scoped_to_guild_and_member(self):
        pulse_db.create_shift_note("shift-1", "1", "7", "Max", "Nur für Max")
        pulse_db.create_shift_note("shift-2", "1", "8", "Mia", "Nur für Mia")
        pulse_db.create_shift_note("shift-3", "2", "7", "Max", "Anderer Server")
        self.assertEqual([row["note"] for row in pulse_db.list_shift_notes("1", "7")], ["Nur für Max"])
        self.assertEqual([row["note"] for row in pulse_db.list_shift_notes("1", "8")], ["Nur für Mia"])
        self.assertEqual(pulse_db.list_shift_notes("2", "7")[0]["note"], "Anderer Server")

    def test_json_backup_restore_contains_shift_notes(self):
        pulse_db.create_shift_note("shift-1", "1", "7", "Max", "Backup-Test")
        state = pulse_db.export_state()
        self.assertEqual(state["shift_notes"][0]["note"], "Backup-Test")
        with pulse_db.connect() as db:
            db.execute("DELETE FROM shift_notes")
        pulse_db.import_state(state)
        restored = pulse_db.list_shift_notes("1", "7")
        self.assertEqual([row["note"] for row in restored], ["Backup-Test"])

    def test_personal_data_export_finds_shift_notes_by_user_id(self):
        import pulse_retention
        pulse_db.create_shift_note("shift-1", "1", "7", "Max", "Export-Test")
        rows, truncated = pulse_retention._rows_for(self.db_path, "7")
        self.assertEqual(truncated, [])
        self.assertEqual(rows["shift_notes"][0]["note"], "Export-Test")


class ShiftNoteCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_command_requires_a_current_shift_and_saves_privately(self):
        from cogs import dienst
        interaction = fake_interaction()
        active_shift = {
            "7": {
                "started_at_iso": "2026-11-10T18:00:00+01:00",
                "shift_event_id": "shift-active-7",
            }
        }
        with (
            patch.object(dienst, "may_use_duty", return_value=True),
            patch("pulse_shift_history.load_member_view", return_value={"active_shifts": active_shift}),
            patch("webserver.log_audit"),
        ):
            asyncio.run(dienst.run_shift_note(interaction, "Bearbeitete Supportfälle"))
        self.assertTrue(interaction.response.sent[0][1]["ephemeral"])
        rows = pulse_db.list_shift_notes("1", "7")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["shift_event_id"], "shift-active-7")
        self.assertEqual(rows[0]["note"], "Bearbeitete Supportfälle")

    def test_command_rejects_notes_without_an_active_shift(self):
        from cogs import dienst
        interaction = fake_interaction()
        with (
            patch.object(dienst, "may_use_duty", return_value=True),
            patch("pulse_shift_history.load_member_view", return_value={"active_shifts": {}}),
        ):
            asyncio.run(dienst.run_shift_note(interaction, "Ohne Dienst"))
        self.assertIn("nicht im Dienst", interaction.response.sent[0][0])
        self.assertEqual(pulse_db.list_shift_notes("1", "7"), [])

    def test_regular_member_cannot_read_another_members_notes(self):
        from cogs import dienst
        interaction = fake_interaction(user_id=7)
        target = SimpleNamespace(id=8, display_name="Mia", name="Mia")
        with (
            patch.object(dienst, "may_use_duty", return_value=True),
            patch("webserver.load_config", return_value={}),
            patch("webserver.compute_perms", return_value=({"can_promote": False, "is_admin": False}, None)),
        ):
            asyncio.run(dienst.run_shift_notes(interaction, target))
        self.assertIn("nur für die Leitung", interaction.response.sent[0][0])
        self.assertTrue(interaction.response.sent[0][1]["ephemeral"])

    def test_leadership_can_read_another_members_notes_privately(self):
        from cogs import dienst
        pulse_db.create_shift_note("shift-8", "1", "8", "Mia", "Interne Übergabe erledigt")
        interaction = fake_interaction(user_id=7)
        target = SimpleNamespace(id=8, display_name="Mia", name="Mia")
        with (
            patch.object(dienst, "may_use_duty", return_value=True),
            patch("webserver.load_config", return_value={}),
            patch("webserver.compute_perms", return_value=({"can_promote": True, "is_admin": False}, None)),
        ):
            asyncio.run(dienst.run_shift_notes(interaction, target))
        message, kwargs = interaction.response.sent[0]
        self.assertIn("Interne Übergabe erledigt", message)
        self.assertTrue(kwargs["ephemeral"])


class ShiftNoteDashboardRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
            patch.object(ws, "SHIFTS_FILE", str(base / "shifts.json")),
            patch.object(ws, "DATA_FILE", str(base / "team_data.json")),
            patch.object(ws, "AUDIT_FILE", str(base / "audit.json")),
            patch.object(ws, "log_audit", lambda *args, **kwargs: None),
        ]
        for item in self.patches:
            item.start()
        pulse_db.init_db()
        ws.save_json(ws.SHIFTS_FILE, {
            "active_shifts": {
                "123": {
                    "started_at_iso": "2026-11-10T18:00:00+01:00",
                    "status": "online",
                }
            },
            "history": [],
        })
        self.client = TestClient(ws.app)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_dashboard_form_saves_only_for_the_signed_in_active_member(self):
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            response = self.client.post(
                "/shift/notes", data={"note": "Dashboard-Übergabe abgeschlossen"},
                follow_redirects=False,
            )
        self.assertIn(response.status_code, (302, 303))
        notes = pulse_db.list_shift_notes(GUILD.id, "123")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["note"], "Dashboard-Übergabe abgeschlossen")

    def test_dashboard_form_rejects_a_member_without_an_active_shift(self):
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": []})
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            response = self.client.post(
                "/shift/notes", data={"note": "Ohne laufende Schicht"},
                follow_redirects=False,
            )
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(pulse_db.list_shift_notes(GUILD.id, "123"), [])


if __name__ == "__main__":
    unittest.main()
