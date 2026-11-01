"""Phase 14.7b: child process for tests/test_shift_history_crash.py – a real Pulse process that dies hard.

    python tests/shift_crash_child.py crash <root> <point> [<op>]  # op (default end), os._exit(77) at <point>
    python tests/shift_crash_child.py crash_recover <root> <point>  # recover() dies at before/after_replace
    python tests/shift_crash_child.py loop <root>            # end shifts one by one until the parent kills it
    python tests/shift_crash_child.py verify <root> [<uid>]  # restart: recovery (twice), read, optionally end <uid>

Only temporary folders under <root>. The last stdout line of `verify` is ``RESULT <json>``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CRASH_EXIT = 77
POINTS = ("after_begin", "after_rows", "before_commit", "after_commit", "before_replace", "after_replace",
          "before_cache")
ACTIVE = ["1001", "1002", "1003"]
LOOP_ACTIVE = [str(5000 + i) for i in range(400)]
OPS = ("end", "edit", "trim", "nohistory")  # INSERT, UPDATE, DELETE of rows, save without the "history" key


def apply_op(data: dict, op: str) -> dict:
    """The change the crashing child saves – the parent applies the same to compute the expected state."""
    if op == "end":
        del data["active_shifts"][ACTIVE[0]]
        data["history"].append(end_entry(ACTIVE[0]))
    elif op == "edit":  # e.g. a leader confirms a shift
        data["history"][5] = dict(data["history"][5], duration_seconds=1, duration_confirmed=True)
    elif op == "trim":  # e.g. a privacy deletion
        data["history"] = data["history"][:-3]
    elif op == "nohistory":
        data = {"active_shifts": {k: v for k, v in data["active_shifts"].items() if k != ACTIVE[0]}}
    return data


def end_entry(uid: str, n: int = 0) -> dict:
    """The history entry the child writes for ending the shift of `uid` – the parent expects exactly this."""
    return {"mod_id": uid, "date": "2026-10-30", "duration_seconds": 3600 + n,
            "started_at_iso": "2026-10-30T10:00:00+01:00", "ended_at_iso": "2026-10-30T11:00:00+01:00",
            "shift_event_id": f"end-{uid}-{n}", "note": "Ünïcødé ✓"}


def _open(root: Path):
    os.environ["PULSE_SHIFT_HISTORY_BACKEND"] = ""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import pulse_db
    import webserver as ws
    ws.SHIFTS_FILE = str(root / "shifts.json")
    ws.DATA_FILE = str(root / "team_data.json")
    ws.BACKUP_DIR = str(root / "backups")
    pulse_db.DB_PATH = str(root / "pulse.db")
    return ws


def _die() -> None:
    sys.stdout.flush()
    os._exit(CRASH_EXIT)


def _end(ws, uid: str, n: int = 0) -> None:
    data = ws.load_shifts_raw()
    del data["active_shifts"][uid]
    data["history"].append(end_entry(uid, n))
    ws.save_json(ws.SHIFTS_FILE, data)


def _trap_replace(shifts: str, point: str) -> None:
    real_replace = os.replace

    def replace(src, dst, *args, **kwargs):
        target = os.path.abspath(dst) == shifts
        if target and point == "before_replace":
            _die()
        real_replace(src, dst, *args, **kwargs)
        if target and point == "after_replace":
            _die()

    os.replace = replace


def crash(root: Path, point: str, op: str = "end") -> int:
    ws = _open(root)
    import pulse_shift_history as store
    shifts = os.path.abspath(ws.SHIFTS_FILE)
    data = ws.load_shifts_raw()  # read before any trap is set

    class Trap:  # sqlite3.Connection with a crash at one statement of the save
        def __init__(self, con):
            self._con = con

        def execute(self, sql, *args):
            if point == "before_commit" and sql == "COMMIT":
                _die()
            cur = self._con.execute(sql, *args)
            if point == "after_begin" and sql.startswith("BEGIN IMMEDIATE"):
                _die()
            if point == "after_rows" and ("INTO shift_history(" in sql or "UPDATE shift_history SET" in sql
                                          or "DELETE FROM shift_history WHERE" in sql):
                _die()
            if point == "after_commit" and sql == "COMMIT":
                _die()
            return cur

        def __getattr__(self, name):
            return getattr(self._con, name)

    real_connect = store._connect
    store._connect = lambda path=None: Trap(real_connect(path))
    _trap_replace(shifts, point)
    if point == "before_cache":
        store.remember_mode = lambda *a, **k: _die()
    ws.save_json(ws.SHIFTS_FILE, apply_op(data, op))
    return 0  # the trap did not fire – the parent treats this as a failed test


def crash_recover(root: Path, point: str) -> int:
    """A restart that dies itself while it writes shifts.json from the journal."""
    ws = _open(root)
    import pulse_shift_history as store
    _trap_replace(os.path.abspath(ws.SHIFTS_FILE), point)
    store.recover(ws)
    return 0


def loop(root: Path) -> int:
    ws = _open(root)
    print("READY", flush=True)
    for n, uid in enumerate(LOOP_ACTIVE):
        _end(ws, uid, n)
    return 0


def verify(root: Path, then_end: str | None) -> int:
    ws = _open(root)
    import pulse_shift_history as store
    first = store.recover(ws)  # what webserver.init_runtime does at start
    second = store.recover(ws)
    result = {"first": first, "second": second, "corrupt": ws.corrupt_json_files()}
    result["state"] = ws.load_json(ws.SHIFTS_FILE, None)
    result["status"] = store.status(ws)
    if then_end and result["state"] is not None:
        try:
            _end(ws, then_end, 99)
            result["after_end"] = ws.load_shifts_raw()
        except Exception as exc:
            result["end_error"] = f"{type(exc).__name__}: {exc}"
    with open(ws.SHIFTS_FILE, encoding="utf-8") as f:
        result["marker"] = json.load(f).get(store.MARKER_KEY)
    print("RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    cmd, where = sys.argv[1], Path(sys.argv[2])
    if cmd == "crash":
        raise SystemExit(crash(where, sys.argv[3], *sys.argv[4:5]))
    if cmd == "crash_recover":
        raise SystemExit(crash_recover(where, sys.argv[3]))
    if cmd == "loop":
        raise SystemExit(loop(where))
    raise SystemExit(verify(where, sys.argv[3] if len(sys.argv) > 3 else None))
