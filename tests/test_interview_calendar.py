"""Audit #99: interview calendar – validation, time zones, participants, conflicts, reschedule/cancel, reminders,
calendar privacy and the routes (synthetic applications, fake login, temp database, no Discord)."""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_app_comms as ac
import pulse_backup
import pulse_db
import pulse_interviews as iv
import webserver as ws

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
EARLY = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def at(hours=24, minutes=0):
    return NOW + timedelta(hours=hours, minutes=minutes)


class FakeDb:
    """Calendar persistence stand-in for the pure functions (records every call)."""

    def __init__(self, fail_create=False, fail_delete=False):
        self.rows, self.fail_create, self.fail_delete, self.n = {}, fail_create, fail_delete, 0

    def create_calendar_event(self, title, starts_at, ends_at, category, location, description, creator_id, creator_name):
        if self.fail_create:
            raise RuntimeError("db down")
        self.n += 1
        self.rows[f"cal_{self.n}"] = {"id": f"cal_{self.n}", "title": title, "starts_at": starts_at, "ends_at": ends_at,
                                      "category": category, "description": description}
        return f"cal_{self.n}"

    def delete_calendar_event(self, event_id):
        if self.fail_delete:
            raise RuntimeError("db down")
        return self.rows.pop(event_id, None) is not None

    def list_calendar_events(self, *a, **k):
        return list(self.rows.values())


def record(app_id="a1", user="100", name="Erika Synthetisch", reviewers=("10", "11"), status="interview", **extra):
    item = {"status": status, "user_id": user, "name": name,
            "assignments": [{"reviewer_id": r, "reviewer_name": f"Prüfer {r}"} for r in reviewers], "reviews": {}}
    item.update(extra)
    return item


class Store:
    """apps dict + save callable that can fail."""

    def __init__(self, apps, fail=False):
        self.apps, self.fail, self.saves = apps, fail, 0

    def save(self, data):
        if self.fail:
            raise OSError("disk full")
        self.saves += 1


def book(store, db, app_id="a1", start=None, minutes=30, lead="", rev=None, now=NOW, actor="1"):
    return iv.schedule(store.apps, app_id, start=start or at(48).astimezone(BERLIN), minutes=minutes, lead_id=lead,
                       lead_name="Leitung" if lead else "", actor_id=actor, actor_name="Admin", save=store.save, db=db,
                       expected_revision=rev, now=now)


class TimeTests(unittest.TestCase):
    def test_winter_and_summer_offsets_stored_as_utc(self):
        winter = ac.parse_slot("2026-12-10T18:30", BERLIN, now=NOW)
        summer = ac.parse_slot("2027-07-10T18:30", BERLIN, now=NOW)
        self.assertEqual(winter.astimezone(UTC).strftime("%H:%M"), "17:30")
        self.assertEqual(summer.astimezone(UTC).strftime("%H:%M"), "16:30")

    def test_dst_gap_and_overlap_are_refused(self):
        with self.assertRaisesRegex(ValueError, "nicht"):
            ac.parse_slot("2027-03-28T02:30", BERLIN, now=NOW)       # spring: 02:00–03:00 does not exist
        with self.assertRaisesRegex(ValueError, "zweimal"):
            ac.parse_slot("2026-10-25T02:30", BERLIN, now=datetime(2026, 10, 1, tzinfo=UTC))  # autumn: exists twice
        self.assertEqual(ac.parse_slot("2027-03-28T03:30", BERLIN, now=NOW).astimezone(UTC).strftime("%H:%M"), "01:30")

    def test_invalid_past_and_aware_input(self):
        for bad in ("", "kaputt", "2026-13-40T10:00", "2026-12-10T18:30+02:00"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ac.parse_slot(bad, BERLIN, now=NOW)
        with self.assertRaisesRegex(ValueError, "Zukunft"):
            ac.parse_slot("2026-10-01T10:00", BERLIN, now=NOW)

    def test_duration_bounds(self):
        self.assertEqual(iv.duration_minutes("45"), 45)
        for bad in ("0", "14", "241", "abc", "", None):
            with self.subTest(bad=bad), self.assertRaises(iv.InterviewError):
                iv.duration_minutes(bad)

    def test_move_across_dst_keeps_wall_clock(self):
        store, db = Store({"a1": record()}), FakeDb()
        first = ac.parse_slot("2026-10-20T18:00", BERLIN, now=EARLY)   # CEST
        later = ac.parse_slot("2026-10-27T18:00", BERLIN, now=EARLY)   # CET after the switch
        book(store, db, start=first, now=EARLY)
        book(store, db, start=later, now=EARLY)
        self.assertEqual(iv.window(store.apps["a1"])[0].strftime("%H:%M"), "17:00")
        self.assertEqual(store.apps["a1"]["interview_at"], "2026-10-27T18:00+01:00")


class ParticipantTests(unittest.TestCase):
    def test_derived_from_record_and_deduplicated(self):
        item = record(interview_lead_id="10", interview_lead_name="Prüfer 10", interview_scheduled_by_id="1", interview_scheduled_by="Admin")
        people = {p["id"]: p["roles"] for p in iv.participants(item)}
        self.assertEqual(people, {"100": ["applicant"], "10": ["reviewer", "lead"], "11": ["reviewer"], "1": ["organizer"]})
        self.assertEqual(sorted(iv.team_ids(item)), ["1", "10", "11"])

    def test_garbage_ids_are_dropped(self):
        item = record(interview_lead_id="abc", assignments=[{"reviewer_id": "x"}, {"reviewer_id": "12"}])
        self.assertEqual(sorted(p["id"] for p in iv.participants(item)), ["100", "12"])

    def test_no_second_copy_of_person_data_in_calendar_row(self):
        store, db = Store({"a1": record()}), FakeDb()
        book(store, db, lead="10")
        row = next(iter(db.rows.values()))
        self.assertEqual(row["title"], "Bewerbungsgespräch")
        blob = json.dumps(row, ensure_ascii=False)
        for forbidden in ("Erika", "100", "a1", "Prüfer"):
            self.assertNotIn(forbidden, blob)


class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.store, self.db = Store({"a1": record("a1", "100", reviewers=("10",)),
                                     "a2": record("a2", "200", reviewers=("10",)),
                                     "a3": record("a3", "300", reviewers=("12",)),
                                     "a4": record("a4", "100", reviewers=("13",))}), FakeDb()
        book(self.store, self.db, "a1", start=at(48).astimezone(BERLIN), lead="50")

    def conflict_kinds(self, app_id, start, **kw):
        with self.assertRaises(iv.InterviewConflict) as ctx:
            book(self.store, self.db, app_id, start=start, **kw)
        return {c["kind"] for c in ctx.exception.conflicts}

    def test_same_reviewer_same_applicant_same_lead(self):
        self.assertEqual(self.conflict_kinds("a2", at(48, 15)), {"reviewer"})
        self.assertEqual(self.conflict_kinds("a4", at(48, 29)), {"applicant"})
        self.assertEqual(self.conflict_kinds("a3", at(48, 10), lead="50"), {"lead"})

    def test_adjacent_and_other_people_are_fine(self):
        book(self.store, self.db, "a2", start=at(48, 30))                 # starts exactly when a1 ends
        book(self.store, self.db, "a3", start=at(48, 5))                  # nobody in common
        self.assertEqual(len(self.db.rows), 3)

    def test_move_does_not_conflict_with_itself(self):
        book(self.store, self.db, "a1", start=at(48, 10), lead="50")
        self.assertEqual(len(self.db.rows), 1)

    def test_cancelled_and_decided_interviews_do_not_block(self):
        iv.cancel(self.store.apps, "a1", actor_id="1", actor_name="Admin", save=self.store.save, db=self.db, now=NOW)
        book(self.store, self.db, "a2", start=at(48, 5))
        self.store.apps["a3"]["interview_at"] = at(100).isoformat()
        self.store.apps["a3"]["status"] = "rejected"
        book(self.store, self.db, "a4", start=at(100, 5))

    def test_unreadable_other_appointment_blocks_instead_of_passing(self):
        self.store.apps["a3"].update(interview_at="irgendwann", interview_state="scheduled")
        self.assertEqual(self.conflict_kinds("a2", at(300)), {"unknown"})

    def test_missing_applicant_data_is_refused(self):
        self.store.apps["a2"]["user_id"] = ""
        with self.assertRaisesRegex(iv.InterviewError, "nicht eindeutig"):
            book(self.store, self.db, "a2", start=at(300))

    def test_applicant_cannot_lead_own_interview(self):
        with self.assertRaisesRegex(iv.InterviewError, "Bewerber"):
            book(self.store, self.db, "a2", start=at(300), lead="200")

    def test_state_written_after_the_callers_read_is_not_overwritten(self):
        self.store.apps["a3"]["assignments"] = [{"reviewer_id": "10", "reviewer_name": "x"}]   # a3 shares reviewer 10 with a2
        stale = {k: dict(v) for k, v in self.store.apps.items()}          # what a slow request read earlier
        book(self.store, self.db, "a2", start=at(500))                    # another request books a2 meanwhile (reviewer 10)
        saved = []
        with self.assertRaises(iv.InterviewConflict):
            iv.schedule(stale, "a3", start=at(500, 10).astimezone(BERLIN), minutes=30, lead_id="", actor_id="1", actor_name="A", save=saved.append,
                        db=self.db, now=NOW, load=lambda: self.store.apps)
        self.assertEqual(saved, [])

    def test_concurrent_bookings_only_one_wins(self):
        import threading
        results = []

        def attempt(app_id):
            try:
                book(self.store, self.db, app_id, start=at(400))
                results.append("ok")
            except iv.InterviewConflict:
                results.append("conflict")
        threads = [threading.Thread(target=attempt, args=(a,)) for a in ("a2", "a3")]
        self.store.apps["a3"]["assignments"] = [{"reviewer_id": "10", "reviewer_name": "x"}]  # share reviewer 10
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["conflict", "ok"])


class ScheduleTests(unittest.TestCase):
    def test_create_move_cancel_keep_exactly_one_row(self):
        store, db = Store({"a1": record()}), FakeDb()
        created = book(store, db, lead="50")
        item = store.apps["a1"]
        self.assertEqual((created["action"], len(db.rows), item["interview_event_id"]), ("created", 1, "cal_1"))
        self.assertEqual(item["interview_end"][11:16], (iv.window(item)[0] + timedelta(minutes=30)).astimezone(BERLIN).strftime("%H:%M"))
        moved = book(store, db, start=at(72).astimezone(BERLIN), minutes=60)
        self.assertEqual((moved["action"], list(db.rows)), ("moved", ["cal_2"]))
        self.assertEqual(db.rows["cal_2"]["ends_at"][:13], (at(72) + timedelta(minutes=60)).isoformat()[:13])
        self.assertEqual((item["interview_state"], item["interview_revision"]), ("scheduled", 2))
        iv.cancel(store.apps, "a1", actor_id="1", actor_name="Admin", save=store.save, db=db, reason="Krank", now=NOW)
        self.assertEqual((db.rows, item["interview_state"], item["interview_at"], item["interview_event_id"]), ({}, "cancelled", "", ""))
        self.assertEqual(item["interview_cancel_reason"], "Krank")
        again = book(store, db, start=at(96).astimezone(BERLIN))     # rebook after cancellation
        self.assertEqual((again["action"], len(db.rows), item["interview_state"]), ("created", 1, "scheduled"))
        self.assertNotIn("interview_cancelled_at", item)

    def test_identical_resubmit_is_a_noop(self):
        store, db = Store({"a1": record()}), FakeDb()
        start = at(48).astimezone(BERLIN)
        book(store, db, start=start)
        saves = store.saves
        self.assertEqual(book(store, db, start=start)["action"], "unchanged")
        self.assertEqual((store.saves, db.n, store.apps["a1"]["interview_revision"]), (saves, 1, 1))

    def test_calendar_failure_changes_nothing(self):
        store, db = Store({"a1": record()}), FakeDb(fail_create=True)
        with self.assertRaisesRegex(iv.InterviewError, "Kalendereintrag"):
            book(store, db)
        self.assertNotIn("interview_at", store.apps["a1"])

    def test_save_failure_rolls_back_and_leaves_no_orphan(self):
        store, db = Store({"a1": record()}), FakeDb()
        book(store, db)
        before = dict(store.apps["a1"])
        store.fail = True
        with self.assertRaisesRegex(iv.InterviewError, "nicht gespeichert"):
            book(store, db, start=at(100).astimezone(BERLIN))
        self.assertEqual((store.apps["a1"], list(db.rows)), (before, ["cal_1"]))   # old row kept, new row removed
        with self.assertRaisesRegex(iv.InterviewError, "Absage"):
            iv.cancel(store.apps, "a1", actor_id="1", actor_name="A", save=store.save, db=db, now=NOW)
        self.assertEqual((store.apps["a1"], list(db.rows)), (before, ["cal_1"]))

    def test_failed_delete_of_old_row_is_cleaned_by_reconcile(self):
        store, db = Store({"a1": record()}), FakeDb()
        book(store, db)
        db.fail_delete = True
        book(store, db, start=at(100).astimezone(BERLIN))
        self.assertEqual(sorted(db.rows), ["cal_1", "cal_2"])
        self.assertEqual(iv.orphaned_events(store.apps, db.list_calendar_events()), ["cal_1"])
        db.fail_delete = False
        self.assertEqual(iv.reconcile(store.apps, save=store.save, db=db, now=NOW)["orphans"], 1)
        self.assertEqual(list(db.rows), ["cal_2"])

    def test_stale_revision_is_rejected(self):
        store, db = Store({"a1": record()}), FakeDb()
        book(store, db)
        with self.assertRaisesRegex(iv.InterviewError, "zwischenzeitlich"):
            book(store, db, start=at(100).astimezone(BERLIN), rev=0)
        with self.assertRaisesRegex(iv.InterviewError, "zwischenzeitlich"):
            iv.cancel(store.apps, "a1", actor_id="1", actor_name="A", save=store.save, db=db, expected_revision="0", now=NOW)
        self.assertEqual(len(db.rows), 1)

    def test_rules(self):
        store, db = Store({"a1": record(), "p": record(status="pending"), "d": record(status="rejected")}), FakeDb()
        for app_id in ("p", "d", "missing"):
            with self.subTest(app_id=app_id), self.assertRaises(iv.InterviewError):
                book(store, db, app_id)
        with self.assertRaisesRegex(iv.InterviewError, "Zukunft"):
            book(store, db, start=(NOW - timedelta(hours=1)).astimezone(BERLIN))
        with self.assertRaises(iv.InterviewError):
            iv.cancel(store.apps, "a1", actor_id="1", actor_name="A", save=store.save, db=db, now=NOW)  # nothing booked
        self.assertEqual((db.rows, db.n), ({}, 0))

    def test_notify_list_excludes_the_actor_and_applicant(self):
        store, db = Store({"a1": record(interview_lead_id="")}), FakeDb()
        self.assertEqual(sorted(book(store, db, lead="50", actor="10")["notify"]), ["11", "50"])


class ReminderLogicTests(unittest.TestCase):
    def apps(self, **extra):
        return {"a": record("a", interview_at=at(0, 30).isoformat(), **extra), "b": record("b", interview_at=at(5).isoformat(), **extra),
                "c": record("c", interview_at=at(30).isoformat(), **extra), "d": record("d", interview_at=at(0, 30).isoformat(), interview_state="cancelled"),
                "e": record("e", interview_at=at(0, 30).isoformat(), status="rejected"), "f": record("f", interview_at=at(-1).isoformat())}

    def test_windows_and_exclusions(self):
        self.assertEqual(sorted(iv.due_notices(self.apps(), NOW)), [("a", "reminder"), ("b", "heads_up")])

    def test_sent_flags_prevent_duplicates_and_reschedule_resets(self):
        apps = self.apps(interview_reminded=True)
        self.assertEqual(iv.due_notices(apps, NOW), [])
        store, db = Store({"a1": record(interview_reminded=True, interview_heads_up=True)}), FakeDb()
        book(store, db, start=(NOW + timedelta(minutes=30)).astimezone(BERLIN))
        self.assertEqual(iv.due_notices(store.apps, NOW), [("a1", "reminder")])

    def test_cancel_stops_notices(self):
        store, db = Store({"a1": record()}), FakeDb()
        book(store, db, start=(NOW + timedelta(minutes=30)).astimezone(BERLIN))
        iv.cancel(store.apps, "a1", actor_id="1", actor_name="A", save=store.save, db=db, now=NOW)
        self.assertEqual(iv.due_notices(store.apps, NOW), [])


class DescribeTests(unittest.TestCase):
    def setUp(self):
        self.store, self.db = Store({"a1": record(interview_lead_id="50", interview_lead_name="Lena Leitung")}), FakeDb()
        book(self.store, self.db, lead="50", start=at(48).astimezone(BERLIN))
        self.index = iv.link_index(self.store.apps)
        self.event = next(iter(self.db.rows.values()))

    def test_privileged_sees_applicant_participants_and_link(self):
        info = iv.describe(self.event, self.index, privileged=True, now=NOW)
        self.assertIn("Erika", info["title"])
        self.assertEqual(info["app_url"], "/applications#app-a1")
        self.assertEqual(info["status_label"], "Geplant")
        self.assertEqual({p["name"] for p in info["participants"]} >= {"Erika Synthetisch", "Leitung"}, True)

    def test_everyone_else_gets_only_generic_facts(self):
        info = iv.describe(self.event, self.index, privileged=False, now=NOW)
        blob = json.dumps(info, ensure_ascii=False)
        for forbidden in ("Erika", "a1", "Prüfer", "Leitung", "/applications"):
            self.assertNotIn(forbidden, blob)
        self.assertEqual((info["title"], info["headcount"], info["minutes"]), ("Bewerbungsgespräch", 5, 30))

    def test_legacy_and_orphan_rows_are_sanitised_and_flagged(self):
        legacy = {"id": "old", "title": "Bewerbungsgespräch Max Geheim", "category": "event", "description": "Bewerbung a9", "starts_at": at(5).isoformat()}
        self.assertEqual(iv.describe(legacy, {}, privileged=False)["title"], "Bewerbungsgespräch")
        self.assertEqual(iv.describe(legacy, {}, privileged=True)["status"], "orphan")
        self.assertIsNone(iv.describe({"id": "x", "title": "Teammeeting", "category": "meeting"}, {}, privileged=False))

    def test_status_progression(self):
        item = self.store.apps["a1"]
        start, end = iv.window(item)
        self.assertEqual(iv.status_key(item, start - timedelta(minutes=1)), "scheduled")
        self.assertEqual(iv.status_key(item, start + timedelta(minutes=1)), "running")
        self.assertEqual(iv.status_key(item, end + timedelta(minutes=1)), "done")
        item["status"] = "hired"
        self.assertEqual(iv.status_key(item, start), "closed")

    def test_reconcile_closes_obsolete_appointments_only(self):
        self.store.apps["a1"]["status"] = "rejected"      # decided before the appointment
        self.db.rows["manual"] = {"id": "manual", "category": "meeting"}
        self.assertEqual(iv.reconcile(self.store.apps, save=self.store.save, db=self.db, now=NOW), {"closed": 1, "orphans": 0})
        self.assertEqual(list(self.db.rows), ["manual"])
        self.assertEqual(self.store.apps["a1"]["interview_state"], "cancelled")


# ----------------------------------------------------------------------------------------------- routes
PERMS_STAFF = {"can_view_dashboard": True, "can_manage_applications": True}
PERMS_LEAD = dict(PERMS_STAFF, can_promote=True)
PERMS_TEAM = {"can_view_dashboard": True}
PEOPLE = {"1": PERMS_LEAD, "2": PERMS_STAFF, "3": PERMS_TEAM, "50": PERMS_LEAD, "10": PERMS_STAFF, "11": PERMS_STAFF, "100": PERMS_TEAM}


def make_auth(user_id="1", guild_id=1):
    perms = PEOPLE[user_id]
    members = {int(k): SimpleNamespace(id=int(k), display_name=f"Mitglied {k}", bot=False) for k in PEOPLE}
    guild = SimpleNamespace(id=guild_id, name="Test", members=list(members.values()), get_member=lambda i: members.get(i), get_role=lambda _: None)

    def fake_auth(request, session, perm="can_view_dashboard", admin=False):
        if perm and not perms.get(perm):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        return SimpleNamespace(user={"id": user_id, "global_name": f"Mitglied {user_id}"}, guild=guild, guild_name="Test", perms=perms,
                               config={"team_role_ids": []}, member=None, msg="", msg_ok=True)
    return fake_auth


class RouteBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")), patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch.object(ws, "APPS_FILE", str(root / "applications.json")),
                        patch.object(ws, "compute_perms", lambda guild, uid, cfg: (PEOPLE.get(str(uid), {}), None)),
                        patch.object(ws, "dm_applicant", AsyncMock(return_value=True))]
        self.dm = None
        for p in self.patches:
            started = p.start()
            if getattr(p, "attribute", "") == "dm_applicant":
                self.dm = started
        pulse_db.init_db()
        self.client = TestClient(ws.app, raise_server_exceptions=False)
        ws.save_json(ws.APPS_FILE, {"a1": record("a1", "100", reviewers=("10",)), "a2": record("a2", "200", reviewers=("10",)),
                                    "a3": record("a3", "300", reviewers=("11",), status="pending")})
        self.future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def post(self, path, data, user="1", **kw):
        with patch.object(ws, "auth", make_auth(user, **kw)):
            return self.client.post(path, data=data, follow_redirects=False)

    def apps(self):
        return ws.load_json(ws.APPS_FILE, {})

    def events(self):
        return pulse_db.list_calendar_events()


class InterviewRouteTests(RouteBase):
    def test_create_move_cancel_end_to_end(self):
        response = self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": self.future, "duration": "45", "lead_id": "50"})
        self.assertIn("t=ok", response.headers["location"])
        self.assertIn("#app-a1", response.headers["location"])
        item = self.apps()["a1"]
        events = self.events()
        self.assertEqual((len(events), events[0]["id"], events[0]["category"], events[0]["title"]), (1, item["interview_event_id"], "interview", "Bewerbungsgespräch"))
        self.assertEqual((item["interview_lead_id"], item["interview_lead_name"], item["interview_scheduled_by_id"]), ("50", "Mitglied 50", "1"))
        self.assertEqual(iv.participants(item)[0]["roles"], ["applicant"])
        self.assertEqual(self.dm.await_args.args[2], "scheduled")
        notified = {n["user_id"] for uid in ("10", "50") for n in pulse_db.notifications(uid)}
        self.assertEqual(notified, {"10", "50"})
        self.assertEqual(pulse_db.notifications("1"), [])                      # the actor is not notified about their own change
        later = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M")
        self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": later, "expected_revision": "1"})
        item = self.apps()["a1"]
        self.assertEqual((len(self.events()), item["interview_lead_id"], item["interview_revision"]), (1, "50", 2))   # lead kept, row replaced
        self.assertEqual(self.dm.await_args.args[2], "rescheduled")
        refused = self.post("/applications/interview-cancel", {"app_id": "a1", "expected_revision": "1"})
        self.assertIn("t=err", refused.headers["location"])
        self.assertEqual(len(self.events()), 1)
        ok = self.post("/applications/interview-cancel", {"app_id": "a1", "expected_revision": "2", "reason": "Termin entfällt"})
        self.assertIn("t=ok", ok.headers["location"])
        self.assertEqual((self.events(), self.apps()["a1"]["interview_state"]), ([], "cancelled"))
        self.assertEqual(self.dm.await_args.args[2], "cancelled")
        titles = [n["title"] for n in pulse_db.notifications("10")]
        self.assertTrue(any("abgesagt" in t for t in titles) and any("verschoben" in t for t in titles))

    def test_conflict_is_reported_and_nothing_changes(self):
        self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": self.future})
        before = self.apps()
        response = self.post("/applications/interview-slot", {"app_id": "a2", "interview_at": self.future})   # same reviewer 10
        self.assertIn("t=err", response.headers["location"])
        self.assertIn("Terminkonflikt", response.headers["location"].replace("%20", " "))
        self.assertEqual((self.apps()["a2"], len(self.events())), (before["a2"], 1))

    def test_rights_fail_closed(self):
        data = {"app_id": "a1", "interview_at": self.future}
        for path, payload in (("/applications/interview-slot", data), ("/applications/interview-cancel", {"app_id": "a1"})):
            self.assertEqual(self.post(path, payload, user="3").status_code, 403)
        self.assertEqual((self.events(), "interview_at" in self.apps()["a1"]), ([], False))
        self.post("/applications/interview-slot", data)
        self.assertEqual(self.post("/applications/interview-cancel", {"app_id": "a1"}, user="3").status_code, 403)
        self.assertEqual(len(self.events()), 1)

    def test_foreign_ids_and_foreign_guild(self):
        for app_id in ("nope", "../a1", "a3"):                                    # unknown, manipulated, wrong phase
            with self.subTest(app_id=app_id):
                response = self.post("/applications/interview-slot", {"app_id": app_id, "interview_at": self.future})
                self.assertIn("t=err", response.headers["location"])
        apps = self.apps()
        apps["a1"]["guild_id"] = "999"
        ws.save_json(ws.APPS_FILE, apps)
        self.assertIn("t=err", self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": self.future}).headers["location"])
        self.assertIn("t=err", self.post("/applications/interview-cancel", {"app_id": "a1"}).headers["location"])
        self.assertEqual((self.events(), self.dm.await_count), ([], 0))

    def test_event_ids_cannot_be_injected(self):
        self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": self.future})
        pulse_db.create_calendar_event("Teammeeting", datetime.now(UTC).isoformat(), None, "meeting", "", "", "1", "x")
        real = self.apps()["a1"]["interview_event_id"]
        self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": self.future, "interview_event_id": "cal_fremd", "event_id": "x"})
        self.post("/applications/interview-cancel", {"app_id": "a2", "interview_event_id": real})     # other application, someone else's row
        self.assertIn(real, {e["id"] for e in self.events()})
        self.assertEqual(len(self.events()), 2)

    def test_invalid_input(self):
        base = {"app_id": "a1", "interview_at": self.future}
        for extra in ({"duration": "5"}, {"duration": "x"}, {"lead_id": "3"}, {"lead_id": "abc"}, {"lead_id": "100"}, {"interview_at": "2020-01-01T10:00"},
                      {"interview_at": "2027-03-28T02:30"}):
            with self.subTest(extra=extra):
                response = self.post("/applications/interview-slot", {**base, **extra})
                self.assertIn("t=err", response.headers["location"])
        self.assertEqual(self.events(), [])

    def test_failed_dm_is_recorded_once_and_visible(self):
        self.dm.return_value = False
        response = self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": self.future})
        self.assertIn("t=err", response.headers["location"])
        self.assertFalse(self.apps()["a1"]["interview_delivery"]["applicant_notice"]["ok"])
        self.assertEqual(self.dm.await_count, 1)
        with patch.object(ws, "auth", make_auth("1")):
            page = self.client.get("/applications")
        self.assertIn("nicht zustellbar", page.text)
        self.assertIn('name="duration"', page.text)
        self.assertIn("interview-cancel", page.text)

    def test_legacy_form_without_new_fields_still_works(self):
        self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": self.future})
        item = self.apps()["a1"]
        self.assertEqual((item["interview_state"], item["interview_lead_id"]), ("scheduled", ""))
        self.assertEqual(iv.window(item)[1] - iv.window(item)[0], timedelta(minutes=30))

    def test_dm_crash_after_saving_does_not_break_the_booking(self):
        self.dm.side_effect = AttributeError("bot ohne fetch_user")
        response = self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": self.future})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(self.events()), 1)
        self.assertFalse(self.apps()["a1"]["interview_delivery"]["applicant_notice"]["ok"])

    def test_audit_trail(self):
        entries = []
        with patch.object(ws, "log_audit", lambda *a, **k: entries.append(a[2])):
            self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": self.future})
            self.post("/applications/interview-slot", {"app_id": "a2", "interview_at": self.future})
            self.post("/applications/interview-cancel", {"app_id": "a1"})
        self.assertEqual(entries, ["Bewerbungsgespräch geplant", "Bewerbungsgespräch: Terminkonflikt abgelehnt", "Bewerbungsgespräch abgesagt"])


class CalendarPageTests(RouteBase):
    def setUp(self):
        super().setUp()
        self.post("/applications/interview-slot", {"app_id": "a1", "interview_at": self.future, "lead_id": "50"})
        self.month = datetime.fromisoformat(self.future).strftime("%Y-%m")
        self.event_id = self.apps()["a1"]["interview_event_id"]

    def page(self, user):
        with patch.object(ws, "auth", make_auth(user)):
            return self.client.get(f"/calendar?month={self.month}")

    def test_recruiting_staff_see_interview_details_and_link(self):
        text = self.page("2").text
        for needle in ("Erika Synthetisch", "/applications#app-a1", "30 Min.", "Geplant", "Führungskraft", "data-interview-status"):
            self.assertIn(needle, text)

    def test_normal_members_never_see_applicant_data(self):
        text = self.page("3").text
        self.assertEqual(self.page("3").status_code, 200)
        for forbidden in ("Erika", "Synthetisch", "app-a1", "#app-", "Prüfer", "Mitglied 50", "interview_event_id", self.event_id):
            self.assertNotIn(forbidden, text)
        self.assertIn("Bewerbungsgespräch", text)
        self.assertIn("4 Teilnehmer", text)

    def test_legacy_event_title_does_not_leak(self):
        pulse_db.create_calendar_event("Bewerbungsgespräch Max Altfall", (datetime.now(UTC) + timedelta(days=3)).isoformat(timespec="seconds"),
                                       None, "event", "Discord", "Bewerbung app_alt", "1", "x")
        text = self.page("3").text
        self.assertNotIn("Altfall", text)
        self.assertNotIn("app_alt", text)

    def test_broken_application_file_fails_closed(self):
        with patch.object(ws, "load_json", side_effect=lambda path, default=None: (_ for _ in ()).throw(OSError()) if path == ws.APPS_FILE else default):
            text = self.page("3").text
        self.assertNotIn("Erika", text)

    def test_empty_state(self):
        for event in self.events():
            pulse_db.delete_calendar_event(event["id"])
        self.assertIn("keine Bewerbungsgespräche geplant", self.page("3").text)

    def test_unauthenticated_is_rejected(self):
        self.assertIn(self.client.get("/calendar", follow_redirects=False).status_code, (302, 303, 307, 401, 403, 503))


class ReminderJobTests(RouteBase):
    def schedule_in(self, minutes):
        apps = self.apps()
        start = datetime.now(UTC) + timedelta(minutes=minutes)
        iv.schedule(apps, "a1", start=start, minutes=30, lead_id="50", lead_name="Lena", actor_id="1", actor_name="Admin",
                    save=lambda d: ws.save_json(ws.APPS_FILE, d), db=pulse_db, now=datetime.now(UTC))

    def run_job(self, sender):
        bot = SimpleNamespace(get_guild=lambda gid: None, fetch_user=AsyncMock(return_value=SimpleNamespace(send=AsyncMock())))
        with patch.object(ws, "send_dm_notification", sender):
            return asyncio.run(ws.send_interview_reminders(bot))

    def test_reminder_once_to_applicant_and_team_even_when_dm_fails(self):
        self.schedule_in(30)
        sender = AsyncMock(return_value=False)
        self.assertEqual(self.run_job(sender), 1)
        self.assertEqual(self.run_job(sender), 0)
        self.assertEqual(sender.await_count, 1)                                   # failed DM is not retried
        item = self.apps()["a1"]
        self.assertFalse(item["interview_delivery"]["applicant_reminder"]["ok"])
        for uid in ("10", "50", "1"):
            self.assertTrue(any("gleich" in n["title"] for n in pulse_db.notifications(uid)), uid)

    def test_heads_up_a_day_ahead_goes_to_team_only(self):
        self.schedule_in(60 * 10)
        sender = AsyncMock(return_value=True)
        self.assertEqual(self.run_job(sender), 0)
        self.assertEqual(sender.await_count, 0)
        self.assertTrue(any("bevorstehend" in n["title"] for n in pulse_db.notifications("50")))
        self.assertFalse(any("gleich" in n["title"] for n in pulse_db.notifications("50")))
        self.run_job(sender)
        self.assertEqual(len([n for n in pulse_db.notifications("50") if "bevorstehend" in n["title"]]), 1)

    def test_reschedule_re_arms_and_cancel_silences(self):
        self.schedule_in(30)
        sender = AsyncMock(return_value=True)
        self.run_job(sender)
        self.schedule_in(40)
        self.assertEqual(self.run_job(sender), 1)
        self.post("/applications/interview-cancel", {"app_id": "a1"})
        self.assertEqual(self.run_job(sender), 0)
        self.assertEqual(sender.await_count, 2)

    def test_job_removes_calendar_row_of_decided_application(self):
        self.schedule_in(60 * 48)
        apps = self.apps()
        apps["a1"]["status"] = "rejected"
        ws.save_json(ws.APPS_FILE, apps)
        self.run_job(AsyncMock(return_value=True))
        self.assertEqual((self.events(), self.apps()["a1"]["interview_state"]), ([], "cancelled"))


@unittest.skipIf(os.name == "nt", "pulse_backup löscht den SQLite-Snapshot auf Windows nicht (WinError 32) – wie tests.test_full_backup läuft das in CI/Linux")
class BackupRestoreTests(unittest.TestCase):
    def test_application_link_and_calendar_row_survive_backup_and_restore(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base, backups = Path(tmp) / "app", Path(tmp) / "backups"
            base.mkdir()
            with patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")):
                pulse_db.init_db()
                apps = {"a1": record()}
                book(Store(apps), pulse_db, start=(datetime.now(UTC) + timedelta(days=2)).astimezone(BERLIN))
                (base / "applications.json").write_text(json.dumps(apps), encoding="utf-8")
                path, _ = pulse_backup.create_full_backup(base, backups)
                self.assertEqual(pulse_backup.verify_full_backup(path), [])
                event_id = apps["a1"]["interview_event_id"]
                pulse_db.delete_calendar_event(event_id)
                (base / "applications.json").write_text("{}", encoding="utf-8")
                pulse_backup.restore_full_backup(path, base, backups)
                restored = json.loads((base / "applications.json").read_text(encoding="utf-8"))["a1"]
                rows = {r["id"]: r for r in pulse_db.list_calendar_events()}
                self.assertEqual(restored["interview_event_id"], event_id)
                self.assertEqual(rows[event_id]["category"], "interview")
                self.assertEqual(iv.orphaned_events({"a1": restored}, list(rows.values())), [])


if __name__ == "__main__":
    unittest.main()
