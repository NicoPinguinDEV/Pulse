"""Fraktionsverwaltung: factions, categories and warnings of the RP server in pulse.db.

Three additive tables (created on first use, nothing existing is touched):
* faction_categories – e.g. Staatlich / Neutral / Illegal (seeded once when the table is created),
* factions           – name, status (official / unassigned / probation), category, optional leader,
* faction_warnings   – reason, date, who issued it; revoked warnings stay in the history.

Access: only the Discord role FACTION_MANAGER_ROLE_ID or administrators (can_manage). Nothing here
touches Discord – no roles, no messages. Reaching the warning maximum is only shown, never acted on.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

import pulse_db
from pulse_ids import FACTION_MANAGER_ROLE_ID

STATUSES = {"official": "Offiziell", "unassigned": "Nicht vergeben", "probation": "Probezeit"}
DEFAULT_CATEGORIES = (("Staatlich", "#2563eb"), ("Neutral", "#64748b"), ("Illegal", "#dc2626"))
DEFAULT_MAX_WARNINGS = 3
MAX_WARNINGS_LIMIT = 10
NAME_MAX, TEXT_MAX, REASON_MAX = 60, 1000, 500
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def can_manage(perms: dict | None, member) -> bool:
    """Deny by default: administrators or members holding the Fraktionsverwaltung role."""
    if (perms or {}).get("is_admin"):
        return True
    return any(getattr(role, "id", None) == FACTION_MANAGER_ROLE_ID for role in getattr(member, "roles", None) or [])


def _tables(db) -> None:
    fresh = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='faction_categories'").fetchone() is None
    db.execute("CREATE TABLE IF NOT EXISTS faction_categories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "name TEXT NOT NULL UNIQUE COLLATE NOCASE, color TEXT NOT NULL DEFAULT '#64748b', "
               "sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS factions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "name TEXT NOT NULL UNIQUE COLLATE NOCASE, status TEXT NOT NULL DEFAULT 'unassigned', "
               "category_id INTEGER, leader_id TEXT, leader_name TEXT, description TEXT NOT NULL DEFAULT '', "
               "max_warnings INTEGER NOT NULL DEFAULT 3, probation_since TEXT, official_since TEXT, "
               "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS faction_warnings (id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "faction_id INTEGER NOT NULL, reason TEXT NOT NULL, warned_on TEXT NOT NULL, "
               "issued_by_id TEXT, issued_by_name TEXT, created_at TEXT NOT NULL, "
               "revoked_at TEXT, revoked_by_name TEXT, revoke_reason TEXT)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_faction_warnings_faction ON faction_warnings(faction_id, revoked_at)")
    if fresh:
        for order, (name, color) in enumerate(DEFAULT_CATEGORIES):
            db.execute("INSERT OR IGNORE INTO faction_categories(name,color,sort_order,created_at) VALUES (?,?,?,?)",
                       (name, color, order, _now()))


# --------------------------------------------------------------------------- validation
def clean_name(value, label: str = "Name") -> str:
    name = " ".join(str(value or "").split())
    if len(name) < 2 or len(name) > NAME_MAX:
        raise ValueError(f"{label}: Bitte 2 bis {NAME_MAX} Zeichen angeben.")
    return name


def clean_status(value) -> str:
    status = str(value or "").strip()
    if status not in STATUSES:
        raise ValueError("Unbekannter Status – bitte Offiziell, Nicht vergeben oder Probezeit wählen.")
    return status


def clean_max(value) -> int:
    raw = str(value if value is not None else "").strip() or str(DEFAULT_MAX_WARNINGS)
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_WARNINGS_LIMIT:
        raise ValueError(f"Maximale Verwarnungen: Bitte eine Zahl von 1 bis {MAX_WARNINGS_LIMIT}.")
    return int(raw)


def clean_color(value) -> str:
    color = str(value or "").strip() or "#64748b"
    if not _COLOR.fullmatch(color):
        raise ValueError("Farbe: Bitte im Format #RRGGBB angeben.")
    return color.lower()


def clean_date(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return date.today().isoformat()
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise ValueError("Datum: Bitte ein gültiges Datum angeben.") from None
    if parsed > date.today():
        raise ValueError("Datum: Eine Verwarnung kann nicht in der Zukunft liegen.")
    return parsed.isoformat()


# --------------------------------------------------------------------------- categories
def list_categories() -> list[dict]:
    with pulse_db.connect() as db:
        _tables(db)
        rows = db.execute("SELECT c.*, (SELECT COUNT(*) FROM factions f WHERE f.category_id=c.id) AS faction_count "
                          "FROM faction_categories c ORDER BY c.sort_order, c.name COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def get_category(category_id: int) -> dict | None:
    with pulse_db.connect() as db:
        _tables(db)
        row = db.execute("SELECT * FROM faction_categories WHERE id=?", (int(category_id),)).fetchone()
    return dict(row) if row else None


def create_category(name, color=None) -> dict:
    name, color = clean_name(name, "Kategorie"), clean_color(color)
    with pulse_db.connect() as db:
        _tables(db)
        if db.execute("SELECT 1 FROM faction_categories WHERE name=?", (name,)).fetchone():
            raise ValueError(f"Die Kategorie „{name}“ gibt es bereits.")
        order = db.execute("SELECT COALESCE(MAX(sort_order),-1)+1 FROM faction_categories").fetchone()[0]
        cur = db.execute("INSERT INTO faction_categories(name,color,sort_order,created_at) VALUES (?,?,?,?)",
                         (name, color, order, _now()))
        new_id = cur.lastrowid
    return get_category(new_id)


def update_category(category_id: int, name, color=None) -> tuple[dict, dict]:
    before = get_category(category_id)
    if not before:
        raise LookupError("Diese Kategorie gibt es nicht (mehr).")
    name, color = clean_name(name, "Kategorie"), clean_color(color)
    with pulse_db.connect() as db:
        _tables(db)
        if db.execute("SELECT 1 FROM faction_categories WHERE name=? AND id<>?", (name, int(category_id))).fetchone():
            raise ValueError(f"Die Kategorie „{name}“ gibt es bereits.")
        db.execute("UPDATE faction_categories SET name=?, color=? WHERE id=?", (name, color, int(category_id)))
    return before, get_category(category_id)


def delete_category(category_id: int) -> tuple[dict, int]:
    """Removes the category; its factions stay and become uncategorised. Returns (category, moved)."""
    before = get_category(category_id)
    if not before:
        raise LookupError("Diese Kategorie gibt es nicht (mehr).")
    with pulse_db.connect() as db:
        _tables(db)
        moved = db.execute("UPDATE factions SET category_id=NULL, updated_at=? WHERE category_id=?",
                           (_now(), int(category_id))).rowcount
        db.execute("DELETE FROM faction_categories WHERE id=?", (int(category_id),))
    return before, moved


# --------------------------------------------------------------------------- factions
_FACTION_SELECT = ("SELECT f.*, c.name AS category_name, c.color AS category_color, "
                   "(SELECT COUNT(*) FROM faction_warnings w WHERE w.faction_id=f.id AND w.revoked_at IS NULL) AS warnings "
                   "FROM factions f LEFT JOIN faction_categories c ON c.id=f.category_id")


def _faction(row) -> dict:
    item = dict(row)
    item["status_label"] = STATUSES.get(item["status"], item["status"])
    item["at_max"] = item["warnings"] >= item["max_warnings"]
    return item


def list_factions(status: str = "", category_id: int | None = None, q: str = "") -> list[dict]:
    where, args = [], []
    if status in STATUSES:
        where.append("f.status=?")
        args.append(status)
    if category_id == 0:
        where.append("f.category_id IS NULL")
    elif category_id:
        where.append("f.category_id=?")
        args.append(int(category_id))
    if q:
        where.append("(f.name LIKE ? ESCAPE '\\' OR COALESCE(f.leader_name,'') LIKE ? ESCAPE '\\')")
        pattern = "%" + re.sub(r"([%_\\])", r"\\\1", q.strip()[:60]) + "%"
        args += [pattern, pattern]
    sql = _FACTION_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY f.name COLLATE NOCASE"
    with pulse_db.connect() as db:
        _tables(db)
        rows = db.execute(sql, args).fetchall()
    return [_faction(r) for r in rows]


def get_faction(faction_id: int) -> dict | None:
    with pulse_db.connect() as db:
        _tables(db)
        row = db.execute(_FACTION_SELECT + " WHERE f.id=?", (int(faction_id),)).fetchone()
    return _faction(row) if row else None


def _category_ok(db, category_id) -> int | None:
    if category_id in (None, "", 0, "0"):
        return None
    if not str(category_id).isdigit() or not db.execute("SELECT 1 FROM faction_categories WHERE id=?", (int(category_id),)).fetchone():
        raise ValueError("Unbekannte Kategorie – bitte die Seite neu laden.")
    return int(category_id)


def _fields(data: dict) -> dict:
    description = str(data.get("description") or "").strip()
    if len(description) > TEXT_MAX:
        raise ValueError(f"Beschreibung: Höchstens {TEXT_MAX} Zeichen.")
    leader_id = str(data.get("leader_id") or "").strip() or None
    leader_name = (" ".join(str(data.get("leader_name") or "").split())[:100] or None) if leader_id else None
    return {"name": clean_name(data.get("name")), "status": clean_status(data.get("status")),
            "max_warnings": clean_max(data.get("max_warnings")), "description": description,
            "leader_id": leader_id, "leader_name": leader_name}


def create_faction(data: dict) -> dict:
    fields = _fields(data)
    now = _now()
    with pulse_db.connect() as db:
        _tables(db)
        if db.execute("SELECT 1 FROM factions WHERE name=?", (fields["name"],)).fetchone():
            raise ValueError(f"Die Fraktion „{fields['name']}“ gibt es bereits.")
        cur = db.execute(
            "INSERT INTO factions(name,status,category_id,leader_id,leader_name,description,max_warnings,"
            "probation_since,official_since,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (fields["name"], fields["status"], _category_ok(db, data.get("category_id")), fields["leader_id"],
             fields["leader_name"], fields["description"], fields["max_warnings"],
             now if fields["status"] == "probation" else None, now if fields["status"] == "official" else None, now, now))
        new_id = cur.lastrowid
    return get_faction(new_id)


def update_faction(faction_id: int, data: dict) -> tuple[dict, dict]:
    before = get_faction(faction_id)
    if not before:
        raise LookupError("Diese Fraktion gibt es nicht (mehr).")
    fields = _fields(data)
    now = _now()
    probation_since = before["probation_since"] if fields["status"] == "probation" else None
    if fields["status"] == "probation" and before["status"] != "probation":
        probation_since = now
    official_since = before["official_since"] if fields["status"] == "official" else None
    if fields["status"] == "official" and before["status"] != "official":
        official_since = now
    with pulse_db.connect() as db:
        _tables(db)
        if db.execute("SELECT 1 FROM factions WHERE name=? AND id<>?", (fields["name"], int(faction_id))).fetchone():
            raise ValueError(f"Die Fraktion „{fields['name']}“ gibt es bereits.")
        db.execute("UPDATE factions SET name=?, status=?, category_id=?, leader_id=?, leader_name=?, description=?, "
                   "max_warnings=?, probation_since=?, official_since=?, updated_at=? WHERE id=?",
                   (fields["name"], fields["status"], _category_ok(db, data.get("category_id")), fields["leader_id"],
                    fields["leader_name"], fields["description"], fields["max_warnings"], probation_since,
                    official_since, now, int(faction_id)))
    return before, get_faction(faction_id)


def make_official(faction_id: int) -> dict:
    item = get_faction(faction_id)
    if not item:
        raise LookupError("Diese Fraktion gibt es nicht (mehr).")
    if item["status"] != "probation":
        raise ValueError("Nur Fraktionen in der Probezeit können offiziell gemacht werden.")
    now = _now()
    with pulse_db.connect() as db:
        _tables(db)
        db.execute("UPDATE factions SET status='official', official_since=?, updated_at=? WHERE id=? AND status='probation'",
                   (now, now, int(faction_id)))
    return get_faction(faction_id)


def delete_faction(faction_id: int) -> tuple[dict, int]:
    """Deletes the faction and its warning history. Returns (faction, number of warnings removed)."""
    item = get_faction(faction_id)
    if not item:
        raise LookupError("Diese Fraktion gibt es nicht (mehr).")
    with pulse_db.connect() as db:
        _tables(db)
        removed = db.execute("DELETE FROM faction_warnings WHERE faction_id=?", (int(faction_id),)).rowcount
        db.execute("DELETE FROM factions WHERE id=?", (int(faction_id),))
    return item, removed


# --------------------------------------------------------------------------- warnings
def warnings_for(faction_id: int) -> list[dict]:
    with pulse_db.connect() as db:
        _tables(db)
        rows = db.execute("SELECT * FROM faction_warnings WHERE faction_id=? ORDER BY warned_on DESC, id DESC",
                          (int(faction_id),)).fetchall()
    return [dict(r) for r in rows]


def add_warning(faction_id: int, reason, warned_on, actor_id, actor_name: str) -> tuple[dict, dict]:
    item = get_faction(faction_id)
    if not item:
        raise LookupError("Diese Fraktion gibt es nicht (mehr).")
    reason = str(reason or "").strip()
    if len(reason) < 3 or len(reason) > REASON_MAX:
        raise ValueError(f"Grund: Bitte 3 bis {REASON_MAX} Zeichen angeben.")
    if item["at_max"]:
        raise ValueError(f"{item['name']} hat bereits {item['warnings']}/{item['max_warnings']} Verwarnungen. "
                         "Erst eine Verwarnung zurückziehen oder das Maximum anpassen.")
    with pulse_db.connect() as db:
        _tables(db)
        cur = db.execute("INSERT INTO faction_warnings(faction_id,reason,warned_on,issued_by_id,issued_by_name,created_at) "
                         "VALUES (?,?,?,?,?,?)", (int(faction_id), reason, clean_date(warned_on), str(actor_id), actor_name, _now()))
        db.execute("UPDATE factions SET updated_at=? WHERE id=?", (_now(), int(faction_id)))
        warning_id = cur.lastrowid
    warning = next(w for w in warnings_for(faction_id) if w["id"] == warning_id)
    return warning, get_faction(faction_id)


def revoke_warning(faction_id: int, warning_id: int, actor_name: str, reason) -> tuple[dict, dict]:
    reason = str(reason or "").strip()
    if len(reason) < 3 or len(reason) > REASON_MAX:
        raise ValueError(f"Grund für das Zurückziehen: Bitte 3 bis {REASON_MAX} Zeichen angeben.")
    with pulse_db.connect() as db:
        _tables(db)
        changed = db.execute("UPDATE faction_warnings SET revoked_at=?, revoked_by_name=?, revoke_reason=? "
                             "WHERE id=? AND faction_id=? AND revoked_at IS NULL",
                             (_now(), actor_name, reason, int(warning_id), int(faction_id))).rowcount
    if not changed:
        raise LookupError("Diese Verwarnung gibt es nicht oder sie wurde bereits zurückgezogen.")
    warning = next(w for w in warnings_for(faction_id) if w["id"] == int(warning_id))
    return warning, get_faction(faction_id)


# --------------------------------------------------------------------------- overview
def summary() -> dict:
    with pulse_db.connect() as db:
        _tables(db)
        counts = {row["status"]: row["n"] for row in db.execute("SELECT status, COUNT(*) AS n FROM factions GROUP BY status")}
        categories = db.execute("SELECT COUNT(*) FROM faction_categories").fetchone()[0]
        warnings = db.execute("SELECT COUNT(*) FROM faction_warnings w JOIN factions f ON f.id=w.faction_id "
                              "WHERE w.revoked_at IS NULL").fetchone()[0]
    return {"official": counts.get("official", 0), "unassigned": counts.get("unassigned", 0),
            "probation": counts.get("probation", 0), "total": sum(counts.values()),
            "categories": categories, "warnings": warnings}
