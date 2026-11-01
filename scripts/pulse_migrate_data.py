#!/usr/bin/env python3
"""Phase 13 / 13.1 (D-06): move the Pulse runtime data between the old checkout and the data directory.

    python3 scripts/pulse_migrate_data.py                         # dry run (default): shows what would happen
    python3 scripts/pulse_migrate_data.py --verify                # compares source and target, writes nothing
    sudo python3 scripts/pulse_migrate_data.py --execute          # real migration, asks for "MIGRIEREN"
    sudo python3 scripts/pulse_migrate_data.py --rollback         # dry run of the way back into the checkout
    sudo python3 scripts/pulse_migrate_data.py --rollback --execute   # way back, asks for "ZURUECK"

Defaults: source /opt/pulse/Pulse, target /opt/pulse/data, backup /root/pulse-migration-backups, UID/GID 1000.
Only the Python standard library – runs on the host without the Pulse image.

Rules (CLAUDE.md, Phase 13/13.1):
* Fail-closed: a real run needs a reachable Docker daemon and proof that the Pulse container is stopped (or, with
  --container-absent, that it does not exist). Docker missing, daemon down or `docker inspect` failing = abort.
  Dry run and --verify only read and work without Docker.
* Nothing is deleted. The migration only reads the source; files in the target are never replaced: an identical file
  is skipped (idempotent), a different one is a conflict and stops before anything is copied. The final step of every
  copy is a hard link (atomic, fails if the name exists) – a file created in parallel is never overwritten.
* Before copying: a complete tar.gz backup of every affected item, read back and verified against SHA-256.
* The marker ``.pulse-data.json`` says ``in_progress`` while copying and ``complete`` only after the count and the
  checksum of every file matched – Pulse and every deploy path refuse an ``in_progress`` (aborted) migration.
  A damaged or foreign marker is never replaced.
* Symlinks are neither followed nor copied; every path is checked to stay inside its root (no traversal).
* ``.env`` and version-controlled files are never moved: secrets stay on the host next to compose.yaml.
* --rollback (the way back to the old layout): moves the outdated copies in the checkout aside into
  ``.pulse-legacy-<time>/`` (rename, nothing deleted), copies the current data back and sets the marker to
  ``rolled_back`` – Pulse in the data-directory layout then refuses to start until a new migration.
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MARKER_NAME = ".pulse-data.json"  # same as pulse_paths.MARKER_NAME (tests keep them in sync)
MARKER_FORMAT = "pulse-data-dir"
CONFIRM_WORD = "MIGRIEREN"
ROLLBACK_WORD = "ZURUECK"
LOCK_NAME = ".pulse-migrate.lock"
TMP_SUFFIX = ".pulse-migrate.tmp"
# Container states that prove no Pulse process can write: created (never started), exited, dead.
STOPPED_STATES = {"created", "exited", "dead"}

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
# Never data: tooling files and the secrets file.
NEVER = {MARKER_NAME, "package.json", ".env"}  # tracked files are excluded through git as well


class MigrationError(Exception):
    """A condition that must stop the run before (or instead of) changing anything."""


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


# --------------------------------------------------------------------------------------------- paths
def safe_path(root: Path, rel: str) -> Path:
    """root/rel, refusing traversal (absolute, '..') and symlinks on the way (existing components only)."""
    pure = PurePosixPath(rel)
    if pure.is_absolute() or not pure.parts or any(part in ("", ".", "..") for part in pure.parts):
        raise MigrationError(f"Unsicherer Pfad abgelehnt: {rel!r}")
    path = root
    for part in pure.parts:
        path = path / part
        if path.is_symlink():
            raise MigrationError(f"Symlink im Pfad abgelehnt: {path}")
    if root.resolve() not in path.resolve().parents:
        raise MigrationError(f"Pfad verlässt das Verzeichnis: {rel!r}")
    return path


def tracked_files(source: Path) -> set[str]:
    """Files under version control are code, never data. A checkout whose git listing fails is not guessed at."""
    if not (source / ".git").exists():
        return set()
    if shutil.which("git") is None:
        raise MigrationError(f"{source} ist ein Git-Checkout, aber git fehlt – versionierte Dateien nicht erkennbar.")
    try:
        out = subprocess.run(["git", "-c", f"safe.directory={source}", "ls-files", "-z"], cwd=source,
                             capture_output=True, timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise MigrationError(f"git ls-files in {source} fehlgeschlagen ({type(exc).__name__}).") from exc
    return {name for name in out.decode("utf-8", "replace").split("\0") if name}


def inventory(source: Path, *, tracked: set[str] | None = None, skip_folders: tuple[str, ...] = ()) -> list[str]:
    """Relative POSIX paths of every runtime file below source (regular files only, symlinks are skipped).
    The root itself may be a symlink (e.g. /opt/pulse/Pulse → /srv/…); it is resolved, links below it are not."""
    source = source.resolve()
    tracked = tracked_files(source) if tracked is None else tracked
    found: set[str] = set()

    def add_file(rel: str) -> None:
        path = source / rel
        if (rel in tracked or Path(rel).name in NEVER or rel.endswith(TMP_SUFFIX) or path.is_symlink()
                or not path.is_file()):
            return
        found.add(rel)

    for name in SQLITE_FILES + JSON_FILES + SPECIAL_FILES + MEDIA_FILES:
        if (source / name).parent.is_symlink():
            continue
        add_file(name)
        for suffix in ("-wal", "-shm", "-journal"):
            add_file(name + suffix)
    for entry in source.iterdir() if source.is_dir() else ():
        if entry.is_file() and any(fnmatch.fnmatch(entry.name, pattern) for pattern in EXTRA_PATTERNS):
            add_file(entry.name)
    for folder in FOLDERS:
        root = source / folder
        if folder in skip_folders or root.is_symlink() or not root.is_dir():
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
        src, dst = safe_path(source, rel), safe_path(target, rel)
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


# --------------------------------------------------------------------------------------------- marker
def read_marker(target: Path) -> tuple[str, dict | None]:
    """('missing', None) · ('ok', data) · ('damaged', None) – a damaged marker is never overwritten."""
    path = target / MARKER_NAME
    if not path.exists() and not path.is_symlink():
        return "missing", None
    if path.is_symlink():
        return "damaged", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "damaged", None
    if not isinstance(data, dict) or data.get("format") != MARKER_FORMAT:
        return "damaged", None
    return "ok", data


def write_marker(target: Path, state: str, **extra) -> None:
    payload = {"format": MARKER_FORMAT, "version": 1, "state": state,
               "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}
    # The data directory belongs to the container user: a fixed temp name could be a planted symlink that root would
    # follow. A unique name opened with O_EXCL (fails on any existing path, symlinks included) and O_NOFOLLOW cannot;
    # os.replace then swaps the directory entry itself and never follows a symlink at the marker name.
    tmp = target / f"{MARKER_NAME}.{secrets.token_hex(6)}.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write((json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target / MARKER_NAME)  # the marker itself is ours and is meant to be updated
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


# --------------------------------------------------------------------------------------------- docker
def container_state(name: str) -> str:
    """Status of the container as Docker reports it. Raises MigrationError whenever that is not proven."""
    if shutil.which("docker") is None:
        raise MigrationError("docker ist nicht installiert – ob Pulse läuft, ist nicht nachweisbar.")
    try:
        info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True,
                              timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MigrationError(f"Docker-Daemon nicht erreichbar ({type(exc).__name__}).") from exc
    if info.returncode != 0 or not info.stdout.strip():
        raise MigrationError("Docker-Daemon nicht erreichbar (docker info schlägt fehl).")
    try:
        out = subprocess.run(["docker", "inspect", "--format", "{{.State.Status}}", name],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MigrationError(f"docker inspect {name} fehlgeschlagen ({type(exc).__name__}).") from exc
    if out.returncode != 0:
        if "no such object" in (out.stderr or "").lower() or "no such container" in (out.stderr or "").lower():
            return "absent"
        raise MigrationError(f"docker inspect {name} fehlgeschlagen: {(out.stderr or '').strip()[:200]}")
    return out.stdout.strip() or "unknown"


def require_stopped(name: str, *, absent_ok: bool) -> str:
    state = container_state(name)
    if state in STOPPED_STATES:
        return state
    if state == "absent":
        if absent_ok:
            return state
        raise MigrationError(f"Container {name} existiert nicht. Nur wenn das so gewollt ist (neuer Server): "
                             "--container-absent angeben.")
    raise MigrationError(f"Container {name} ist im Zustand {state!r} – erst stoppen (docker compose stop pulse).")


# --------------------------------------------------------------------------------------------- lock
@contextlib.contextmanager
def run_lock(directory: Path):
    """One migration/rollback at a time per data directory (O_EXCL lock file; a leftover is reported, not removed)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOCK_NAME
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise MigrationError(f"{path} existiert – läuft bereits eine Migration? Wenn sicher nicht: Datei prüfen und "
                             "von Hand entfernen.") from None
    try:
        os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n".encode())
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


# --------------------------------------------------------------------------------------------- backup
def create_backup(root: Path, items: list[str], backup_dir: Path, label: str) -> tuple[Path, dict]:
    """tar.gz of every item plus a checksum manifest; read back and verified before anything else happens."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = backup_dir / f"pulse-{label}-{stamp}.tar.gz"
    manifest = {rel: sha256(root / rel) for rel in items}
    partial = archive.with_name(archive.name + ".partial")
    with tarfile.open(partial, "w:gz") as tar:
        for rel in items:
            tar.add(root / rel, arcname=rel, recursive=False)
        data = json.dumps({"source": str(root), "files": manifest}, indent=2).encode("utf-8")
        info = tarfile.TarInfo("pulse-migration-manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    problems = verify_backup(partial, manifest)
    if problems:
        raise MigrationError(f"Backup-Prüfung fehlgeschlagen ({problems[0]}) – nichts verändert, Teildatei: {partial}")
    os.link(partial, archive)  # no-overwrite publish; the .partial name is removed only after the link exists
    partial.unlink()
    os.chmod(archive, 0o600)
    return archive, manifest


def verify_backup(archive: Path, manifest: dict) -> list[str]:
    seen, problems = {}, []
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name in manifest and member.isfile():
                    fh = tar.extractfile(member)
                    if fh is None:
                        continue
                    digest = hashlib.sha256()
                    for block in iter(lambda fh=fh: fh.read(1024 * 1024), b""):
                        digest.update(block)
                    seen[member.name] = digest.hexdigest()
    except (OSError, tarfile.TarError, EOFError) as exc:
        return [f"Archiv nicht lesbar: {type(exc).__name__}"]
    for rel, digest in manifest.items():
        if seen.get(rel) != digest:
            problems.append(f"{rel} fehlt oder weicht ab")
    return problems


# --------------------------------------------------------------------------------------------- copy
def copy_file(src: Path, dst: Path) -> None:
    """Copy src to dst without ever replacing anything.

    The content goes into a unique temp file next to dst (O_EXCL), is fsynced and only then published with
    ``os.link`` – atomic, and it fails with FileExistsError if dst appeared in the meantime (parallel writer, earlier
    run). The temp name is removed after the link; after a crash only a ``*.pulse-migrate.tmp`` leftover remains,
    which inventory and verify ignore. Never os.replace/rename onto dst."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{secrets.token_hex(6)}{TMP_SUFFIX}")
    fin = open(src, "rb")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with fin, os.fdopen(fd, "wb") as fout:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
            fout.flush()
            os.fsync(fout.fileno())
        shutil.copystat(src, tmp)
        try:
            os.link(tmp, dst)
        except FileExistsError:
            raise FileExistsError(f"{dst} existiert bereits (paralleler Zugriff?) – nicht überschrieben.") from None
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()
    with contextlib.suppress(OSError, AttributeError):
        dirfd = os.open(dst.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)


def leftover_temp_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob(f"*{TMP_SUFFIX}") if p.is_file()) if root.is_dir() else []


# --------------------------------------------------------------------------------------------- verify
def sqlite_ok(paths: list[Path]) -> str:
    """PRAGMA integrity_check on a private copy (db + -wal + -shm): the checked files are never opened or touched."""
    db = paths[0]
    with tempfile.TemporaryDirectory(prefix="pulse-sqlite-check-") as tmp:
        for path in paths:
            if path.is_file():
                shutil.copy2(path, Path(tmp) / path.name)
        try:
            con = sqlite3.connect(Path(tmp) / db.name, timeout=15)
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
        if dst.is_symlink() or not dst.is_file():
            problems.append(f"fehlt im Ziel: {rel}")
        elif sha256(dst) != sha256(source / rel):
            problems.append(f"Prüfsumme weicht ab: {rel}")
    for rel in items:
        if rel.endswith((".db", ".sqlite", ".sqlite3")) and (target / rel).is_file():
            result = sqlite_ok([target / rel] + [target / (rel + s) for s in ("-wal", "-shm")])
            if result != "ok":
                problems.append(f"SQLite-Integrität {rel}: {result}")
    return problems


def set_permissions(target: Path, uid: int, gid: int) -> None:
    """Directories 0750, files 0640, .session_secret and the marker 0600 – owned by the container user."""
    paths = [target] + [Path(dp) / n for dp, dns, fns in os.walk(target) for n in dns + fns]
    for path in paths:
        if path.is_symlink():
            continue
        if hasattr(os, "lchown") and getattr(os, "geteuid", lambda: -1)() == 0:
            getattr(os, "lchown")(path, uid, gid)  # never follows a link swapped in after the check
        mode = 0o750 if path.is_dir() else (0o600 if path.name in (".session_secret", MARKER_NAME, LOCK_NAME) else 0o640)
        os.chmod(path, mode)


def free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free


def check_space(paths: list[Path], needed: int) -> None:
    free = min(free_bytes(p) for p in paths)
    if free < needed:
        raise MigrationError(f"Zu wenig Speicher: frei {free / 1024 / 1024:.0f} MB, benötigt ca. "
                             f"{needed / 1024 / 1024:.0f} MB.")


def confirm(args, word: str) -> None:
    answer = args.confirm or (input(f"Zum Starten {word} eingeben: ") if sys.stdin.isatty() else "")
    if answer.strip() != word:
        raise MigrationError("Abgebrochen – keine Bestätigung. Es wurde nichts verändert.")


# --------------------------------------------------------------------------------------------- migrate
def migrate(args, source: Path, target: Path, backup_dir: Path) -> int:
    marker_status, marker = read_marker(target) if target.is_dir() else ("missing", None)
    if marker_status == "damaged":
        raise MigrationError(f"{target / MARKER_NAME} ist beschädigt oder fremd – wird nicht ersetzt. Bitte prüfen.")
    state = (marker or {}).get("state")
    if state == "rolled_back":
        raise MigrationError("Das Datenverzeichnis wurde per --rollback zurückgegeben. Für eine neue Migration den "
                             "Ordner erst beiseitelegen (umbenennen) – er enthält den Stand vor dem Rückweg.")
    items = inventory(source)
    log(f"Quelle: {source}\nZiel:   {target}\nMarkierung im Ziel: {state or '—'}")
    log(f"Gefundene Laufzeitdateien: {len(items)}")
    leftovers = leftover_temp_files(target)
    if leftovers:
        log(f"  ℹ️ {len(leftovers)} Reste abgebrochener Kopien (*{TMP_SUFFIX}) im Ziel – werden ignoriert, nicht gelöscht.")

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
    check_space([target, backup_dir], result["total_bytes"] * 2 + 50 * 1024 * 1024)  # copy + backup + margin
    if state == "complete" and not result["copy"] and not result["conflicts"]:
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

    container = require_stopped(args.container, absent_ok=args.container_absent)
    log(f"Container {args.container}: {container} – keine Schreibzugriffe möglich.")
    confirm(args, CONFIRM_WORD)
    with run_lock(target):
        archive, manifest = create_backup(source, items, backup_dir, "premigration")
        log(f"✅ Backup erstellt und geprüft: {archive} ({len(manifest)} Dateien)")
        write_marker(target, "in_progress", source=str(source), backup=str(archive), files=len(items))
        for rel in plan(source, target, items)["copy"]:  # re-planned under the lock
            copy_file(safe_path(source, rel), safe_path(target, rel))
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


# --------------------------------------------------------------------------------------------- rollback
def rollback(args, source: Path, target: Path, backup_dir: Path) -> int:
    """Way back to the old layout: the data directory (current) → the checkout (outdated copies moved aside)."""
    marker_status, marker = read_marker(target) if target.is_dir() else ("missing", None)
    if marker_status != "ok" or (marker or {}).get("state") not in {"complete", "initialized"}:
        raise MigrationError(f"{target} trägt keine gültige Markierung complete/initialized – Rückweg abgebrochen.")
    tracked = tracked_files(source)
    items = [rel for rel in inventory(target, tracked=set()) if rel not in tracked]
    blocked = [rel for rel in inventory(target, tracked=set()) if rel in tracked]
    if blocked:
        raise MigrationError(f"Datenverzeichnis enthält versionierte Dateinamen: {blocked[:5]} – Rückweg abgebrochen.")
    replace, new, same, total = rollback_plan(source, target, items)
    log(f"Rückweg: {target} → {source}")
    log(f"  neu: {len(new)} · veraltet (werden beiseitegelegt): {len(replace)} · identisch: {len(same)} · "
        f"Größe: {total / 1024 / 1024:.1f} MB")
    check_space([source, backup_dir], total * 2 + 50 * 1024 * 1024)
    if not args.execute:
        for rel in replace[:40]:
            log(f"  ↺ {rel}")
        log(f"Probelauf – nichts verändert. Rückweg: --rollback --execute (Bestätigung mit {ROLLBACK_WORD}).")
        return 0

    container = require_stopped(args.container, absent_ok=args.container_absent)
    log(f"Container {args.container}: {container} – keine Schreibzugriffe möglich.")
    confirm(args, ROLLBACK_WORD)
    with run_lock(target):
        # Re-planned under the lock: what is moved aside and copied is decided on the current state, not the dry run.
        items = [rel for rel in inventory(target, tracked=set()) if rel not in tracked]
        replace, new, _same, _total = rollback_plan(source, target, items)
        archive, _ = create_backup(target, items, backup_dir, "prerollback-data")
        log(f"✅ Backup des Datenverzeichnisses: {archive}")
        legacy_items = [rel for rel in replace if (source / rel).is_file()]
        if legacy_items:
            legacy_archive, _ = create_backup(source, legacy_items, backup_dir, "prerollback-legacy")
            log(f"✅ Backup der veralteten Checkout-Dateien: {legacy_archive}")
        aside = source / f".pulse-legacy-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        write_marker(target, "rollback_in_progress", checkout=str(source), backup=str(archive))
        for rel in replace:
            old = safe_path(source, rel)
            moved = aside / rel  # rel was validated by safe_path above; `aside` is new and ours
            moved.parent.mkdir(parents=True, exist_ok=True)
            os.rename(old, moved)  # same file system, nothing deleted; restorable from `aside`
        for rel in replace + new:
            copy_file(safe_path(target, rel), safe_path(source, rel))
        problems = verify(target, source, items)
        if problems:
            for problem in problems:
                log(f"  ❌ {problem}")
            log("❌ Prüfung fehlgeschlagen – Markierung bleibt rollback_in_progress; beide Pulse-Layouts starten so nicht.")
            return 1
        set_permissions_files(source, items, args.uid, args.gid)
        write_marker(target, "rolled_back", checkout=str(source), backup=str(archive),
                     aside=str(aside) if replace else "")
    log(f"✅ Rückweg abgeschlossen: {len(items)} Dateien im Checkout geprüft. Veraltete Kopien: {aside if replace else '—'}.")
    log("Jetzt den alten Code-Stand starten (VPS_DEPLOYMENT.md, Rückweg). Das Datenverzeichnis ist als rolled_back "
        "markiert – der neue Stand startet darauf erst nach einer neuen Migration.")
    return 0


def rollback_plan(source: Path, target: Path, items: list[str]) -> tuple[list[str], list[str], list[str], int]:
    """(outdated in the checkout → moved aside, new, identical, bytes) – reads only."""
    replace, new, same, total = [], [], [], 0
    for rel in items:
        src, dst = safe_path(target, rel), safe_path(source, rel)
        total += src.stat().st_size
        if dst.exists() or dst.is_symlink():
            (same if dst.is_file() and not dst.is_symlink() and sha256(dst) == sha256(src) else replace).append(rel)
        else:
            new.append(rel)
    return replace, new, same, total


def set_permissions_files(root: Path, rels: list[str], uid: int, gid: int) -> None:
    """Old layout: the checkout files the container writes belong to UID 1000 (as remote_deploy.sh expects)."""
    if not (hasattr(os, "chown") and getattr(os, "geteuid", lambda: -1)() == 0):
        return
    for rel in rels:
        path = root / rel
        for item in [path] + list(path.parents):
            if item == root or root not in item.parents:
                break
            if not item.is_symlink():
                getattr(os, "chown")(item, uid, gid)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Pulse-Laufzeitdaten in das Datenverzeichnis übernehmen (Phase 13).")
    parser.add_argument("--source", default="/opt/pulse/Pulse", help="alter Checkout mit den Laufzeitdaten")
    parser.add_argument("--target", default="/opt/pulse/data", help="neues Datenverzeichnis (PULSE_DATA_HOST_DIR)")
    parser.add_argument("--backup-dir", default="/root/pulse-migration-backups")
    parser.add_argument("--uid", type=int, default=1000)
    parser.add_argument("--gid", type=int, default=1000)
    parser.add_argument("--container", default="pulse-teamos")
    parser.add_argument("--container-absent", action="store_true",
                        help="nur neuer Server: der Container existiert nachweislich (noch) nicht")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="echte Ausführung (sonst Probelauf)")
    mode.add_argument("--verify", action="store_true", help="nur Quelle und Ziel vergleichen")
    parser.add_argument("--rollback", action="store_true", help="Rückweg: Daten zurück in den Checkout")
    parser.add_argument("--confirm", default="", help=f"nicht-interaktive Bestätigung ({CONFIRM_WORD} bzw. {ROLLBACK_WORD})")
    args = parser.parse_args(argv)

    source, target, backup_dir = Path(args.source).resolve(), Path(args.target).resolve(), Path(args.backup_dir)
    if not source.is_dir():
        log(f"❌ Quelle {source} existiert nicht.")
        return 2
    if target == source or source in target.parents or target in source.parents:
        log("❌ Ziel und Quelle dürfen nicht ineinander liegen.")
        return 2
    if args.rollback and args.verify:
        log("❌ --rollback und --verify schließen sich aus.")
        return 2
    try:
        return rollback(args, source, target, backup_dir) if args.rollback else migrate(args, source, target, backup_dir)
    except MigrationError as exc:
        log(f"❌ {exc}")
        return 3 if "keine Bestätigung" in str(exc) else 2


if __name__ == "__main__":
    sys.exit(main())
