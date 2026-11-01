"""Phase 14.7 (D-07 / A-05): the shift history of shifts.json in SQLite – opt-in, verified, reversible.

Before: shifts.json holds ``active_shifts`` and the endless ``history`` list; every finished shift rewrote
(and fsync'ed) the whole file. Now, once migrated:

* ``history`` lives in the table ``shift_history`` of pulse.db: one row per entry, ``seq`` = position in the
  list (order kept), ``payload`` = the entry as JSON text (every field kept, unknown ones included; duplicates
  stay separate rows – nothing is merged). ``mod_id``/``shift_date`` are only an index for later queries.
* ``active_shifts`` (and any other top-level key) stay in shifts.json, together with the marker
  ``"_history_store": {"backend": "sqlite", ...}``. The marker in the JSON file decides where the history is:
  restoring an old backup of shifts.json (full history, no marker) automatically means "JSON" again.
* The single write point stays ``webserver.save_json(SHIFTS_FILE, …)`` and the single read point
  ``webserver.load_json(SHIFTS_FILE, …)`` – both route here in SQLite mode, so ``load_shifts()``,
  ``load_shifts_raw()`` and every caller keep getting ``{"active_shifts": {...}, "history": [...]}``.
  A save writes only the rows that changed and the small JSON file, both under ``_io_lock`` in one SQLite
  transaction (the JSON file is replaced before COMMIT; if the COMMIT fails the previous file is put back).

Switching happens only at start (``webserver.init_runtime``) and only when ``PULSE_SHIFT_HISTORY_BACKEND`` is
set explicitly: ``sqlite`` → migrate (idempotent), ``json`` → way back, unset → nothing changes. The data move
runs through ``pulse_migrations.run`` (pulse.db backup, one transaction, ``schema_migrations`` entry); before
it, shifts.json is copied to ``backups/shifts_pre_sqlite_<time>.json``. On any error shifts.json stays as it
was and Pulse keeps working with JSON.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone

ENV_VAR = "PULSE_SHIFT_HISTORY_BACKEND"
MARKER_KEY = "_history_store"
MIGRATION_NAME = "shift_history_sqlite_v1"
FORMAT_VERSION = 1
KEEP_JSON_BACKUPS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS shift_history (
    seq INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    mod_id TEXT,
    shift_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_shift_history_member ON shift_history(mod_id, shift_date);
CREATE TABLE IF NOT EXISTS shift_history_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Mode of shifts.json for save_json without parsing the (in JSON mode large) file again: keyed by its stat.
_MODE: dict = {"key": None, "sqlite": False}


class ShiftHistoryUnavailable(RuntimeError):
    """The marker says SQLite, but the history table cannot be read – writes must stay blocked."""


_ENCODER = json.JSONEncoder(ensure_ascii=False)  # one encoder: json.dumps(…, ensure_ascii=False) builds one per call


def _dumps(entry) -> str:
    return _ENCODER.encode(entry)


def _index_columns(entry) -> tuple:
    if not isinstance(entry, dict):
        return None, None
    mod_id, day = entry.get("mod_id"), entry.get("date")
    return (None if mod_id is None else str(mod_id)), (day if isinstance(day, str) else None)


def _db_path() -> str:
    import pulse_db
    return os.path.abspath(pulse_db.DB_PATH)


def _connect(path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or _db_path(), timeout=15, isolation_level=None)
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _stat_key(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.abspath(path), st.st_mtime_ns, st.st_size)


def is_sqlite(raw) -> bool:
    marker = raw.get(MARKER_KEY) if isinstance(raw, dict) else None
    return isinstance(marker, dict) and marker.get("backend") == "sqlite"


def remember_mode(path, raw, key=None) -> None:
    """Called with the parsed file content under _io_lock (load_json / our own writes). `key`: the stat taken
    before the file was read – if the file was replaced while it was parsed, the next save looks again."""
    _MODE.update(key=key if key is not None else _stat_key(path), sqlite=is_sqlite(raw))


def file_is_sqlite(path) -> bool:
    """Is shifts.json in SQLite mode right now? Parses the file only if it changed since we last saw it."""
    key = _stat_key(path)
    if key is None:
        return False
    if _MODE["key"] != key:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            # JSON mode (large file): the marker text is not there, no full parse needed.
            raw = json.loads(text) if f'"{MARKER_KEY}"' in text else {}
        except (OSError, ValueError):
            raw = {}
        _MODE.update(key=key, sqlite=is_sqlite(raw))
    return bool(_MODE["sqlite"])


def _table_exists(con) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shift_history'").fetchone() is not None


def _payloads(con) -> list[str]:
    return [row[0] for row in con.execute("SELECT payload FROM shift_history ORDER BY seq")]


def _generation(con) -> str | None:
    try:
        row = con.execute("SELECT value FROM shift_history_meta WHERE key='generation'").fetchone()
    except sqlite3.OperationalError:  # table from before the meta table existed / not created yet
        return None
    return row[0] if row else None


def _read(path: str | None = None) -> tuple[list, str | None]:
    con = _connect(path)
    try:
        if not _table_exists(con):
            raise ShiftHistoryUnavailable("Tabelle shift_history fehlt in pulse.db")
        # One parse of the joined rows (C speed, like json.load of the old file) instead of one per row.
        return json.loads("[" + ",".join(_payloads(con)) + "]"), _generation(con)
    finally:
        con.close()


def read_history(path: str | None = None) -> list:
    return _read(path)[0]


def attach(raw: dict) -> dict:
    """shifts.json in SQLite mode → the old shape: marker removed, history read from pulse.db."""
    data = {k: v for k, v in raw.items() if k != MARKER_KEY}
    marker = raw[MARKER_KEY]
    try:
        history, generation = _read()
    except (sqlite3.Error, ValueError) as exc:
        raise ShiftHistoryUnavailable(f"Schichthistorie in pulse.db nicht lesbar ({type(exc).__name__})") from exc
    if marker.get("generation") and marker.get("generation") != generation:
        # shifts.json and pulse.db belong to different migrations (e.g. rows left from before a way back).
        raise ShiftHistoryUnavailable("shifts.json und pulse.db gehören zu unterschiedlichen Ständen der Schichthistorie")
    expected = marker.get("rows")
    if isinstance(expected, int) and expected > len(history):
        # pulse.db replaced by an older copy: continuing would write the loss down for good – block instead.
        raise ShiftHistoryUnavailable(f"pulse.db enthält {len(history)} statt {expected} Schichteinträge")
    if isinstance(expected, int) and expected != len(history):
        print(f"⚠️ Schichthistorie: {len(history)} Einträge in pulse.db, shifts.json erwartet {expected}. "
              "Es wird mit pulse.db weitergearbeitet.")
    data["history"] = history
    if "active_shifts" not in data:
        data["active_shifts"] = {}
    return data


def _sync(con, history: list) -> int:
    """Write only what differs from the stored rows (same position = same seq). Returns changed rows."""
    stored = _payloads(con)
    new = [_dumps(e) for e in history]
    changed = 0
    for pos, payload in enumerate(new):
        if pos < len(stored):
            if stored[pos] == payload:
                continue
            con.execute("UPDATE shift_history SET payload=?, mod_id=?, shift_date=? WHERE seq=?",
                        (payload, *_index_columns(history[pos]), pos))
        else:
            con.execute("INSERT INTO shift_history(seq, payload, mod_id, shift_date) VALUES (?, ?, ?, ?)",
                        (pos, payload, *_index_columns(history[pos])))
        changed += 1
    if len(stored) > len(new):
        con.execute("DELETE FROM shift_history WHERE seq >= ?", (len(new),))
        changed += len(stored) - len(new)
    return changed


def _marker(rows: int, previous: dict | None = None) -> dict:
    marker = dict(previous or {})
    marker.update(backend="sqlite", version=FORMAT_VERSION, table="shift_history", rows=rows,
                  updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return marker


def save(ws, path, data: dict) -> None:
    """save_json(SHIFTS_FILE, data) in SQLite mode. Caller holds ws._io_lock."""
    previous_text = _read_text(path)
    previous = json.loads(previous_text)
    rest = {k: v for k, v in data.items() if k not in ("history", MARKER_KEY)}
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        if "history" in data:
            history = data.get("history")
            if not isinstance(history, list):
                raise ValueError("history muss eine Liste sein")
            _sync(con, history)
            rows = len(history)
        else:  # a caller without "history" never erases the stored history
            rows = con.execute("SELECT COUNT(*) FROM shift_history").fetchone()[0]
        payload = {**rest, "history": [], MARKER_KEY: _marker(rows, previous.get(MARKER_KEY))}
        try:
            ws._write_json_file(path, payload)
            con.execute("COMMIT")
        except BaseException:
            if _read_text(path) != previous_text:  # the history rows are rolled back – so is the file
                _restore_text(path, previous_text)
            raise
        remember_mode(path, payload)
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def _read_text(path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _restore_text(path, text: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    remember_mode(path, json.loads(text))


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_backup(path, backup_dir: str, label: str) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    target = os.path.join(backup_dir, f"shifts_{label}_{stamp}.json")
    shutil.copy2(path, target)
    if _sha256(path) != _sha256(target):
        raise OSError("Sicherung von shifts.json stimmt nicht mit dem Original überein")
    old = sorted((f for f in os.listdir(backup_dir) if f.startswith(f"shifts_{label}_") and f.endswith(".json")),
                 reverse=True)
    for name in old[KEEP_JSON_BACKUPS:]:
        try:
            os.remove(os.path.join(backup_dir, name))
        except OSError:
            pass
    return target


def _read_raw(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _forget_migration(db_path: str) -> None:
    con = _connect(db_path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        con.execute("DELETE FROM schema_migrations WHERE name=?", (MIGRATION_NAME,))
    finally:
        con.close()


def migrate(ws) -> dict:
    """shifts.json history → pulse.db. Idempotent; on any error shifts.json is left exactly as it was."""
    import pulse_migrations
    path, db_path, backup_dir = ws.SHIFTS_FILE, _db_path(), os.path.abspath(ws.BACKUP_DIR)
    with ws._io_lock:
        try:
            raw = _read_raw(path)
        except (OSError, ValueError) as exc:
            return {"status": "failed", "error": f"shifts.json nicht lesbar ({type(exc).__name__})"}
        if raw is None:
            raw = {"active_shifts": {}, "history": []}
        if not isinstance(raw, dict) or not isinstance(raw.get("history", []), list):
            return {"status": "failed", "error": "shifts.json hat keine gültige Struktur (history ist keine Liste)"}
        if is_sqlite(raw):
            return {"status": "already", "rows": len(read_history())}
        history = raw.get("history", [])
        json_backup = _json_backup(path, backup_dir, "pre_sqlite") if os.path.exists(path) else ""
        expected = [_dumps(e) for e in history]
        generation = uuid.uuid4().hex

        def move(con):
            for statement in [s for s in SCHEMA.split(";") if s.strip()]:
                con.execute(statement)
            con.execute("DELETE FROM shift_history")  # leftovers of an earlier migration that was rolled back
            con.executemany("INSERT INTO shift_history(seq, payload, mod_id, shift_date) VALUES (?, ?, ?, ?)",
                            ((pos, payload, *_index_columns(entry))
                             for pos, (payload, entry) in enumerate(zip(expected, history))))
            con.execute("INSERT OR REPLACE INTO shift_history_meta(key, value) VALUES ('generation', ?)", (generation,))
            if _payloads(con) != expected:
                raise RuntimeError("Prüfung nach dem Import: Einträge weichen ab")

        _forget_migration(db_path)  # a rolled-back or half-finished run may have left its name behind
        report = pulse_migrations.run(db_path, backup_dir, [(MIGRATION_NAME, move)])
        if report["failed"] or MIGRATION_NAME not in report["applied"]:
            return {"status": "failed", "error": report["failed"] or "Migration nicht ausgeführt",
                    "json_backup": json_backup, "db_backup": report["backup"]}
        payload = {k: v for k, v in raw.items() if k != "history"}
        payload.setdefault("active_shifts", {})
        payload["history"] = []
        payload[MARKER_KEY] = _marker(len(history)) | {
            "generation": generation,
            "migrated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "json_backup": os.path.basename(json_backup)}
        original_text = _read_text(path) if os.path.exists(path) else None
        try:
            ws._write_json_file(path, payload)
            remember_mode(path, payload)
            check = attach(_read_raw(path) or {})
            original = dict(raw)
            original.setdefault("active_shifts", {})
            original.setdefault("history", [])
            if check != original:
                raise RuntimeError("Prüfung nach dem Umschalten: Daten weichen ab")
        except Exception as exc:
            if original_text is not None:
                _restore_text(path, original_text)
            elif os.path.exists(path):
                os.remove(path)
            _forget_migration(db_path)
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "json_backup": json_backup,
                    "db_backup": report["backup"]}
        return {"status": "migrated", "rows": len(history), "json_backup": json_backup, "db_backup": report["backup"]}


def rollback(ws) -> dict:
    """Way back: history from pulse.db into shifts.json (old format, no marker). The rows stay in pulse.db."""
    import pulse_migrations
    path, db_path, backup_dir = ws.SHIFTS_FILE, _db_path(), os.path.abspath(ws.BACKUP_DIR)
    with ws._io_lock:
        try:
            raw = _read_raw(path)
        except (OSError, ValueError) as exc:
            return {"status": "failed", "error": f"shifts.json nicht lesbar ({type(exc).__name__})"}
        if raw is None or not is_sqlite(raw):
            return {"status": "already"}
        try:
            data = attach(raw)
        except ShiftHistoryUnavailable as exc:
            return {"status": "failed", "error": str(exc)}
        json_backup = _json_backup(path, backup_dir, "pre_json")
        db_backup = pulse_migrations._backup(db_path, backup_dir, "shift_history_rollback")
        original_text = _read_text(path)
        restored = {k: v for k, v in raw.items() if k not in ("history", MARKER_KEY)}
        restored["history"] = data["history"]
        try:
            ws._write_json_file(path, restored)
            remember_mode(path, restored)
            if _read_raw(path) != restored:
                raise RuntimeError("Prüfung nach dem Zurückschreiben: Daten weichen ab")
        except Exception as exc:
            _restore_text(path, original_text)
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "json_backup": json_backup}
        _forget_migration(db_path)
        con = _connect(db_path)
        try:  # rows stay (no data deleted), but a later copy of the marked shifts.json no longer matches them
            con.execute("INSERT OR REPLACE INTO shift_history_meta(key, value) VALUES ('generation', ?)",
                        (f"rolled_back_{uuid.uuid4().hex}",))
        finally:
            con.close()
        return {"status": "rolled_back", "rows": len(data["history"]), "json_backup": json_backup, "db_backup": db_backup}


def status(ws) -> dict:
    with ws._io_lock:
        try:
            raw = _read_raw(ws.SHIFTS_FILE)
        except (OSError, ValueError) as exc:
            return {"backend": "unknown", "error": type(exc).__name__}
        info: dict = {"backend": "sqlite" if is_sqlite(raw) else "json", "configured": os.getenv(ENV_VAR, "").strip().lower()}
        if info["backend"] == "sqlite":
            info["marker_rows"] = raw[MARKER_KEY].get("rows")
            try:
                con = _connect()
                try:
                    info["db_rows"] = con.execute("SELECT COUNT(*) FROM shift_history").fetchone()[0]
                finally:
                    con.close()
            except sqlite3.Error as exc:
                info["error"] = type(exc).__name__
        else:
            info["json_rows"] = len((raw or {}).get("history", []) or [])
        return info


def apply_configured_backend(ws) -> dict:
    """At start, before bot and dashboard write: switch only if PULSE_SHIFT_HISTORY_BACKEND asks for it."""
    wanted = os.getenv(ENV_VAR, "").strip().lower()
    if not wanted:
        return {"status": "unchanged"}
    if wanted in ("sqlite", "json"):
        try:
            report = migrate(ws) if wanted == "sqlite" else rollback(ws)
        except Exception as exc:  # never stop the start of Pulse: the file is untouched or restored by migrate/rollback
            report = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    else:
        print(f"⚠️ {ENV_VAR}={wanted!r} ist unbekannt (erlaubt: sqlite, json) – Schichthistorie bleibt unverändert.")
        return {"status": "unchanged", "error": "unknown value"}
    if report["status"] == "failed":
        print(f"🔴 Schichthistorie: Umstellung auf {wanted} fehlgeschlagen – {report.get('error')}. "
              "shifts.json ist unverändert, Pulse arbeitet mit dem bisherigen Speicher weiter.")
    elif report["status"] in ("migrated", "rolled_back"):
        print(f"✅ Schichthistorie: {report['rows']} Einträge auf {wanted} umgestellt "
              f"(Sicherung: {os.path.basename(report.get('json_backup') or '')}).")
    return report
