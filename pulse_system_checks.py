"""U-12: extra checks for the system page – gateway, cog errors, time zone data, IDs, role hierarchy, .env.

Every check returns (name, ok, detail). Nothing here changes Discord or data, and .env values are never
shown – only whether a variable is set.
"""
import math
import os

import pulse_env_validation
import pulse_ids

# Without these Pulse cannot log in to Discord or run the dashboard login. SESSION_SECRET is not listed:
# Pulse falls back to a generated .session_secret, and the system page checks it via SESSION_SECRET_PROBLEM.
REQUIRED_ENV = ("DISCORD_TOKEN", "DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET")
# One of these is needed so the OAuth redirect points back to this dashboard.
REDIRECT_ENV = ("DISCORD_REDIRECT_URI", "PUBLIC_BASE_URL")
# Roles Pulse hands out or takes away itself; the bot's highest role must sit above them.
MANAGED_ROLE_NAMES = ("APPLICATION_ROLE_ID", "TEST_SUPPORTER_ROLE_ID", "WARN_UPRANK_LOCK_ROLE_ID",
                      "WARN_IC_LOCK_ROLE_ID", "EX_TEAMLER_ROLE_ID", "UNVERIFIED_ROLE_ID", "CITIZEN_ROLE_ID")


def _ids(value):
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, int)]
    if isinstance(value, (tuple, list, set, frozenset)):
        return list(value)
    return [value] if isinstance(value, int) else []


def fixed_ids():
    """(role ids, channel ids) from pulse_ids, each as {constant name: [ids]}."""
    roles, channels = {}, {}
    for name in dir(pulse_ids):
        if not name.isupper() or name == "GUILD_ID":
            continue
        value = getattr(pulse_ids, name)
        if name == "OFFICE_ROLE_IDS":  # voice channel → responsible roles
            channels[name] = list(value)
            roles[name] = [r for group in value.values() for r in group]
        elif "_ROLE_ID" in name:
            roles[name] = _ids(value)
        elif name.endswith(("_CHANNEL_ID", "_CHANNEL_IDS", "_ROOM_ID", "_CATEGORY_ID")):
            channels[name] = _ids(value)
    return roles, channels


def gateway(bot):
    latency = getattr(bot, "latency", None) if bot else None
    if latency is None or not isinstance(latency, (int, float)) or math.isnan(latency) or math.isinf(latency):
        return ("Gateway-Latenz", False, "keine Messung – Bot nicht verbunden")
    ms = latency * 1000
    return ("Gateway-Latenz", ms < 500, f"{ms:.0f} ms" + ("" if ms < 500 else " – Verbindung langsam"))


def cog_errors(bot):
    failed = list(getattr(bot, "pulse_failed_cogs", []) or []) if bot else []
    if not failed:
        return ("Cog-Ladefehler", True, "keine")
    return ("Cog-Ladefehler", False, f"{len(failed)}: " + ", ".join(failed[:5]) + (" …" if len(failed) > 5 else ""))


def timezone():
    name = os.getenv("PANEL_TIMEZONE", "Europe/Berlin")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return ("Zeitzonendaten", True, name)
    except Exception as exc:
        return ("Zeitzonendaten", False, f"{name} nicht verfügbar ({type(exc).__name__}) – Paket tzdata installieren")


def env_values(environ=None):
    environ = os.environ if environ is None else environ
    missing = [n for n in REQUIRED_ENV if not str(environ.get(n, "")).strip()]
    if not any(str(environ.get(n, "")).strip() for n in REDIRECT_ENV):
        missing.append(" oder ".join(REDIRECT_ENV))
    total = len(REQUIRED_ENV) + 1
    if missing:
        return (".env-Pflichtwerte", False, f"{total - len(missing)}/{total} gesetzt · fehlt: {', '.join(missing)}")
    return (".env-Pflichtwerte", True, f"{total}/{total} gesetzt")


def discord_ids(guild):
    if guild is None:
        return [("Feste Discord-IDs", False, "Server nicht im Cache")]
    roles, channels = fixed_ids()
    missing_roles = sorted({n for n, ids in roles.items() for i in ids if not guild.get_role(int(i))})
    missing_channels = sorted({n for n, ids in channels.items() for i in ids if not guild.get_channel(int(i))})
    out = []
    for label, missing, total in (("Rollen", missing_roles, len(roles)), ("Kanäle", missing_channels, len(channels))):
        detail = f"{total - len(missing)}/{total} Einträge vollständig"
        if missing:
            detail += " · fehlt bei: " + ", ".join(missing[:4]) + (" …" if len(missing) > 4 else "")
        out.append((f"Feste {label}", not missing, detail))
    return out


def role_hierarchy(guild, config):
    me = getattr(guild, "me", None) if guild is not None else None
    top = getattr(me, "top_role", None)
    if top is None:
        return ("Rollenhierarchie", False, "Bot-Mitglied nicht im Cache")
    if not getattr(getattr(me, "guild_permissions", None), "manage_roles", False):
        return ("Rollenhierarchie", False, "Bot fehlt die Berechtigung „Rollen verwalten“")
    wanted = [getattr(pulse_ids, n) for n in MANAGED_ROLE_NAMES]
    wanted += list(pulse_ids.WARN_ROLE_IDS.values())
    wanted += [int(r) for r in (config or {}).get("team_role_ids", []) if str(r).isdigit()]
    too_high = []
    for rid in dict.fromkeys(wanted):
        role = guild.get_role(int(rid))
        if role is not None and role.position >= top.position:
            too_high.append(role.name)
    if too_high:
        return ("Rollenhierarchie", False, f"{len(too_high)} Rolle(n) über oder gleich „{top.name}“: " + ", ".join(too_high[:4]))
    return ("Rollenhierarchie", True, f"„{top.name}“ steht über allen verwalteten Rollen")


def moderation_permissions(bot, guild, config):
    """Check the permissions required by enabled server-side moderation protections."""
    automod = (config or {}).get("pulse_automod", {})
    antinuke = (config or {}).get("pulse_antinuke", {})
    automod = automod if isinstance(automod, dict) else {}
    antinuke = antinuke if isinstance(antinuke, dict) else {}
    me = getattr(guild, "me", None) if guild is not None else None
    permissions = getattr(me, "guild_permissions", None)
    checks = []

    if automod.get("enabled"):
        can_read = bool(getattr(getattr(bot, "intents", None), "message_content", False))
        checks.append(("AutoMod · Nachrichteninhalt", can_read,
                       "Message Content Intent aktiv" if can_read else "Message Content Intent im Developer Portal und Bot aktivieren"))
        can_delete = bool(getattr(permissions, "manage_messages", False))
        checks.append(("AutoMod · Nachrichten löschen", can_delete,
                       "Berechtigung vorhanden" if can_delete else "Bot benötigt „Nachrichten verwalten“"))

    if antinuke.get("enabled"):
        can_audit = bool(getattr(permissions, "view_audit_log", False))
        checks.append(("Anti-Nuke · Audit-Log", can_audit,
                       "Berechtigung vorhanden" if can_audit else "Bot benötigt „Audit-Log ansehen“"))
        can_ban = bool(getattr(permissions, "ban_members", False))
        checks.append(("Anti-Nuke · Sperren", can_ban,
                       "Berechtigung vorhanden" if can_ban else "Bot benötigt „Mitglieder bannen“"))

    alert_id = (automod.get("alert_channel_id") or antinuke.get("alert_channel_id")) if (automod.get("enabled") or antinuke.get("enabled")) else 0
    if alert_id:
        try:
            channel = guild.get_channel(int(alert_id)) if guild is not None else None
        except (TypeError, ValueError):
            channel = None
        can_send = bool(channel and me and getattr(channel.permissions_for(me), "send_messages", False))
        checks.append(("AutoMod/Anti-Nuke · Alarmkanal", can_send,
                       "Alarmkanal erreichbar und beschreibbar" if can_send else "Kanal-ID prüfen und Bot „Nachrichten senden“ erlauben"))
    return checks


def run(bot, guild, config, environ=None):
    checks = [gateway(bot), cog_errors(bot), timezone(), env_values(environ), pulse_env_validation.system_check(environ)]  # O-06
    checks += discord_ids(guild)
    checks.append(role_hierarchy(guild, config))
    checks += moderation_permissions(bot, guild, config)
    checks.append(shift_history())
    return checks


def shift_history():
    """Phase 14.7b: backend, revision and the last crash recovery of the shift history."""
    import sys
    ws = sys.modules.get("webserver")
    if ws is None:
        return ("Schichthistorie", True, "Dashboard nicht geladen")
    try:
        import pulse_shift_history
        return pulse_shift_history.system_check(ws)
    except Exception as exc:
        return ("Schichthistorie", False, f"Prüfung fehlgeschlagen ({type(exc).__name__})")
