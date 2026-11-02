"""D-01 / D-02 / D-04: one complete Pulse backup (ZIP + manifest) and a safe restore.

* Contains every runtime store from docs/DATENLANDKARTE.md: all SQLite databases (consistent
  snapshot via the SQLite backup API, also in WAL mode), all JSON files and the folders
  uploads/, attachments/, transcripts/ and the uploaded waiting music (assets/support_wait.*). Optionally the Discord structure document (the existing
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

import contextlib
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
# predeploy_/prerollback_: written by deploy/remote_deploy.sh and deploy/rollback_code.sh (never pruned automatically).
NAME_RE = re.compile(r"^pulse_full_(?:auto_|pre_restore_|predeploy_|prerollback_)?\d{8}_\d{6}(?:_\d{6})?\.zip$")

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
# Single runtime files outside those folders: the voice-support waiting music uploaded in the dashboard
# (pulse_pro: assets/support_wait.<mp3|ogg|wav>). It is not in git, so only the backup can bring it back.
MEDIA_FILES = ("assets/support_wait.mp3", "assets/support_wait.ogg", "assets/support_wait.wav")
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


_JSON_ENV = {"social_promo_sources.json": "PULSE_SOCIAL_PROMO_FILE", "ai_moderation_cases.json": "PULSE_AI_MODERATION_CASES_FILE"}


def json_path(base_dir, name: str) -> Path:
    """JSON stores whose location can be moved by an environment variable are backed up from there."""
    override = os.getenv(_JSON_ENV.get(name, ""), "").strip() if name in _JSON_ENV else ""
    return Path(override) if override else Path(base_dir) / name


def kind_of(name: str) -> str:
    """The kind of an archive entry comes from its path – never from the (editable) manifest."""
    if name.startswith("db/"):
        return "sqlite"
    if name.startswith("json/"):
        return "json"
    if name.startswith("files/"):
        return "file"
    return "other"


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
        with open(source, "rb") as src:
            add_stream(zf, src, arcname, kind)

    def add_stream(zf, src, arcname, kind):
        digest, size = hashlib.sha256(), 0
        with zf.open(arcname, "w", force_zip64=True) as dst:
            for block in iter(lambda: src.read(_CHUNK), b""):
                digest.update(block)
                size += len(block)
                dst.write(block)
        manifest["files"].append({"name": arcname, "kind": kind, "size": size, "sha256": digest.hexdigest()})

    try:
        _write_archive(base_dir, partial, manifest, add, add_stream, discord_structure, budget_bytes)
    except BaseException:
        # Phase 8: never leave a half-written archive behind – list_backups() ignores *.tmp, so it would never be
        # pruned and would silently fill the disk with every failed (e.g. disk-full) auto backup.
        with contextlib.suppress(OSError):
            partial.unlink()
        raise
    os.replace(partial, target)
    return target, manifest


def _json_io_lock():
    """Phase 14.7: the JSON I/O lock of the dashboard, if it is loaded (tools/tests may back up without it)."""
    import sys
    lock = getattr(sys.modules.get("webserver"), "_io_lock", None)
    return lock if lock is not None else contextlib.nullcontext()



def _sqlite_snapshot_metadata(path) -> dict:
    """Metadata needed to prove that a pulse.db snapshot contains the declared Melonly generation."""
    with sqlite3.connect(path, timeout=15) as con:
        integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity.lower() != "ok":
            raise sqlite3.DatabaseError("PRAGMA integrity_check ist nicht OK")
        user_version = int(con.execute("PRAGMA user_version").fetchone()[0])
        tables = {str(row[0]) for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        has_meta = "melonly_logs_meta" in tables
        has_logs = "melonly_logs" in tables
        if has_meta != has_logs:
            raise sqlite3.DatabaseError("Melonly-Schema ist unvollständig")
        melonly = {"schema_present": has_meta}
        if has_meta:
            values = {str(key): str(value) for key, value in con.execute(
                "SELECT key,value FROM melonly_logs_meta"
            ).fetchall()}
            melonly.update({
                key: values.get(key, "")
                for key in (
                    "format_version", "active_backend", "generation", "revision",
                    "import_count", "import_source_sha256",
                )
            })
            melonly["row_count"] = int(con.execute(
                "SELECT COUNT(*) FROM melonly_logs"
            ).fetchone()[0])
        return {"integrity": integrity, "user_version": user_version, "melonly": melonly}


def _verify_sqlite_manifest_metadata(zf, manifest, archive_names: set[str]) -> list[str]:
    """Validate optional Melonly metadata against the actual archived SQLite snapshot."""
    declared = manifest.get("sqlite_metadata")
    if declared is None:  # backwards compatibility with older full backups
        return []
    if not isinstance(declared, dict):
        return ["SQLite-Metadaten im Backup-Manifest sind ungültig."]
    problems = []
    for name, expected in declared.items():
        if not isinstance(name, str) or not name.startswith("db/") or name[3:] not in SQLITE_FILES:
            problems.append("SQLite-Metadaten verweisen auf kein unterstütztes Datenbankziel.")
            continue
        if name not in archive_names or not isinstance(expected, dict):
            problems.append(f"SQLite-Metadaten oder Datenbank-Snapshot fehlen: {name}")
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="pulse-backup-meta-") as tmp:
                snapshot = Path(tmp) / Path(name).name
                with zf.open(name) as src, open(snapshot, "wb") as dst:
                    shutil.copyfileobj(src, dst, _CHUNK)
                actual = _sqlite_snapshot_metadata(snapshot)
            if actual != expected:
                problems.append(f"SQLite-Metadaten stimmen nicht mit dem Snapshot überein: {name}")
            melonly = actual.get("melonly", {})
            if melonly.get("active_backend") == "sqlite" and "json/logs.json" not in archive_names:
                problems.append("SQLite-Melonly ist aktiv, aber die archivierte logs.json-Migrationssicherung fehlt.")
        except (OSError, sqlite3.Error, ValueError, TypeError, zipfile.BadZipFile) as exc:
            problems.append(f"SQLite-Metadaten konnten nicht geprüft werden ({type(exc).__name__}).")
    return problems


def _write_archive(base_dir, partial, manifest, add, add_stream, discord_structure, budget_bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="pulse-backup-") as tmp, \
            zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        # Phase 14.7: shifts.json (active shifts) and pulse.db (shift history in SQLite mode) are copied together
        # under the JSON lock of the dashboard, so no save of a finished shift lands between the two copies.
        early: dict = {}
        shifts_copy = Path(tmp) / "shifts_tmp_copy.json"
        logs_copy = Path(tmp) / "logs_tmp_copy.json"
        logs_snapshot_exists = False
        with _json_io_lock():
            source = sqlite_path(base_dir, "pulse.db")
            if source.is_file():
                try:
                    _snapshot_sqlite(source, Path(tmp) / "pulse.db")
                    early["pulse.db"] = None
                    manifest.setdefault("sqlite_metadata", {})["db/pulse.db"] = _sqlite_snapshot_metadata(
                        Path(tmp) / "pulse.db"
                    )
                except sqlite3.Error as exc:
                    early["pulse.db"] = exc
                    manifest.setdefault("warnings", []).append(
                        f"pulse.db: SQLite-Metadaten konnten nicht sicher erfasst werden ({type(exc).__name__})"
                    )
            shifts_source = json_path(base_dir, "shifts.json")
            if shifts_source.is_file():
                shutil.copy2(shifts_source, shifts_copy)
            # Melonly JSON writes and pulse.db writes use this same lock. Snapshotting both
            # together prevents an export/restore pair with a mismatched SQLite revision.
            logs_source = json_path(base_dir, "logs.json")
            logs_snapshot_exists = logs_source.is_file()
            if logs_snapshot_exists:
                shutil.copy2(logs_source, logs_copy)
        for name in SQLITE_FILES:
            source = sqlite_path(base_dir, name)
            if name not in early and not source.is_file():
                manifest["skipped"].append({"name": f"db/{name}", "reason": "nicht vorhanden"})
                continue
            snap = Path(tmp) / name
            try:
                if name in early:
                    if early[name] is not None:
                        raise early[name]
                else:
                    _snapshot_sqlite(source, snap)
            except sqlite3.Error as exc:
                manifest["warnings"].append(f"{name}: Datenbank nicht lesbar ({type(exc).__name__}) – nicht gesichert")
                manifest["skipped"].append({"name": f"db/{name}", "reason": "nicht lesbar"})
                continue
            add(zf, snap, f"db/{name}", "sqlite")
            snap.unlink()
        for name in JSON_FILES:
            if name == "shifts.json":
                source = shifts_copy
            elif name == "logs.json":
                source = logs_copy if logs_snapshot_exists else None
            else:
                source = json_path(base_dir, name)
            if source is not None and source.is_file():
                add(zf, source, f"json/{name}", "json")
        used = 0
        media = [base_dir / rel for rel in MEDIA_FILES if (base_dir / rel).is_file()]
        for folder in (None, *FOLDERS):
            if folder is None:
                files = media
            else:
                root = base_dir / folder
                if not root.is_dir():
                    continue
                files = sorted(p for p in root.rglob("*") if p.is_file())
            for path in files:
                arcname = "files/" + PurePosixPath(*path.relative_to(base_dir).parts).as_posix()
                # Phase 8: uploads/transcripts can be deleted (retention, ticket cleanup) between listing and copying.
                # Only *opening* the source is tolerated – errors while writing the archive (disk full) still abort.
                try:
                    size = path.stat().st_size
                    if used + size > budget_bytes:
                        manifest["skipped"].append({"name": arcname, "reason": "Größenbudget", "size": size})
                        continue
                    src = open(path, "rb")
                except OSError as exc:
                    manifest["skipped"].append({"name": arcname, "reason": "nicht lesbar", "error": type(exc).__name__})
                    continue
                with src:
                    add_stream(zf, src, arcname, "file")
                used += size
        if manifest["skipped"] and any(s["reason"] == "Größenbudget" for s in manifest["skipped"]):
            manifest["warnings"].append(
                f"{sum(1 for s in manifest['skipped'] if s['reason'] == 'Größenbudget')} Datei(en) über dem Größenbudget "
                f"von {budget_bytes // (1024 * 1024)} MB nicht gesichert (PULSE_BACKUP_MAX_MB erhöhen)")
        unreadable = [s["name"] for s in manifest["skipped"] if s["reason"] == "nicht lesbar" and s["name"].startswith("files/")]
        if unreadable:
            manifest["warnings"].append(f"{len(unreadable)} Datei(en) während der Sicherung nicht lesbar oder gelöscht – "
                                        f"nicht gesichert: {', '.join(unreadable[:3])}")
        if discord_structure is not None:
            data = json.dumps(discord_structure, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            zf.writestr("discord_structure.json", data)
            manifest["files"].append({"name": "discord_structure.json", "kind": "discord", "size": len(data),
                                      "sha256": hashlib.sha256(data).hexdigest()})
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))


def read_manifest(path) -> dict:
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Backup-Manifest muss ein JSON-Objekt sein.")
    version = manifest.get("format_version")
    if (
        manifest.get("format") != FORMAT
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version > FORMAT_VERSION
        or version < 1
    ):
        raise ValueError("Keine unterstützte Pulse-Komplettsicherung.")
    if not isinstance(manifest.get("files"), list):
        raise ValueError("Backup-Manifest enthält keine gültige Dateiliste.")
    return manifest


def _safe_member(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return bool(parts) and not name.startswith("/") and ".." not in parts and ":" not in name and "\\" not in name


def _supported_backup_member(name: str) -> bool:
    """Only restore targets owned by the current backup format are accepted."""
    if name == "discord_structure.json":
        return True
    if name.startswith("db/"):
        return name[3:] in SQLITE_FILES
    if name.startswith("json/"):
        return name[5:] in JSON_FILES
    if name.startswith("files/"):
        relative = PurePosixPath(name[6:])
        return bool(relative.parts) and (
            relative.parts[0] in FOLDERS or relative.as_posix() in MEDIA_FILES
        ) and _safe_member(name)
    return False


def verify_full_backup(path) -> list[str]:
    """Problems found (empty list = archive complete, unambiguous and unchanged)."""
    problems = []
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            names = [info.filename for info in infos]
            if names.count("manifest.json") != 1:
                return ["Manifest fehlt oder ist mehrfach im Archiv enthalten."]
            if len(names) != len(set(names)):
                duplicates = sorted({name for name in names if names.count(name) > 1})
                return ["Doppelte ZIP-Einträge: " + ", ".join(duplicates[:5])]
        manifest = read_manifest(path)
    except (KeyError, ValueError, TypeError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return [f"Manifest fehlt oder ist ungültig ({type(exc).__name__}: {exc})"]

    seen = set()
    try:
        with zipfile.ZipFile(path) as zf:
            info_by_name = {info.filename: info for info in zf.infolist()}
            for index, entry in enumerate(manifest["files"]):
                if not isinstance(entry, dict):
                    problems.append(f"Dateieintrag {index} im Manifest ist kein Objekt.")
                    continue
                name = entry.get("name")
                if not isinstance(name, str) or not name:
                    problems.append(f"Dateieintrag {index} im Manifest hat keinen gültigen Namen.")
                    continue
                if name in seen:
                    problems.append(f"Doppelter Manifest-Eintrag: {name}")
                    continue
                seen.add(name)
                if not _safe_member(name):
                    problems.append(f"Unzulässiger Pfad: {name}")
                    continue
                if not _supported_backup_member(name):
                    problems.append(f"Kein unterstütztes Wiederherstellungsziel: {name}")
                    continue
                info = info_by_name.get(name)
                if info is None:
                    problems.append(f"Fehlt im Archiv: {name}")
                    continue
                size = entry.get("size")
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    problems.append(f"Ungültige Dateigröße im Manifest: {name}")
                    continue
                if info.file_size != size:
                    problems.append(f"Dateigröße weicht vom Manifest ab: {name}")
                    continue
                digest_value = entry.get("sha256")
                if not isinstance(digest_value, str) or re.fullmatch(r"[0-9a-f]{64}", digest_value) is None:
                    problems.append(f"Ungültige SHA-256-Prüfsumme im Manifest: {name}")
                    continue
                digest = hashlib.sha256()
                with zf.open(info) as fh:
                    for block in iter(lambda: fh.read(_CHUNK), b""):
                        digest.update(block)
                if digest.hexdigest() != digest_value:
                    problems.append(f"Prüfsumme falsch: {name}")
            extra = sorted(set(info_by_name) - seen - {"manifest.json"})
            if extra:
                problems.append("Nicht im Manifest deklarierte ZIP-Einträge: " + ", ".join(extra[:5]))
    except (OSError, RuntimeError, zipfile.BadZipFile, KeyError) as exc:
        problems.append(f"Archiv konnte nicht vollständig gelesen werden ({type(exc).__name__}: {exc})")
    if not problems:
        try:
            with zipfile.ZipFile(path) as zf:
                archive_names = set(zf.namelist())
                problems.extend(_verify_sqlite_manifest_metadata(zf, manifest, archive_names))
        except (OSError, zipfile.BadZipFile) as exc:
            problems.append(f"SQLite-Metadatenprüfung fehlgeschlagen ({type(exc).__name__}).")
    return problems


def _target_for(base_dir, name: str) -> Path | None:
    if name.startswith("db/") and name[3:] in SQLITE_FILES:
        return sqlite_path(base_dir, name[3:])
    if name.startswith("json/") and name[5:] in JSON_FILES:
        return json_path(base_dir, name[5:])
    if name.startswith("files/"):
        rel = PurePosixPath(name[6:])
        if rel.parts and (rel.parts[0] in FOLDERS or rel.as_posix() in MEDIA_FILES) and _safe_member(name):
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
    result = {"manifest": manifest, "problems": verify_full_backup(path), "files": [], "tables": [],
              "sqlite_metadata": manifest.get("sqlite_metadata", {})}
    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory(prefix="pulse-preview-") as tmp:
        present = set(zf.namelist())
        for entry in manifest.get("files", []):
            name = str(entry.get("name", ""))
            target = _target_for(base_dir, name)
            if target is None:
                continue
            if name not in present:  # already listed by verify_full_backup ("Fehlt im Archiv") – restore stays locked
                result["files"].append({"name": name, "status": "fehlt im Archiv", "size": entry.get("size", 0)})
                continue
            if kind_of(name) == "sqlite":
                extracted = Path(tmp) / Path(name).name
                with zf.open(name) as src, open(extracted, "wb") as dst:
                    shutil.copyfileobj(src, dst, _CHUNK)
                # Phase 8: a damaged database must show up as a problem in the dry run (restore locked), not as HTTP 500.
                try:
                    backup_counts = _table_counts(extracted)
                except sqlite3.Error as exc:
                    result["problems"].append(f"{name}: Datenbank im Backup nicht lesbar ({type(exc).__name__}: {exc})")
                    result["files"].append({"name": name, "status": "beschädigt", "size": entry.get("size", 0)})
                    continue
                try:
                    current_counts = _table_counts(target) if target.is_file() else {}
                except sqlite3.Error as exc:  # the live file is damaged: the backup is exactly what is needed
                    current_counts = {}
                    result.setdefault("warnings", []).append(f"Aktuelle {target.name} nicht lesbar ({type(exc).__name__})")
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


def _stage(zf, manifest, base_dir, tmp, include_files: bool) -> list[tuple]:
    """Phase 1: extract every entry to a temp dir and check it. Nothing live is touched."""
    staged = []
    for entry in manifest["files"]:
        name = entry["name"]
        kind, target = kind_of(name), _target_for(base_dir, name)
        if target is None or (kind == "file" and not include_files):
            continue
        extracted = Path(tmp) / f"{len(staged):05d}_{Path(name).name}"
        with zf.open(name) as src, open(extracted, "wb") as dst:
            shutil.copyfileobj(src, dst, _CHUNK)
        if kind == "sqlite":
            check = _integrity(extracted)
            if check != "ok":
                raise ValueError(f"{Path(name).name} im Backup ist beschädigt ({check})")
        elif kind == "json":
            try:
                json.loads(extracted.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{Path(name).name} im Backup ist kein gültiges JSON") from exc
        staged.append((kind, extracted, target))
    return staged


def _apply(staged, report) -> None:
    """Phase 2: write the checked files into place (databases via backup API, files atomically)."""
    for kind, extracted, target in staged:
        if kind == "sqlite":
            restore_sqlite_file(extracted, target)
            report["databases"].append(target.name)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".restore.tmp")
            shutil.copyfile(extracted, partial)
            with open(partial, "rb+") as fh:
                os.fsync(fh.fileno())
            os.replace(partial, target)
            if kind == "json":
                report["json"].append(target.name)
            else:
                report["files"] += 1


def _restore_from(path, base_dir, include_files, lock) -> dict:
    manifest = read_manifest(path)
    report = {"databases": [], "json": [], "files": 0, "pulse_version": manifest.get("pulse_version")}
    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory(prefix="pulse-restore-") as tmp:
        staged = _stage(zf, manifest, base_dir, tmp, include_files)
        with (lock if lock is not None else contextlib.nullcontext()):
            _apply(staged, report)
    return report


def restore_full_backup(path, base_dir, backup_dir, *, include_files: bool = True, lock=None) -> dict:
    """Restore data from a complete backup. Returns a report; raises ValueError with a clear message.

    Every entry is extracted and checked first; only then is anything written. `lock` (the JSON I/O lock
    of the dashboard) is held while writing so no load-modify-save of another flow can interleave. If
    writing fails part-way, the safety backup taken before is restored automatically.
    """
    if not _RESTORE_LOCK.acquire(blocking=False):
        raise ValueError("Es läuft bereits eine Wiederherstellung.")
    try:
        problems = verify_full_backup(path)
        if problems:
            raise ValueError("Backup unvollständig oder verändert: " + "; ".join(problems[:5]))
        safety, _ = create_full_backup(base_dir, backup_dir, prefix="pre_restore_")
        _MAINTENANCE.set()
        try:
            try:
                report = _restore_from(path, base_dir, include_files, lock)
                expected_sqlite = (read_manifest(path).get("sqlite_metadata") or {}).get("db/pulse.db")
                if expected_sqlite is not None:
                    actual_sqlite = _sqlite_snapshot_metadata(sqlite_path(base_dir, "pulse.db"))
                    if actual_sqlite != expected_sqlite:
                        raise ValueError("Wiederhergestellte Melonly-Metadaten stimmen nicht mit dem Backup überein.")
            except Exception as exc:
                try:
                    _restore_from(safety, base_dir, include_files, lock)
                except Exception as rollback_exc:
                    raise ValueError(f"Wiederherstellung abgebrochen ({exc}); automatisches Zurücksetzen fehlgeschlagen "
                                     f"({type(rollback_exc).__name__}) – bitte Sicherheitskopie {safety.name} wiederherstellen.") from exc
                raise ValueError(f"Wiederherstellung abgebrochen ({exc}); der vorherige Stand wurde aus {safety.name} "
                                 "zurückgespielt.") from exc
        finally:
            _MAINTENANCE.clear()
        report["safety_backup"] = safety.name
        report["sqlite_metadata"] = read_manifest(path).get("sqlite_metadata", {})
        return report
    finally:
        _RESTORE_LOCK.release()


def restore_test(path) -> dict:
    """D-04: a complete trial restore into a temp directory. Writes nothing live.

    Every entry goes through the same checks and copy routines as a real restore (SQLite backup API with
    integrity check before/after, JSON parse, file copy), then the restored files are compared with the
    manifest (SHA-256) and the tables/rows of every database are counted. Targets are always inside the
    temp directory – never `_target_for`, which maps pulse.db and some JSON stores to their live paths.
    An entry a real restore would not know where to put is reported, so such data cannot be lost silently.
    """
    result = {"backup": Path(path).name, "ok": False, "problems": [], "at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "databases": 0, "tables": 0, "rows": 0, "json": 0, "files": 0, "skipped": 0,
              "sqlite_metadata": {}}
    result["problems"] = verify_full_backup(path)
    if not result["problems"]:
        try:
            manifest = read_manifest(path)
            result["skipped"] = len(manifest.get("skipped") or [])
            result["sqlite_metadata"] = manifest.get("sqlite_metadata", {})
            with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory(prefix="pulse-restoretest-") as tmp:
                root = Path(tmp) / "restored"
                for entry in manifest["files"]:
                    name, kind = str(entry["name"]), kind_of(str(entry["name"]))
                    if kind == "other":
                        continue  # discord_structure.json: restored through the Discord structure flow, not here
                    if _target_for(tmp, name) is None:
                        result["problems"].append(f"{name}: kein Wiederherstellungsziel – würde beim Restore fehlen")
                        continue
                    target = root.joinpath(*PurePosixPath(name).parts)
                    extracted = Path(tmp) / "x"
                    with zf.open(name) as src, open(extracted, "wb") as dst:
                        shutil.copyfileobj(src, dst, _CHUNK)
                    try:
                        if kind == "sqlite":
                            restore_sqlite_file(extracted, target)
                            expected_meta = (manifest.get("sqlite_metadata") or {}).get(name)
                            if expected_meta is not None and _sqlite_snapshot_metadata(target) != expected_meta:
                                result["problems"].append(f"{name}: Melonly-Metadaten nach Test-Restore abweichend")
                            counts = _table_counts(target)
                            result["databases"] += 1
                            result["tables"] += len(counts)
                            result["rows"] += sum(counts.values())
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(extracted, target)
                            if _sha256(target) != entry.get("sha256"):
                                result["problems"].append(f"{name}: nach dem Kopieren abweichend")
                            if kind == "json":
                                json.loads(target.read_text(encoding="utf-8"))
                                result["json"] += 1
                            else:
                                result["files"] += 1
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        result["problems"].append(f"{name}: kein gültiges JSON")
                    except (ValueError, sqlite3.Error) as exc:
                        result["problems"].append(f"{name}: {exc}")
                    finally:
                        extracted.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, sqlite3.Error, zipfile.BadZipFile) as exc:
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


SAFETY_COPIES_KEPT = 3
TOTAL_MAX_BYTES = int(float(os.getenv("PULSE_BACKUP_TOTAL_MAX_MB", "4000")) * 1024 * 1024)


def total_size(backup_dir) -> int:
    return sum(p.stat().st_size for p in list_backups(backup_dir))


def prune_auto_backups(backup_dir, **limits) -> list[str]:
    """Delete automatic full backups outside the retention scheme and all but the newest safety copies.
    Manually created backups are never deleted automatically."""
    backups = list_backups(backup_dir)
    autos = [(p, datetime.fromtimestamp(p.stat().st_mtime)) for p in backups if p.name.startswith("pulse_full_auto_")]
    keep = retention_keep([(p.name, t) for p, t in autos], **limits)
    keep |= {p.name for p in [b for b in backups if b.name.startswith("pulse_full_pre_restore_")][:SAFETY_COPIES_KEPT]}
    removed = []
    for path in [p for p, _ in autos] + [b for b in backups if b.name.startswith("pulse_full_pre_restore_")]:
        if path.name not in keep:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                pass
    return removed


def due(last: datetime | None, every: timedelta, now: datetime | None = None) -> bool:
    return last is None or (now or datetime.now()) - last >= every
