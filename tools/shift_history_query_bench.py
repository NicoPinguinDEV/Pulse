"""Reproducible Phase 14.8 shift-history read benchmark.

Runs against isolated temporary JSON and SQLite stores. It never reads or writes
production paths. Default fixture: 20,000 rows / 200 members.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def seed_history(ws, count: int, members: list[str]) -> list[dict]:
    rng = random.Random(14_8)
    now = ws.now_de().replace(hour=12, minute=0, second=0, microsecond=0)
    history = []
    for index in range(count):
        day = now - timedelta(days=rng.randint(0, 365), hours=rng.randint(0, 23))
        if index % 97 == 0:
            day = now - timedelta(days=rng.randint(0, 20), hours=rng.randint(0, 23))
        duration = rng.randint(300, 4 * 3600)
        history.append({
            "mod_id": rng.choice(members),
            "date": day.strftime("%Y-%m-%d"),
            "started_at_iso": day.isoformat(timespec="seconds"),
            "ended_at_iso": (day + timedelta(seconds=duration)).isoformat(timespec="seconds"),
            "duration_seconds": duration,
            "shift_event_id": f"phase148-{index}",
            "benchmark_payload": {"ordinal": index, "preserve": True},
        })
    return history


def median_ms(fn, runs: int) -> float:
    samples = []
    fn()  # warm-up
    for _ in range(runs):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 3)


def peak_mib(fn) -> float:
    tracemalloc.start()
    try:
        fn()
        return round(tracemalloc.get_traced_memory()[1] / 1048576, 3)
    finally:
        tracemalloc.stop()


def digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def measure_backend(ws, store, members: list[str], now, runs: int) -> dict:
    target = members[0]
    date_from = (now.date() - timedelta(days=30)).isoformat()
    date_to = (now.date() + timedelta(days=1)).isoformat()

    def full_read():
        return ws.load_shifts_raw()["history"]

    def member_read():
        return store.query_history(ws, mod_id=target, limit=1000)

    def range_read():
        return store.query_history(ws, mod_id=target, date_from=date_from, date_to=date_to, limit=1000)

    def aggregate_read():
        return {
            "weekly": store.weekly_totals(ws, mod_ids=members),
            "lifetime_seconds": store.total_credited_seconds(ws),
            "range": store.range_totals(ws, members, date_from, date_to),
        }

    history = full_read()
    member = member_read()
    period = range_read()
    aggregate = aggregate_read()

    if len(history) < 20000:
        raise RuntimeError(f"Baseline enthält nur {len(history)} Einträge; mindestens 20.000 erwartet")

    metrics = {
        "full_history_ms": median_ms(full_read, runs),
        "member_query_ms": median_ms(member_read, runs),
        "period_query_ms": median_ms(range_read, runs),
        "weekly_and_lifetime_aggregate_ms": median_ms(aggregate_read, runs),
        "full_history_peak_mib": peak_mib(full_read),
        "member_query_peak_mib": peak_mib(member_read),
        "period_query_peak_mib": peak_mib(range_read),
        "aggregate_peak_mib": peak_mib(aggregate_read),
        "full_history_rows": len(history),
        "member_matching_rows": member["total"],
        "member_page_rows": len(member["items"]),
        "period_matching_rows": period["total"],
        "period_page_rows": len(period["items"]),
        "weekly_members_with_rows": len(aggregate["weekly"]),
        "range_members_with_rows": len(aggregate["range"]),
        "lifetime_credited_seconds": aggregate["lifetime_seconds"],
        "full_history_sha256": digest(history),
        "member_result_sha256": digest(member["items"]),
        "period_result_sha256": digest(period["items"]),
        "weekly_result_sha256": digest(aggregate["weekly"]),
        "weekly_and_lifetime_sha256": digest(aggregate),
    }
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=int, default=20000)
    parser.add_argument("--members", type=int, default=200)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--json", default="shift-history-benchmark.json")
    args = parser.parse_args()
    if args.history < 20000:
        parser.error("--history muss mindestens 20000 sein")
    if args.members < 1 or args.runs < 3:
        parser.error("--members muss >= 1 und --runs >= 3 sein")

    os.environ.pop("PULSE_SHIFT_HISTORY_BACKEND", None)
    os.environ.pop("DISCORD_TOKEN", None)
    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="pulse-phase148-bench-") as tmp:
        root = Path(tmp)
        os.environ["PULSE_DB_PATH"] = str(root / "pulse.db")
        sys.path.insert(0, str(ROOT))
        os.chdir(root)
        import webserver as ws
        import pulse_db
        import pulse_shift_history as store

        for name in ("DATA_FILE", "CONFIG_FILE", "LOGS_FILE", "SHIFTS_FILE", "MEETINGS_FILE",
                     "APPS_FILE", "AUDIT_FILE"):
            setattr(ws, name, str(root / Path(getattr(ws, name)).name))
        ws.BASE_DIR = ws.DATA_DIR = root
        ws.BACKUP_DIR = str(root / "backups")
        pulse_db.DB_PATH = str(root / "pulse.db")
        ws.init_runtime()

        members = [str(10000 + index) for index in range(args.members)]
        now = ws.now_de()
        date_from = (now.date() - timedelta(days=30)).isoformat()
        history = seed_history(ws, args.history, members)
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": history})
        json_metrics = measure_backend(ws, store, members, now, args.runs)

        migrated = store.migrate(ws)
        if migrated.get("status") != "migrated":
            raise RuntimeError(f"SQLite-Testmigration fehlgeschlagen: {migrated.get('status')}")
        sqlite_metrics = measure_backend(ws, store, members, now, args.runs)
        query_plan = store.explain_member_query_plan(ws, members[0], date_from)
        if not any("idx_shift_history_member" in item for item in query_plan):
            raise RuntimeError(f"Erwarteter Mitglieds-/Datumsindex fehlt im Query-Plan: {query_plan}")

        compared = (
            "full_history_sha256", "member_result_sha256", "period_result_sha256",
            "weekly_result_sha256", "weekly_and_lifetime_sha256",
        )
        mismatches = [key for key in compared if json_metrics[key] != sqlite_metrics[key]]
        if mismatches:
            raise RuntimeError(f"JSON-/SQLite-Ergebnisse weichen ab: {', '.join(mismatches)}")

        report = {
            "phase": "14.8",
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "seed_rows": args.history,
            "members": args.members,
            "median_runs": args.runs,
            "json_fallback": json_metrics,
            "sqlite_indexed": sqlite_metrics,
            "explain_query_plan": query_plan,
            "result_equivalence": "identical",
            "comparison_keys": list(compared),
            "notes": [
                "Alle Daten stammen aus einem temporären Verzeichnis.",
                "member_query und period_query liefern nur die angeforderte Seite; totals zeigen alle passenden Zeilen.",
                "Aggregate materialisieren keine vollständige Historienliste im Python-Aufrufer.",
            ],
        }
        output = json.dumps(report, indent=2, ensure_ascii=False)
        print(output)
    os.chdir(original_cwd)
    Path(args.json).write_text(output + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
