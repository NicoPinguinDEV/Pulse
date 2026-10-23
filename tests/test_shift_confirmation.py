"""E-2: shifts closed automatically after 24 h count 0 h until a higher-ranked leader confirms the real duration.

Runs the real FastAPI routes with a fake login (no Discord connection, temp files).
"""
import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord
from fastapi.testclient import TestClient

import pulse_cockpit_utils
import pulse_db
import pulse_home_utils
import pulse_insights
import pulse_timeline
import webserver as ws
from pulse_shift_utils import (AUTO_SHIFT_END_SECONDS, OFFBOARDING_CLOSE_REASON, credited_shift_seconds,
                               needs_duration_confirmation, parse_max_shift_hours)
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

ACTOR = SimpleNamespace(id=123, bot=False, display_name="Leitung", roles=[])
TARGET = SimpleNamespace(id=555, bot=False, display_name="Ziel", roles=[], avatar=None, nick=None,
                         joined_at=None, status="offline", top_role=None, guild_permissions=SimpleNamespace(administrator=False))
RANKS = {ACTOR.id: 5, TARGET.id: 2}
LEADER = dict(TEAMLER, can_promote=True)
EVENT_ID = "shift_test_event"


def guild():
    members = {m.id: m for m in (ACTOR, TARGET)}
    return SimpleNamespace(id=1, name="G", members=list(members.values()), roles=[], channels=[], text_channels=[],
                           member_count=2, owner_id=0, icon=None, get_member=lambda uid: members.get(int(uid)),
                           get_role=lambda _: None, get_channel=lambda _: None)


def auto_closed(**extra):
    record = {"mod_id": str(TARGET.id), "date": "2026-10-20", "started_at_iso": "2026-10-19T10:00:00+02:00",
              "ended_at_iso": "2026-10-20T10:00:00+02:00", "duration_seconds": 0, "duration_confirmed": False,
              "measured_seconds": AUTO_SHIFT_END_SECONDS, "auto_closed": True, "shift_event_id": EVENT_ID,
              "auto_warn_status": "done"}
    record.update(extra)
    return record


class CreditRuleTests(unittest.TestCase):
    def test_unconfirmed_auto_close_counts_zero_also_for_legacy_records(self):
        self.assertEqual(credited_shift_seconds(auto_closed()), 0)
        legacy = {"auto_closed": True, "duration_seconds": AUTO_SHIFT_END_SECONDS}  # stored 24 h before E-2
        self.assertEqual(credited_shift_seconds(legacy), 0)
        self.assertTrue(needs_duration_confirmation(legacy))

    def test_confirmed_and_normal_shifts_are_credited(self):
        self.assertEqual(credited_shift_seconds(auto_closed(duration_seconds=5400, duration_confirmed=True)), 5400)
        self.assertEqual(credited_shift_seconds({"duration_seconds": 1800}), 1800)
        self.assertEqual(credited_shift_seconds({"duration_seconds": "kaputt"}), 0)
        self.assertEqual(credited_shift_seconds(None), 0)

    def test_offboarding_keeps_real_time_below_24_hours(self):
        short = {"auto_closed": True, "auto_closed_reason": OFFBOARDING_CLOSE_REASON, "duration_seconds": 3 * 3600}
        self.assertEqual(credited_shift_seconds(short), 3 * 3600)
        self.assertFalse(needs_duration_confirmation(short))
        forgotten = dict(short, duration_seconds=AUTO_SHIFT_END_SECONDS + 60)
        self.assertEqual(credited_shift_seconds(forgotten), 0)

    def test_max_shift_hours_is_validated(self):
        self.assertEqual(parse_max_shift_hours("12"), 12.0)
        self.assertEqual(parse_max_shift_hours("0.5"), 0.5)
        for bad in ("25", "0.1", "nan", "inf", "oops", None, ""):
            self.assertEqual(parse_max_shift_hours(bad), 12.0, bad)


class ReaderTests(unittest.TestCase):
    """Every statistic uses the same crediting – a forgotten shift must not show up as 24 h anywhere."""

    def setUp(self):
        self.history = [auto_closed(duration_seconds=AUTO_SHIFT_END_SECONDS),  # legacy value, unconfirmed
                        {"mod_id": str(TARGET.id), "date": "2026-10-20", "started_at_iso": "2026-10-20T12:00:00+02:00",
                         "ended_at_iso": "2026-10-20T13:00:00+02:00", "duration_seconds": 3600}]

    def test_weekly_totals(self):
        with patch.object(ws, "_in_week", lambda *a: True):
            ws._WEEKLY_MEMO.update(key=None, history=None, totals={})
            self.assertEqual(ws.weekly_history_totals(self.history)[str(TARGET.id)], 3600)

    def test_cockpit_home_insights_and_timeline(self):
        day = ws.datetime(2026, 10, 20).date()
        self.assertEqual(pulse_cockpit_utils.seconds_per_member(self.history, day, day)[str(TARGET.id)], 3600)
        per_day = dict(pulse_home_utils.daily_seconds(self.history, str(TARGET.id), day, days=1))
        self.assertEqual(per_day[day], 3600)
        start = ws.datetime.fromisoformat("2026-10-01T00:00:00+02:00")
        end = ws.datetime.fromisoformat("2026-11-01T00:00:00+01:00")
        review = pulse_insights.month_review(TARGET.id, start=start, end=end, shift_history=self.history,
                                             tickets=[], attempts=[], unlocked=[])
        self.assertEqual(review["hours"], 1.0)
        stats = pulse_insights.member_stats(TARGET.id, now=ws.datetime.fromisoformat("2026-10-21T12:00:00+02:00"),
                                            shift_history=self.history, tickets=[], attempts=[], entry={}, active_warns=[])
        self.assertEqual(stats["hours_30d"], 1.0)
        items = pulse_timeline.build(shifts=self.history, user_id=TARGET.id)
        texts = " ".join(f"{i.get('title')} {i.get('detail')}" for i in items)
        self.assertIn("unbestätigt (0 h)", texts)
        self.assertNotIn("24 h", texts)

    def test_overdue_shift_is_stored_with_zero_credit(self):
        async def not_on_server(_uid):
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")
        # The member is not on the server, so the automatic-warning step stops before touching Discord or team data.
        empty_guild = SimpleNamespace(id=1, get_member=lambda _uid: None, fetch_member=not_on_server)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            isolation = [patch.object(ws, name, str(base / name.lower())) for name in ("SHIFTS_FILE", "DATA_FILE", "AUDIT_FILE")]
            isolation += [patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")), patch.object(ws, "log_audit", lambda *a, **k: None)]
            for p in isolation:
                p.start()
            try:
                started = (ws.now_de() - timedelta(hours=30)).isoformat(timespec="seconds")
                ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {str(TARGET.id): {"date": "2026-10-20", "started_at_iso": started,
                                                                                 "status": "online"}}, "history": []})
                asyncio.run(ws.process_overdue_shifts(empty_guild, {"team_role_ids": []}))
                history = ws.load_shifts_raw()["history"]
            finally:
                for p in reversed(isolation):
                    p.stop()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["duration_seconds"], 0)
        self.assertFalse(history[0]["duration_confirmed"])
        self.assertEqual(history[0]["measured_seconds"], AUTO_SHIFT_END_SECONDS)


class ConfirmRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.audit = []
        self.patches = [patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
                        patch.object(ws, "DATA_FILE", str(base / "team_data.json")),
                        patch.object(ws, "SHIFTS_FILE", str(base / "shifts.json")),
                        patch.object(ws, "log_audit", lambda *a, **k: self.audit.append(a)),
                        patch.object(ws, "team_rank", lambda member, _ids: RANKS.get(getattr(member, "id", None), 0)),
                        patch.object(ws, "MAX_CONFIRMED_SHIFT_HOURS", 12.0)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": [auto_closed()]})
        self.client = TestClient(ws.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def as_user(self, perms, actor=ACTOR):
        base = fake_auth_for(perms)

        def auth(*a, **k):
            ctx = base(*a, **k)
            ctx.guild = guild()
            ctx.user = {"id": str(actor.id), "global_name": actor.display_name}
            ctx.member = actor
            return ctx
        return patch.object(ws, "auth", auth)

    def confirm(self, perms, minutes=90, reason="Laut Log um 11:30 beendet", actor=ACTOR):
        with self.as_user(perms, actor):
            return self.client.post("/shift/history/confirm-duration", follow_redirects=False,
                                    data={"target_user_id": TARGET.id, "shift_event_id": EVENT_ID,
                                          "duration_minutes": minutes, "reason": reason})

    def stored(self):
        return ws.load_shifts_raw()["history"][0]

    def test_leader_confirms_once_with_audit(self):
        response = self.confirm(LEADER)
        self.assertIn(response.status_code, (302, 303))
        event = self.stored()
        self.assertTrue(event["duration_confirmed"])
        self.assertEqual(credited_shift_seconds(event), 90 * 60)
        self.assertEqual(event["duration_confirmed_by"], str(ACTOR.id))
        self.assertEqual(len(self.audit), 1)
        self.confirm(LEADER, minutes=600)  # a second confirmation must not overwrite the first
        self.assertEqual(credited_shift_seconds(self.stored()), 90 * 60)
        self.assertEqual(len(self.audit), 1)

    def test_without_promote_permission_nothing_changes(self):
        self.assertEqual(self.confirm(TEAMLER).status_code, 403)
        self.assertFalse(self.stored()["duration_confirmed"])

    def test_same_or_lower_rank_is_refused_admin_may(self):
        peer = SimpleNamespace(id=777, bot=False, display_name="Gleichrangig", roles=[])
        RANKS[peer.id] = RANKS[TARGET.id]
        try:
            self.assertEqual(self.confirm(LEADER, actor=peer).status_code, 403)
            self.assertFalse(self.stored()["duration_confirmed"])
            self.assertIn(self.confirm(ADMIN, actor=peer).status_code, (302, 303))
            self.assertTrue(self.stored()["duration_confirmed"])
        finally:
            RANKS.pop(peer.id, None)

    def test_own_shift_cannot_be_confirmed(self):
        self.assertEqual(self.confirm(ADMIN, actor=TARGET).status_code, 404)
        self.assertFalse(self.stored()["duration_confirmed"])

    def test_cap_and_reason_are_enforced(self):
        self.confirm(LEADER, minutes=12 * 60 + 1)
        self.confirm(LEADER, reason="kurz")
        self.confirm(LEADER, minutes=-5)
        self.assertFalse(self.stored()["duration_confirmed"])
        self.assertEqual(self.audit, [])

    def test_team_file_shows_form_only_to_higher_ranked_leaders(self):
        with self.as_user(LEADER):
            html = self.client.get(f"/member/{TARGET.id}").text
        self.assertIn('action="/shift/history/confirm-duration"', html)
        self.assertIn("Derzeit 0 h angerechnet", html)
        with self.as_user(TEAMLER):
            html = self.client.get(f"/member/{TARGET.id}").text
        self.assertNotIn('action="/shift/history/confirm-duration"', html)
        self.assertIn("Bestätigung durch eine höherrangige Führungskraft ausstehend", html)


if __name__ == "__main__":
    unittest.main()
