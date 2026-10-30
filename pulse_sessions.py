"""U-14: server-side dashboard sessions – list with device and last activity, sign out everywhere,
idle timeout, automatic revocation when someone leaves the team.

The signed cookie stays the carrier; new logins additionally get a session id (`sid`) that must exist
here and must not be revoked or idle. Cookies issued before this update have no `sid`: they stay valid
until their own expiry unless the person signs out other devices or everywhere (both end them through
`revoked_before`, which only applies to cookies without `sid`). Nothing secret is stored – only a
shortened user agent.
"""
from __future__ import annotations

import os
import secrets
import time

IDLE_SECONDS = max(900, int(float(os.getenv("PULSE_SESSION_IDLE_HOURS", "24")) * 3600))
TOUCH_EVERY = 60          # last_seen is written at most once a minute per session
KEEP_DAYS = 30            # revoked/expired rows are cleaned up after this
MAX_PER_USER = 20         # oldest sessions beyond this are revoked on login


def _db():
    import pulse_db
    return pulse_db


def _init(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS dashboard_sessions (sid TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at REAL NOT NULL,"
        " last_seen REAL NOT NULL, user_agent TEXT, revoked_at REAL, revoke_reason TEXT)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_dashboard_sessions_user ON dashboard_sessions(user_id, revoked_at)")


def device_label(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    system = next((name for key, name in (("android", "Android"), ("iphone", "iPhone"), ("ipad", "iPad"), ("windows", "Windows"),
                                         ("mac os", "macOS"), ("linux", "Linux")) if key in ua), "Unbekanntes Gerät")
    browser = next((name for key, name in (("edg/", "Edge"), ("opr/", "Opera"), ("firefox", "Firefox"), ("chrome", "Chrome"),
                                          ("safari", "Safari")) if key in ua), "")
    return f"{browser} · {system}" if browser else system


def create(user_id, user_agent: str = "", now: float | None = None) -> str:
    now = time.time() if now is None else now
    sid = secrets.token_urlsafe(18)
    with _db().connect() as db:
        _init(db)
        db.execute("INSERT INTO dashboard_sessions(sid,user_id,created_at,last_seen,user_agent) VALUES (?,?,?,?,?)",
                   (sid, str(user_id), now, now, (user_agent or "")[:300]))
        extra = db.execute("SELECT sid FROM dashboard_sessions WHERE user_id=? AND revoked_at IS NULL ORDER BY last_seen DESC LIMIT -1 OFFSET ?",
                           (str(user_id), MAX_PER_USER)).fetchall()
        for (old,) in extra:
            db.execute("UPDATE dashboard_sessions SET revoked_at=?, revoke_reason='zu viele Sitzungen' WHERE sid=?", (now, old))
        db.execute("DELETE FROM dashboard_sessions WHERE (revoked_at IS NOT NULL AND revoked_at<?) OR last_seen<?",
                   (now - KEEP_DAYS * 86400, now - KEEP_DAYS * 86400))
    return sid


def is_valid(token: dict, now: float | None = None) -> bool:
    """Checks a verified dashboard token against the server-side state and records activity."""
    now = time.time() if now is None else now
    user_id = str(token.get("id"))
    sid = token.get("sid")
    if not sid:
        # Cookie from before U-14: valid until its own expiry unless the person signed out elsewhere.
        # (Only these cookies use revoked_before – new logins always have their own revocable session.)
        try:
            revoked_before = float(_db().get_setting(f"sessions_revoked_before:{user_id}", "0") or 0)
        except (TypeError, ValueError):
            revoked_before = 0.0
        return not (revoked_before and float(token.get("iat") or 0) <= revoked_before)
    with _db().connect() as db:
        _init(db)
        row = db.execute("SELECT user_id,last_seen,revoked_at FROM dashboard_sessions WHERE sid=?", (str(sid),)).fetchone()
        if not row or str(row[0]) != user_id or row[2] is not None:
            return False
        if now - float(row[1]) > IDLE_SECONDS:
            db.execute("UPDATE dashboard_sessions SET revoked_at=?, revoke_reason='inaktiv' WHERE sid=?", (now, str(sid)))
            return False
        if now - float(row[1]) >= TOUCH_EVERY:
            db.execute("UPDATE dashboard_sessions SET last_seen=? WHERE sid=?", (now, str(sid)))
    return True


def list_for(user_id) -> list[dict]:
    with _db().connect() as db:
        _init(db)
        rows = db.execute("SELECT sid,created_at,last_seen,user_agent FROM dashboard_sessions WHERE user_id=? AND revoked_at IS NULL"
                          " ORDER BY last_seen DESC", (str(user_id),)).fetchall()
    return [{"sid": r[0], "created_at": r[1], "last_seen": r[2], "device": device_label(r[3])} for r in rows]


def revoke(user_id, sid: str, reason: str = "abgemeldet") -> bool:
    with _db().connect() as db:
        _init(db)
        cur = db.execute("UPDATE dashboard_sessions SET revoked_at=?, revoke_reason=? WHERE sid=? AND user_id=? AND revoked_at IS NULL",
                         (time.time(), reason, str(sid), str(user_id)))
        return cur.rowcount > 0


def revoke_all(user_id, reason: str = "überall abgemeldet", keep_sid: str | None = None) -> int:
    """Revokes every session except keep_sid, and every older cookie without sid."""
    now = time.time()
    with _db().connect() as db:
        _init(db)
        cur = db.execute("UPDATE dashboard_sessions SET revoked_at=?, revoke_reason=? WHERE user_id=? AND revoked_at IS NULL AND sid IS NOT ?",
                         (now, reason, str(user_id), keep_sid))
        count = cur.rowcount
    _db().set_setting(f"sessions_revoked_before:{user_id}", str(int(now)))
    return count


def revoke_unbound(user_id) -> None:
    """SEC-03: ends this person's cookies without sid (pre-U-14), e.g. on logout with such a cookie.
    Sessions with their own sid stay untouched – only revoke_all ends those."""
    _db().set_setting(f"sessions_revoked_before:{user_id}", str(int(time.time())))
