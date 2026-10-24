"""Privacy-aware audit trail for successful Pulse dashboard logins.

Only authentication events are recorded (not every page/API request). Raw IPs are retained
for 30 days, with a hard row cap. The admin UI must remain admin-only and audit access to it.
"""
from __future__ import annotations

import ipaddress
import time

KEEP_DAYS = 30
MAX_ROWS = 10_000


def _db():
    import pulse_db
    return pulse_db


def _init(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS dashboard_login_ip_audit ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, username TEXT NOT NULL, "
        "ip_address TEXT NOT NULL, user_agent TEXT NOT NULL, logged_at REAL NOT NULL)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dashboard_login_ip_user_time "
        "ON dashboard_login_ip_audit(user_id, logged_at DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_dashboard_login_ip_time "
        "ON dashboard_login_ip_audit(logged_at DESC)"
    )


def normalize_ip(value: str | None) -> str:
    """Accept only a literal IP address; never trust arbitrary forwarded-header text."""
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        return ""


def record_login(user_id, username: str, ip_address: str | None, user_agent: str = "", now: float | None = None) -> bool:
    """Record a successful dashboard login. Returns False for unparseable IPs."""
    ip = normalize_ip(ip_address)
    uid = str(user_id or "").strip()
    if not ip or not uid.isdigit():
        return False
    now = time.time() if now is None else float(now)
    with _db().connect() as db:
        _init(db)
        db.execute(
            "INSERT INTO dashboard_login_ip_audit(user_id, username, ip_address, user_agent, logged_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid[:22], str(username or uid)[:100], ip, str(user_agent or "")[:250], now),
        )
        db.execute("DELETE FROM dashboard_login_ip_audit WHERE logged_at < ?", (now - KEEP_DAYS * 86400,))
        db.execute(
            "DELETE FROM dashboard_login_ip_audit WHERE id NOT IN "
            "(SELECT id FROM dashboard_login_ip_audit ORDER BY logged_at DESC, id DESC LIMIT ?)",
            (MAX_ROWS,),
        )
    return True


def list_entries(limit: int = 100, *, now: float | None = None) -> list[dict]:
    """Newest login events first. Callers are responsible for admin authorization."""
    limit = max(1, min(200, int(limit)))
    now = time.time() if now is None else float(now)
    with _db().connect() as db:
        _init(db)
        db.execute("DELETE FROM dashboard_login_ip_audit WHERE logged_at < ?", (now - KEEP_DAYS * 86400,))
        rows = db.execute(
            "SELECT user_id, username, ip_address, user_agent, logged_at "
            "FROM dashboard_login_ip_audit ORDER BY logged_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"user_id": row[0], "username": row[1], "ip_address": row[2],
         "user_agent": row[3], "logged_at": float(row[4])}
        for row in rows
    ]


def delete_user(user_id) -> int:
    """Delete this user's stored login IP history (for a privacy/deletion request)."""
    with _db().connect() as db:
        _init(db)
        cur = db.execute("DELETE FROM dashboard_login_ip_audit WHERE user_id = ?", (str(user_id),))
        return int(cur.rowcount)
