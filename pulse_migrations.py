"""A-05 (Grundlage): versioned, idempotent database migrations with a backup before the first change.

* Every migration has a unique name and runs exactly once (`schema_migrations` table in pulse.db).
* Before the first pending migration, pulse.db is copied with the SQLite backup API to
  `backups/pre_migration_<name>_<time>.db`.
* A migration runs inside one transaction: if it fails, nothing of it is kept and it is retried
  on the next start; later migrations wait.
* `MIGRATIONS` is still empty – the JSON → SQLite moves (shifts, logs, applications) will be added
  here one file at a time, each with read fallback and a way back.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Callable

Migration = tuple[str, Callable[[sqlite3.Connection], None]]
MIGRATIONS: list[Migration] = []


def _applied(con: sqlite3.Connection) -> set[str]:
    con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    return {row[0] for row in con.execute("SELECT name FROM schema_migrations")}


def _backup(db_path: str, backup_dir: str, name: str) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:60]
    target = os.path.join(backup_dir, f"pre_migration_{safe}_{stamp}.db")
    src, dst = sqlite3.connect(db_path), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def run(db_path: str, backup_dir: str, migrations: list[Migration] | None = None) -> dict:
    """Apply pending migrations. Returns {"applied": [...], "backup": path or "", "failed": name or ""}."""
    migrations = MIGRATIONS if migrations is None else migrations
    report = {"applied": [], "backup": "", "failed": ""}
    if not migrations:
        return report
    con = sqlite3.connect(db_path, timeout=30)
    try:
        done = _applied(con)
        con.commit()
        pending = [(name, fn) for name, fn in migrations if name not in done]
        if not pending:
            return report
        report["backup"] = _backup(db_path, backup_dir, pending[0][0])
        for name, fn in pending:
            try:
                with con:  # one transaction per migration
                    fn(con)
                    con.execute("INSERT INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                                (name, datetime.now(timezone.utc).isoformat(timespec="seconds")))
            except Exception as exc:
                report["failed"] = f"{name}: {type(exc).__name__}: {exc}"
                print(f"🔴 Migration {name} fehlgeschlagen – Sicherung: {report['backup']} ({type(exc).__name__}: {exc})")
                break
            report["applied"].append(name)
    finally:
        con.close()
    return report
