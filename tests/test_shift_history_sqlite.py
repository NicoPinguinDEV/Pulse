"""Phase 14.7 (D-07 / A-05): shift history of shifts.json in SQLite – migration, way back, same results.

Only temporary folders and synthetic data. Every test patches SHIFTS_FILE, BACKUP_DIR and pulse_db.DB_PATH.
"""
import hashlib
import json
import os
import random
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_backup
import pulse_db
import pulse_shift_history as store
import webserver as ws
from pulse_shift_utils import credited_shift_seconds
from tests.test_pulse_admin_area import ADMIN, fake_auth_for


def synthetic_history(n: int, members: int = 200, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    now = ws.now_de().replace(hour=12, minute=0, second=0, microsecond=0)
    out = []
    for i in range(n):
        day = now - timedelta(days=rng.randint(0, 60), hours=rng.randint(0, 11))
        dur = rng.randint(600, 4 * 3600)
        out.append({"mod_id": str(1000 + rng.randrange(members)), "date": day.strftime("%Y-%m-%d"),
                    "started_at_iso": day.isoformat(timespec="seconds"),
                    "ended_at_iso": (day + timedelta(seconds=dur)).isoformat(timespec="seconds"),
                    "duration_seconds": dur, "shift_event_id": f"s{i}"})
    return out


def team_hours(history) -> dict:
    totals: dict = {}
    for entry in history:
        if isinstance(entry, dict):
            uid = str(entry.get("mod_id"))
            totals[uid] = totals.get(uid, 0) + credited_shift_seconds(entry)
    return totals


def weekly(history, active, members) -> dict:
    ws._WEEKLY_MEMO.update(key=None, history=None, totals=None)
    return {m: ws.calculate_weekly_seconds(m, history, active) for m in members}


class ShiftHistoryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(ws, "SHIFTS_FILE", str(self.root / "shifts.json")),
                        patch.object(ws, "DATA_FILE", str(self.root / "team_data.json")),
                        patch.object(ws, "BACKUP_DIR", str(self.root / "backups")),
                        patch.object(pulse_db, "DB_PATH", str(self.root / "pulse.db")),
                        patch.dict(os.environ, {store.ENV_VAR: ""})]
        for p in self.patches:
            p.start()
        pulse_db.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        ws._CORRUPT_JSON.pop(os.path.abspath(str(self.root / "shifts.json")), None)
        self.tmp.cleanup()

    def seed(self, history, active=None, **extra):
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": active or {}, "history": history, **extra})

    def file_bytes(self) -> bytes:
        return Path(ws.SHIFTS_FILE).read_bytes()

    def raw_file(self) -> dict:
        return json.loads(self.file_bytes())

    def db_rows(self) -> list:
        con = sqlite3.connect(pulse_db.DB_PATH)
        try:
            return [json.loads(r[0]) for r in con.execute("SELECT payload FROM shift_history ORDER BY seq")]
        finally:
            con.close()

    def migration_recorded(self) -> bool:
        con = sqlite3.connect(pulse_db.DB_PATH)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            return con.execute("SELECT 1 FROM schema_migrations WHERE name=?", (store.MIGRATION_NAME,)).fetchone() is not None
        finally:
            con.close()


class MigrationTests(ShiftHistoryBase):
    def test_default_is_unchanged_json(self):
        self.seed([{"mod_id": "1", "duration_seconds": 60}])
        self.assertEqual(store.apply_configured_backend(ws), {"status": "unchanged"})
        self.assertNotIn(store.MARKER_KEY, self.raw_file())
        self.assertEqual(len(self.raw_file()["history"]), 1)

    def test_empty_history(self):
        self.seed([])
        report = store.migrate(ws)
        self.assertEqual((report["status"], report["rows"]), ("migrated", 0))
        self.assertEqual(ws.load_shifts_raw(), {"active_shifts": {}, "history": []})
        self.assertTrue(store.is_sqlite(self.raw_file()))

    def test_missing_shifts_file(self):
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        self.assertEqual(ws.load_shifts_raw(), {"active_shifts": {}, "history": []})

    def test_20000_entries_with_identical_results(self):
        history = synthetic_history(20000)
        active = {"1001": {"status": "online", "started_at_iso": ws.now_de().isoformat(), "date": ws.now_de().strftime("%Y-%m-%d"),
                           "segment_start_iso": ws.now_de().isoformat(), "accumulated_seconds": 0}}
        self.seed(history, active, extra_key={"kept": True})
        members = sorted({e["mod_id"] for e in history})
        before = ws.load_shifts_raw()
        hours_before, weekly_before = team_hours(before["history"]), weekly(before["history"], {}, members)
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        after = ws.load_shifts_raw()
        self.assertEqual(after, before)  # order, all fields, extra top-level key
        self.assertEqual(len(self.db_rows()), 20000)
        self.assertEqual(self.raw_file()["history"], [])
        self.assertEqual(self.raw_file()["active_shifts"], active)
        self.assertEqual(team_hours(after["history"]), hours_before)
        self.assertEqual(weekly(after["history"], {}, members), weekly_before)
        self.assertGreater(sum(weekly_before.values()), 0)
        self.assertEqual(ws.weekly_history_totals(after["history"]), ws.weekly_history_totals(before["history"]))

    def test_old_timestamp_formats_and_missing_fields_are_kept_verbatim(self):
        history = [
            {"mod_id": "1", "date": "2026-1-5", "duration_seconds": 100},  # legacy day without zero padding
            {"mod_id": "1", "date": "2026-10-20", "started_at_iso": "2026-10-20T10:00:00", "ended_at_iso": "2026-10-20T11:00:00",
             "duration_seconds": 3600},  # naive timestamps
            {"mod_id": 2, "date": "20.10.2026", "started_at_iso": "2026-10-20T10:00:00.123456+02:00", "duration_seconds": "3600"},
            {"mod_id": "3", "started_at_iso": 1761000000, "ended_at_iso": None},  # no date, no duration
            {"mod_id": "4"},
            {},
            {"mod_id": "5", "date": "2026-10-20", "duration_seconds": 1.5, "notiz": "Ümläute ✓", "nested": {"a": [1, None]}},
            "kein-dict",
        ]
        self.seed(history)
        before = ws.load_shifts_raw()
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        after = ws.load_shifts_raw()
        self.assertEqual(after["history"], history)
        self.assertEqual([list(e) for e in after["history"] if isinstance(e, dict)],
                         [list(e) for e in history if isinstance(e, dict)])  # field order kept
        self.assertEqual(team_hours(after["history"]), team_hours(before["history"]))
        with patch.object(ws, "_in_week", lambda *a: True):
            self.assertEqual(ws.weekly_history_totals(list(after["history"][:7])),
                             ws.weekly_history_totals(list(before["history"][:7])))

    def test_duplicates_are_never_merged(self):
        entry = {"mod_id": "1", "date": "2026-10-20", "duration_seconds": 600, "shift_event_id": "x"}
        history = [entry, dict(entry), dict(entry, duration_seconds=601), dict(entry), {"shift_event_id": "x"}]
        self.seed(history)
        store.migrate(ws)
        self.assertEqual(ws.load_shifts_raw()["history"], history)
        self.assertEqual(len(self.db_rows()), 5)

    def test_failed_import_leaves_json_untouched_and_can_be_retried(self):
        self.seed(synthetic_history(1000))
        original = self.file_bytes()
        calls = {"n": 0}
        real = store._index_columns

        def broken(entry):
            calls["n"] += 1
            if calls["n"] == 500:
                raise RuntimeError("Absturz mitten im Import")
            return real(entry)

        with patch.object(store, "_index_columns", broken):
            report = store.migrate(ws)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.file_bytes(), original)
        self.assertFalse(self.migration_recorded())
        con = sqlite3.connect(pulse_db.DB_PATH)
        self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='shift_history'").fetchone())  # DDL rolled back
        con.close()
        self.assertTrue(os.path.isfile(report["json_backup"]))
        self.assertEqual(Path(report["json_backup"]).read_bytes(), original)
        self.assertEqual(len(ws.load_shifts_raw()["history"]), 1000)
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        self.assertEqual(len(self.db_rows()), 1000)

    def test_failure_while_switching_the_json_file_keeps_json_mode(self):
        self.seed(synthetic_history(50))
        original = self.file_bytes()
        with patch.object(ws, "_write_json_file", side_effect=OSError("Datenträger voll")):
            report = store.migrate(ws)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.file_bytes(), original)
        self.assertFalse(self.migration_recorded())
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        self.assertEqual(len(ws.load_shifts_raw()["history"]), 50)

    def test_failed_verification_restores_the_original_file(self):
        self.seed(synthetic_history(20))
        original = self.file_bytes()
        with patch.object(store, "attach", side_effect=lambda raw: {"active_shifts": {}, "history": []}):
            report = store.migrate(ws)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.file_bytes(), original)

    def test_invalid_structure_is_refused(self):
        Path(ws.SHIFTS_FILE).write_text(json.dumps({"active_shifts": {}, "history": {"a": 1}}), encoding="utf-8")
        original = self.file_bytes()
        self.assertEqual(store.migrate(ws)["status"], "failed")
        self.assertEqual(self.file_bytes(), original)

    def test_repeated_migration_is_a_no_op(self):
        self.seed(synthetic_history(300))
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        state = (self.file_bytes(), self.db_rows())
        for _ in range(3):
            self.assertEqual(store.migrate(ws)["status"], "already")
            with patch.dict(os.environ, {store.ENV_VAR: "sqlite"}):
                self.assertEqual(store.apply_configured_backend(ws)["status"], "already")
        self.assertEqual((self.file_bytes(), self.db_rows()), state)

    def test_backups_are_written_before_the_move(self):
        self.seed(synthetic_history(10))
        original = self.file_bytes()
        report = store.migrate(ws)
        self.assertEqual(Path(report["json_backup"]).read_bytes(), original)
        self.assertTrue(os.path.isfile(report["db_backup"]))


class RollbackTests(ShiftHistoryBase):
    def test_way_back_to_json_keeps_everything_written_in_sqlite_mode(self):
        history = synthetic_history(2000)
        self.seed(history, {}, other={"x": 1})
        store.migrate(ws)
        ok, _ = ws.apply_shift_action("1001", "start")
        self.assertTrue(ok)
        ok, _ = ws.apply_shift_action("1001", "end")
        self.assertTrue(ok)
        in_sqlite = ws.load_shifts_raw()
        self.assertEqual(len(in_sqlite["history"]), 2001)
        report = store.rollback(ws)
        self.assertEqual((report["status"], report["rows"]), ("rolled_back", 2001))
        raw = self.raw_file()
        self.assertNotIn(store.MARKER_KEY, raw)
        self.assertEqual(raw["history"], in_sqlite["history"])
        self.assertEqual(raw["other"], {"x": 1})
        self.assertEqual(ws.load_shifts_raw(), in_sqlite)
        self.assertFalse(self.migration_recorded())
        self.assertEqual(store.rollback(ws)["status"], "already")
        # JSON mode again: a write goes to the file only; a new migration replaces the old rows (no duplicates)
        ws.apply_shift_action("1002", "start")
        ws.apply_shift_action("1002", "end")
        self.assertEqual(len(self.raw_file()["history"]), 2002)
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        self.assertEqual(len(self.db_rows()), 2002)
        self.assertEqual(ws.load_shifts_raw()["history"], self.db_rows())

    def test_environment_switch_both_ways(self):
        self.seed(synthetic_history(5))
        with patch.dict(os.environ, {store.ENV_VAR: "sqlite"}):
            self.assertEqual(store.apply_configured_backend(ws)["status"], "migrated")
        with patch.dict(os.environ, {store.ENV_VAR: "json"}):
            self.assertEqual(store.apply_configured_backend(ws)["status"], "rolled_back")
        with patch.dict(os.environ, {store.ENV_VAR: "kaputt"}):
            self.assertEqual(store.apply_configured_backend(ws)["status"], "unchanged")
        self.assertEqual(len(self.raw_file()["history"]), 5)

    def test_init_runtime_applies_the_configured_backend(self):
        self.seed(synthetic_history(5))
        with patch.object(ws, "_RUNTIME_READY", False), patch.object(ws, "_session_key", lambda: None), \
                patch("pulse_next.setup_next"), patch.object(ws, "init_db"), patch.dict(os.environ, {store.ENV_VAR: "sqlite"}):
            ws.init_runtime()
        self.assertTrue(store.is_sqlite(self.raw_file()))


class SqliteModeTests(ShiftHistoryBase):
    def setUp(self):
        super().setUp()
        self.history = synthetic_history(500)
        self.seed(self.history)
        store.migrate(ws)

    def test_duty_actions_and_status(self):
        for action in ("start", "break", "resume"):
            ok, _ = ws.apply_shift_action("4242", action)
            self.assertTrue(ok, action)
        self.assertIn("4242", self.raw_file()["active_shifts"])
        self.assertIn("Du bist im Dienst", ws.shift_status_text("4242"))
        ok, _ = ws.apply_shift_action("4242", "end")
        self.assertTrue(ok)
        self.assertEqual(self.raw_file()["history"], [])
        self.assertNotIn("4242", self.raw_file()["active_shifts"])
        rows = self.db_rows()
        self.assertEqual((len(rows), rows[-1]["mod_id"]), (501, "4242"))
        self.assertEqual(self.raw_file()[store.MARKER_KEY]["rows"], 501)
        self.assertFalse(ws.apply_shift_action("4242", "end")[0])

    def test_a_save_writes_only_changed_rows(self):
        data = ws.load_shifts_raw()
        data["history"].append({"mod_id": "1", "duration_seconds": 5})
        con = sqlite3.connect(pulse_db.DB_PATH, isolation_level=None)
        try:
            con.execute("BEGIN")
            self.assertEqual(store._sync(con, data["history"]), 1)
            data["history"][3]["duration_confirmed"] = True
            self.assertEqual(store._sync(con, data["history"]), 1)
            self.assertEqual(store._sync(con, data["history"][:-10]), 10)
            con.execute("ROLLBACK")
        finally:
            con.close()

    def test_commit_shift_event_updates_the_row(self):
        event = dict(self.history[42], auto_warn_status="done")
        ws.commit_shift_event(event)
        self.assertEqual(self.db_rows()[42], event)
        self.assertEqual(len(self.db_rows()), 500)

    def test_auto_close_view_and_raw_load(self):
        old = (ws.now_de() - timedelta(hours=30)).isoformat()
        data = ws.load_shifts_raw()
        data["active_shifts"]["77"] = {"status": "online", "started_at_iso": old, "segment_start_iso": old,
                                       "date": old[:10], "accumulated_seconds": 0}
        ws.save_json(ws.SHIFTS_FILE, data)
        self.assertIn("77", ws.load_shifts_raw()["active_shifts"])
        shown = ws.load_shifts()
        self.assertNotIn("77", shown["active_shifts"])
        self.assertTrue(shown["history"][-1]["auto_closed"])
        self.assertEqual(len(self.db_rows()), 500)  # pure read: nothing persisted

    def test_save_without_history_key_never_erases_rows(self):
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}})
        self.assertEqual(len(self.db_rows()), 500)
        self.assertEqual(len(ws.load_shifts_raw()["history"]), 500)

    def test_missing_database_rows_block_writes_instead_of_overwriting(self):
        con = sqlite3.connect(pulse_db.DB_PATH)
        con.execute("DELETE FROM shift_history")
        con.commit()
        con.close()
        self.assertEqual(ws.load_json(ws.SHIFTS_FILE, {"leer": True}), {"leer": True})
        with self.assertRaises(ws.JsonWriteBlocked):
            ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": []})
        with self.assertRaises(ws.JsonWriteBlocked):
            ws.apply_shift_action("1", "start")
        self.assertEqual(self.raw_file()[store.MARKER_KEY]["rows"], 500)

    def test_failed_file_write_after_commit_keeps_the_committed_state(self):
        # Phase 14.7b: the file is written only after COMMIT – a failure there no longer rolls anything back;
        # pulse.db holds the save and the journal brings shifts.json up to it (tests/test_shift_history_crash.py).
        data = ws.load_shifts_raw()
        data["active_shifts"]["9"] = {"status": "online"}
        data["history"].append({"mod_id": "9"})
        real_write = ws._write_json_file

        def write_then_fail(path, payload):
            real_write(path, payload)
            raise sqlite3.OperationalError("disk I/O error")

        with patch.object(ws, "_write_json_file", write_then_fail):  # committed: no error for the caller
            ws.save_json(ws.SHIFTS_FILE, data)
        self.assertEqual(len(self.db_rows()), 501)
        stored = ws.load_shifts_raw()
        self.assertEqual(stored["active_shifts"], {"9": {"status": "online"}})
        self.assertEqual(stored["history"], data["history"])
        self.assertTrue(store.status(ws)["consistent"])

    def test_failing_commit_rolls_back_rows_and_file(self):
        before = self.file_bytes()

        class FailingCommit:
            def __init__(self, con):
                self._con = con

            def execute(self, sql, *args):
                if sql == "COMMIT":
                    raise sqlite3.OperationalError("disk I/O error")
                return self._con.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._con, name)

        real_connect = store._connect
        data = ws.load_shifts_raw()
        data["active_shifts"]["9"] = {"status": "online"}
        data["history"].append({"mod_id": "9"})
        with patch.object(store, "_connect", lambda path=None: FailingCommit(real_connect(path))), \
                self.assertRaises(sqlite3.OperationalError):
            ws.save_json(ws.SHIFTS_FILE, data)
        self.assertEqual(self.file_bytes(), before)
        self.assertEqual(len(self.db_rows()), 500)
        self.assertEqual(ws.load_shifts_raw()["active_shifts"], {})

    def test_partly_missing_rows_block_writes(self):
        con = sqlite3.connect(pulse_db.DB_PATH)
        con.execute("DELETE FROM shift_history WHERE seq >= 490")
        con.commit()
        con.close()
        self.assertEqual(ws.load_json(ws.SHIFTS_FILE, {"leer": True}), {"leer": True})
        with self.assertRaises(ws.JsonWriteBlocked):
            ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": []})
        self.assertEqual(self.raw_file()[store.MARKER_KEY]["rows"], 500)
        self.assertEqual(len(self.db_rows()), 490)

    def test_marked_file_from_before_a_way_back_does_not_read_stale_rows(self):
        marked = self.file_bytes()
        self.assertEqual(store.rollback(ws)["status"], "rolled_back")
        Path(ws.SHIFTS_FILE).write_bytes(marked)  # e.g. a hand copy of the old marked shifts.json
        self.assertEqual(ws.load_json(ws.SHIFTS_FILE, {"leer": True}), {"leer": True})
        with self.assertRaises(ws.JsonWriteBlocked):
            ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": []})

    def test_startup_switch_never_raises(self):
        store.rollback(ws)
        original = self.file_bytes()
        with patch.object(store, "_json_backup", side_effect=OSError("Datenträger voll")), \
                patch.dict(os.environ, {store.ENV_VAR: "sqlite"}):
            report = store.apply_configured_backend(ws)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.file_bytes(), original)
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        marked = self.file_bytes()
        with patch.object(store, "_read", side_effect=store.ShiftHistoryUnavailable("Tabelle fehlt")), \
                patch.dict(os.environ, {store.ENV_VAR: "json"}):
            self.assertEqual(store.apply_configured_backend(ws)["status"], "failed")
        self.assertEqual(self.file_bytes(), marked)

    def test_privacy_export_and_consistency_read_the_sqlite_history(self):
        import pulse_retention
        uid = self.history[0]["mod_id"]
        exported = pulse_retention.export_person(ws, uid)
        self.assertEqual(exported["shifts"]["history"], [e for e in self.history if e["mod_id"] == uid])

    def test_status(self):
        info = store.status(ws)
        self.assertEqual((info["backend"], info["marker_rows"], info["db_rows"]), ("sqlite", 500, 500))

    def test_status_script_is_read_only(self):
        import contextlib
        import io
        import importlib.util
        spec = importlib.util.spec_from_file_location("shift_history_cli", Path(__file__).resolve().parent.parent / "scripts" / "shift_history.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        before = (self.file_bytes(), self.db_rows())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["x", "status"]), 0)
        self.assertTrue(json.loads(out.getvalue())["consistent"])
        self.assertEqual((self.file_bytes(), self.db_rows()), before)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["x", "migrate"]), 2)  # no switching from the script


class BackupRestoreTests(ShiftHistoryBase):
    def test_full_backup_and_restore_in_sqlite_mode(self):
        self.seed(synthetic_history(300))
        store.migrate(ws)
        backup, manifest = pulse_backup.create_full_backup(self.root, self.root / "backups")
        self.assertIn("db/pulse.db", {f["name"] for f in manifest["files"]})
        snapshot = ws.load_shifts_raw()
        ws.apply_shift_action("1", "start")
        ws.apply_shift_action("1", "end")
        self.assertEqual(len(ws.load_shifts_raw()["history"]), 301)
        pulse_backup.restore_full_backup(backup, self.root, self.root / "backups", include_files=False, lock=ws._io_lock)
        self.assertEqual(ws.load_shifts_raw(), snapshot)

    def test_backup_copies_database_and_shifts_file_under_the_json_lock(self):
        import threading
        self.seed(synthetic_history(20))
        store.migrate(ws)
        seen = []
        real = pulse_backup._snapshot_sqlite

        def snapshot(source, target):
            if Path(source).name == "pulse.db":
                probe = threading.Thread(target=lambda: seen.append(ws._io_lock.acquire(blocking=False)))
                probe.start()
                probe.join()
            return real(source, target)

        with patch.object(pulse_backup, "_snapshot_sqlite", snapshot):
            _, manifest = pulse_backup.create_full_backup(self.root, self.root / "backups")
        self.assertEqual(seen, [False])  # another thread could not save shifts meanwhile
        self.assertTrue({"db/pulse.db", "json/shifts.json"} <= {f["name"] for f in manifest["files"]})

    def test_old_json_backup_restores_into_json_mode(self):
        history = synthetic_history(300)
        self.seed(history)
        old_backup, _ = pulse_backup.create_full_backup(self.root, self.root / "backups", prefix="alt_")
        store.migrate(ws)
        ws.apply_shift_action("1", "start")
        ws.apply_shift_action("1", "end")
        pulse_backup.restore_full_backup(old_backup, self.root, self.root / "backups", include_files=False, lock=ws._io_lock)
        raw = self.raw_file()
        self.assertNotIn(store.MARKER_KEY, raw)
        self.assertEqual(ws.load_shifts_raw()["history"], history)
        ws.apply_shift_action("2", "start")
        ws.apply_shift_action("2", "end")
        self.assertEqual(len(self.raw_file()["history"]), 301)  # JSON mode: writes go to the file again


class DashboardResultTests(ShiftHistoryBase):
    PAGES = ("/stats", "/dashboard", "/ultimate/reports.csv", "/ultimate/export.json", "/ultimate/api/team", "/export/team.csv")

    def render(self):
        with patch.object(ws, "auth", fake_auth_for(ADMIN)), patch.object(ws, "now_de", lambda: self.frozen):
            client = TestClient(ws.app, raise_server_exceptions=False)
            return {p: (r.status_code, hashlib.sha256(self.comparable(p, r.content)).hexdigest()) for p in self.PAGES
                    for r in [client.get(p, follow_redirects=False)]}

    @staticmethod
    def comparable(page, content: bytes) -> bytes:
        if page == "/ultimate/export.json":  # "generated_at" is the real clock of the request, not shift data
            data = json.loads(content)
            data.pop("generated_at", None)
            return json.dumps(data, sort_keys=True).encode()
        return content

    def test_pages_render_identically_after_migration(self):
        import pulse_next
        pulse_next.setup_next()  # tables of the reports/export pages, as at start
        self.frozen = ws.now_de()
        self.seed(synthetic_history(3000, members=40))
        before = self.render()
        self.assertEqual({status for status, _ in before.values()}, {200}, before)
        store.migrate(ws)
        self.assertEqual(self.render(), before)

class ExistingSuitesInSqliteModeTests(unittest.TestCase):
    def test_duty_confirmation_and_stats_suites_pass_with_the_sqlite_history(self):
        from tests import shift_history_sqlite_mode
        result = shift_history_sqlite_mode.run(["tests.test_dienst", "tests.test_shift_confirmation", "tests.test_stats_page"],
                                               verbosity=0)
        self.assertTrue(result.wasSuccessful(), [str(t) for t, _ in result.failures + result.errors])
        self.assertGreaterEqual(result.testsRun, 15)
        self.assertGreater(result.migrations, 5)


if __name__ == "__main__":
    unittest.main()
