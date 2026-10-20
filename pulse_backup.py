"""D-01 / D-02 / D-04: one complete Pulse backup (ZIP + manifest) and a safe restore.

* Contains every runtime store from docs/DATENLANDKARTE.md: all SQLite databases (consistent
  snapshot via the SQLite backup API, also in WAL mode), all JSON files and the folders
  uploads/, attachments/, transcripts/. Optionally the Discord structure document (the existing
  JSON backup format) as `discord_structure.json`.
* `manifest.json`: format version, Pulse version, file list with size and SHA-256, skipped files
  (size budget) and warnings – nothing is left out silently.
* Not included on purpose: backups/ (no recursion), `.env` and `.session_secret` (secrets).
* Restore: hashes are verified, a safety backup of the current state is written first, databases
  are copied *into* the live files with the backup API (no `os.replace` under open connections),
  `PRAGMA integrity_check` before and after, JSON files are replaced atomically, uploaded files are
  added/overwritten but never deleted. While it runs, maintenance mode blocks JSON writes.

This module never touches Discord and is safe to run in a worker thread.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

FORMAT = "pulse-full-backup"
FORMAT_VERSION = 1
NAME_RE = re.compile(r"^pulse_full_(?:auto_|pre_restore_)?\d{8}_\d{6}(?:_\d{6})?\.zip$")

SQLITE_FILES = (
    "pulse.db", "abmeldungen.db", "activity_check.db", "pulse_suite.db", "teamliste.db",
    "buero_system.db", "rp_verwaltung.db", "flaggenquiz.db",
)
JSON_FILES = (
    "config.json", "team_data.json", "applications.json", "shifts.json", "logs.json", "audit_logs.json",
    "meetings.json", "pulse_announcement_archive.json", "role_nickname_state.json",
    "social_promo_sources.json", "ai_moderation_cases.json",
)
FOLDERS = ("uploads", "attachments", "transcripts")
# Runtime stores that are deliberately not part of a backup (checked by tests/test_full_backup.py).
EXCLUDED = {"backups": "Sicherungen selbst (keine Rekursion)", ".session_secret": "Geheimnis – bei Verlust nur Neuanmeldung",
            ".env": "Geheimnisse"}

DEFAULT_BUDGET_BYTES = int(float(os.getenv("PULSE_BACKUP_MAX_MB", "500")) * 1024 * 1024)
_CHUNK = 1024 * 1024
_MAINTENANCE = threading.Event()
_RESTORE_LOCK = threading.Lock()


def maintenance_active() -> bool:
    return _MAINTENANCE.is_set()


def _pulse_db_path() -> str:
    try:
        import pulse_db
        return os.path.abspath(pulse_db.DB_PATH)
    except Exception:
        return ""


def sqlite_path(base_dir, name: str) -> Path:
    if name == "pulse.db" and _pulse_db_path():
        return Path(_pulse_db_path())
    return Path(base_dir) / name


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_sqlite(source: Path, target: Path) -> None:
    src = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _integrity(path) -> str:
    con = sqlite3.connect(path, timeout=15)
    try:
        return str(con.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        con.close()


def _pulse_version() -> str:
    try:
        from pulse_version import PULSE_VERSION
        return PULSE_VERSION
    except Exception:
        return "unbekannt"


def backup_name(prefix: str = "", now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S_%f")
    return f"pulse_full_{prefix}{stamp}.zip"


def create_full_backup(base_dir, backup_dir, *, discord_structure: dict | None = None, prefix: str = "",
                       budget_bytes: int = DEFAULT_BUDGET_BYTES) -> tuple[Path, dict]:
    """Write a complete backup ZIP and return (path, manifest)."""
    base_dir, backup_dir = Path(base_dir), Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / backup_name(prefix)
    partial = target.with_name(target.name + ".tmp")
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "pulse_version": _pulse_version(),
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "files": [], "skipped": [], "warnings": [], "excluded": EXCLUDED}

    def add(zf, source, arcname, kind):
        zf.write(source, arcname)
        manifest["files"].append({"name": arcname, "kind": kind, "size": os.path.getsize(source), "sha256": _sha256(source)})

    with tempfile.TemporaryDirectory(prefix="pulse-backup-") as tmp, \
            zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for name in SQLITE_FILES:
            source = sqlite_path(base_dir, name)
            if not source.is_file():
                continue
            snap = Path(tmp) / name
            try:
                _snapshot_sqlite(source, snap)
            except sqlite3.Error as exc:
                manifest["warnings"].append(f"{name}: Datenbank nicht lesbar ({type(exc).__name__}) – nicht gesichert")
                manifest["skipped"].append({"name": f"db/{name}", "reason": "nicht lesbar"})
                continue
            add(zf, snap, f"db/{name}", "sqlite")
            snap.unlink()
        for name in JSON_FILES:
            source = base_dir / name
            if source.is_file():
                add(zf, source, f"json/{name}", "json")
        used = 0
        for folder in FOLDERS:
            root = base_dir / folder
            if not root.is_dir():
                continue
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                arcname = "files/" + PurePosixPath(*path.relative_to(base_dir).parts).as_posix()
                size = path.stat().st_size
                if used + size > budget_bytes:
                    manifest["skipped"].append({"name": arcname, "reason": "Größenbudget", "size": size})
                    continue
                add(zf, path, arcname, "file")
                used += size
        if manifest["skipped"] and any(s["reason"] == "Größenbudget" for s in manifest["skipped"]):
            manifest["warnings"].append(
                f"{sum(1 for s in manifest['skipped'] if s['reason'] == 'Größenbudget')} Datei(en) über dem Größenbudget "
                f"von {budget_bytes // (1024 * 1024)} MB nicht gesichert (PULSE_BACKUP_MAX_MB erhöhen)")
        if discord_structure is not None:
            data = json.dumps(discord_structure, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            zf.writestr("discord_structure.json", data)
            manifest["files"].append({"name": "discord_structure.json", "kind": "discord", "size": len(data),
                                      "sha256": hashlib.sha256(data).hexdigest()})
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    os.replace(partial, target)
    return target, manifest


def read_manifest(path) -> dict:
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    if manifest.get("format") != FORMAT or int(manifest.get("format_version", 0)) > FORMAT_VERSION:
        raise ValueError("Keine unterstützte Pulse-Komplettsicherung.")
    return manifest


def _safe_member(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return bool(parts) and not name.startswith("/") and ".." not in parts and ":" not in name and "\\" not in name


def verify_full_backup(path) -> list[str]:
    """Problems found (empty list = archive complete and unchanged)."""
    problems = []
    try:
        manifest = read_manifest(path)
    except (KeyError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        return [f"Manifest fehlt oder ist ungültig ({type(exc).__name__})"]
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        for entry in manifest.get("files", []):
            name = str(entry.get("name", ""))
            if not _safe_member(name):
                problems.append(f"Unzulässiger Pfad: {name}")
                continue
            if name not in names:
                problems.append(f"Fehlt im Archiv: {name}")
                continue
            digest = hashlib.sha256()
            with zf.open(name) as fh:
                for block in iter(lambda: fh.read(_CHUNK), b""):
                    digest.update(block)
            if digest.hexdigest() != entry.get("sha256"):
                problems.append(f"Prüfsumme falsch: {name}")
    return problems


def _target_for(base_dir, name: str) -> Path | None:
    if name.startswith("db/") and name[3:] in SQLITE_FILES:
        return sqlite_path(base_dir, name[3:])
    if name.startswith("json/") and name[5:] in JSON_FILES:
        return Path(base_dir) / name[5:]
    if name.startswith("files/"):
        rel = PurePosixPath(name[6:])
        if rel.parts and rel.parts[0] in FOLDERS and _safe_member(name):
            return Path(base_dir).joinpath(*rel.parts)
    return None


def _table_counts(path) -> dict[str, int]:
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: int(con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tables}
    finally:
        con.close()


def preview_restore(path, base_dir) -> dict:
    """Dry run: what would change (files new/changed/unchanged, row counts per table). Writes nothing."""
    manifest = read_manifest(path)
    result = {"manifest": manifest, "problems": verify_full_backup(path), "files": [], "tables": []}
    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory(prefix="pulse-preview-") as tmp:
        for entry in manifest.get("files", []):
            name = entry["name"]
            target = _target_for(base_dir, name)
            if target is None:
                continue
            if entry["kind"] == "sqlite":
                extracted = Path(tmp) / Path(name).name
                with zf.open(name) as src, open(extracted, "wb") as dst:
                    shutil.copyfileobj(src, dst, _CHUNK)
                backup_counts = _table_counts(extracted)
                current_counts = _table_counts(target) if target.is_file() else {}
                for table in sorted(set(backup_counts) | set(current_counts)):
                    if backup_counts.get(table) != current_counts.get(table):
                        result["tables"].append({"db": Path(name).name, "table": table,
                                                 "current": current_counts.get(table), "backup": backup_counts.get(table)})
                status = "neu" if not target.is_file() else "geprüft"
            elif not target.is_file():
                status = "neu"
            else:
                status = "unverändert" if _sha256(target) == entry["sha256"] else "geändert"
            result["files"].append({"name": name, "status": status, "size": entry.get("size", 0)})
    return result


def _restore_sqlite(zf, name: str, target: Path, tmp: str) -> None:
    extracted = Path(tmp) / Path(name).name
    with zf.open(name) as src, open(extracted, "wb") as dst:
        shutil.copyfileobj(src, dst, _CHUNK)
    restore_sqlite_file(extracted, target)


def restore_sqlite_file(extracted, target) -> None:
    """Copy a database file into the live database with the SQLite backup API (integrity checked twice)."""
    extracted, target = Path(extracted), Path(target)
    check = _integrity(extracted)
    if check != "ok":
        raise ValueError(f"{extracted.name} im Backup ist beschädigt ({check})")
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(extracted)
    dst = sqlite3.connect(target, timeout=30)
    try:
        src.backup(dst)  # copies into the live file under SQLite's own locking
    finally:
        dst.close()
        src.close()
    check = _integrity(target)
    if check != "ok":
        raise ValueError(f"{target.name} nach der Wiederherstellung fehlerhaft ({check})")


def restore_full_backup(path, base_dir, backup_dir, *, include_files: bool = True) -> dict:
    """Restore data from a complete backup. Returns a report; raises ValueError before any change if invalid."""
    if not _RESTORE_LOCK.acquire(blocking=False):
        raise ValueError("Es läuft bereits eine Wiederherstellung.")
    try:
        manifest = read_manifest(path)
        problems = verify_full_backup(path)
        if problems:
            raise ValueError("Backup unvollständig oder verändert: " + "; ".join(problems[:5]))
        safety, _ = create_full_backup(base_dir, backup_dir, prefix="pre_restore_")
        report = {"safety_backup": safety.name, "databases": [], "json": [], "files": 0, "pulse_version": manifest.get("pulse_version")}
        _MAINTENANCE.set()
        try:
            with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory(prefix="pulse-restore-") as tmp:
                for entry in manifest["files"]:
                    name = entry["name"]
                    target = _target_for(base_dir, name)
                    if target is None:
                        continue
                    if entry["kind"] == "sqlite":
                        _restore_sqlite(zf, name, target, tmp)
                        report["databases"].append(target.name)
                    elif entry["kind"] == "json":
                        partial = target.with_name(target.name + ".restore.tmp")
                        with zf.open(name) as src, open(partial, "wb") as dst:
                            shutil.copyfileobj(src, dst, _CHUNK)
                            dst.flush()
                            os.fsync(dst.fileno())
                        os.replace(partial, target)
                        report["json"].append(target.name)
                    elif entry["kind"] == "file" and include_files:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        partial = target.with_name(target.name + ".restore.tmp")
                        with zf.open(name) as src, open(partial, "wb") as dst:
                            shutil.copyfileobj(src, dst, _CHUNK)
                        os.replace(partial, target)
                        report["files"] += 1
        finally:
            _MAINTENANCE.clear()
        return report
    finally:
        _RESTORE_LOCK.release()


def restore_test(path) -> dict:
    """D-04: verify hashes and integrity of every database in a temp directory. Writes nothing live."""
    result = {"backup": Path(path).name, "ok": False, "problems": [], "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    result["problems"] = verify_full_backup(path)
    if not result["problems"]:
        try:
            manifest = read_manifest(path)
            with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory(prefix="pulse-restoretest-") as tmp:
                for entry in manifest["files"]:
                    if entry["kind"] != "sqlite":
                        continue
                    extracted = Path(tmp) / Path(entry["name"]).name
                    with zf.open(entry["name"]) as src, open(extracted, "wb") as dst:
                        shutil.copyfileobj(src, dst, _CHUNK)
                    check = _integrity(extracted)
                    if check != "ok":
                        result["problems"].append(f"{extracted.name}: {check}")
                    extracted.unlink()
        except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as exc:
            result["problems"].append(f"{type(exc).__name__}: {exc}")
    result["ok"] = not result["problems"]
    return result


def list_backups(backup_dir) -> list[Path]:
    folder = Path(backup_dir)
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and NAME_RE.fullmatch(p.name)),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def retention_keep(names_with_time: list[tuple[str, datetime]], daily: int = 7, weekly: int = 4, monthly: int = 6) -> set[str]:
    """D-04: names to keep – newest per day (7), per ISO week (4) and per month (6)."""
    keep: set[str] = set()
    ordered = sorted(names_with_time, key=lambda x: x[1], reverse=True)
    for count, key in ((daily, lambda d: d.date()), (weekly, lambda d: d.isocalendar()[:2]), (monthly, lambda d: (d.year, d.month))):
        seen = []
        for name, stamp in ordered:
            bucket = key(stamp)
            if bucket in seen:
                continue
            if len(seen) >= count:
                break
            seen.append(bucket)
            keep.add(name)
    return keep


def prune_auto_backups(backup_dir, **limits) -> list[str]:
    """Delete automatic full backups outside the retention scheme; manual and safety backups are kept."""
    autos = [(p, datetime.fromtimestamp(p.stat().st_mtime)) for p in list_backups(backup_dir) if p.name.startswith("pulse_full_auto_")]
    keep = retention_keep([(p.name, t) for p, t in autos], **limits)
    removed = []
    for path, _ in autos:
        if path.name not in keep:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                pass
    return removed


def due(last: datetime | None, every: timedelta, now: datetime | None = None) -> bool:
    return last is None or (now or datetime.now()) - last >= every
