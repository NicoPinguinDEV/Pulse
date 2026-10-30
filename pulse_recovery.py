"""Opt-in Discord backup-server recovery.

A user must explicitly authorize the Discord OAuth2 scopes identify + guilds.join.
Refresh tokens are encrypted at rest using the existing Pulse session secret and PyNaCl.
The recovery flow never copies roles from the primary guild: newly added users receive
only the configured, permission-reviewed citizen role.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
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


def _box() -> SecretBox:
    """Derive a separate encryption key from Pulse's existing persistent session secret."""
    import webserver
    secret = webserver._session_key()
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise RuntimeError("Pulse-Session-Geheimnis ist nicht sicher genug.")
    key = hashlib.sha256(b"Pulse recovery OAuth token encryption v1\x00" + secret).digest()
    return SecretBox(key)


def _encrypt(token: str) -> str:
    return base64.urlsafe_b64encode(bytes(_box().encrypt(token.encode("utf-8")))).decode("ascii")


def _decrypt(token: str) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        return _box().decrypt(raw).decode("utf-8")
    except (ValueError, UnicodeError, CryptoError, RuntimeError):
        return None


def store_refresh_token(user_id, refresh_token: str, *, now: float | None = None) -> None:
    uid = _snowflake(user_id)
    if not uid or not isinstance(refresh_token, str) or not refresh_token or len(refresh_token) > 4096:
        raise ValueError("Ungültige Recovery-Anmeldedaten.")
    encrypted = _encrypt(refresh_token)
    now = time.time() if now is None else float(now)
    with _db().connect() as db:
        _init(db)
        db.execute(
            "INSERT INTO recovery_oauth_tokens(user_id, refresh_token_enc, consented_at, last_sync_at, last_status) "
            "VALUES (?, ?, ?, NULL, 'freigeschaltet') "
            "ON CONFLICT(user_id) DO UPDATE SET refresh_token_enc=excluded.refresh_token_enc, "
            "consented_at=excluded.consented_at, last_sync_at=NULL, last_status='freigeschaltet'",
            (uid, encrypted, now),
        )


def get_refresh_token(user_id) -> str | None:
    uid = str(user_id or "")
    with _db().connect() as db:
        _init(db)
        row = db.execute("SELECT refresh_token_enc FROM recovery_oauth_tokens WHERE user_id=?", (uid,)).fetchone()
    return _decrypt(str(row[0])) if row else None


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


def unlink(user_id) -> bool:
    with _db().connect() as db:
        _init(db)
        cur = db.execute("DELETE FROM recovery_oauth_tokens WHERE user_id=?", (str(user_id),))
        return cur.rowcount > 0


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
                return False, "Der Hauptserver ist gerade nicht verfügbar; die Verifikation kann nicht geprüft werden.", uid
            primary_member = primary_guild.get_member(int(uid))
            if primary_member is None:
                try:
                    primary_member = await primary_guild.fetch_member(int(uid))
                except Exception:
                    return False, "Nur Mitglieder des Hauptservers können den Backup-Zugang freischalten.", uid
            from pulse_verification import CITIZEN_ROLE_ID
            if not any(getattr(member_role, "id", None) == int(CITIZEN_ROLE_ID) for member_role in getattr(primary_member, "roles", [])):
                return False, "Bitte verifiziere dich zuerst im Hauptserver. Der Backup-Zugang ist nur für verifizierte Bürger freigegeben.", uid
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
            # If the person was already there, Discord returns 204 without changing roles.
            # Add only the citizen role; never copy roles from the primary guild or remove existing roles.
            if joined.status_code == 204:
                member = await guild.fetch_member(int(uid))
                if role.id not in {r.id for r in getattr(member, "roles", [])}:
                    await member.add_roles(role, reason="Pulse Recovery: ausdrücklich freigegebene Bürgerrolle")
        store_refresh_token(uid, tokens["refresh_token"])
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
) -> tuple[bool, str]:
    """Refresh one user's consent, then add them to the backup guild with only its citizen role."""
    import webserver as ws
    uid = _snowflake(user_id)
    if not uid:
        return False, "ungültige Benutzer-ID"
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
                unlink(uid)  # Revoked/expired consent must be renewed by the user.
                return False, "Freigabe abgelaufen/widerrufen; Nutzer muss erneut freischalten"
            return False, f"Token-Aktualisierung fehlgeschlagen (HTTP {response.status_code})"
        tokens = response.json()
        if not isinstance(tokens, dict) or not tokens.get("access_token") or not tokens.get("refresh_token"):
            return False, "Discord lieferte keine gültige Token-Erneuerung"
        refreshed_scopes = str(tokens.get("scope") or "").split()
        if refreshed_scopes and "guilds.join" not in refreshed_scopes:
            return False, "guilds.join fehlt in der erneuerten Freigabe"
        # Discord can rotate refresh tokens, so save the replacement before the next API request.
        store_refresh_token(uid, tokens["refresh_token"])
        joined = await _request_with_429_retry(lambda: client.put(
            f"https://discord.com/api/v10/guilds/{guild.id}/members/{uid}",
            headers={"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
            json={"access_token": tokens["access_token"], "roles": [str(role.id)]},
        ))
        if joined.status_code not in (201, 204):
            mark_sync(uid, f"Beitritt fehlgeschlagen HTTP {joined.status_code}")
            return False, f"Beitritt fehlgeschlagen (HTTP {joined.status_code})"
        if joined.status_code == 204:
            member = await guild.fetch_member(int(uid))
            if role.id not in {r.id for r in getattr(member, "roles", [])}:
                await member.add_roles(role, reason="Pulse Recovery Sync: ausschließlich Bürgerrolle")
        mark_sync(uid, "Backup-Beitritt geprüft/abgeschlossen")
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
