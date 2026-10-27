"""Verification via Discord OAuth2 (identify + guilds.join) and hourly auto-rejoin.

When an admin switches this on (default: off), the verification button no longer hands out the
citizen role directly: members authorize Pulse once with the scopes identify + guilds.join, after a
consent text that says they will be added back automatically. Pulse stores the access and refresh
token encrypted (PyNaCl, key derived from the session secret, separate from pulse_recovery's key).

Members who later leave BOCHUM RP | V1 are re-added by the hourly job via
PUT /guilds/{guild_id}/members/{user_id} – but only after a 24-hour grace period, never when they are
banned or were kicked, and at most MAX_PER_RUN per run. A revoked or invalid authorization
(invalid_grant / 401) deletes the stored tokens and is not retried. Members can delete their data
at any time (/rejoin-abmelden, or "Deauthorize" in Discord's own app settings).

Everything here is pure or takes an injected httpx client/bot, so tests never reach Discord.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from typing import Any

import httpx
from nacl.exceptions import CryptoError
from nacl.secret import SecretBox

SETTING_KEY = "rejoin_config"
API = "https://discord.com/api/v10"
SCOPES = ("identify", "guilds.join")  # no email: Pulse does not need it
TOKEN_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
GRACE_SECONDS = 24 * 3600          # wait 24 h after a member left before re-adding them
RETRY_SECONDS = 3600               # at most one attempt per member and hour
MAX_PER_RUN = 25                   # Discord rate limits: small, bounded batches
MAX_FAILURES = 5                   # give up on a member after this many failed attempts in a row
MAX_TRANSPORT_ERRORS = 3           # stop a run when Discord is unreachable
LIST_LIMIT = 100

DEFAULTS = {"enabled": False}

CONSENT_TEXT = (
    "Zur Verifizierung auf **© BOCHUM RP | V1** autorisierst du die Pulse-App bei Discord mit zwei Rechten: "
    "**Benutzername/Avatar lesen** und **„Server für dich beitreten“**. Mit der Autorisierung stimmst du zu, dass Pulse "
    "dich automatisch wieder zum Server hinzufügt, falls du ihn verlässt – frühestens 24 Stunden nach dem Verlassen, "
    "nie nach einem Bann oder Kick. Pulse speichert dafür deine Discord-ID und den Discord-Zugang verschlüsselt; "
    "deine E-Mail-Adresse wird nicht abgefragt. Du kannst jederzeit widersprechen und deine Daten löschen: mit "
    "`/rejoin-abmelden` auf dem Server oder über Discord → Einstellungen → Autorisierte Apps → „Deautorisieren“."
)


def _db():
    import pulse_db
    return pulse_db


def _snowflake(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.isdecimal() and 15 <= len(text) <= 22 and int(text) > 0 else ""


# --------------------------------------------------------------------------- settings
def normalize_settings(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    enabled = raw.get("enabled") is True or str(raw.get("enabled", "")).lower() in {"1", "true", "on", "yes"}
    return {"enabled": enabled}


def load_settings() -> dict:
    return normalize_settings(_db().get_setting(SETTING_KEY, DEFAULTS))


def save_settings(raw: dict) -> dict:
    settings = normalize_settings(raw)
    _db().set_setting(SETTING_KEY, settings)
    return settings


def is_enabled() -> bool:
    try:
        return load_settings()["enabled"]
    except Exception:
        return False


def setup_problems() -> list[str]:
    """Why the OAuth verification cannot work yet (checked before an admin enables it)."""
    import webserver as ws
    problems = []
    if not ws.CLIENT_ID or not ws.CLIENT_SECRET:
        problems.append("DISCORD_CLIENT_ID/DISCORD_CLIENT_SECRET fehlen.")
    if not str(getattr(ws, "PUBLIC_BASE_URL", "") or "").startswith("https://"):
        problems.append("PUBLIC_BASE_URL muss eine https://-Adresse sein (Link im Verifizierungs-Hinweis).")
    if not os.getenv("DISCORD_TOKEN", "").strip():
        problems.append("DISCORD_TOKEN fehlt.")
    return problems


def authorize_url() -> str:
    import webserver as ws
    base = str(getattr(ws, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    return f"{base}/verify/authorize" if base.startswith("https://") else ""


# Short consent line shown in the verification panel itself (Discord's own dialog lists the permissions).
PANEL_NOTE = (
    "🔐 Mit dem Knopf autorisierst du Pulse bei Discord („Server für dich beitreten“). Damit stimmst du zu, dass Pulse "
    "dich frühestens 24 h nach dem Verlassen automatisch wieder hinzufügt (nie nach Bann/Kick). Widerruf jederzeit: "
    "`/rejoin-abmelden` oder Discord → Autorisierte Apps."
)


def direct_state() -> str:
    """Fixed, signed OAuth state for the panel's link button (a static link cannot carry a per-browser cookie).

    Binding to a browser is not needed here: the account that authorizes is the account that gets verified,
    so a foreign code could only ever verify the foreigner's own account.
    """
    import hmac as _hmac
    import webserver as ws
    digest = _hmac.new(ws._session_key(), b"Pulse verify direct OAuth state v1", hashlib.sha256).hexdigest()
    return "pv1." + digest[:40]


def is_direct_state(state) -> bool:
    import hmac as _hmac
    try:
        return bool(state) and _hmac.compare_digest(str(state), direct_state())
    except Exception:
        return False


def redirect_uri() -> str:
    """Callback URL outside a request – the same value oauth_redirect_uri() uses when it is configured."""
    import webserver as ws
    configured = str(getattr(ws, "REDIRECT_URI", "") or "").rstrip("/")
    if configured:
        return configured
    base = str(getattr(ws, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    return f"{base}/callback" if base else ""


def direct_authorize_url() -> str:
    """discord.com authorization link for the panel button ('' when OAuth is not configured)."""
    from urllib.parse import quote, urlencode
    import webserver as ws
    uri = redirect_uri()
    if not ws.CLIENT_ID or not uri.startswith("https://"):
        return ""
    query = urlencode({"client_id": ws.CLIENT_ID, "redirect_uri": uri, "response_type": "code",
                       "scope": " ".join(SCOPES), "state": direct_state()}, quote_via=quote)
    return f"https://discord.com/oauth2/authorize?{query}"


# --------------------------------------------------------------------------- storage
def _init(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS rejoin_oauth_tokens ("
        "user_id TEXT PRIMARY KEY, access_token_enc TEXT NOT NULL, refresh_token_enc TEXT NOT NULL, "
        "access_expires_at REAL NOT NULL DEFAULT 0, scopes TEXT NOT NULL DEFAULT '', consented_at REAL NOT NULL, "
        "left_at REAL, left_reason TEXT NOT NULL DEFAULT '', last_attempt_at REAL, "
        "last_status TEXT NOT NULL DEFAULT 'autorisiert', failures INTEGER NOT NULL DEFAULT 0, "
        "rejoin_count INTEGER NOT NULL DEFAULT 0)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_rejoin_left ON rejoin_oauth_tokens(left_at)")


def _box() -> SecretBox:
    import webserver as ws
    secret = ws._session_key()
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise RuntimeError("Pulse-Session-Geheimnis ist nicht sicher genug.")
    return SecretBox(hashlib.sha256(b"Pulse rejoin OAuth token encryption v1\x00" + secret).digest())


def _encrypt(token: str) -> str:
    return base64.urlsafe_b64encode(bytes(_box().encrypt(token.encode("utf-8")))).decode("ascii")


def _decrypt(token: str) -> str | None:
    try:
        return _box().decrypt(base64.urlsafe_b64decode(token.encode("ascii"))).decode("utf-8")
    except (ValueError, UnicodeError, CryptoError, RuntimeError):
        return None


def _valid_token(token: Any) -> bool:
    return isinstance(token, str) and 0 < len(token) <= 4096


def store_tokens(user_id, tokens: dict, *, now: float | None = None, consent: bool = False) -> None:
    """Save (or rotate) a member's tokens. consent=True marks a fresh authorization and resets the state."""
    uid = _snowflake(user_id)
    access, refresh = tokens.get("access_token"), tokens.get("refresh_token")
    if not uid or not _valid_token(access) or not _valid_token(refresh):
        raise ValueError("Ungültige OAuth-Anmeldedaten.")
    now = time.time() if now is None else float(now)
    try:
        expires = now + max(0, int(tokens.get("expires_in") or 0))
    except (TypeError, ValueError):
        expires = now
    scopes = " ".join(str(tokens.get("scope") or " ".join(SCOPES)).split())[:200]
    values = (uid, _encrypt(str(access)), _encrypt(str(refresh)), expires, scopes, now)
    with _db().connect() as db:
        _init(db)
        if consent:
            db.execute(
                "INSERT INTO rejoin_oauth_tokens(user_id, access_token_enc, refresh_token_enc, access_expires_at, scopes, consented_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET access_token_enc=excluded.access_token_enc, "
                "refresh_token_enc=excluded.refresh_token_enc, access_expires_at=excluded.access_expires_at, scopes=excluded.scopes, "
                "consented_at=excluded.consented_at, left_at=NULL, left_reason='', failures=0, last_status='autorisiert'",
                values,
            )
        else:
            db.execute(
                "UPDATE rejoin_oauth_tokens SET access_token_enc=?, refresh_token_enc=?, access_expires_at=?, scopes=? WHERE user_id=?",
                (values[1], values[2], expires, scopes, uid),
            )


def get_entry(user_id) -> dict | None:
    with _db().connect() as db:
        _init(db)
        row = db.execute(
            "SELECT user_id, access_token_enc, refresh_token_enc, access_expires_at, scopes, consented_at, left_at, "
            "left_reason, last_attempt_at, last_status, failures, rejoin_count FROM rejoin_oauth_tokens WHERE user_id=?",
            (str(user_id),),
        ).fetchone()
    return _row(row, with_tokens=True) if row else None


def _row(row, *, with_tokens: bool) -> dict:
    item = {
        "user_id": str(row[0]), "access_expires_at": float(row[3] or 0), "scopes": str(row[4] or ""),
        "consented_at": float(row[5]), "left_at": float(row[6]) if row[6] is not None else None,
        "left_reason": str(row[7] or ""), "last_attempt_at": float(row[8]) if row[8] is not None else None,
        "last_status": str(row[9] or ""), "failures": int(row[10] or 0), "rejoin_count": int(row[11] or 0),
    }
    if with_tokens:
        item["access_token"] = _decrypt(str(row[1]))
        item["refresh_token"] = _decrypt(str(row[2]))
    return item


def has_authorization(user_id) -> bool:
    with _db().connect() as db:
        _init(db)
        return db.execute("SELECT 1 FROM rejoin_oauth_tokens WHERE user_id=?", (str(user_id),)).fetchone() is not None


def delete_entry(user_id) -> bool:
    with _db().connect() as db:
        _init(db)
        return db.execute("DELETE FROM rejoin_oauth_tokens WHERE user_id=?", (str(user_id),)).rowcount > 0


def mark_left(user_id, reason: str, *, now: float | None = None) -> bool:
    """reason: 'leave' (eligible after the grace period), 'kick' or 'unknown' (never re-added)."""
    reason = reason if reason in ("leave", "kick", "ban", "unknown") else "unknown"
    with _db().connect() as db:
        _init(db)
        return db.execute(
            "UPDATE rejoin_oauth_tokens SET left_at=?, left_reason=?, failures=0, last_status=? WHERE user_id=?",
            (time.time() if now is None else float(now), reason, LEFT_STATUS[reason], str(user_id)),
        ).rowcount > 0


LEFT_STATUS = {"leave": "Server verlassen – Rejoin nach Karenz", "kick": "gekickt – kein Rejoin",
               "ban": "gebannt – kein Rejoin", "unknown": "Abgang ungeklärt – kein Rejoin"}


def mark_present(user_id) -> bool:
    with _db().connect() as db:
        _init(db)
        return db.execute(
            "UPDATE rejoin_oauth_tokens SET left_at=NULL, left_reason='', failures=0, last_status='auf dem Server' "
            "WHERE user_id=? AND left_at IS NOT NULL", (str(user_id),),
        ).rowcount > 0


def mark_attempt(user_id, status: str, *, failed: bool, rejoined: bool = False, now: float | None = None) -> None:
    with _db().connect() as db:
        _init(db)
        db.execute(
            "UPDATE rejoin_oauth_tokens SET last_attempt_at=?, last_status=?, failures=CASE WHEN ? THEN failures+1 ELSE 0 END, "
            "rejoin_count=rejoin_count+?, left_at=CASE WHEN ? THEN NULL ELSE left_at END, "
            "left_reason=CASE WHEN ? THEN '' ELSE left_reason END WHERE user_id=?",
            (time.time() if now is None else float(now), str(status)[:120], int(failed), int(rejoined),
             int(rejoined), int(rejoined), str(user_id)),
        )


def due_entries(*, now: float | None = None, limit: int = MAX_PER_RUN) -> list[dict]:
    """Members who left voluntarily more than GRACE_SECONDS ago and are due for an attempt."""
    now = time.time() if now is None else float(now)
    with _db().connect() as db:
        _init(db)
        rows = db.execute(
            "SELECT user_id, access_token_enc, refresh_token_enc, access_expires_at, scopes, consented_at, left_at, "
            "left_reason, last_attempt_at, last_status, failures, rejoin_count FROM rejoin_oauth_tokens "
            "WHERE left_at IS NOT NULL AND left_reason='leave' AND left_at <= ? AND failures < ? "
            "AND (last_attempt_at IS NULL OR last_attempt_at <= ?) ORDER BY left_at LIMIT ?",
            (now - GRACE_SECONDS, MAX_FAILURES, now - RETRY_SECONDS + 60, max(1, min(MAX_PER_RUN, int(limit)))),
        ).fetchall()
    return [_row(r, with_tokens=True) for r in rows]


def list_entries(limit: int = LIST_LIMIT) -> list[dict]:
    """Admin overview without tokens."""
    with _db().connect() as db:
        _init(db)
        rows = db.execute(
            "SELECT user_id, access_token_enc, refresh_token_enc, access_expires_at, scopes, consented_at, left_at, "
            "left_reason, last_attempt_at, last_status, failures, rejoin_count FROM rejoin_oauth_tokens "
            "ORDER BY COALESCE(left_at, 0) DESC, consented_at DESC LIMIT ?",
            (max(1, min(LIST_LIMIT, int(limit))),),
        ).fetchall()
    return [_row(r, with_tokens=False) for r in rows]


def stats() -> dict:
    with _db().connect() as db:
        _init(db)
        row = db.execute(
            "SELECT COUNT(*), SUM(left_at IS NOT NULL AND left_reason='leave' AND failures < ?), "
            "SUM(failures >= ?), COALESCE(SUM(rejoin_count), 0) FROM rejoin_oauth_tokens",
            (MAX_FAILURES, MAX_FAILURES),
        ).fetchone()
    return {"linked": int(row[0] or 0), "waiting": int(row[1] or 0), "given_up": int(row[2] or 0), "rejoins": int(row[3] or 0)}


def export_person(user_id) -> dict | None:
    """Privacy export: what Pulse stores for this member (never the tokens)."""
    entry = get_entry(user_id)
    if not entry:
        return None
    entry.pop("access_token", None)
    entry.pop("refresh_token", None)
    return entry


# --------------------------------------------------------------------------- Discord HTTP
def _retry_after(response: httpx.Response) -> float:
    value: Any = response.headers.get("Retry-After", "")
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("retry_after") is not None:
            value = body["retry_after"]
    except ValueError:
        pass
    try:
        return max(1.0, float(value))
    except (TypeError, ValueError):
        return 60.0


def _oauth_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return str(body.get("error") or "") if isinstance(body, dict) else ""


async def exchange_code(client: httpx.AsyncClient, code: str, redirect_uri: str) -> dict:
    import webserver as ws
    response = await client.post(
        f"{API}/oauth2/token",
        data={"client_id": ws.CLIENT_ID, "client_secret": ws.CLIENT_SECRET, "grant_type": "authorization_code",
              "code": code, "redirect_uri": redirect_uri},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code == 429:
        raise ValueError("Discord begrenzt gerade Anfragen. Bitte in einer Minute erneut versuchen.")
    if response.status_code != 200:
        raise ValueError("Discord hat die Autorisierung abgelehnt. Bitte die Verifizierung erneut starten.")
    data = response.json()
    if not isinstance(data, dict) or not _valid_token(data.get("access_token")) or not _valid_token(data.get("refresh_token")):
        raise ValueError("Discord hat keinen gültigen Zugang geliefert. Bitte erneut autorisieren.")
    scopes = str(data.get("scope") or "").split()
    if scopes and not set(SCOPES) <= set(scopes):  # an omitted "scope" means: as requested
        raise ValueError("Die Freigabe „Server für dich beitreten“ fehlt. Bitte alle angefragten Rechte bestätigen.")
    return data


async def refresh_tokens(client: httpx.AsyncClient, refresh_token: str) -> tuple[str, dict | float | None]:
    """('ok', tokens) · ('revoked', None) · ('rate_limited', retry_after) · ('error', None)."""
    import webserver as ws
    response = await client.post(
        f"{API}/oauth2/token",
        data={"client_id": ws.CLIENT_ID, "client_secret": ws.CLIENT_SECRET, "grant_type": "refresh_token",
              "refresh_token": refresh_token},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code == 429:
        return "rate_limited", _retry_after(response)
    if response.status_code == 401 or (response.status_code == 400 and _oauth_error(response) in ("invalid_grant", "")):
        return "revoked", None
    if response.status_code != 200:
        return "error", None
    data = response.json()
    if not isinstance(data, dict) or not _valid_token(data.get("access_token")) or not _valid_token(data.get("refresh_token")):
        return "error", None
    scopes = str(data.get("scope") or "").split()
    if scopes and "guilds.join" not in scopes:
        return "revoked", None
    return "ok", data


async def revoke_token(client: httpx.AsyncClient, token: str) -> None:
    """Best effort: tell Discord to drop the authorization when a member deletes their data."""
    import webserver as ws
    try:
        await client.post(f"{API}/oauth2/token/revoke",
                          data={"client_id": ws.CLIENT_ID, "client_secret": ws.CLIENT_SECRET, "token": token,
                                "token_type_hint": "refresh_token"},
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    except httpx.HTTPError:
        pass


async def forget(user_id, client: httpx.AsyncClient | None = None) -> bool:
    """Opt-out: revoke at Discord (best effort) and delete everything Pulse stored for this member."""
    entry = get_entry(user_id)
    if not entry:
        return False
    if entry.get("refresh_token"):
        if client is None:
            async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as own:
                await revoke_token(own, entry["refresh_token"])
        else:
            await revoke_token(client, entry["refresh_token"])
    return delete_entry(user_id)


# --------------------------------------------------------------------------- verification (OAuth callback)
async def complete_verification(code: str, redirect_uri: str, bot, client: httpx.AsyncClient | None = None) -> tuple[bool, str]:
    """Exchange the code, store the tokens and give the member the citizen role."""
    import webserver as ws
    if not is_enabled():
        return False, "Die Verifizierung per Discord-Autorisierung ist gerade nicht aktiv."
    guild = bot.get_guild(int(ws.GUILD_ID)) if bot else None
    if guild is None:
        return False, "Pulse startet gerade. Bitte in einer Minute erneut versuchen."
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=TOKEN_TIMEOUT)
    try:
        tokens = await exchange_code(client, code, redirect_uri)
        user_res = await client.get(f"{API}/users/@me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        if user_res.status_code != 200:
            return False, "Discord konnte dein Konto nicht bestätigen. Bitte erneut autorisieren."
        user = user_res.json()
        user = user if isinstance(user, dict) else {}
        uid = _snowflake(user.get("id"))
        if not uid or user.get("bot"):
            return False, "Discord hat keine gültige Benutzer-ID geliefert."
        member = guild.get_member(int(uid))
        if member is None:
            try:
                member = await guild.fetch_member(int(uid))
            except Exception:
                member = None
        if member is None:
            return False, "Du bist nicht Mitglied von © BOCHUM RP | V1. Bitte tritt dem Server zuerst bei."
        store_tokens(uid, tokens, consent=True)
        cog = bot.get_cog("Verification") if hasattr(bot, "get_cog") else None
        if cog is None:
            return False, "Autorisierung gespeichert, aber die Verifikation ist gerade nicht geladen. Bitte im Panel erneut klicken."
        status = await cog.apply_verification(guild, member)
        messages = {
            "verified": "Verifizierung abgeschlossen – willkommen! Du kannst dieses Fenster schließen und zu Discord zurückkehren.",
            "already": "Du warst bereits verifiziert. Deine Autorisierung wurde gespeichert.",
            "partial": "Du hast die Bürgerrolle erhalten, aber eine alte Rolle konnte nicht entfernt werden. Das Team wurde informiert.",
        }
        if status in messages:
            return True, messages[status]
        return False, "Die Verifikation konnte gerade nicht abgeschlossen werden. Das Team wurde informiert – bitte später erneut versuchen."
    except ValueError as exc:
        return False, str(exc)[:300]
    except httpx.HTTPError as exc:
        return False, f"Discord ist gerade nicht erreichbar ({type(exc).__name__}). Bitte später erneut versuchen."
    finally:
        if own_client:
            await client.aclose()


# --------------------------------------------------------------------------- hourly job
async def _is_banned(guild, uid: str) -> bool | None:
    """True/False, or None when Pulse cannot check (then nobody is re-added)."""
    import discord
    try:
        await guild.fetch_ban(discord.Object(id=int(uid)))
        return True
    except discord.NotFound:
        return False
    except (discord.Forbidden, discord.HTTPException):
        return None


async def rejoin_one(entry: dict, guild, client: httpx.AsyncClient, bot_token: str, citizen_role_id: int,
                     *, now: float | None = None) -> str:
    """Try to re-add one member. Returns a status code (see run_rejoin)."""
    uid = entry["user_id"]
    now = time.time() if now is None else float(now)
    if guild.get_member(int(uid)) is not None:
        mark_present(uid)
        return "present"
    banned = await _is_banned(guild, uid)
    if banned:
        mark_left(uid, "ban", now=now)
        return "banned"
    if banned is None:
        mark_attempt(uid, "Bannstatus nicht prüfbar – kein Rejoin", failed=True, now=now)
        return "ban_unknown"
    if not entry.get("refresh_token"):
        delete_entry(uid)  # unreadable (key changed): the member has to authorize again
        return "revoked"
    access = entry.get("access_token")
    if not access or entry["access_expires_at"] - 300 <= now:
        result, data = await refresh_tokens(client, entry["refresh_token"])
        if result == "revoked":
            delete_entry(uid)
            return "revoked"
        if result == "rate_limited":
            return "rate_limited"
        if result != "ok" or not isinstance(data, dict):
            mark_attempt(uid, "Token-Erneuerung fehlgeschlagen", failed=True, now=now)
            return "failed"
        store_tokens(uid, data, now=now)  # Discord rotates refresh tokens: save before the next call
        access = data["access_token"]
    response = await client.put(
        f"{API}/guilds/{guild.id}/members/{uid}",
        headers={"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
        json={"access_token": access, "roles": [str(citizen_role_id)]},
    )
    if response.status_code == 201:
        mark_attempt(uid, "automatisch wieder hinzugefügt", failed=False, rejoined=True, now=now)
        return "added"
    if response.status_code == 204:
        mark_present(uid)
        return "present"
    if response.status_code == 429:
        return "rate_limited"
    if response.status_code == 401:
        delete_entry(uid)  # access token rejected although freshly valid: authorization is gone
        return "revoked"
    if response.status_code == 403:
        mark_attempt(uid, "Discord verweigert den Beitritt (HTTP 403)", failed=True, now=now)
        return "forbidden"
    if response.status_code == 404:
        mark_attempt(uid, "Server oder Konto nicht gefunden (HTTP 404)", failed=True, now=now)
        return "not_found"
    mark_attempt(uid, f"Beitritt fehlgeschlagen (HTTP {response.status_code})", failed=True, now=now)
    return "failed"


async def run_rejoin(bot, client: httpx.AsyncClient, *, now: float | None = None, limit: int = MAX_PER_RUN) -> dict:
    """One hourly run. Never raises for Discord errors; stops early on 429 or repeated outages."""
    import webserver as ws
    from pulse_verification import CITIZEN_ROLE_ID
    report = {"ran": False, "added": 0, "present": 0, "banned": 0, "revoked": [], "failed": 0, "stopped": ""}
    if not is_enabled():
        report["stopped"] = "deaktiviert"
        return report
    guild = bot.get_guild(int(ws.GUILD_ID)) if bot else None
    bot_token = os.getenv("DISCORD_TOKEN", "").strip()
    if guild is None or not bot_token:
        report["stopped"] = "Server/Bot nicht bereit"
        return report
    report["ran"] = True
    transport_errors = 0
    for entry in due_entries(now=now, limit=limit):
        try:
            status = await rejoin_one(entry, guild, client, bot_token, CITIZEN_ROLE_ID, now=now)
            transport_errors = 0
        except httpx.HTTPError as exc:
            mark_attempt(entry["user_id"], f"Discord nicht erreichbar ({type(exc).__name__})", failed=True, now=now)
            report["failed"] += 1
            transport_errors += 1
            if transport_errors >= MAX_TRANSPORT_ERRORS:
                report["stopped"] = "Discord nicht erreichbar"
                break
            continue
        if status == "rate_limited":
            report["stopped"] = "Discord-Rate-Limit (429)"
            break
        if status == "added":
            report["added"] += 1
        elif status == "present":
            report["present"] += 1
        elif status == "banned":
            report["banned"] += 1
        elif status == "revoked":
            report["revoked"].append(entry["user_id"])
        else:
            report["failed"] += 1
    return report
