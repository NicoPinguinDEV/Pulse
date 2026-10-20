"""Phase 3: performance changes must give identical results (P-03, P-04, P-05)."""
import asyncio
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

import webserver as ws


def old_weekly_seconds(mod_id, history, active_shifts=None):
    """The implementation before P-03, kept here as the reference."""
    total = 0
    start = ws.week_start()
    for entry in history:
        if str(entry.get("mod_id")) == str(mod_id):
            try:
                if datetime.strptime(entry.get("date"), "%Y-%m-%d") >= start:
                    total += int(entry.get("duration_seconds", 0))
            except Exception:
                pass
    s = (active_shifts or {}).get(str(mod_id))
    if s:
        try:
            if datetime.strptime(s.get("date"), "%Y-%m-%d") >= start:
                total += ws.shift_elapsed(s)
        except Exception:
            pass
    return total


class WeeklySecondsTests(unittest.TestCase):
    def test_identical_to_previous_implementation(self):
        rng = random.Random(7)
        start = ws.week_start()
        dates = []
        for offset in range(-20, 8):
            day = start + timedelta(days=offset)
            dates += [day.strftime("%Y-%m-%d"), f"{day.year}-{day.month}-{day.day}"]
        dates += [None, "", "kaputt", "2026-13-01", 12345]
        history = [{"mod_id": str(rng.randint(1, 30)), "date": rng.choice(dates),
                    "duration_seconds": rng.choice([60, 3600, "120", "x", None])} for _ in range(3000)]
        active = {"3": {"date": start.strftime("%Y-%m-%d"), "started_at_iso": ws.now_de().isoformat()},
                  "4": {"date": "kaputt"}}
        for uid in [str(i) for i in range(0, 32)]:
            self.assertEqual(ws.calculate_weekly_seconds(uid, history, active), old_weekly_seconds(uid, history, active), uid)

    def test_new_list_is_not_served_from_memo(self):
        day = ws.week_start().strftime("%Y-%m-%d")
        first = [{"mod_id": "1", "date": day, "duration_seconds": 60}]
        self.assertEqual(ws.calculate_weekly_seconds("1", first), 60)
        second = [{"mod_id": "1", "date": day, "duration_seconds": 90}]
        self.assertEqual(ws.calculate_weekly_seconds("1", second), 90)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.patches = [patch.object(ws, "CONFIG_FILE", str(base / "config.json")),
                        patch.object(ws, "SHIFTS_FILE", str(base / "shifts.json")),
                        patch.object(ws, "DATA_FILE", str(base / "team_data.json"))]
        for p in self.patches:
            p.start()
        ws._CONFIG_CACHE.update(key=None, data=None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        ws._CONFIG_CACHE.update(key=None, data=None)
        self.tmp.cleanup()

    def test_config_is_parsed_once_and_invalidated_on_save(self):
        ws.save_json(ws.CONFIG_FILE, {"team_role_ids": [1]})
        real = ws.load_json
        calls = []
        with patch.object(ws, "load_json", lambda *a, **k: calls.append(a[0]) or real(*a, **k)):
            first = ws.load_config()
            ws.load_config()
            self.assertEqual(len(calls), 1)
            first["team_role_ids"].append(99)  # a caller changing its copy must not affect others
            self.assertEqual(ws.load_config()["team_role_ids"], [1])
            ws.save_json(ws.CONFIG_FILE, {"team_role_ids": [2]})
            self.assertEqual(ws.load_config()["team_role_ids"], [2])
        # an external edit (other mtime/size) is picked up as well
        Path(ws.CONFIG_FILE).write_text(json.dumps({"team_role_ids": [3, 4]}), encoding="utf-8")
        self.assertEqual(ws.load_config()["team_role_ids"], [3, 4])

    def test_reading_shifts_never_writes(self):
        old = (ws.now_de() - timedelta(hours=30)).isoformat(timespec="seconds")
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {"1": {"date": old[:10], "started_at_iso": old}}, "history": []})
        before = Path(ws.SHIFTS_FILE).read_bytes()
        view = ws.load_shifts()
        self.assertEqual(view["active_shifts"], {})  # shown as ended …
        self.assertTrue(view["history"][0]["auto_closed"])
        self.assertEqual(Path(ws.SHIFTS_FILE).read_bytes(), before)  # … but nothing written

        async def missing(_):
            raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "gone")
        guild = SimpleNamespace(get_member=lambda _: None, fetch_member=missing)
        with patch("builtins.print"):
            report = asyncio.run(ws.process_overdue_shifts(guild, {"team_role_ids": []}))
        self.assertEqual(report["closed"], 1)
        stored = ws.load_json(ws.SHIFTS_FILE, {})
        self.assertEqual(stored["active_shifts"], {})
        self.assertEqual(stored["history"][0]["auto_warn_status"], "member_left_server")


if __name__ == "__main__":
    unittest.main()
