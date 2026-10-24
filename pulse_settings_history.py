"""F-03: settings history – who changed which setting when, with a diff and "zurücksetzen".

Recorded are changes made from the dashboard (the request's signed-in person is known via `set_actor`,
called by `webserver.auth`): every `pulse_db.set_setting` and every top-level key of `config.json` that
`webserver.save_json` changes. Internal bot writes (no person) are not recorded. Only while the function
is switched on (pulse_flags "settings_history"). Bounded to MAX_ROWS entries; secrets are never stored.
"""
from __future__ import annotations

import contextvars
import json
import re

MAX_ROWS = 1000
MAX_VALUE_CHARS = 20000
_SECRET = re.compile(r"secret|token|password|passwort|api[_-]?key|webhook", re.I)
# High-frequency internal keys that change as a side effect, not as a decision.
IGNORED_KEYS = frozenset({"command_tree_hash", "duty_panel_state"})

_actor: contextvars.ContextVar = contextvars.ContextVar("pulse_settings_actor", default=None)


def set_actor(user_id, name: str) -> None:
    _actor.set({"id": str(user_id or ""), "name": str(name or "")[:80]} if user_id else None)


def clear_actor() -> None:
    _actor.set(None)


def wanted() -> bool:
    """Cheap pre-check so callers only read the previous value when something will be recorded."""
    if _actor.get() is None:
        return False
    try:
        import pulse_flags
        return pulse_flags.enabled("settings_history")
    except Exception:
        return False


def _ensure(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS settings_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL,
        source TEXT NOT NULL, key TEXT NOT NULL, before_json TEXT, after_json TEXT, revertible INTEGER NOT NULL DEFAULT 1)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_settings_history_at ON settings_history(at DESC)")


def _dump(value) -> tuple[str | None, bool]:
    if value is None:
        return None, True
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(text) > MAX_VALUE_CHARS:
        return json.dumps(f"(zu groß für die Historie: {len(text)} Zeichen)"), False
    return text, True


def _redact(key: str, value):
    if _SECRET.search(str(key)):
        return "***"
    if isinstance(value, dict):
        return {k: _redact(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(key, v) if isinstance(v, (dict, list)) else v for v in value]
    return value


def record(source: str, key: str, before, after) -> bool:
    """Store one change if the function is on and a person made it. Never raises."""
    try:
        import pulse_flags
        actor = _actor.get()
        if not actor or not pulse_flags.enabled("settings_history") or key in IGNORED_KEYS or before == after:
            return False
        secret = bool(_SECRET.search(str(key)))
        before_red, after_red = _redact(key, before), _redact(key, after)
        before_text, ok_b = _dump(before_red)
        after_text, ok_a = _dump(after_red)
        revertible = int(ok_b and ok_a and not secret and before_red == before)
        import pulse_db
        from datetime import datetime, timezone
        with pulse_db.connect() as db:
            _ensure(db)
            db.execute("INSERT INTO settings_history(at,actor_id,actor_name,source,key,before_json,after_json,revertible) VALUES (?,?,?,?,?,?,?,?)",
                       (datetime.now(timezone.utc).isoformat(timespec="seconds"), actor["id"], actor["name"], source, str(key)[:120],
                        before_text, after_text, revertible))
            db.execute("DELETE FROM settings_history WHERE id NOT IN (SELECT id FROM settings_history ORDER BY id DESC LIMIT ?)", (MAX_ROWS,))
        return True
    except Exception as exc:  # history must never break saving a setting
        print(f"⚠️ Einstellungs-Historie: Eintrag nicht gespeichert ({type(exc).__name__}).")
        return False


def record_config(before: dict, after: dict) -> int:
    """One entry per changed top-level key of config.json."""
    if not isinstance(before, dict) or not isinstance(after, dict) or _actor.get() is None:
        return 0
    return sum(record("config", key, before.get(key), after.get(key)) for key in sorted(set(before) | set(after))
               if before.get(key) != after.get(key))


def entries(limit: int = 200, key: str = "") -> list[dict]:
    import pulse_db
    with pulse_db.connect() as db:
        _ensure(db)
        sql, args = "SELECT * FROM settings_history", []
        if key:
            sql, args = sql + " WHERE key=?", [key]
        rows = db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()
    return [dict(r) for r in rows]


def get(entry_id) -> dict | None:
    import pulse_db
    with pulse_db.connect() as db:
        _ensure(db)
        row = db.execute("SELECT * FROM settings_history WHERE id=?", (int(entry_id),)).fetchone()
    return dict(row) if row else None


def diff_lines(before_json: str | None, after_json: str | None) -> list[tuple[str, str]]:
    """[("-"|"+"|" ", line)] of the pretty-printed values – small and dependency-free."""
    import difflib

    def pretty(text):
        if text is None:
            return ["(nicht gesetzt)"]
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        except ValueError:
            return [str(text)]
    out = []
    for line in difflib.unified_diff(pretty(before_json), pretty(after_json), lineterm="", n=2):
        if line.startswith(("---", "+++", "@@")):
            continue
        out.append((line[:1], line[1:]))
    return out[:400]


def revert(entry_id, *, load_config, save_config) -> dict:
    """Restore the value before this change. Raises ValueError with a readable reason."""
    import pulse_db
    entry = get(entry_id)
    if entry is None:
        raise ValueError("Eintrag nicht gefunden.")
    if not entry["revertible"]:
        raise ValueError("Dieser Eintrag lässt sich nicht automatisch zurücksetzen (Geheimnis oder zu groß).")
    before = json.loads(entry["before_json"]) if entry["before_json"] is not None else None
    if entry["source"] == "setting":
        if before is None:
            pulse_db.delete_setting(entry["key"])
        else:
            pulse_db.set_setting(entry["key"], before)
    elif entry["source"] == "config":
        config = load_config()
        if before is None:
            config.pop(entry["key"], None)
        else:
            config[entry["key"]] = before
        save_config(config)
    else:
        raise ValueError("Unbekannte Quelle.")
    if entry["key"] in ("phase7_features", "maintenance_mode"):
        import pulse_flags
        pulse_flags.invalidate()
    return entry
