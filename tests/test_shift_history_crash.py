"""Phase 14.7b: crash-safe saving of the SQLite shift history – real process crashes, restart, recovery.

Every crash test kills a real Pulse child process (os._exit / kill) at one point of a save and then starts a
fresh process that runs the start-up recovery and reads the data back (tests/shift_crash_child.py). Only
temporary folders and synthetic data.
"""
import hashlib
import json
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pulse_backup
import pulse_db
import pulse_shift_history as store
import webserver as ws
from tests import shift_crash_child as child
from tests.test_shift_history_sqlite import ShiftHistoryBase, synthetic_history, team_hours

CHILD = Path(child.__file__)
_STARTED = (ws.now_de() - timedelta(hours=1)).isoformat(timespec="seconds")  # younger than the 24 h auto-close
ACTIVE = {uid: {"status": "online", "started_at_iso": _STARTED, "date": _STARTED[:10], "segment_start_iso": _STARTED}
          for uid in child.ACTIVE}


def _env() -> dict:
    env = dict(os.environ)
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", **{store.ENV_VAR: ""})
    return env


def run_child(*args, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHILD), *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=_env())


def restart(root, then_end: str | None = None) -> dict:
    proc = run_child("verify", root, *([then_end] if then_end else []))
    lines = [line for line in proc.stdout.splitlines() if line.startswith("RESULT ")]
    if proc.returncode != 0 or not lines:
        raise AssertionError(f"Neustart fehlgeschlagen ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}")
    return json.loads(lines[-1][len("RESULT "):])


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CrashBase(ShiftHistoryBase):
    def setUp(self):
        super().setUp()
        self.history = synthetic_history(60)
        self.seed(self.history, active=json.loads(json.dumps(ACTIVE)))
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        self.generation = self.raw_file()[store.MARKER_KEY]["generation"]

    def old_state(self) -> dict:
        return {"active_shifts": ACTIVE, "history": self.history}

    def new_state(self) -> dict:
        active = {k: v for k, v in ACTIVE.items() if k != child.ACTIVE[0]}
        return {"active_shifts": active, "history": self.history + [child.end_entry(child.ACTIVE[0])]}

    def state_after(self, op: str) -> dict:
        data = child.apply_op(json.loads(json.dumps(self.old_state())), op)
        data.setdefault("history", self.history)  # a save without "history" keeps the stored rows
        return data

    def fresh(self) -> None:
        store.rollback(ws)  # back to JSON, then a new migration: a new generation for each case
        self.seed(self.history, active=json.loads(json.dumps(ACTIVE)))
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        self.generation = self.raw_file()[store.MARKER_KEY]["generation"]

    def crash(self, point: str, op: str = "end") -> None:
        proc = run_child("crash", self.root, point, op)
        self.assertEqual(proc.returncode, child.CRASH_EXIT, f"Absturzpunkt {point} nicht erreicht:\n{proc.stderr[-3000:]}")

    def assert_consistent(self, result: dict, expected: dict) -> None:
        self.assertEqual(result["corrupt"], {})
        self.assertEqual(result["state"], expected)  # count, order and every payload field
        self.assertEqual(team_hours(result["state"]["history"]), team_hours(expected["history"]))
        status = result["status"]
        self.assertEqual(status["backend"], "sqlite")
        self.assertTrue(status["consistent"], status)
        self.assertFalse(status["pending"], status)
        self.assertEqual(status["db_rows"], len(expected["history"]))
        self.assertEqual(status["marker_rows"], len(expected["history"]))
        self.assertEqual(status["marker_revision"], status["db_revision"])
        self.assertEqual(result["marker"]["generation"], self.generation)
        self.assertEqual(result["second"]["status"], "consistent")  # recovery again: nothing left to do


class ProcessCrashTests(CrashBase):
    """A hard crash at each step of a save, then a restart of a fresh instance."""

    BEFORE_COMMIT = ("after_begin", "after_rows", "before_commit")
    AFTER_COMMIT = ("after_commit", "before_replace", "after_replace", "before_cache")

    def check_point(self, point: str) -> None:
        self.crash(point)
        result = restart(self.root, then_end=child.ACTIVE[1])
        committed = point in self.AFTER_COMMIT
        expected = self.new_state() if committed else self.old_state()
        self.assert_consistent(result, expected)
        if point in ("after_commit", "before_replace"):
            self.assertEqual(result["first"]["status"], "recovered")
            self.assertEqual(result["first"]["revision"], result["status"]["db_revision"])
        else:
            self.assertEqual(result["first"]["status"], "consistent")
        # writing goes on normally: the next shift end lands exactly once
        after = result["after_end"]
        self.assertNotIn(child.ACTIVE[1], after["active_shifts"])
        self.assertEqual(after["history"], expected["history"] + [child.end_entry(child.ACTIVE[1], 99)])
        ids = [e.get("shift_event_id") for e in after["history"]]
        self.assertEqual(len(ids), len(set(ids)), "doppelter Schichteintrag")

    def test_crash_after_begin(self):
        self.check_point("after_begin")

    def test_crash_after_rows_changed(self):
        self.check_point("after_rows")

    def test_crash_right_before_commit(self):
        self.check_point("before_commit")

    def test_crash_after_commit(self):
        self.check_point("after_commit")

    def test_crash_while_writing_the_json_file(self):
        self.check_point("before_replace")

    def test_crash_right_after_the_json_file_was_replaced(self):
        self.check_point("after_replace")

    def test_crash_before_the_mode_cache_is_updated(self):
        self.check_point("before_cache")

    def test_every_point_is_covered(self):
        self.assertEqual(set(self.BEFORE_COMMIT) | set(self.AFTER_COMMIT), set(child.POINTS))

    def test_edit_trim_and_save_without_history_at_both_sides_of_the_commit(self):
        cases = [("edit", "after_rows"), ("trim", "after_rows")] + [
            (op, point) for op in ("edit", "trim", "nohistory") for point in ("before_commit", "after_commit")]
        for op, point in cases:
            with self.subTest(op=op, point=point):
                self.fresh()
                self.crash(point, op)
                result = restart(self.root)
                expected = self.state_after(op) if point == "after_commit" else self.old_state()
                self.assert_consistent(result, expected)

    def test_crash_while_the_recovery_itself_writes(self):
        for point in ("before_replace", "after_replace"):
            with self.subTest(point=point):
                self.fresh()
                self.crash("after_commit")
                proc = run_child("crash_recover", self.root, point)
                self.assertEqual(proc.returncode, child.CRASH_EXIT, proc.stderr[-3000:])
                result = restart(self.root)
                self.assert_consistent(result, self.new_state())
                self.assertEqual(result["first"]["status"], "recovered" if point == "before_replace" else "consistent")


class ShutdownTests(ShiftHistoryBase):
    """Kill/terminate (SIGKILL/SIGTERM, TerminateProcess on Windows) while shifts are saved in a loop."""

    def test_kill_at_random_moments_never_loses_or_doubles_a_shift(self):
        history = synthetic_history(40)
        active = {uid: {"status": "online", "started_at_iso": "2026-10-30T10:00:00+01:00"} for uid in child.LOOP_ACTIVE}
        rng = random.Random(147)
        for attempt in range(4):
            with self.subTest(attempt=attempt):
                store.rollback(ws)  # fresh start in JSON mode, then a new migration (new generation)
                self.seed(history, active=json.loads(json.dumps(active)))
                self.assertEqual(store.migrate(ws)["status"], "migrated")
                proc = subprocess.Popen([sys.executable, str(CHILD), "loop", str(self.root)], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=_env())
                assert proc.stdout is not None
                try:
                    while (line := proc.stdout.readline()) and line.strip() != "READY":
                        pass  # start-up banners of the modules
                    self.assertEqual(line.strip(), "READY")
                    time.sleep(rng.uniform(0.05, 0.8))
                    if attempt % 2:
                        proc.terminate()  # shutdown (SIGTERM; TerminateProcess on Windows)
                    else:
                        proc.kill()
                    proc.wait(timeout=30)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.stdout.close()
                result = restart(self.root)
                self.assertEqual(result["corrupt"], {})
                state = result["state"]
                ended = len(state["history"]) - len(history)
                # exactly a prefix of the loop is done: every shift is either still active or once in the history
                expected_tail = [child.end_entry(uid, n) for n, uid in enumerate(child.LOOP_ACTIVE[:ended])]
                self.assertEqual(state["history"], history + expected_tail)
                self.assertEqual(sorted(state["active_shifts"]), sorted(child.LOOP_ACTIVE[ended:]))
                self.assertTrue(result["status"]["consistent"], result["status"])
                self.assertEqual(result["second"]["status"], "consistent")


class RecoveryRuleTests(CrashBase):
    """Unclear states are reported and block writes – never corrected silently."""

    def crashed_after_commit(self):
        self.crash("after_commit")
        store._MODE.update(key=None, sqlite=False)  # the file changed behind this process

    def assert_blocked(self, reason_part: str):
        before = self.file_bytes()
        report = store.recover(ws)
        self.assertEqual(report["status"], "blocked")
        self.assertIn(reason_part, report["error"])
        self.assertEqual(ws.load_json(ws.SHIFTS_FILE, {"leer": True}), {"leer": True})
        self.assertTrue(any(reason_part in p for p in ws.corrupt_json_files().values()))
        with self.assertRaises(ws.JsonWriteBlocked):
            ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": []})
        self.assertEqual(self.file_bytes(), before)  # nothing written
        self.assertFalse(store.status(ws)["consistent"])
        name, ok, detail = store.system_check(ws)
        self.assertFalse(ok)
        self.assertIn(reason_part, detail)

    def set_meta(self, key, value):
        con = sqlite3.connect(pulse_db.DB_PATH)
        con.execute("INSERT OR REPLACE INTO shift_history_meta(key, value) VALUES (?, ?)", (key, value))
        con.commit()
        con.close()

    def test_corrupt_journal_blocks_and_is_reported(self):
        self.crashed_after_commit()
        self.set_meta("journal", '{"revision": 1, "payload": "{kaputt')
        self.assert_blocked("Änderungsjournal")

    def test_journal_with_wrong_checksum_blocks(self):
        self.crashed_after_commit()
        con = sqlite3.connect(pulse_db.DB_PATH)
        journal = json.loads(con.execute("SELECT value FROM shift_history_meta WHERE key='journal'").fetchone()[0])
        con.close()
        journal["payload"] = journal["payload"].replace("1002", "9999")
        self.set_meta("journal", json.dumps(journal))
        self.assert_blocked("Änderungsjournal")

    def test_file_changed_after_the_crash_is_not_overwritten(self):
        self.crashed_after_commit()
        raw = self.raw_file()
        raw["active_shifts"]["4242"] = {"status": "online"}  # e.g. edited by hand meanwhile
        Path(ws.SHIFTS_FILE).write_text(json.dumps(raw, indent=4, ensure_ascii=False), encoding="utf-8")
        self.assert_blocked("unvollständig")

    def test_older_database_than_file_blocks(self):
        ok, _ = ws.apply_shift_action("1002", "end")
        self.assertTrue(ok)
        self.assertEqual(self.raw_file()[store.MARKER_KEY]["revision"], 1)
        self.set_meta("revision", "0")  # e.g. pulse.db restored alone from an older copy
        self.assert_blocked("Revision")

    def test_database_more_than_one_save_ahead_blocks(self):
        self.crashed_after_commit()
        self.set_meta("revision", "7")
        self.assert_blocked("Revision")

    def test_recovery_is_logged(self):
        self.crashed_after_commit()
        with patch("builtins.print") as printed:
            report = store.recover(ws)
        self.assertEqual(report["status"], "recovered")
        self.assertTrue(any("wiederhergestellt" in str(c.args[0]) for c in printed.call_args_list))
        name, ok, detail = store.system_check(ws)
        self.assertTrue(ok)
        self.assertIn("wiederhergestellt", detail)

    def test_first_read_in_the_running_process_recovers_too(self):
        self.crashed_after_commit()
        self.assertEqual(ws.load_shifts_raw(), self.new_state())
        self.assertEqual(store.recover(ws)["status"], "consistent")

    def test_json_write_failure_after_commit_is_recovered_without_doubles(self):
        data = ws.load_shifts_raw()
        del data["active_shifts"]["1002"]
        data["history"].append(child.end_entry("1002"))
        with patch.object(ws, "_write_json_file", side_effect=OSError("Datenträger voll")), \
                patch("builtins.print") as printed:
            ws.save_json(ws.SHIFTS_FILE, data)  # committed: the caller sees success, the shift *is* ended
        self.assertTrue(any("in pulse.db gespeichert" in str(c.args[0]) for c in printed.call_args_list))
        self.assertIn("1002", self.raw_file()["active_shifts"])  # file still old, pulse.db already new
        self.assertTrue(store.status(ws)["pending"])
        _, ok, detail = store.system_check(ws)
        self.assertFalse(ok)
        self.assertIn("nicht nachgezogen", detail)
        state = ws.load_shifts_raw()  # recovers
        self.assertNotIn("1002", state["active_shifts"])
        self.assertEqual(state["history"].count(child.end_entry("1002")), 1)
        ok, msg = ws.apply_shift_action("1002", "end")
        self.assertEqual((ok, msg), (False, "Diese Aktion ist gerade nicht möglich."))  # no second end
        self.assertEqual(ws.load_shifts_raw()["history"].count(child.end_entry("1002")), 1)

    def test_a_failed_catch_up_does_not_block_until_restart(self):
        self.crashed_after_commit()
        real_write, fails = ws._write_json_file, [2]

        def flaky(path, payload):
            if fails[0]:
                fails[0] -= 1
                raise PermissionError("Datei wird von einem anderen Prozess verwendet")
            return real_write(path, payload)

        with patch.object(ws, "_write_json_file", flaky):
            self.assertEqual(ws.load_json(ws.SHIFTS_FILE, {"leer": True}), {"leer": True})  # 1st try fails
            self.assertTrue(ws.corrupt_json_files())
            self.assertEqual(ws.load_json(ws.SHIFTS_FILE, {"leer": True}), {"leer": True})  # 2nd try fails
            self.assertEqual(ws.load_shifts_raw(), self.new_state())  # disk fine again: no restart needed
        self.assertEqual(ws.corrupt_json_files(), {})
        self.assertTrue(store.status(ws)["consistent"])

    def test_stale_mode_cache_never_switches_a_json_file(self):
        self.assertEqual(store.rollback(ws)["status"], "rolled_back")
        data = ws.load_shifts_raw()
        store._MODE.update(key=store._stat_key(ws.SHIFTS_FILE), sqlite=True)  # stale: says SQLite
        data["active_shifts"]["4711"] = {"status": "online"}
        ws.save_json(ws.SHIFTS_FILE, data)
        raw = self.raw_file()
        self.assertNotIn(store.MARKER_KEY, raw)
        self.assertEqual(raw, data)

    def test_journal_of_another_generation_or_with_history_blocks(self):
        for change in ("generation", "history"):
            with self.subTest(change=change):
                self.fresh()
                self.crashed_after_commit()
                con = sqlite3.connect(pulse_db.DB_PATH)
                original = con.execute("SELECT value FROM shift_history_meta WHERE key='journal'").fetchone()[0]
                con.close()
                journal = json.loads(original)
                payload = json.loads(journal["payload"])
                if change == "generation":
                    payload[store.MARKER_KEY]["generation"] = "fremd"
                else:
                    payload["history"] = [{"mod_id": "untergeschoben"}]
                journal["payload"] = json.dumps(payload)
                journal["payload_sha256"] = hashlib.sha256(journal["payload"].encode("utf-8")).hexdigest()
                self.set_meta("journal", json.dumps(journal))
                self.assert_blocked("Änderungsjournal")
                self.set_meta("journal", original)  # the intact journal is recovered normally
                self.assertEqual(store.recover(ws)["status"], "recovered")
                ws._CORRUPT_JSON.pop(os.path.abspath(ws.SHIFTS_FILE), None)

    def test_messages_contain_no_member_data(self):
        self.crashed_after_commit()
        self.set_meta("journal", "kaputt")
        report = store.recover(ws)
        texts = [report["error"], *ws.corrupt_json_files().values(), store.system_check(ws)[2], str(store.status(ws))]
        for text in texts:
            for secret in ("1001", "1002", "Ünïcødé", str(self.root)):
                self.assertNotIn(secret, text)

    def test_line_endings_changed_after_the_crash_are_still_recognised(self):
        self.crashed_after_commit()
        raw = Path(ws.SHIFTS_FILE).read_bytes()  # flip the line endings: CRLF -> LF on Windows, LF -> CRLF on Linux
        flipped = raw.replace(b"\r\n", b"\n") if b"\r\n" in raw else raw.replace(b"\n", b"\r\n")
        self.assertNotEqual(flipped, raw)
        Path(ws.SHIFTS_FILE).write_bytes(flipped)
        self.assertEqual(store.recover(ws)["status"], "recovered")  # e.g. data folder copied Windows <-> Linux
        self.assertEqual(ws.load_shifts_raw(), self.new_state())

    def test_way_back_after_a_crash_does_not_double_the_shift(self):
        self.crashed_after_commit()
        report = store.rollback(ws)
        self.assertEqual(report["status"], "rolled_back")
        raw = self.raw_file()
        self.assertNotIn(store.MARKER_KEY, raw)
        self.assertEqual(raw, self.new_state())

    def test_init_runtime_recovers_before_switching(self):
        calls = []
        with patch.object(store, "recover", side_effect=lambda w: calls.append("recover") or {"status": "consistent"}), \
                patch.object(store, "apply_configured_backend", side_effect=lambda w: calls.append("switch")), \
                patch.object(ws, "_RUNTIME_READY", False), patch.object(ws, "_session_key"), \
                patch("pulse_next.setup_next"), patch("pulse_migrations.run"), patch.object(ws, "init_db"), \
                patch.object(pulse_db, "init_db"), patch("os.makedirs"), patch("pathlib.Path.mkdir"):
            ws.init_runtime()
        self.assertEqual(calls, ["recover", "switch"])

    def test_recover_never_raises(self):
        with patch.object(store, "_read_meta", side_effect=sqlite3.OperationalError("disk I/O error")):
            self.assertEqual(store.recover(ws)["status"], "blocked")

    def test_json_mode_needs_no_recovery(self):
        self.assertEqual(store.rollback(ws)["status"], "rolled_back")
        self.assertEqual(store.recover(ws)["status"], "json")

    def test_marker_from_phase_14_7_without_revision_still_works(self):
        raw = self.raw_file()
        raw[store.MARKER_KEY].pop("revision", None)
        Path(ws.SHIFTS_FILE).write_text(json.dumps(raw, indent=4, ensure_ascii=False), encoding="utf-8")
        con = sqlite3.connect(pulse_db.DB_PATH)
        con.execute("DELETE FROM shift_history_meta WHERE key IN ('revision', 'journal')")
        con.commit()
        con.close()
        self.assertEqual(store.recover(ws)["status"], "consistent")
        self.assertEqual(ws.load_shifts_raw(), self.old_state())
        ok, _ = ws.apply_shift_action("1002", "end")
        self.assertTrue(ok)
        self.assertEqual(self.raw_file()[store.MARKER_KEY]["revision"], 1)
        self.assertTrue(store.status(ws)["consistent"])


class ParallelTests(CrashBase):
    def test_parallel_shift_changes_and_reads_stay_consistent(self):
        uids = [str(7000 + i) for i in range(40)]
        with ws._io_lock:
            data = ws.load_shifts_raw()
            data["active_shifts"].update({u: {"status": "online"} for u in uids})
            ws.save_json(ws.SHIFTS_FILE, data)
        errors = []

        def end(chunk):
            try:
                for n, uid in enumerate(chunk):
                    with ws._io_lock:  # load and save without an await in between, like apply_shift_action
                        d = ws.load_shifts_raw()
                        del d["active_shifts"][uid]
                        d["history"].append(child.end_entry(uid, n))
                        ws.save_json(ws.SHIFTS_FILE, d)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        def read():
            for _ in range(30):
                d = ws.load_shifts_raw()
                ended = {e["mod_id"] for e in d["history"] if str(e.get("shift_event_id", "")).startswith("end-7")}
                both = ended & set(d["active_shifts"])
                missing = set(uids) - ended - set(d["active_shifts"])
                if both or missing:
                    errors.append(AssertionError(f"Zwischenstand inkonsistent: {both or missing}"))

        threads = [threading.Thread(target=end, args=(uids[i::4],)) for i in range(4)]
        threads += [threading.Thread(target=read) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        state = ws.load_shifts_raw()
        self.assertFalse(set(uids) & set(state["active_shifts"]))
        self.assertEqual(sorted(e["mod_id"] for e in state["history"][len(self.history):]), sorted(uids))
        self.assertTrue(store.status(ws)["consistent"])


class BackupRecoveryTests(CrashBase):
    def test_backup_of_a_crashed_state_restores_and_recovers(self):
        self.crash("after_commit")
        store._MODE.update(key=None, sqlite=False)
        backup, manifest = pulse_backup.create_full_backup(self.root, self.root / "backups")
        self.assertTrue({"db/pulse.db", "json/shifts.json"} <= {f["name"] for f in manifest["files"]})
        backup_hash = sha(backup)
        first = restart(self.root, then_end=child.ACTIVE[1])  # recovered and written on
        self.assertEqual(first["first"]["status"], "recovered")
        pulse_backup.restore_full_backup(backup, self.root, self.root / "backups", include_files=False, lock=ws._io_lock)
        store._MODE.update(key=None, sqlite=False)
        again = restart(self.root)  # the restored pair is the crashed state again → same recovery
        self.assertEqual(again["first"]["status"], "recovered")
        self.assert_consistent(again, self.new_state())
        self.assertEqual(sha(backup), backup_hash)  # a valid backup is never changed

    def test_backup_right_after_a_save_is_consistent(self):
        ws.apply_shift_action("1002", "end")
        backup, _ = pulse_backup.create_full_backup(self.root, self.root / "backups")
        expected = ws.load_shifts_raw()
        ws.apply_shift_action("1003", "end")
        pulse_backup.restore_full_backup(backup, self.root, self.root / "backups", include_files=False, lock=ws._io_lock)
        store._MODE.update(key=None, sqlite=False)
        self.assertEqual(store.recover(ws)["status"], "consistent")
        self.assertEqual(ws.load_shifts_raw(), expected)

    def test_old_full_json_backup_still_restores_into_json_mode(self):
        self.assertEqual(store.rollback(ws)["status"], "rolled_back")
        old, _ = pulse_backup.create_full_backup(self.root, self.root / "backups", prefix="alt_")
        old_hash = sha(old)
        self.assertEqual(store.migrate(ws)["status"], "migrated")
        ws.apply_shift_action("1002", "end")
        pulse_backup.restore_full_backup(old, self.root, self.root / "backups", include_files=False, lock=ws._io_lock)
        store._MODE.update(key=None, sqlite=False)
        self.assertEqual(store.recover(ws)["status"], "json")
        self.assertEqual(ws.load_shifts_raw(), self.old_state())
        self.assertEqual(sha(old), old_hash)

    def test_shifts_file_of_another_generation_is_blocked(self):
        old_marked = self.file_bytes()
        self.assertEqual(store.rollback(ws)["status"], "rolled_back")
        self.assertEqual(store.migrate(ws)["status"], "migrated")  # new generation
        Path(ws.SHIFTS_FILE).write_bytes(old_marked)  # restored alone from an older SQLite-mode backup
        store._MODE.update(key=None, sqlite=False)
        report = store.recover(ws)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("unterschiedlichen Ständen", report["error"])
        self.assertEqual(self.file_bytes(), old_marked)
