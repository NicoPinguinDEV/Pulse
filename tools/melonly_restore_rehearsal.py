#!/usr/bin/env python3
"""Manual synthetic 100K Melonly full-backup/restore and JSON rollback rehearsal.

This tool uses an isolated temporary directory. It never opens the configured
production data directory and never changes the application backend setting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pulse_backup
import pulse_db
from pulse_melonly_migration import export_sqlite_to_json
from pulse_melonly_store import MelonlyStore

_ACTIONS = ("Warn", "Kick", "Ban", "Bolo", "Note")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resource_snapshot() -> dict:
    result: dict[str, float] = {}
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                key, value, *_ = line.split()
                result[key.rstrip(":").lower() + "_mb"] = round(int(value) / 1024, 1)
    except (OSError, ValueError):
        pass
    for key, candidate in (
        ("container_current_mb", Path("/sys/fs/cgroup/memory.current")),
        ("container_peak_mb", Path("/sys/fs/cgroup/memory.peak")),
        ("container_limit_mb", Path("/sys/fs/cgroup/memory.max")),
    ):
        try:
            raw = candidate.read_text(encoding="ascii").strip()
            if raw != "max":
                result[key] = round(int(raw) / (1024 * 1024), 1)
        except (OSError, ValueError):
            continue
    return result


def _synthetic_entries(count: int) -> list[dict]:
    return [
        {
            "id": f"BENCH-{index:07d}",
            "target_user": f"Player{index % 5000:04d}",
            "roblox_id": str(100000 + index % 5000),
            "type": _ACTIONS[index % len(_ACTIONS)],
            "reason": f"synthetic restore rehearsal record {index % 97}",
            "moderator_id": str(900000 + index % 25),
            "created_at": f"2026-10-{(index % 28) + 1:02d}T12:00:00+00:00",
            "fixture": {"source": "phase-15.5", "sequence": index},
        }
        for index in range(count)
    ]


def run_rehearsal(count: int) -> dict:
    timings: dict[str, float] = {}
    with tempfile.TemporaryDirectory(prefix="pulse-melonly-restore-rehearsal-") as temp:
        root = Path(temp)
        data_dir = root / "data"
        backup_dir = root / "backups"
        data_dir.mkdir()
        pulse_db.DB_PATH = str(data_dir / "pulse.db")

        logs_path = data_dir / "logs.json"
        rows = _synthetic_entries(count)
        with logs_path.open("w", encoding="utf-8", newline="") as stream:
            json.dump(rows, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        source_hash = _sha256(logs_path)

        store = MelonlyStore(data_dir / "pulse.db")
        store.initialize()
        started = time.perf_counter()
        imported = store.import_entries(rows, source_sha256=source_hash)
        timings["sqlite_import_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if imported != count or store.count() != count:
            raise RuntimeError("SQLite-Importanzahl weicht vom synthetischen Datensatz ab.")
        del rows

        # Simulate a future SQLite-active state in this isolated fixture only.
        with sqlite3.connect(data_dir / "pulse.db", timeout=15) as connection:
            connection.execute(
                "UPDATE melonly_logs_meta SET value='sqlite' WHERE key='active_backend'"
            )
        baseline_meta = store.metadata()
        if baseline_meta.get("active_backend") != "sqlite":
            raise RuntimeError("Synthetische SQLite-Testkonfiguration konnte nicht hergestellt werden.")

        started = time.perf_counter()
        archive, manifest = pulse_backup.create_full_backup(data_dir, backup_dir)
        timings["backup_create_ms"] = round((time.perf_counter() - started) * 1000, 3)
        manifest_names = {entry["name"] for entry in manifest["files"]}
        if "db/pulse.db" not in manifest_names:
            raise RuntimeError("pulse.db fehlt im Vollbackup.")
        if "json/logs.json" not in manifest_names:
            raise RuntimeError("logs.json-Migrationssicherung fehlt im Vollbackup.")

        expected_metadata = manifest["sqlite_metadata"]["db/pulse.db"]
        expected_melonly = expected_metadata["melonly"]
        if expected_melonly.get("row_count") != count:
            raise RuntimeError("Melonly-Zeilenanzahl in den Backup-Metadaten ist falsch.")
        if expected_melonly.get("active_backend") != "sqlite":
            raise RuntimeError("Aktives Backend in den Backup-Metadaten ist falsch.")

        started = time.perf_counter()
        verification_problems = pulse_backup.verify_full_backup(archive)
        timings["archive_verify_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if verification_problems:
            raise RuntimeError("Backup-Prüfung fehlgeschlagen: " + "; ".join(verification_problems[:5]))

        started = time.perf_counter()
        preview = pulse_backup.preview_restore(archive, data_dir)
        timings["restore_preview_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if preview.get("problems"):
            raise RuntimeError("Restore-Vorschau meldet Probleme: " + "; ".join(preview["problems"][:5]))
        if preview.get("sqlite_metadata") != manifest.get("sqlite_metadata"):
            raise RuntimeError("SQLite-Metadaten in der Restore-Vorschau weichen ab.")

        started = time.perf_counter()
        trial = pulse_backup.restore_test(archive)
        timings["isolated_trial_restore_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if not trial.get("ok"):
            raise RuntimeError("Isolierter Test-Restore fehlgeschlagen: " + "; ".join(trial["problems"][:5]))
        if trial.get("sqlite_metadata") != manifest.get("sqlite_metadata"):
            raise RuntimeError("SQLite-Metadaten des Test-Restores weichen ab.")

        # Create a clearly different live state. A restore must put the archived
        # SQLite generation and logs.json migration copy back exactly.
        store.append({
            "id": "AFTER-BACKUP",
            "target_user": "SyntheticMutation",
            "type": "Note",
            "reason": "must disappear after restore",
        })
        logs_path.write_text(
            json.dumps([{"fixture": "changed-after-backup"}], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        started = time.perf_counter()
        restore_report = pulse_backup.restore_full_backup(archive, data_dir, backup_dir)
        timings["full_restore_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if store.count() != count:
            raise RuntimeError("Zeilenanzahl nach vollständigem Restore stimmt nicht.")
        restored_meta = store.metadata()
        for key in ("active_backend", "generation", "revision", "import_count", "import_source_sha256"):
            if restored_meta.get(key) != baseline_meta.get(key):
                raise RuntimeError(f"SQLite-Metadatum {key!r} wurde nicht exakt wiederhergestellt.")
        if _sha256(logs_path) != source_hash:
            raise RuntimeError("logs.json-Migrationssicherung wurde beim Restore nicht bytegleich wiederhergestellt.")
        if pulse_backup._integrity(data_dir / "pulse.db") != "ok":
            raise RuntimeError("PRAGMA integrity_check ist nach dem Restore nicht OK.")
        if restore_report.get("sqlite_metadata") != manifest.get("sqlite_metadata"):
            raise RuntimeError("Restore-Bericht enthält abweichende SQLite-Metadaten.")

        # Verify the explicit SQLite -> JSON rollback path and its generation /
        # revision guards. This changes only the temporary fixture.
        started = time.perf_counter()
        rollback_report = export_sqlite_to_json(data_dir / "pulse.db", logs_path)
        timings["sqlite_to_json_rollback_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if rollback_report.get("count") != count or rollback_report.get("sha256") != source_hash:
            raise RuntimeError("JSON-Rückexport ist nicht bytegleich mit dem ursprünglichen logs.json.")
        for stale_guard in (
            {
                "expected_generation": "stale-generation",
                "expected_revision": baseline_meta["revision"],
            },
            {
                "expected_generation": baseline_meta["generation"],
                "expected_revision": "stale-revision",
            },
        ):
            try:
                store.set_active_backend("json", **stale_guard)
            except ValueError:
                pass
            else:
                raise RuntimeError("Ein veralteter Generation-/Revision-Guard wurde akzeptiert.")
            if store.metadata().get("active_backend") != "sqlite":
                raise RuntimeError("Ein abgelehnter Guard hat den Backend-Marker verändert.")

        store.set_active_backend(
            "json",
            expected_generation=baseline_meta["generation"],
            expected_revision=baseline_meta["revision"],
        )
        if store.metadata().get("active_backend") != "json":
            raise RuntimeError("JSON-Backend-Marker wurde nach dem geprüften Rückexport nicht gesetzt.")
        if _sha256(logs_path) != source_hash:
            raise RuntimeError("logs.json stimmt nach dem Backend-Rückweg nicht mit der Quelle überein.")

        resources = _resource_snapshot()
        return {
            "rehearsal": "Pulse Melonly 100K full backup, restore and JSON rollback",
            "synthetic_data_only": True,
            "production_data_accessed": False,
            "production_backend_changed": False,
            "entries": count,
            "parity": "passed",
            "backup_verified": True,
            "isolated_trial_restore": "passed",
            "full_restore": "passed",
            "sqlite_integrity": "ok",
            "sqlite_metadata": {
                "active_backend_during_fixture": expected_melonly.get("active_backend"),
                "generation_restored": restored_meta.get("generation") == baseline_meta.get("generation"),
                "revision_restored": restored_meta.get("revision") == baseline_meta.get("revision"),
                "row_count": expected_melonly.get("row_count"),
            },
            "json_rollback": {
                "status": rollback_report.get("status"),
                "count": rollback_report.get("count"),
                "sha256_matches_source": rollback_report.get("sha256") == source_hash,
                "active_backend_after_guarded_rollback": store.metadata().get("active_backend"),
            },
            "backup": {
                "filename": archive.name,
                "bytes": archive.stat().st_size,
                "manifest_files": len(manifest.get("files", [])),
                "safety_backup_created_by_restore": restore_report.get("safety_backup"),
            },
            "timings_ms": timings,
            "memory": resources,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=100000)
    args = parser.parse_args()
    if not 1 <= args.entries <= 100000:
        parser.error("--entries muss zwischen 1 und 100000 liegen.")
    result = run_rehearsal(args.entries)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
