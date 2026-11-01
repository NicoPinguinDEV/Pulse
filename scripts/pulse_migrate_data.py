#!/usr/bin/env python3
"""Phase 13 (D-06): copy the Pulse runtime data from the old checkout into the separate data directory.

    python3 scripts/pulse_migrate_data.py                         # dry run (default): shows what would happen
    python3 scripts/pulse_migrate_data.py --verify                # compares source and target, writes nothing
    sudo python3 scripts/pulse_migrate_data.py --execute          # real migration, asks for "MIGRIEREN"

Defaults: source /opt/pulse/Pulse, target /opt/pulse/data, backup /root/pulse-migration-backups, UID/GID 1000.
Only the Python standard library – runs on the host without the Pulse image.

Rules (CLAUDE.md, Phase 13):
* The source is only read – nothing is deleted, moved or changed there. A rollback to the old layout keeps working.
* Nothing in the target is overwritten: an identical file is skipped (idempotent), a different one is a conflict and
  stops the migration before anything is copied.
* Before copying: a complete tar.gz backup of every migrated item, verified against SHA-256 checksums.
* The marker ``.pulse-data.json`` says ``in_progress`` while copying and ``complete`` only after the count and the
  checksum of every file matched – Pulse refuses to start on an ``in_progress`` (aborted) migration.
* ``.env`` is not migrated: it stays on the host next to compose.yaml and is passed in as environment variables.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

MARKER_NAME = ".pulse-data.json"  # same as pulse_paths.MARKER_NAME (tests keep them in sync)
MARKER_FORMAT = "pulse-data-dir"
CONFIRM_WORD = "MIGRIEREN"

# Same lists as pulse_backup (tests/test_phase13_data_isolation.py keeps them in sync) plus what the backup leaves
# out on purpose but the running Pulse still needs: backups/, logs/, .session_secret.
SQLITE_FILES = ("pulse.db", "abmeldungen.db", "activity_check.db", "pulse_suite.db", "teamliste.db",
                "buero_system.db", "rp_verwaltung.db", "flaggenquiz.db")
JSON_FILES = ("config.json", "team_data.json", "applications.json", "shifts.json", "logs.json", "audit_logs.json",
              "meetings.json", "pulse_announcement_archive.json", "role_nickname_state.json",
              "social_promo_sources.json", "ai_moderation_cases.json")
FOLDERS = ("uploads", "attachments", "transcripts", "backups", "logs")
MEDIA_FILES = ("assets/support_wait.mp3", "assets/support_wait.ogg", "assets/support_wait.wav")
SPECIAL_FILES = (".session_secret",)
# Further top-level runtime files (SQLite side files, safety copies of repairs, unknown databases/JSON stores).
EXTRA_PATTERNS = ("*.db", "*.db-wal", "*.db-shm", "*.db-journal", "*.sqlite", "*.sqlite3", "*.json",
                  "*.corrupt-*", "*.vor-reparatur-*.bak", "*.restore.tmp*")
# Never data: tooling and editor files that may match *.json in a checkout.
NEVER = {MARKER_NAME, "package.json", ".env"}  # tracked files are excluded through git as well


def log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:  # non-UTF-8 console: never let output stop a migration
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tracked_files(source: Path) -> set[str]:
    """Files under version control are code, never data (no git → empty set, the explicit lists still apply)."""
    if not (source / ".git").exists() or shutil.which("git") is None:
        return set()
    try:
        out = subprocess.run(["git", "-c", f"safe.directory={source}", "ls-files", "-z"], cwd=source,
                             capture_output=True, timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {name for name in out.decode("utf-8", "replace").split("\0") if name}


def inventory(source: Path) -> list[str]:
    """Relative POSIX paths of every runtime file below source (files only, symlinks are skipped)."""
    tracked = tracked_files(source)
    found: set[str] = set()

    def add_file(rel: str) -> None:
        path = source / rel
        if rel in tracked or Path(rel).name in NEVER or path.is_symlink() or not path.is_file():
            return
        found.add(rel)

    for name in SQLITE_FILES + JSON_FILES + SPECIAL_FILES + MEDIA_FILES:
        add_file(name)
        for suffix in ("-wal", "-shm", "-journal"):
            add_file(name + suffix)
    for entry in source.iterdir() if source.is_dir() else ():
        if entry.is_file() and any(fnmatch.fnmatch(entry.name, pattern) for pattern in EXTRA_PATTERNS):
            add_file(entry.name)
    for folder in FOLDERS:
        root = source / folder
        if root.is_symlink() or not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not (Path(dirpath) / d).is_symlink())
            for filename in filenames:
                add_file((Path(dirpath) / filename).relative_to(source).as_posix())
    return sorted(found)


def plan(source: Path, target: Path, items: list[str]) -> dict:
    """What a run would do – reads only."""
    copy, skip, conflicts, total = [], [], [], 0
    for rel in items:
        src, dst = source / rel, target / rel
        size = src.stat().st_size
        total += size
        if dst.exists() or dst.is_symlink():
            if dst.is_file() and not dst.is_symlink() and dst.stat().st_size == size and sha256(dst) == sha256(src):
                skip.append(rel)
            else:
                conflicts.append(rel)
        else:
            copy.append(rel)
    return {"copy": copy, "skip": skip, "conflicts": conflicts, "total_bytes": total}


def read_marker(target: Path) -> dict | None:
    try:
        data = json.loads((target / MARKER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("format") == MARKER_FORMAT else None


def write_marker(target: Path, state: str, **extra) -> None:
    payload = {"format": MARKER_FORMAT, "version": 1, "state": state,
               "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}
    tmp = target / (MARKER_NAME + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, target / MARKER_NAME)


def container_running(name: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        out = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", name],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def create_backup(source: Path, items: list[str], backup_dir: Path) -> tuple[Path, dict]:
    """tar.gz of every migrated item plus a checksum manifest; verified by reading the archive back."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = backup_dir / f"pulse-premigration-{stamp}.tar.gz"
    manifest = {rel: sha256(source / rel) for rel in items}
    partial = archive.with_name(archive.name + ".partial")
    with tarfile.open(partial, "w:gz") as tar:
        for rel in items:
            tar.add(source / rel, arcname=rel, recursive=False)
        data = json.dumps({"source": str(source), "files": manifest}, indent=2).encode("utf-8")
        info = tarfile.TarInfo("pulse-migration-manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    seen = {}
    with tarfile.open(partial, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name in manifest and member.isfile():
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                digest = hashlib.sha256()
                for block in iter(lambda: fh.read(1024 * 1024), b""):
                    digest.update(block)
                seen[member.name] = digest.hexdigest()
    if seen != manifest:
        raise RuntimeError(f"Backup-Prüfung fehlgeschlagen: {len(seen)} von {len(manifest)} Dateien stimmen.")
    os.replace(partial, archive)
    os.chmod(archive, 0o600)
    return archive, manifest


def copy_file(src: Path, dst: Path) -> None:
    """Copy without ever replacing an existing file (O_EXCL on a temp name, then a no-overwrite link/rename)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".pulse-migrate.tmp")
    if tmp.exists():
        tmp.unlink()  # leftover of an aborted run of this script – never a user file (own suffix)
    with open(src, "rb") as fin, open(tmp, "xb") as fout:
        shutil.copyfileobj(fin, fout, 1024 * 1024)
        fout.flush()
        os.fsync(fout.fileno())
    shutil.copystat(src, tmp)
    if dst.exists():
        tmp.unlink()
        raise FileExistsError(f"{dst} ist während der Migration entstanden – nicht überschrieben.")
    os.replace(tmp, dst)


def sqlite_ok(path: Path) -> str:
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
        try:
            return str(con.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error as exc:
        return f"Fehler: {exc}"


def verify(source: Path, target: Path, items: list[str]) -> list[str]:
    problems = []
    for rel in items:
        dst = target / rel
        if not dst.is_file():
            problems.append(f"fehlt im Ziel: {rel}")
        elif sha256(dst) != sha256(source / rel):
            problems.append(f"Prüfsumme weicht ab: {rel}")
    for rel in items:
        if rel.endswith(".db") and (target / rel).is_file():
            result = sqlite_ok(target / rel)
            if result != "ok":
                problems.append(f"SQLite-Integrität {rel}: {result}")
    return problems


def set_permissions(target: Path, uid: int, gid: int) -> None:
    """Directories 0750, files 0640, .session_secret 0600 – owned by the container user."""
    paths = [target] + [Path(dp) / n for dp, dns, fns in os.walk(target) for n in dns + fns]
    for path in paths:
        if path.is_symlink():
            continue
        if hasattr(os, "chown") and getattr(os, "geteuid", lambda: -1)() == 0:
            getattr(os, "chown")(path, uid, gid)
        mode = 0o750 if path.is_dir() else (0o600 if path.name in (".session_secret", MARKER_NAME) else 0o640)
        os.chmod(path, mode)


def free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pulse-Laufzeitdaten in das Datenverzeichnis übernehmen (Phase 13).")
    parser.add_argument("--source", default="/opt/pulse/Pulse", help="alter Checkout mit den Laufzeitdaten")
    parser.add_argument("--target", default="/opt/pulse/data", help="neues Datenverzeichnis (PULSE_DATA_HOST_DIR)")
    parser.add_argument("--backup-dir", default="/root/pulse-migration-backups")
    parser.add_argument("--uid", type=int, default=1000)
    parser.add_argument("--gid", type=int, default=1000)
    parser.add_argument("--container", default="pulse-teamos")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="echte Migration (sonst Probelauf)")
    mode.add_argument("--verify", action="store_true", help="nur Quelle und Ziel vergleichen")
    parser.add_argument("--confirm", default="", help=f"nicht-interaktive Bestätigung: --confirm {CONFIRM_WORD}")
    parser.add_argument("--allow-running", action="store_true", help="nur für Tests: laufenden Container ignorieren")
    args = parser.parse_args(argv)

    source, target, backup_dir = Path(args.source).resolve(), Path(args.target).resolve(), Path(args.backup_dir)
    if not source.is_dir():
        log(f"❌ Quelle {source} existiert nicht.")
        return 2
    if target == source or source in target.parents or target in source.parents:
        log("❌ Ziel und Quelle dürfen nicht ineinander liegen.")
        return 2

    items = inventory(source)
    marker = read_marker(target) if target.is_dir() else None
    log(f"Quelle: {source}\nZiel:   {target}\nMarkierung im Ziel: {(marker or {}).get('state', '—')}")
    log(f"Gefundene Laufzeitdateien: {len(items)}")

    if args.verify:
        problems = verify(source, target, items)
        for problem in problems:
            log(f"  ❌ {problem}")
        log("✅ Ziel stimmt mit der Quelle überein." if not problems else f"❌ {len(problems)} Abweichung(en).")
        return 0 if not problems else 1

    result = plan(source, target, items)
    log(f"  kopieren: {len(result['copy'])} · bereits identisch: {len(result['skip'])} · "
        f"Konflikte: {len(result['conflicts'])} · Größe: {result['total_bytes'] / 1024 / 1024:.1f} MB")
    for rel in result["conflicts"]:
        log(f"  ⚠️ Konflikt (Ziel existiert mit anderem Inhalt, wird NICHT überschrieben): {rel}")
    needed = result["total_bytes"] * 2 + 50 * 1024 * 1024  # copy + backup + margin
    free = min(free_bytes(target), free_bytes(backup_dir))
    if free < needed:
        log(f"❌ Zu wenig Speicher: frei {free / 1024 / 1024:.0f} MB, benötigt ca. {needed / 1024 / 1024:.0f} MB.")
        return 2
    if marker and marker.get("state") == "complete" and not result["copy"] and not result["conflicts"]:
        log("✅ Migration ist bereits abgeschlossen – nichts zu tun.")
        return 0
    if result["conflicts"]:
        log("❌ Abbruch: Konflikte zuerst von Hand klären. Es wurde nichts verändert.")
        return 1
    if not args.execute:
        for rel in result["copy"][:40]:
            log(f"  → {rel}")
        if len(result["copy"]) > 40:
            log(f"  … und {len(result['copy']) - 40} weitere")
        log(f"Probelauf – nichts verändert. Echte Migration: --execute (Bestätigung mit {CONFIRM_WORD}).")
        return 0

    if container_running(args.container) and not args.allow_running:
        log(f"❌ Container {args.container} läuft – erst stoppen (docker compose stop pulse), damit sich SQLite/JSON "
            "beim Kopieren nicht ändern.")
        return 2
    answer = args.confirm or (input(f"Zum Starten {CONFIRM_WORD} eingeben: ") if sys.stdin.isatty() else "")
    if answer.strip() != CONFIRM_WORD:
        log("Abgebrochen – keine Bestätigung. Es wurde nichts verändert.")
        return 3

    archive, manifest = create_backup(source, items, backup_dir)
    log(f"✅ Backup erstellt und geprüft: {archive} ({len(manifest)} Dateien)")
    target.mkdir(parents=True, exist_ok=True)
    write_marker(target, "in_progress", source=str(source), backup=str(archive), files=len(items))
    for rel in result["copy"]:
        copy_file(source / rel, target / rel)
    problems = verify(source, target, items)
    if problems:
        for problem in problems:
            log(f"  ❌ {problem}")
        log("❌ Prüfung nach dem Kopieren fehlgeschlagen – Markierung bleibt in_progress, Pulse startet so nicht.")
        return 1
    for name in ("logs", "tmp", "backups"):
        (target / name).mkdir(exist_ok=True)
    set_permissions(target, args.uid, args.gid)
    total = sum((target / rel).stat().st_size for rel in items)
    write_marker(target, "complete", source=str(source), backup=str(archive), files=len(items), bytes=total,
                 manifest_sha256=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest())
    set_permissions(target, args.uid, args.gid)  # once more for the new marker file
    log(f"✅ Migration abgeschlossen: {len(items)} Dateien geprüft (Anzahl + SHA-256), Quelle unverändert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
