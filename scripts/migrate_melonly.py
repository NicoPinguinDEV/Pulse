"""Operator-invoked Melonly migration utility; never run automatically at Pulse startup."""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Allow direct execution as: python scripts/migrate_melonly.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pulse_melonly_migration import (  # noqa: E402
    _load_source,
    export_sqlite_to_json,
    migrate_json_to_sqlite,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Melonly logs.json / SQLite migration (does not activate a backend)."
    )
    parser.add_argument("--json", required=True, help="Path to the current logs.json")
    parser.add_argument("--database", required=True, help="Path to the existing pulse.db")
    parser.add_argument("--backup-dir", required=True, help="Directory for verified pre-migration backups")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate JSON only; do not write anything")
    mode.add_argument("--migrate", action="store_true", help="Back up and import JSON into SQLite")
    mode.add_argument("--rollback-export", action="store_true", help="Export active SQLite data to JSON")
    parser.add_argument(
        "--confirm-offline",
        action="store_true",
        help="Confirm that the Pulse bot and dashboard are stopped for write operations",
    )
    args = parser.parse_args()

    source = Path(args.json)
    database = Path(args.database)
    backups = Path(args.backup_dir)

    if args.dry_run:
        raw, entries = _load_source(source)
        print(f"Dry-Run OK | Einträge: {len(entries)} | SHA-256: {hashlib.sha256(raw).hexdigest()}")
        print("Keine Dateien oder Datenbanken wurden verändert.")
        return 0

    if not args.confirm_offline:
        parser.error("Schreiboperationen erfordern --confirm-offline (Pulse muss gestoppt sein).")

    if args.migrate:
        result = migrate_json_to_sqlite(source, database, backups)
        print(
            f"{result['status']} | Einträge: {result['count']} | "
            f"Backend bleibt: {result['active_backend']}"
        )
        print(f"JSON-Sicherung: {result['json_backup']}")
        print(f"DB-Sicherung: {result['database_backup'] or '(keine vorhandene DB)'}")
        print(f"Quell-SHA-256: {result['source_sha256']}")
        return 0

    result = export_sqlite_to_json(database, source)
    print(
        f"{result['status']} | Einträge: {result['count']} | "
        f"Revision: {result['revision']} | Generation: {result['generation']}"
    )
    print(f"JSON-Datei: {result['path']}")
    print(f"SHA-256: {result['sha256']}")
    print(f"Vorherige JSON-Sicherung: {result['previous_json_backup'] or '(keine vorhandene Datei)'}")
    print("Hinweis: Backend-Metadaten wurden nicht umgeschaltet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
