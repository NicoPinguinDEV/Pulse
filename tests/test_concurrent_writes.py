"""K-04: overlapping actions do not lose warnings, notes or support cases."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import webserver as ws
from pulse_store import merge_entry


class MergeTests(unittest.TestCase):
    def test_concurrent_warning_survives_stale_snapshot(self):
        before = {"warns_list": [{"id": "w1", "active": True}], "notes": [], "rank": "A"}
        ours = {"warns_list": [{"id": "w1", "active": True}, {"id": "w2", "active": True}], "notes": [], "rank": "A"}
        current = {"warns_list": [{"id": "w1", "active": True}, {"id": "w9", "active": True}],
                   "notes": [{"text": "Notiz"}], "rank": "B"}
        merged = merge_entry(before, ours, current)
        self.assertEqual([w["id"] for w in merged["warns_list"]], ["w1", "w9", "w2"])
        self.assertEqual(merged["notes"], [{"text": "Notiz"}])  # not touched by us → current wins
        self.assertEqual(merged["rank"], "B")

    def test_our_change_to_existing_warning_and_removal(self):
        before = {"warns_list": [{"id": "w1", "active": True}, {"id": "w2", "active": True}]}
        ours = {"warns_list": [{"id": "w1", "active": False}]}
        current = {"warns_list": [{"id": "w1", "active": True}, {"id": "w2", "active": True}, {"id": "w3"}]}
        merged = merge_entry(before, ours, current)
        self.assertEqual(merged["warns_list"], [{"id": "w1", "active": False}, {"id": "w3"}])

    def test_revocation_meanwhile_survives_our_consequence_update(self):
        before = {"warns_list": [{"id": "w1", "active": True}]}
        ours = {"warns_list": [{"id": "w1", "active": True, "consequences": {"role": 1}}]}
        current = {"warns_list": [{"id": "w1", "active": False, "revoked_by": "Leitung"}]}
        merged = merge_entry(before, ours, current)["warns_list"][0]
        self.assertEqual(merged, {"id": "w1", "active": False, "revoked_by": "Leitung", "consequences": {"role": 1}})

    def test_notes_without_ids_keep_concurrent_additions(self):
        before = {"notes": [{"text": "a"}]}
        ours = {"notes": [{"text": "a"}, {"text": "unsere"}]}
        current = {"notes": [{"text": "a"}, {"text": "andere"}]}
        self.assertEqual(merge_entry(before, ours, current)["notes"], [{"text": "a"}, {"text": "unsere"}, {"text": "andere"}])


class CallSiteTests(unittest.TestCase):
    """The flows that await Discord between load and save must use merge-on-save (no stale snapshot)."""

    def block(self, path, start, end):
        source = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
        begin = source.index(start)
        return source[begin:source.index(end, begin + len(start))]

    def test_converted_flows_do_not_save_stale_team_data(self):
        # Warning creation is now shared by issue_team_warning(); its runtime behavior is
        # tested with fake actions in tests/test_team_warning_domain.py, not source strings.
        cases = [
            ("webserver.py", "async def process_overdue_shifts(", "def calculate_weekly_seconds("),
            ("webserver.py", 'if action == "remove_warn":', 'if action == "add_note":'),
            ("cogs/activity_check.py", "# K-04: fresh entry per member", "except Exception as exc:"),
        ]
        for path, start, end in cases:
            block = self.block(path, start, end)
            with self.subTest(path=path, start=start):
                self.assertNotRegex(block, r"save_json\((ws\.|web\.)?DATA_FILE")
                self.assertIn("commit()", block)


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [patch.object(ws, "DATA_FILE", str(Path(self.tmp.name) / "team_data.json")),
                        patch.object(ws, "SHIFTS_FILE", str(Path(self.tmp.name) / "shifts.json")),
                        patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_two_overlapping_warn_flows_keep_both_warnings(self):
        async def warn(warn_id, delay):
            team_db = ws.load_json(ws.DATA_FILE, {})
            entry = ws.user_entry(team_db, "42")
            tracker = ws.track_member_entry("42", entry)
            await asyncio.sleep(delay)  # protection checks / Discord role sync
            entry["warns_list"].append({"id": warn_id, "reason": "x", "active": True})
            tracker.commit()
            await asyncio.sleep(0.01)  # consequences, team update
            entry["warns_list"][-1]["consequences"] = {"checked": True}
            tracker.commit()

        async def scenario():
            await asyncio.gather(warn("warn_a", 0.01), warn("warn_b", 0.005))
        asyncio.run(scenario())
        warns = ws.load_json(ws.DATA_FILE, {})["42"]["warns_list"]
        self.assertEqual(sorted(w["id"] for w in warns), ["warn_a", "warn_b"])
        self.assertTrue(all(w.get("consequences") == {"checked": True} for w in warns))

    def test_shift_event_commit_does_not_drop_new_active_shift(self):
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": [{"shift_event_id": "e1", "mod_id": "1"}]})
        event = dict(ws.load_json(ws.SHIFTS_FILE, {})["history"][0])
        # meanwhile someone starts a shift
        data = ws.load_json(ws.SHIFTS_FILE, {})
        data["active_shifts"]["2"] = {"started_at_iso": "2026-10-20T10:00:00"}
        ws.save_json(ws.SHIFTS_FILE, data)
        event["auto_warn_status"] = "issued"
        ws.commit_shift_event(event)
        stored = ws.load_json(ws.SHIFTS_FILE, {})
        self.assertIn("2", stored["active_shifts"])
        self.assertEqual(stored["history"][0]["auto_warn_status"], "issued")

    def test_voice_case_store_keeps_cases_opened_meanwhile(self):
        from cogs import voice_support
        stale_case = {"case_id": "c1", "assigned_staff_id": 7}  # loaded before the Discord calls
        # meanwhile a second citizen opened case c2
        pulse_db.set_setting(voice_support.CASES_KEY, {"waiting:1": {"case_id": "c1"}, "waiting:2": {"case_id": "c2"}})
        object.__new__(voice_support.VoiceSupport)._store_case(stale_case, "555")
        cases = pulse_db.get_setting(voice_support.CASES_KEY, {})
        self.assertEqual(set(cases), {"555", "waiting:2"})


if __name__ == "__main__":
    unittest.main()
