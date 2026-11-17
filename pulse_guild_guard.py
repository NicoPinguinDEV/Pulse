"""Guild allowlist for Pulse's bot actions (audit finding DISC-8).

Slash commands are global and the permission model derives ``is_admin`` from the Discord administrator right
*in the guild where the command runs*. If the bot sits in a second server (the Recovery/backup server, a test
server, a stranger's server after an invitation), an administrator over there could otherwise run Pulse
commands and change settings that are global to this installation, and AutoMod/Anti-Nuke would act there with
the home server's configuration.

Allowed guilds: the home server (``DISCORD_GUILD_ID`` / pulse_ids), the Recovery backup server **when it is
configured** and any ids listed in ``PULSE_ALLOWED_GUILD_IDS`` (comma separated). Direct messages are not
affected. If the home server id cannot be determined the guard does not block anything (never brick the bot).
Nothing here leaves a server or changes Discord state - it only decides whether Pulse acts.
"""

from __future__ import annotations

import os


def _home_guild_id() -> int:
    try:
        raw = os.getenv("DISCORD_GUILD_ID")
        if raw:
            return int(raw)
        import pulse_ids
        return int(pulse_ids.GUILD_ID)
    except (TypeError, ValueError, ImportError):
        return 0


def _env_parts() -> list[str]:
    return [p.strip() for p in str(os.getenv("PULSE_ALLOWED_GUILD_IDS", "")).replace(";", ",").split(",") if p.strip()]


def _env_ids() -> set[int]:
    return {int(part) for part in _env_parts() if part.isdigit()}


def allowlist_disabled() -> bool:
    """``PULSE_ALLOWED_GUILD_IDS=*`` is an explicit operator opt-out (development/tests); never a default."""
    return "*" in _env_parts()


def _recovery_backup_id() -> int:
    try:
        import pulse_recovery
        value = str(pulse_recovery.load_settings().get("backup_guild_id") or "")
        return int(value) if value.isdigit() else 0
    except Exception:  # noqa: BLE001 - a settings/database problem must not decide about allowed guilds
        return 0


def allowed_guild_ids() -> set[int]:
    allowed = {_home_guild_id(), _recovery_backup_id()} | _env_ids()
    allowed.discard(0)
    return allowed


def is_allowed_guild(guild_id) -> bool:
    """True for allowed guilds and for non-guild contexts (DMs); True when no allowlist can be built."""
    if guild_id in (None, 0, "", "0") or allowlist_disabled():
        return True
    allowed = allowed_guild_ids()
    if not allowed:
        return True
    try:
        return int(guild_id) in allowed
    except (TypeError, ValueError):
        return False
