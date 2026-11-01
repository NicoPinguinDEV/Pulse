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
KEEP_BACKUPS = 5  # newest pre-migration copies that are kept


def _applied(con: sqlite3.Connection) -> set[str]:
    con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    return {row[0] for row in con.execute("SELECT name FROM schema_migrations")}


def _backup(db_path: str, backup_dir: str, name: str) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:60]
    target = os.path.join(backup_dir, f"pre_migration_{safe}_{stamp}_{os.getpid()}_{datetime.now().microsecond}.db")
    src = dst = None
    try:
        src, dst = sqlite3.connect(db_path), sqlite3.connect(target)
        src.backup(dst)
        integrity = dst.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise sqlite3.DatabaseError("Pre-Migration-Sicherung besteht integrity_check nicht")
    except Exception:
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass
        raise
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()
    old = sorted((f for f in os.listdir(backup_dir) if f.startswith("pre_migration_") and f.endswith(".db")),
                 key=lambda f: os.path.getmtime(os.path.join(backup_dir, f)), reverse=True)
    for name in old[KEEP_BACKUPS:]:
        try:
            os.remove(os.path.join(backup_dir, name))
        except OSError:
            pass
    return target


def run(db_path: str, backup_dir: str, migrations: list[Migration] | None = None) -> dict:
    """Apply pending migrations. Returns {"applied": [...], "backup": path or "", "failed": name or ""}."""
    migrations = MIGRATIONS if migrations is None else migrations
    report = {"applied": [], "backup": "", "failed": ""}
    if not migrations:
        return report
    # Explicit transactions: in its default mode sqlite3 does not wrap CREATE/ALTER in a transaction,
    # so a failed migration would leave half a schema behind. SQLite itself can roll DDL back.
    con = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    try:
        # Inspect the migration ledger without creating it. The first database write
        # must happen only after the pre-migration snapshot has been completed.
        ledger_exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone() is not None
        done = (
            {row[0] for row in con.execute("SELECT name FROM schema_migrations")}
            if ledger_exists else set()
        )
        pending = [(name, fn) for name, fn in migrations if name not in done]
        if not pending:
            return report
        report["backup"] = _backup(db_path, backup_dir, pending[0][0])
        _applied(con)  # create the ledger only after the verified pre-migration backup
        for name, fn in pending:
            try:
                con.execute("BEGIN")
                fn(con)
                con.execute("INSERT INTO schema_migrations(name, applied_at) VALUES (?, ?)",
                            (name, datetime.now(timezone.utc).isoformat(timespec="seconds")))
                con.execute("COMMIT")
            except Exception as exc:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                report["failed"] = f"{name}: {type(exc).__name__}: {exc}"
                print(f"🔴 Migration {name} fehlgeschlagen – Sicherung: {report['backup']} ({type(exc).__name__}: {exc})")
                break
            report["applied"].append(name)
    finally:
        con.close()
    return report
