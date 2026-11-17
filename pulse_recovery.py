"""Opt-in Discord backup-server recovery.

A user must explicitly authorize the Discord OAuth2 scopes identify + guilds.join.
Refresh tokens are encrypted at rest with PyNaCl and a dedicated, versioned key (PULSE_RECOVERY_KEY, AUTH-2).
The recovery flow never copies roles from the primary guild: newly added users receive
only the configured, permission-reviewed citizen role.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import time
from typing import Any

import httpx
from nacl.exceptions import CryptoError
from nacl.secret import SecretBox

SETTING_KEY = "recovery_server_config"
TOKEN_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
MAX_BATCH_SIZE = 100


async def _request_with_429_retry(request_factory: Any):
    """Retry Discord 429 responses using Discord's supplied retry_after value."""
    response = None
    for attempt in range(5):
        response = await request_factory()
        if response.status_code != 429 or attempt == 4:
            return response
        retry_after = response.headers.get("Retry-After", "1")
        try:
            body = response.json()
            retry_after = body.get("retry_after", retry_after) if isinstance(body, dict) else retry_after
        except (ValueError, TypeError):
            pass
        try:
            delay = float(retry_after)
        except (TypeError, ValueError):
            delay = 1.0
        await asyncio.sleep(max(delay, 0.5))
    return response
DANGEROUS_ROLE_PERMISSIONS = (
    "administrator", "manage_guild", "manage_roles", "manage_channels",
    "kick_members", "ban_members", "manage_webhooks", "manage_messages",
    "moderate_members", "manage_nicknames", "manage_threads", "manage_events",
)

DEFAULT_SETTINGS = {
    "enabled": False,
    "backup_guild_id": "",
    "citizen_role_id": "",
    "log_channel_id": "",
}


def _db():
    import pulse_db
    return pulse_db


def _snowflake(value: Any, *, optional: bool = False) -> str:
    text = str(value or "").strip()
    if optional and not text:
        return ""
    return text if text.isdecimal() and 15 <= len(text) <= 22 and int(text) > 0 else ""


def normalize_settings(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "enabled": raw.get("enabled") is True or str(raw.get("enabled", "")).lower() in {"1", "true", "on", "yes"},
        "backup_guild_id": _snowflake(raw.get("backup_guild_id")),
        "citizen_role_id": _snowflake(raw.get("citizen_role_id")),
        "log_channel_id": _snowflake(raw.get("log_channel_id"), optional=True),
    }


def load_settings() -> dict:
    return normalize_settings(_db().get_setting(SETTING_KEY, {}))


def save_settings(raw: dict) -> dict:
    candidate = normalize_settings(raw)
    if raw.get("enabled") and (not candidate["backup_guild_id"] or not candidate["citizen_role_id"]):
        raise ValueError("Für den Backup-Server müssen gültige Server- und Bürgerrollen-IDs gesetzt sein.")
    _db().set_setting(SETTING_KEY, candidate)
    return candidate


def _init(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS recovery_oauth_tokens ("
        "user_id TEXT PRIMARY KEY, refresh_token_enc TEXT NOT NULL, consented_at REAL NOT NULL, "
        "last_sync_at REAL, last_status TEXT NOT NULL DEFAULT 'freigeschaltet')"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_recovery_oauth_consent ON recovery_oauth_tokens(consented_at DESC)")
    # A ban tombstone survives token deletion and blocks stale in-flight sync snapshots.
    db.execute(
        "CREATE TABLE IF NOT EXISTS recovery_primary_ban_blocks ("
        "user_id TEXT PRIMARY KEY, blocked_at REAL NOT NULL, "
        "reason TEXT NOT NULL DEFAULT 'primary_ban')"
    )


class RecoveryKeyError(RuntimeError):
    """The dedicated recovery encryption key is missing or too weak (fail closed – nothing is stored)."""


class RecoveryBlockedError(ValueError):
    """A primary-guild ban blocks previously consented backup recovery."""


# ---------------------------------------------------------------------------------------------------------
# AUTH-2: dedicated, versioned encryption key (independent of the session signing secret)
#
# Stored format (v2):  "v2:<key id>:<urlsafe-base64 SecretBox>"   key id = first 8 hex chars of SHA-256(derived key)
# Legacy format (v1):  "<urlsafe-base64 SecretBox>" encrypted with a key derived from the session secret. It stays
# readable so existing consents keep working, but nothing new is written in it; migrate_to_current_key() moves
# rows to the dedicated key as an explicit, separate step (it never runs automatically).
#
# Keys: PULSE_RECOVERY_KEY (current, required to store anything) and optionally PULSE_RECOVERY_KEY_PREVIOUS
# (read-only, used during a key rotation). Any secret of at least 32 characters is accepted; the 32-byte key is
# derived with HKDF-SHA256 so the raw value is never used directly.
# ---------------------------------------------------------------------------------------------------------
_V2_PREFIX = "v2:"
_HKDF_SALT = b"pulse-recovery-oauth-salt-v2"
_HKDF_INFO = b"pulse recovery oauth refresh-token encryption v2"
_MIN_KEY_CHARS = 32


def _derive_key(raw: str) -> bytes:
    secret = str(raw or "").strip()
    if len(secret) < _MIN_KEY_CHARS or len(set(secret)) < 10:
        raise RecoveryKeyError("Recovery-Schlüssel ist zu kurz oder zu einfach (mindestens 32 Zeichen, zufällig).")
    prk = hmac.new(_HKDF_SALT, secret.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(prk, _HKDF_INFO + b"\x01", hashlib.sha256).digest()


def _key_id(key: bytes) -> str:
    return hashlib.sha256(b"key-id:" + key).hexdigest()[:8]


def _configured_keys() -> tuple[tuple[str, SecretBox] | None, dict[str, SecretBox]]:
    """(current key, all readable keys by id). Raises RecoveryKeyError for a configured but unusable key."""
    current = None
    readable: dict[str, SecretBox] = {}
    for env_name, is_current in (("PULSE_RECOVERY_KEY", True), ("PULSE_RECOVERY_KEY_PREVIOUS", False)):
        raw = os.getenv(env_name, "").strip()
        if not raw:
            continue
        key = _derive_key(raw)
        box = SecretBox(key)
        readable[_key_id(key)] = box
        if is_current:
            current = (_key_id(key), box)
    return current, readable


def _legacy_box() -> SecretBox:
    """v1: key derived from Pulse's persistent session secret (read-only legacy support)."""
    import webserver
    secret = webserver._session_key()
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise RuntimeError("Pulse-Session-Geheimnis ist nicht sicher genug.")
    return SecretBox(hashlib.sha256(b"Pulse recovery OAuth token encryption v1\x00" + secret).digest())


def _encrypt(token: str) -> str:
    current, _readable = _configured_keys()
    if current is None:
        raise RecoveryKeyError("PULSE_RECOVERY_KEY ist nicht gesetzt – Recovery-Freigaben können nicht gespeichert werden.")
    key_id, box = current
    payload = base64.urlsafe_b64encode(bytes(box.encrypt(token.encode("utf-8")))).decode("ascii")
    return f"{_V2_PREFIX}{key_id}:{payload}"


def token_state(stored: str) -> str:
    """'current' | 'old-key' | 'legacy' | 'unreadable' – never raises, never returns the secret."""
    stored = str(stored or "")
    try:
        current, readable = _configured_keys()
        if stored.startswith(_V2_PREFIX):
            _prefix, key_id, payload = stored.split(":", 2)
            box = readable.get(key_id)
            if box is None:
                return "unreadable"
            box.decrypt(base64.urlsafe_b64decode(payload.encode("ascii")))
            return "current" if current and current[0] == key_id else "old-key"
        _legacy_box().decrypt(base64.urlsafe_b64decode(stored.encode("ascii")))
        return "legacy"
    except (ValueError, UnicodeError, CryptoError, RuntimeError):
        return "unreadable"


def _decrypt(token: str) -> str | None:
    token = str(token or "")
    try:
        if token.startswith(_V2_PREFIX):
            _prefix, key_id, payload = token.split(":", 2)
            _current, readable = _configured_keys()
            box = readable.get(key_id)
            if box is None:
                return None
            return box.decrypt(base64.urlsafe_b64decode(payload.encode("ascii"))).decode("utf-8")
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        return _legacy_box().decrypt(raw).decode("utf-8")
    except (ValueError, UnicodeError, CryptoError, RuntimeError):
        return None


def key_configured() -> bool:
    """True when a usable PULSE_RECOVERY_KEY exists (a configured but weak key counts as not configured)."""
    try:
        return _configured_keys()[0] is not None
    except RecoveryKeyError:
        return False


def encryption_status() -> dict:
    """Counts per storage state for the admin/status view – makes unreadable consents visible instead of silent."""
    counts = {"current": 0, "old-key": 0, "legacy": 0, "unreadable": 0}
    with _db().connect() as db:
        _init(db)
        rows = db.execute("SELECT refresh_token_enc FROM recovery_oauth_tokens").fetchall()
    for (stored,) in rows:
        counts[token_state(str(stored))] += 1
    try:
        configured = _configured_keys()[0] is not None
    except RecoveryKeyError:
        configured = False
    return {"key_configured": configured, "total": len(rows), **counts}


def migrate_to_current_key(*, dry_run: bool = True) -> dict:
    """Explicit, separate migration step: re-encrypt legacy (v1) and old-key rows with the current key.

    Never runs automatically. ``dry_run=True`` (default) only reports what would change. Every row is first
    decrypted and the new ciphertext is verified by decrypting it again before the row is updated, rows that
    cannot be read are left untouched, and the whole step needs PULSE_RECOVERY_KEY. Take a database backup first."""
    current, _readable = _configured_keys()
    if current is None:
        raise RecoveryKeyError("PULSE_RECOVERY_KEY ist nicht gesetzt – Migration nicht möglich.")
    result = {"dry_run": bool(dry_run), "migrated": 0, "would_migrate": 0, "already_current": 0, "unreadable": 0}
    with _db().connect() as db:
        _init(db)
        rows = db.execute("SELECT user_id, refresh_token_enc FROM recovery_oauth_tokens").fetchall()
        for user_id, stored in rows:
            state = token_state(str(stored))
            if state == "current":
                result["already_current"] += 1
            elif state == "unreadable":
                result["unreadable"] += 1
            elif dry_run:
                result["would_migrate"] += 1
            else:
                plain = _decrypt(str(stored))
                fresh = _encrypt(plain) if plain else None
                if not fresh or _decrypt(fresh) != plain:
                    result["unreadable"] += 1
                    continue
                db.execute("UPDATE recovery_oauth_tokens SET refresh_token_enc=? WHERE user_id=? AND refresh_token_enc=?",
                           (fresh, user_id, stored))
                result["migrated"] += 1
    return result


def store_refresh_token(
    user_id,
    refresh_token: str,
    *,
    now: float | None = None,
    consent_started_at: float | None = None,
) -> None:
    """Store new user-consented OAuth tokens; the started-at marker fences stale callbacks."""
    uid = _snowflake(user_id)
    if not uid or not isinstance(refresh_token, str) or not refresh_token or len(refresh_token) > 4096:
        raise ValueError("Ungültige Recovery-Anmeldedaten.")
    encrypted = _encrypt(refresh_token)
    now = time.time() if now is None else float(now)
    with _db().connect() as db:
        _init(db)
        blocked = db.execute(
            "SELECT blocked_at FROM recovery_primary_ban_blocks WHERE user_id=?", (uid,)
        ).fetchone()
        if blocked:
            if consent_started_at is None or float(blocked[0]) > float(consent_started_at):
                raise RecoveryBlockedError(
                    "Die Recovery-Einwilligung wurde widerrufen oder gesperrt. Eine danach gestartete ausdrückliche "
                    "OAuth-Zustimmung ist erforderlich."
                )
        if consent_started_at is not None:
            previous = db.execute(
                "SELECT consented_at FROM recovery_oauth_tokens WHERE user_id=?", (uid,)
            ).fetchone()
            if previous is not None and float(previous[0]) >= float(consent_started_at):
                raise RecoveryBlockedError("Eine neuere Recovery-Einwilligung wurde bereits abgeschlossen.")
        if blocked:
            db.execute("DELETE FROM recovery_primary_ban_blocks WHERE user_id=?", (uid,))
        db.execute(
            "INSERT INTO recovery_oauth_tokens(user_id, refresh_token_enc, consented_at, last_sync_at, last_status) "
            "VALUES (?, ?, ?, NULL, 'freigeschaltet') "
            "ON CONFLICT(user_id) DO UPDATE SET refresh_token_enc=excluded.refresh_token_enc, "
            "consented_at=excluded.consented_at, last_sync_at=NULL, last_status='freigeschaltet'",
            (uid, encrypted, now),
        )




async def revoke_refresh_token(refresh_token: str, client: httpx.AsyncClient | None = None) -> bool:
    """Best-effort Discord OAuth revocation after a persistent local consent block is recorded."""
    import webserver as ws

    if not refresh_token or not ws.CLIENT_ID or not ws.CLIENT_SECRET:
        return False

    async def send_revoke(active_client):
        return await _request_with_429_retry(lambda: active_client.post(
            "https://discord.com/api/v10/oauth2/token/revoke",
            data={
                "client_id": ws.CLIENT_ID,
                "client_secret": ws.CLIENT_SECRET,
                "token": refresh_token,
                "token_type_hint": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ))

    try:
        if client is not None:
            response = await send_revoke(client)
        else:
            async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT, follow_redirects=False) as active_client:
                response = await send_revoke(active_client)
        return response.status_code in (200, 204)
    except httpx.HTTPError:
        return False



def rotate_refresh_token(user_id, refresh_token: str, *, expected_consented_at: float) -> None:
    """Rotate a refresh token only if the exact consent snapshot is still current."""
    uid = _snowflake(user_id)
    if not uid or not isinstance(refresh_token, str) or not refresh_token or len(refresh_token) > 4096:
        raise ValueError("Ungültige Recovery-Anmeldedaten.")
    expected = float(expected_consented_at)
    encrypted = _encrypt(refresh_token)
    with _db().connect() as db:
        _init(db)
        blocked = db.execute(
            "SELECT 1 FROM recovery_primary_ban_blocks WHERE user_id=?", (uid,)
        ).fetchone()
        if blocked:
            raise RecoveryBlockedError("Recovery-Einwilligung wurde während des Syncs widerrufen.")
        cur = db.execute(
            "UPDATE recovery_oauth_tokens SET refresh_token_enc=?, last_sync_at=NULL, "
            "last_status='freigeschaltet' WHERE user_id=? AND consented_at=?",
            (encrypted, uid, expected),
        )
        if cur.rowcount != 1:
            raise RecoveryBlockedError("Einwilligung wurde seit dem Sync-Snapshot erneuert oder widerrufen.")


def unlink_if_current(user_id, *, expected_consented_at: float, now: float | None = None) -> bool:
    """Expire a token only when it still belongs to the sync snapshot that received invalid_grant."""
    uid = _snowflake(user_id)
    if not uid:
        return False
    blocked_at = time.time() if now is None else float(now)
    expected = float(expected_consented_at)
    with _db().connect() as db:
        _init(db)
        row = db.execute(
            "SELECT consented_at FROM recovery_oauth_tokens WHERE user_id=?", (uid,)
        ).fetchone()
        if row is None or float(row[0]) != expected:
            return False
        db.execute(
            "INSERT INTO recovery_primary_ban_blocks(user_id, blocked_at, reason) "
            "VALUES (?, ?, 'invalid_grant') ON CONFLICT(user_id) DO UPDATE SET "
            "blocked_at=MAX(blocked_at, excluded.blocked_at), reason='invalid_grant'",
            (uid, blocked_at),
        )
        db.execute(
            "DELETE FROM recovery_oauth_tokens WHERE user_id=? AND consented_at=?",
            (uid, expected),
        )
        return True


def mark_sync_if_current(user_id, status: str, *, expected_consented_at: float) -> bool:
    """Keep stale sync outcomes from mutating the status of a newer consent."""
    safe_status = str(status or "unbekannt")[:80]
    with _db().connect() as db:
        _init(db)
        cur = db.execute(
            "UPDATE recovery_oauth_tokens SET last_sync_at=?, last_status=? "
            "WHERE user_id=? AND consented_at=?",
            (time.time(), safe_status, str(user_id), float(expected_consented_at)),
        )
        return cur.rowcount == 1


def recovery_consent_block_reason(user_id) -> str:
    uid = _snowflake(user_id)
    if not uid:
        return "invalid"
    with _db().connect() as db:
        _init(db)
        row = db.execute(
            "SELECT reason FROM recovery_primary_ban_blocks WHERE user_id=?", (uid,)
        ).fetchone()
    return str(row[0]) if row else ""


def is_recovery_consent_blocked(user_id) -> bool:
    return bool(recovery_consent_block_reason(user_id))


def is_primary_ban_blocked(user_id) -> bool:
    return recovery_consent_block_reason(user_id) == "primary_ban"


def _block_consent(user_id, reason: str, *, now: float | None = None) -> tuple[str | None, bool]:
    """Persist a revocation tombstone and delete the old consent atomically."""
    uid = _snowflake(user_id)
    if not uid:
        return None, False
    blocked_at = time.time() if now is None else float(now)
    with _db().connect() as db:
        _init(db)
        row = db.execute(
            "SELECT refresh_token_enc FROM recovery_oauth_tokens WHERE user_id=?", (uid,)
        ).fetchone()
        db.execute(
            "INSERT INTO recovery_primary_ban_blocks(user_id, blocked_at, reason) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "blocked_at=MAX(blocked_at, excluded.blocked_at), reason=excluded.reason",
            (uid, blocked_at, reason),
        )
        db.execute("DELETE FROM recovery_oauth_tokens WHERE user_id=?", (uid,))
    token = _decrypt(str(row[0])) if row else None
    return token, row is not None


def block_primary_ban(user_id, *, now: float | None = None) -> tuple[str | None, bool]:
    """Persist a primary-server ban tombstone and remove the previous consent."""
    return _block_consent(user_id, "primary_ban", now=now)


def can_replace_revocation_with_new_consent(user_id, *, consent_started_at: float) -> bool:
    """Read-only age check; keep the tombstone until the final OAuth token write."""
    uid = _snowflake(user_id)
    if not uid:
        return False
    with _db().connect() as db:
        _init(db)
        row = db.execute(
            "SELECT blocked_at FROM recovery_primary_ban_blocks WHERE user_id=?", (uid,)
        ).fetchone()
    return row is None or float(row[0]) <= float(consent_started_at)


async def _remove_backup_member_after_consent_revocation(guild, user_id) -> bool:
    """Compensate when a ban or user withdrawal lands during an in-flight Discord join request."""
    try:
        member = await guild.fetch_member(int(user_id))
    except Exception:
        return False
    try:
        await guild.kick(
            member,
            reason="Pulse Recovery: Bann im Hauptserver während der Recovery-Wiederherstellung",
        )
        return True
    except Exception:
        return False


def get_refresh_token(user_id) -> str | None:
    uid = str(user_id or "")
    with _db().connect() as db:
        _init(db)
        row = db.execute("SELECT refresh_token_enc FROM recovery_oauth_tokens WHERE user_id=?", (uid,)).fetchone()
    return _decrypt(str(row[0])) if row else None


def has_stored_consent(user_id) -> bool:
    """True when a consent row exists, even if its token can currently not be decrypted (AUTH-2)."""
    with _db().connect() as db:
        _init(db)
        return db.execute("SELECT 1 FROM recovery_oauth_tokens WHERE user_id=?", (str(user_id or ""),)).fetchone() is not None


def list_linked_users(limit: int = 100) -> list[dict]:
    limit = max(1, min(MAX_BATCH_SIZE, int(limit)))
    with _db().connect() as db:
        _init(db)
        rows = db.execute(
            "SELECT user_id, refresh_token_enc, consented_at, last_sync_at, last_status "
            "FROM recovery_oauth_tokens ORDER BY COALESCE(last_sync_at, 0), consented_at LIMIT ?",
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        token = _decrypt(str(row[1]))
        if token:
            items.append({"user_id": str(row[0]), "refresh_token": token, "consented_at": float(row[2]),
                          "last_sync_at": float(row[3]) if row[3] is not None else None, "last_status": str(row[4])})
    return items


def recovery_snapshot() -> tuple[int, float]:
    """Return the stored-consent count and a cutoff for a consistent background run."""
    snapshot_at = time.time()
    with _db().connect() as db:
        _init(db)
        row = db.execute(
            "SELECT COUNT(*) FROM recovery_oauth_tokens WHERE consented_at <= ?",
            (snapshot_at,),
        ).fetchone()
    return (int(row[0]) if row else 0), snapshot_at


def list_linked_users_page(
    after_user_id: str | None = None,
    limit: int = 100,
    *,
    consented_before: float | None = None,
) -> tuple[list[dict], str | None, int, bool]:
    """Return a stable, cursor-paginated page of users with stored explicit consent.

    The timestamp cutoff freezes the migration's candidate set. The cursor advances
    over database rows even when an encrypted token cannot be decrypted, so one
    damaged entry cannot block processing later users.
    """
    limit = max(1, min(MAX_BATCH_SIZE, int(limit)))
    conditions = []
    params: list[Any] = []
    if after_user_id is not None:
        conditions.append("user_id > ?")
        params.append(str(after_user_id))
    if consented_before is not None:
        conditions.append("consented_at <= ?")
        params.append(float(consented_before))
    query = (
        "SELECT user_id, refresh_token_enc, consented_at, last_sync_at, last_status "
        "FROM recovery_oauth_tokens "
    )
    if conditions:
        query += "WHERE " + " AND ".join(conditions) + " "
    query += "ORDER BY user_id LIMIT ?"
    params.append(limit)
    with _db().connect() as db:
        _init(db)
        rows = db.execute(query, tuple(params)).fetchall()
    items = []
    for row in rows:
        token = _decrypt(str(row[1]))
        if token:
            items.append({
                "user_id": str(row[0]),
                "refresh_token": token,
                "consented_at": float(row[2]),
                "last_sync_at": float(row[3]) if row[3] is not None else None,
                "last_status": str(row[4]),
            })
    next_cursor = str(rows[-1][0]) if rows else None
    return items, next_cursor, len(rows), len(rows) == limit


def count_linked_users() -> int:
    with _db().connect() as db:
        _init(db)
        row = db.execute("SELECT COUNT(*) FROM recovery_oauth_tokens").fetchone()
    return int(row[0]) if row else 0


def mark_sync(user_id, status: str, *, now: float | None = None) -> None:
    safe_status = str(status or "unbekannt")[:80]
    with _db().connect() as db:
        _init(db)
        db.execute("UPDATE recovery_oauth_tokens SET last_sync_at=?, last_status=? WHERE user_id=?",
                   (time.time() if now is None else float(now), safe_status, str(user_id)))


def unlink_with_refresh_token(user_id, *, now: float | None = None) -> tuple[str | None, bool]:
    """Atomically block in-flight syncs, delete the latest consent and return its token for revocation."""
    return _block_consent(user_id, "user_revoked", now=now)


def unlink(user_id, *, now: float | None = None) -> bool:
    """Persist withdrawal so a token already held by a running sync cannot recreate consent."""
    _token, had_consent = unlink_with_refresh_token(user_id, now=now)
    return had_consent


def target_status(bot, settings: dict | None = None) -> tuple[Any | None, Any | None, list[str]]:
    """Validate the target guild and ensure the configured citizen role cannot grant admin/mod powers."""
    settings = normalize_settings(settings if settings is not None else load_settings())
    problems = []
    if not settings["enabled"]:
        return None, None, ["Die Backup-Wiederherstellung ist deaktiviert."]
    if not settings["backup_guild_id"] or not settings["citizen_role_id"]:
        return None, None, ["Backup-Server-ID oder Bürgerrollen-ID fehlt."]
    try:
        import webserver as ws
        if settings["backup_guild_id"] == str(ws.GUILD_ID):
            return None, None, ["Der Backup-Server muss ein eigener, vom Hauptserver getrennter Discord-Server sein."]
    except Exception:
        pass
    try:
        guild = bot.get_guild(int(settings["backup_guild_id"]))
    except (AttributeError, TypeError, ValueError):
        guild = None
    if guild is None:
        return None, None, ["Pulse ist nicht im konfigurierten Backup-Server oder der Server ist nicht im Cache."]
    try:
        role = guild.get_role(int(settings["citizen_role_id"]))
    except (AttributeError, TypeError, ValueError):
        role = None
    if role is None:
        problems.append("Die konfigurierte Bürgerrolle existiert auf dem Backup-Server nicht.")
    me = getattr(guild, "me", None)
    perms = getattr(me, "guild_permissions", None)
    if not me or not getattr(perms, "manage_roles", False):
        problems.append("Pulse benötigt auf dem Backup-Server „Rollen verwalten“.")
    if not me or not getattr(perms, "create_instant_invite", False):
        problems.append("Pulse benötigt auf dem Backup-Server „Soforteinladung erstellen“ (Discord-API-Voraussetzung).")
    if role is not None and me and getattr(me, "top_role", None) and role.position >= me.top_role.position:
        problems.append("Die Bürgerrolle muss in der Rollenliste unter der höchsten Pulse-Rolle stehen.")
    if role is not None:
        role_perms = getattr(role, "permissions", None)
        unsafe = [name for name in DANGEROUS_ROLE_PERMISSIONS if getattr(role_perms, name, False)]
        if unsafe:
            problems.append("Die Bürgerrolle hat zu weitreichende Rechte und wird nicht vergeben: " + ", ".join(unsafe) + ".")
    return guild, role, problems


async def _exchange_code(code: str, redirect_uri: str) -> dict:
    import webserver as ws
    async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
        response = await client.post(
            "https://discord.com/api/v10/oauth2/token",
            data={"client_id": ws.CLIENT_ID, "client_secret": ws.CLIENT_SECRET,
                  "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise ValueError("Discord hat die Backup-Freigabe abgelehnt. Bitte den OAuth-Vorgang erneut starten.")
        data = response.json()
    if not isinstance(data, dict) or not data.get("access_token") or not data.get("refresh_token"):
        raise ValueError("Discord hat keinen wiederverwendbaren Recovery-Zugang geliefert. Bitte erneut autorisieren.")
    scopes = str(data.get("scope") or "").split()
    # OAuth2 permits the token endpoint to omit "scope" when it equals the requested scope.
    # When Discord does include it, reject any response that omitted a required permission.
    if scopes and ("guilds.join" not in scopes or "identify" not in scopes):
        raise ValueError("Die erforderlichen Discord-Freigaben fehlen. Bitte den Vorgang erneut bestätigen.")
    return data


async def complete_consent(code: str, redirect_uri: str, bot) -> tuple[bool, str, str]:
    """Exchange an explicit OAuth consent and add the user with only the configured citizen role."""
    consent_started_at = time.time()
    settings = load_settings()
    guild, role, problems = target_status(bot, settings)
    if problems:
        return False, "Backup-Server nicht einsatzbereit: " + " ".join(problems), ""
    try:
        tokens = await _exchange_code(code, redirect_uri)
        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
            user_res = await client.get("https://discord.com/api/v10/users/@me",
                                        headers={"Authorization": f"Bearer {tokens['access_token']}"})
            if user_res.status_code != 200:
                return False, "Discord konnte dein Konto nicht bestätigen. Bitte erneut autorisieren.", ""
            user = user_res.json()
            uid = _snowflake(user.get("id"))
            if not uid:
                return False, "Discord hat keine gültige Benutzer-ID geliefert.", ""
            # Initial opt-in is only available to members already verified on the primary guild.
            # The recovery sync later uses this stored consent even if the primary guild is gone.
            import webserver as ws
            primary_guild = bot.get_guild(int(ws.GUILD_ID)) if bot else None
            if primary_guild is None:
                return False, "Der Hauptserver ist gerade nicht verfügbar; die Mitgliedschaft kann nicht geprüft werden.", uid
            # Always perform a live API check. A cached member can be stale while a ban is being processed.
            try:
                primary_member = await primary_guild.fetch_member(int(uid))
            except Exception:
                return False, "Nur aktuell aufgenommene Mitglieder des Hauptservers können den Backup-Zugang freischalten.", uid
            from pulse_ids import CITIZEN_ROLE_ID
            if not any(getattr(member_role, "id", None) == int(CITIZEN_ROLE_ID) for member_role in getattr(primary_member, "roles", [])):
                return False, "Für den Backup-Zugang wird die Bürgerrolle im Hauptserver benötigt.", uid
            # An older ban can be cleared only by a genuinely new explicit OAuth flow after the ban.
            # A ban timestamp newer than this flow wins even when its callback arrives late.
            if not can_replace_revocation_with_new_consent(uid, consent_started_at=consent_started_at):
                return False, "Deine Recovery-Freigabe wurde während der Anmeldung durch einen Hauptserver-Bann gesperrt.", uid
            bot_token = os.getenv("DISCORD_TOKEN", "").strip()
            if not bot_token:
                return False, "Pulse ist nicht korrekt für die Backup-Wiederherstellung konfiguriert.", ""
            joined = await client.put(
                f"https://discord.com/api/v10/guilds/{guild.id}/members/{uid}",
                headers={"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
                json={"access_token": tokens["access_token"], "roles": [str(role.id)]},
            )
            if joined.status_code not in (201, 204):
                if joined.status_code == 429:
                    return False, "Discord begrenzt gerade Beitritte. Bitte später erneut versuchen.", uid
                if joined.status_code in (401, 403):
                    return False, "Pulse fehlen Rechte im Backup-Server oder die Discord-Freigabe ist ungültig.", uid
                return False, f"Discord konnte den Beitritt nicht abschließen (HTTP {joined.status_code}).", uid
            # A ban can arrive after the live primary-guild lookup and before/while Discord joins the backup.
            # The tombstone is authoritative; remove any just-added membership before returning.
            if is_recovery_consent_blocked(uid):
                reason = recovery_consent_block_reason(uid)
                if reason == "primary_ban" or joined.status_code == 201:
                    await _remove_backup_member_after_consent_revocation(guild, uid)
                detail = "Bann im Hauptserver" if reason == "primary_ban" else "Widerruf der Recovery-Freigabe"
                return False, f"Recovery wurde während der Anmeldung durch {detail} gesperrt.", uid
            # If the person was already there, Discord returns 204 without changing roles.
            # Add only the citizen role; never copy roles from the primary guild or remove existing roles.
            if joined.status_code == 204:
                member = await guild.fetch_member(int(uid))
                if role.id not in {r.id for r in getattr(member, "roles", [])}:
                    await member.add_roles(role, reason="Pulse Recovery: ausdrücklich freigegebene Bürgerrolle")
        try:
            store_refresh_token(
                uid, tokens["refresh_token"], now=consent_started_at,
                consent_started_at=consent_started_at,
            )
        except RecoveryBlockedError as exc:
            await revoke_refresh_token(tokens["refresh_token"])
            if joined.status_code == 201:
                await _remove_backup_member_after_consent_revocation(guild, uid)
            return False, str(exc), uid
        mark_sync(uid, "Backup-Zugriff freigeschaltet")
        return True, "Freigabe erfolgreich. Du bist dem Backup-Server mit der konfigurierten Bürgerrolle beigetreten. Es werden keine Team- oder Moderationsrollen übernommen.", uid
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else f"Verbindung, Discord-Rollenaktion oder sichere Speicherung fehlgeschlagen ({type(exc).__name__})."
        return False, message[:300], ""


# SEC-11: returned by rejoin_user when Discord rejects Pulse's client credentials; sync loops stop on it.
CONFIG_ERROR_MESSAGE = "Pulse-OAuth-Konfiguration ungültig (Client-ID/Secret) – Sync abgebrochen, keine Freigabe gelöscht"


def _oauth_error_code(response) -> str:
    try:
        body = response.json()
    except Exception:
        return ""
    return str(body.get("error") or "") if isinstance(body, dict) else ""


async def rejoin_user(
    user_id: str,
    refresh_token: str,
    bot,
    client: httpx.AsyncClient,
    *,
    settings: dict | None = None,
    expected_consented_at: float | None = None,
) -> tuple[bool, str]:
    """Refresh one user's consent, then add them to the backup guild with only its citizen role."""
    import webserver as ws
    uid = _snowflake(user_id)
    if not uid:
        return False, "ungültige Benutzer-ID"
    if is_recovery_consent_blocked(uid):
        reason = recovery_consent_block_reason(uid)
        detail = "Bann im Hauptserver" if reason == "primary_ban" else "widerrufene Recovery-Freigabe"
        return False, f"Recovery wegen {detail} gesperrt; eine alte Freigabe wird nicht wiederverwendet."
    if expected_consented_at is None:
        return False, "Einwilligungszeitpunkt fehlt; ein sicherer Recovery-Sync ist nicht möglich."
    selected_settings = normalize_settings(settings if settings is not None else load_settings())
    current_settings = load_settings()
    if settings is not None and (
        not current_settings["enabled"]
        or current_settings["backup_guild_id"] != selected_settings["backup_guild_id"]
        or current_settings["citizen_role_id"] != selected_settings["citizen_role_id"]
    ):
        return False, "Backup-Konfiguration wurde während des Syncs geändert oder deaktiviert"
    settings = selected_settings
    guild, role, problems = target_status(bot, settings)
    if problems:
        return False, " · ".join(problems)
    bot_token = os.getenv("DISCORD_TOKEN", "").strip()
    if not bot_token:
        return False, "DISCORD_TOKEN fehlt"
    if not key_configured():
        # AUTH-2: Discord rotates refresh tokens on use. Without the dedicated key the replacement could not be
        # stored and the consent would be lost – so the stored token is not used at all.
        return False, "PULSE_RECOVERY_KEY fehlt – die Freigabe wurde nicht verwendet, damit sie nicht verloren geht"
    try:
        response = await _request_with_429_retry(lambda: client.post(
            "https://discord.com/api/v10/oauth2/token",
            data={"client_id": ws.CLIENT_ID, "client_secret": ws.CLIENT_SECRET,
                  "grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ))
        if response.status_code != 200:
            error_code = _oauth_error_code(response)
            # SEC-11: only a refresh token Discord itself calls invalid ("invalid_grant") is a revoked/expired
            # consent. 401/"invalid_client" means Pulse's own client credentials are wrong (rotated secret,
            # typo) – deleting every stored consent then would destroy the recovery exactly in the emergency.
            if response.status_code == 401 or error_code in {"invalid_client", "unauthorized_client"}:
                return False, CONFIG_ERROR_MESSAGE
            if response.status_code == 400 and error_code == "invalid_grant":
                if unlink_if_current(uid, expected_consented_at=expected_consented_at):
                    return False, "Freigabe abgelaufen/widerrufen; Nutzer muss erneut freischalten"
                return False, "Veralteter Sync-Token abgewiesen; eine neuere Einwilligung bleibt bestehen."
                return False, "Freigabe abgelaufen/widerrufen; Nutzer muss erneut freischalten"
            return False, f"Token-Aktualisierung fehlgeschlagen (HTTP {response.status_code})"
        tokens = response.json()
        if not isinstance(tokens, dict) or not tokens.get("access_token") or not tokens.get("refresh_token"):
            return False, "Discord lieferte keine gültige Token-Erneuerung"
        refreshed_scopes = str(tokens.get("scope") or "").split()
        if refreshed_scopes and "guilds.join" not in refreshed_scopes:
            return False, "guilds.join fehlt in der erneuerten Freigabe"
        # Rotate only while the exact consent snapshot from this page remains current.
        try:
            rotate_refresh_token(uid, tokens["refresh_token"], expected_consented_at=expected_consented_at)
        except RecoveryBlockedError:
            await revoke_refresh_token(tokens["refresh_token"], client)
            reason = recovery_consent_block_reason(uid)
            detail = "Bann im Hauptserver" if reason == "primary_ban" else "Widerruf der Recovery-Freigabe"
            return False, f"Recovery während der Token-Erneuerung durch {detail} gesperrt."
        # Re-check after rotating the token; a ban may have arrived during the OAuth exchange.
        if is_recovery_consent_blocked(uid):
            reason = recovery_consent_block_reason(uid)
            detail = "Bann im Hauptserver" if reason == "primary_ban" else "widerrufene Recovery-Freigabe"
            return False, f"Recovery während des Syncs wegen {detail} gesperrt."
        joined = await _request_with_429_retry(lambda: client.put(
            f"https://discord.com/api/v10/guilds/{guild.id}/members/{uid}",
            headers={"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
            json={"access_token": tokens["access_token"], "roles": [str(role.id)]},
        ))
        if joined.status_code not in (201, 204):
            mark_sync_if_current(uid, f"Beitritt fehlgeschlagen HTTP {joined.status_code}", expected_consented_at=expected_consented_at)
            return False, f"Beitritt fehlgeschlagen (HTTP {joined.status_code})"
        # The ban listener may have run before this in-flight join completed. Re-check and compensate.
        if is_recovery_consent_blocked(uid):
            reason = recovery_consent_block_reason(uid)
            if reason == "primary_ban" or joined.status_code == 201:
                await _remove_backup_member_after_consent_revocation(guild, uid)
            detail = "Bann im Hauptserver" if reason == "primary_ban" else "Widerruf der Recovery-Freigabe"
            return False, f"Recovery während des Syncs wegen {detail} gesperrt."
        if joined.status_code == 204:
            member = await guild.fetch_member(int(uid))
            if role.id not in {r.id for r in getattr(member, "roles", [])}:
                await member.add_roles(role, reason="Pulse Recovery Sync: ausschließlich Bürgerrolle")
        mark_sync_if_current(uid, "Backup-Beitritt geprüft/abgeschlossen", expected_consented_at=expected_consented_at)
        return True, "bereits vorhanden" if joined.status_code == 204 else "hinzugefügt (nur Bürgerrolle)"
    except Exception as exc:
        mark_sync(uid, f"Fehler: {type(exc).__name__}")
        return False, f"Verbindung, Discord-Rollenaktion oder Token-Verarbeitung fehlgeschlagen ({type(exc).__name__})"


async def setup_recovery(bot) -> tuple[bool, str]:
    """Validate live configuration before enabling the feature."""
    settings = load_settings()
    _, _, problems = target_status(bot, {**settings, "enabled": True})
    if problems:
        return False, " ".join(problems)
    save_settings({**settings, "enabled": True})
    return True, "Backup-Ziel validiert."
