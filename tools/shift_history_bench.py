"""Phase 14.7: measure the shift history store with synthetic data (temporary folder, never the live data).

    python tools/shift_history_bench.py --history 20000 --members 200 [--backend json|sqlite] [--json out.json]

Measures loading the history, writing one finished shift, the weekly statistics for every member, peak
Python memory while loading, the number of JSON parses and SQLite connections, and a checksum of all
results (team hours per member, weekly totals) so that runs of both backends can be compared for equality.
Works on code without the SQLite store, too (then only --backend json), for a before/after comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sqlite3
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _history(ws, n: int, members: list[str]) -> list[dict]:
    rng = random.Random(14_7)
    now = ws.now_de().replace(hour=12, minute=0, second=0, microsecond=0)  # same data for every run of a day
    out = []
    for i in range(n):
        day = now - timedelta(days=rng.randint(0, 400), hours=rng.randint(0, 23))
        dur = rng.randint(600, 4 * 3600)
        out.append({"mod_id": rng.choice(members), "date": day.strftime("%Y-%m-%d"),
                    "started_at_iso": day.isoformat(timespec="seconds"),
                    "ended_at_iso": (day + timedelta(seconds=dur)).isoformat(timespec="seconds"),
                    "duration_seconds": dur, "shift_event_id": f"s{i}"})
    for i in range(0, n, 97):  # recent entries so that the current week is not empty
        out[i]["date"] = now.strftime("%Y-%m-%d")
    return out


def _median_ms(fn, runs: int) -> float:
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(times), 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=int, default=20000)
    parser.add_argument("--members", type=int, default=200)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--backend", choices=("json", "sqlite"), default="json")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="pulse-shiftbench-")
    os.environ["PULSE_DB_PATH"] = str(Path(tmp) / "pulse.db")
    os.environ.pop("DISCORD_TOKEN", None)
    sys.path.insert(0, str(ROOT))
    os.chdir(tmp)
    import webserver as ws
    for name in ("DATA_FILE", "CONFIG_FILE", "LOGS_FILE", "SHIFTS_FILE", "MEETINGS_FILE", "APPS_FILE", "AUDIT_FILE"):
        setattr(ws, name, str(Path(tmp) / Path(getattr(ws, name)).name))
    ws.BASE_DIR = ws.DATA_DIR = Path(tmp)
    ws.BACKUP_DIR = str(Path(tmp) / "backups")
    ws.init_runtime()

    members = [str(10_000 + i) for i in range(args.members)]
    ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": _history(ws, args.history, members)})
    if args.backend == "sqlite":
        import pulse_shift_history
        report = pulse_shift_history.migrate(ws)
        if report.get("status") != "migrated":
            print(f"Migration fehlgeschlagen: {report}")
            return 1

    counts = {"parses": 0, "connects": 0}
    orig_load, orig_loads, orig_connect = json.load, json.loads, sqlite3.connect

    def c_load(*a, **k):
        counts["parses"] += 1
        return orig_load(*a, **k)

    def c_loads(*a, **k):
        counts["parses"] += 1
        return orig_loads(*a, **k)

    def c_connect(*a, **k):
        counts["connects"] += 1
        return orig_connect(*a, **k)

    result = {"backend": args.backend, "history": args.history, "members": args.members, "python": sys.version.split()[0]}
    result["load_raw_ms"] = _median_ms(ws.load_shifts_raw, args.runs)
    result["load_ms"] = _median_ms(ws.load_shifts, args.runs)

    json.load, json.loads, sqlite3.connect = c_load, c_loads, c_connect
    ws.load_shifts_raw()
    result["load_json_parses"], result["load_sqlite_connects"] = counts["parses"], counts["connects"]
    json.load, json.loads, sqlite3.connect = orig_load, orig_loads, orig_connect

    tracemalloc.start()
    db = ws.load_shifts_raw()
    result["load_peak_mib"] = round(tracemalloc.get_traced_memory()[1] / 1048576, 2)
    tracemalloc.stop()
    del db

    def week_stats():
        shifts = ws.load_shifts()
        ws._WEEKLY_MEMO.update(key=None, history=None, totals=None)  # measure the full pass, not the memo
        return {m: ws.calculate_weekly_seconds(m, shifts["history"], shifts["active_shifts"]) for m in members}

    result["weekly_stats_200_ms"] = _median_ms(week_stats, args.runs)

    write_times = []
    for i in range(args.runs):
        uid = members[i % len(members)]
        ok, _ = ws.apply_shift_action(uid, "start")
        assert ok
        t = time.perf_counter()
        ok, _ = ws.apply_shift_action(uid, "end")
        write_times.append((time.perf_counter() - t) * 1000)
        assert ok
    result["write_end_shift_ms"] = round(statistics.median(write_times), 2)
    json.load, json.loads, sqlite3.connect = c_load, c_loads, c_connect
    counts.update(parses=0, connects=0)
    ws.apply_shift_action(members[0], "start")
    ws.apply_shift_action(members[0], "end")
    result["write_json_parses"], result["write_sqlite_connects"] = counts["parses"], counts["connects"]
    json.load, json.loads, sqlite3.connect = orig_load, orig_loads, orig_connect

    shifts = ws.load_shifts()
    hist = shifts["history"]
    totals: dict = {}
    for e in hist:
        totals[str(e.get("mod_id"))] = totals.get(str(e.get("mod_id")), 0) + ws.credited_shift_seconds(e)
    ws._WEEKLY_MEMO.update(key=None, history=None, totals=None)
    weekly = {m: ws.calculate_weekly_seconds(m, hist, shifts["active_shifts"]) for m in members}
    # Entries written by this run carry the current time: only the seeded part is compared across runs.
    seeded = json.dumps(hist[:args.history], sort_keys=True, ensure_ascii=False).encode()
    result["history_entries"] = len(hist)
    result["seeded_history_sha256"] = hashlib.sha256(seeded).hexdigest()[:16]
    result["team_hours_total_s"] = sum(totals.values())
    result["weekly_total_s_ex_written"] = sum(weekly.values()) - sum(
        ws.credited_shift_seconds(e) for e in hist[args.history:])
    result["shifts_json_mb"] = round(os.path.getsize(ws.SHIFTS_FILE) / 1048576, 3)
    db_file = Path(os.environ["PULSE_DB_PATH"])
    result["pulse_db_mb"] = round(db_file.stat().st_size / 1048576, 3) if db_file.exists() else 0.0
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
