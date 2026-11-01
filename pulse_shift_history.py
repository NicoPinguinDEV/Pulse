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
  A save writes only the rows that changed and the small JSON file, both under ``_io_lock``.

Phase 14.7b – crash safety. SQLite and a separate JSON file share no transaction, so the SQLite COMMIT is the
one commit point of a save and shifts.json only follows it:

1. ``BEGIN IMMEDIATE``; the pair (marker in shifts.json ↔ ``shift_history_meta``) must be in a known state.
2. Changed history rows + ``revision`` (+1) + ``journal`` (the complete new shifts.json – active shifts,
   other keys and marker, without history –, its SHA-256 and the SHA-256 of the file it replaces) → COMMIT.
3. shifts.json is replaced (tmp, fsync, ``os.replace``) with exactly the journalled content.

A crash before 2 leaves the old state (SQLite rolls back, the file was not touched). A crash after 2 leaves
pulse.db one revision ahead of the file: ``recover()`` at start (and the first read) rewrites shifts.json from
the journal – only if the journal is intact and the file is exactly the one the save replaced. Every other
mismatch (journal damaged, file changed since, revisions further apart, other generation, pulse.db older than
the file) blocks writing and is reported in the logs and the system status – nothing is corrected silently.

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
# Phase 14.7b: last recovery and last problem of this process, for the system status (two small values).
_LAST: dict = {"recovery": None, "problem": None}


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


def _read_meta(con) -> dict:
    try:
        return dict(con.execute("SELECT key, value FROM shift_history_meta").fetchall())
    except sqlite3.OperationalError:  # table from before the meta table existed / not created yet
        return {}


def _read(path: str | None = None) -> tuple[list, dict]:
    con = _connect(path)
    try:
        if not _table_exists(con):
            raise ShiftHistoryUnavailable("Tabelle shift_history fehlt in pulse.db")
        con.execute("BEGIN")  # rows and meta from one snapshot
        # One parse of the joined rows (C speed, like json.load of the old file) instead of one per row.
        return json.loads("[" + ",".join(_payloads(con)) + "]"), _read_meta(con)
    finally:
        con.close()


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _revision(value) -> int:
    """Revision of the marker (int) or of the meta table (text); missing = 0 (Phase 14.7), unreadable = -1."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _journal(meta: dict, revision: int) -> dict:
    """The journalled shifts.json of `revision`, checked. Raises ShiftHistoryUnavailable if it is damaged."""
    try:
        journal = json.loads(meta["journal"])
        text = journal["payload"]
        if journal.get("revision") != revision or _sha_text(text) != journal.get("payload_sha256"):
            raise ValueError("Prüfsumme oder Revision weicht ab")
        payload = json.loads(text)
        if not is_sqlite(payload) or _revision(payload[MARKER_KEY].get("revision")) != revision:
            raise ValueError("Marker im Journal passt nicht")
        if payload.get("history") != [] or payload[MARKER_KEY].get("generation") != meta.get("generation"):
            raise ValueError("Journal gehört nicht zu diesem Stand der Schichthistorie")
        if not isinstance(journal.get("prev_sha256"), str):
            raise ValueError("Prüfsumme der Vorgängerdatei fehlt")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ShiftHistoryUnavailable(f"Änderungsjournal der Schichthistorie (Revision {revision}) ist beschädigt "
                                      "oder unvollständig – bitte Backup prüfen") from exc
    return {"payload": payload, "prev_sha256": journal["prev_sha256"]}


def _pair_state(marker: dict, meta: dict, path) -> tuple[str, dict | None]:
    """Marker of shifts.json vs. pulse.db: ("consistent", None), or ("pending", journalled file) for a save that
    was committed in pulse.db but did not reach shifts.json. Anything else raises ShiftHistoryUnavailable."""
    if marker.get("generation") and marker.get("generation") != meta.get("generation"):
        # shifts.json and pulse.db belong to different migrations (e.g. rows left from before a way back).
        raise ShiftHistoryUnavailable("shifts.json und pulse.db gehören zu unterschiedlichen Ständen der Schichthistorie")
    file_rev, db_rev = _revision(marker.get("revision")), _revision(meta.get("revision"))
    if file_rev >= 0 and file_rev == db_rev:
        return "consistent", None
    if file_rev >= 0 and db_rev == file_rev + 1:
        journal = _journal(meta, db_rev)
        if path is None or journal["prev_sha256"] != _sha_text(_read_text(path)):
            raise ShiftHistoryUnavailable(f"Schreibvorgang unvollständig (Revision {db_rev}), aber shifts.json passt "
                                          "nicht zum Änderungsjournal – nicht automatisch korrigiert, bitte prüfen")
        return "pending", journal["payload"]
    raise ShiftHistoryUnavailable(f"Revision von shifts.json ({file_rev}) passt nicht zu pulse.db ({db_rev}) – "
                                  "eine der Dateien stammt aus einem anderen Stand")


def _roll_forward(ws, path, payload: dict) -> None:
    """Finish a committed save: shifts.json := the journalled content. Caller holds ws._io_lock."""
    revision = payload[MARKER_KEY]["revision"]
    # The pair was just verified (journal intact, file = the one the save replaced): a block left by an earlier
    # failed attempt (disk full, file held open) is stale – otherwise only a restart could clear it.
    getattr(ws, "_CORRUPT_JSON", {}).pop(os.path.abspath(path), None)
    try:
        ws._write_json_file(path, payload)
        if _read_raw(path) != payload:
            raise RuntimeError("Prüfung nach dem Schreiben: Inhalt weicht ab")
    except Exception as exc:
        raise ShiftHistoryUnavailable(f"Wiederherstellung von shifts.json (Revision {revision}) fehlgeschlagen "
                                      f"({type(exc).__name__})") from exc
    remember_mode(path, payload)
    _LAST.update(recovery={"revision": revision, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                 problem=None)
    print(f"✅ Schichthistorie: unvollständiger Schreibvorgang (Revision {revision}) aus dem Änderungsjournal "
          "von pulse.db wiederhergestellt – shifts.json passt wieder zu pulse.db.")


def read_history(path: str | None = None) -> list:
    return _read(path)[0]


def attach(raw: dict, ws=None, path=None) -> dict:
    """shifts.json in SQLite mode → the old shape: marker removed, history read from pulse.db.

    Phase 14.7b: a save committed in pulse.db that did not reach the file is finished first when `ws` and
    `path` are given (caller holds ws._io_lock); without them it raises – the two halves are never mixed."""
    try:
        history, meta = _read()
    except (sqlite3.Error, ValueError) as exc:
        raise ShiftHistoryUnavailable(f"Schichthistorie in pulse.db nicht lesbar ({type(exc).__name__})") from exc
    state, pending = _pair_state(raw[MARKER_KEY], meta, path)
    if state == "pending":
        if ws is None:
            raise ShiftHistoryUnavailable("Schreibvorgang unvollständig – Wiederherstellung beim Start nötig")
        _roll_forward(ws, path, pending)
        raw = pending
    data = {k: v for k, v in raw.items() if k != MARKER_KEY}
    marker = raw[MARKER_KEY]
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
    """save_json(SHIFTS_FILE, data) in SQLite mode. Caller holds ws._io_lock.

    Phase 14.7b: COMMIT first (rows + revision + journal of the new file), then the file – see the docstring above."""
    previous_text = _read_text(path)
    previous = json.loads(previous_text)
    if not is_sqlite(previous):  # the mode cache was stale: the file is in JSON mode – save it as such
        ws._write_json_file(path, data)
        remember_mode(path, data)
        return
    rest = {k: v for k, v in data.items() if k not in ("history", MARKER_KEY)}
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        meta = _read_meta(con)
        state, pending = _pair_state(previous.get(MARKER_KEY) or {}, meta, path)
        if state == "pending":  # an earlier save of this process did not reach the file: finish it first
            _roll_forward(ws, path, pending)
            previous_text, previous = _read_text(path), pending
        if "history" in data:
            history = data.get("history")
            if not isinstance(history, list):
                raise ValueError("history muss eine Liste sein")
            _sync(con, history)
            rows = len(history)
        else:  # a caller without "history" never erases the stored history
            rows = con.execute("SELECT COUNT(*) FROM shift_history").fetchone()[0]
        revision = _revision(meta.get("revision")) + 1
        payload = {**rest, "history": [], MARKER_KEY: _marker(rows, previous.get(MARKER_KEY)) | {"revision": revision}}
        text = _dumps(payload)
        journal = _dumps({"revision": revision, "prev_sha256": _sha_text(previous_text),
                          "payload": text, "payload_sha256": _sha_text(text)})
        con.executemany("INSERT OR REPLACE INTO shift_history_meta(key, value) VALUES (?, ?)",
                        (("revision", str(revision)), ("journal", journal)))
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    # Committed: from here on pulse.db is the truth and the save has happened – callers must not see a failure
    # (the shift *is* ended). If the file cannot follow (or the process dies now), the next read / recover()
    # writes exactly this payload from the journal; until then the system status shows it.
    try:
        ws._write_json_file(path, payload)
    except Exception as exc:
        _LAST["problem"] = (f"Revision {revision} in pulse.db gespeichert, shifts.json nicht nachgezogen "
                            f"({type(exc).__name__}) – wird beim nächsten Lesen/Start wiederhergestellt")
        print(f"🔴 Schichthistorie: {_LAST['problem']}.")
        return
    remember_mode(path, payload)


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
            # Phase 14.7b: a new generation starts at revision 0; a journal of an older generation is obsolete
            con.execute("INSERT OR REPLACE INTO shift_history_meta(key, value) VALUES ('revision', '0')")
            con.execute("DELETE FROM shift_history_meta WHERE key='journal'")
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
            "generation": generation, "revision": 0,
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
            data = attach(raw, ws, path)  # Phase 14.7b: finishes an interrupted save first, never mixes halves
            raw = _read_raw(path)
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
        info: dict = {"backend": "sqlite" if is_sqlite(raw) else "json", "configured": os.getenv(ENV_VAR, "").strip().lower(),
                      "pending": False}
        if info["backend"] == "sqlite":
            marker = raw[MARKER_KEY]
            info["marker_rows"] = marker.get("rows")
            info["marker_revision"] = _revision(marker.get("revision"))
            try:
                con = _connect()
                try:
                    con.execute("BEGIN")
                    info["db_rows"] = con.execute("SELECT COUNT(*) FROM shift_history").fetchone()[0]
                    meta = _read_meta(con)
                finally:
                    con.close()
                info["db_revision"] = _revision(meta.get("revision"))
                info["pending"] = _pair_state(marker, meta, ws.SHIFTS_FILE)[0] == "pending"  # read only, no repair
            except sqlite3.Error as exc:
                info["error"] = type(exc).__name__
            except ShiftHistoryUnavailable as exc:
                info["error"] = str(exc)
            info["consistent"] = ("error" not in info and not info["pending"]
                                  and info.get("marker_rows") == info.get("db_rows"))
        else:
            info["json_rows"] = len((raw or {}).get("history", []) or [])
            info["consistent"] = True
        info.update(last_recovery=_LAST["recovery"], last_problem=_LAST["problem"])
        return info


def recover(ws) -> dict:
    """Phase 14.7b, at start (webserver.init_runtime) before anything writes: finish a save that a crash
    interrupted. Idempotent (a second run finds nothing to do). Never raises: an unclear state blocks writing
    shifts.json and is reported in the logs and the system status."""
    path = ws.SHIFTS_FILE
    with ws._io_lock:
        try:
            raw = _read_raw(path)
            if raw is None or not is_sqlite(raw):
                return {"status": "json"}
            before = _LAST["recovery"]
            attach(raw, ws, path)
            if _LAST["recovery"] is not before:
                return {"status": "recovered", "revision": _LAST["recovery"]["revision"]}
            return {"status": "consistent"}
        except Exception as exc:
            # own messages hold only counts/revisions; foreign ones (OSError …) may name paths – type only
            error = str(exc) if isinstance(exc, ShiftHistoryUnavailable) else f"Prüfung fehlgeschlagen ({type(exc).__name__})"
            _LAST["problem"] = error
            print(f"🔴 Schichthistorie: {error}. Schreiben von shifts.json bleibt gesperrt.")
            mark = getattr(ws, "_mark_corrupt", None)
            if mark is not None:
                mark(path, ShiftHistoryUnavailable(error))
            return {"status": "blocked", "error": error}


def system_check(ws) -> tuple[str, bool, str]:
    """One row of the system status. JSON mode: the cached mode is enough – no parse of the (large) file."""
    name = "Schichthistorie"
    recovered = _LAST["recovery"]
    note = f" · Revision {recovered['revision']} nach Absturz wiederhergestellt ({recovered['at']})" if recovered else ""
    if not file_is_sqlite(ws.SHIFTS_FILE):
        return name, True, "JSON (shifts.json)" + note
    info = status(ws)
    if info.get("error"):
        return name, False, f"SQLite – {info['error']}"
    if info.get("pending"):
        return name, False, f"SQLite – {info.get('last_problem') or 'Schreibvorgang unvollständig'}"
    return (name, bool(info.get("consistent")),
            f"SQLite · {info.get('db_rows')} Einträge · Revision {info.get('db_revision')}" + note)


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
