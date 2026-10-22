"""Audit-log based protection against bursts of destructive server actions."""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from pulse_moderation import (
    ANTINUKE_ACTION_LIMIT,
    ANTINUKE_EVENTS,
    ANTINUKE_WINDOW_SECONDS,
    antinuke_window_exceeded,
    is_trusted_actor,
)

AUDIT_ACTIONS = {
    "ban": discord.AuditLogAction.ban,
    "kick": discord.AuditLogAction.kick,
    "role_delete": discord.AuditLogAction.role_delete,
    "role_update": discord.AuditLogAction.role_update,
    "channel_delete": discord.AuditLogAction.channel_delete,
    "channel_update": discord.AuditLogAction.channel_update,
    "webhook_create": discord.AuditLogAction.webhook_create,
    "webhook_delete": discord.AuditLogAction.webhook_delete,
    "webhook_update": discord.AuditLogAction.webhook_update,
    "bot_add": discord.AuditLogAction.bot_add,
}

ACTION_LABELS = {
    "ban": "Ban",
    "kick": "Kick",
    "role_delete": "Rolle gelöscht",
    "role_update": "Rolle geändert",
    "channel_delete": "Kanal gelöscht",
    "channel_update": "Kanal geändert",
    "webhook_create": "Webhook erstellt",
    "webhook_delete": "Webhook gelöscht",
    "webhook_update": "Webhook geändert",
    "bot_add": "Bot hinzugefügt",
}


def _ids(values) -> set[int]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return set()
    return {int(value) for value in values if str(value).isdigit() and int(value) > 0}


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


class PulseAntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._actor_events: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._seen_entries: dict[int, deque[int]] = defaultdict(lambda: deque(maxlen=1000))
        self._punished: dict[int, set[int]] = defaultdict(set)
        self._event_count = 0

    def _config(self) -> dict:
        import webserver
        config = webserver.load_config().get("pulse_antinuke", {})
        return config if isinstance(config, dict) else {}

    async def _find_entry(self, guild: discord.Guild, action: str, target_id: int | None):
        try:
            async for entry in guild.audit_logs(
                limit=10,
                after=datetime.now(timezone.utc) - timedelta(seconds=20),
                action=AUDIT_ACTIONS[action],
            ):
                target = getattr(entry, "target", None)
                entry_target_id = getattr(target, "id", None)
                if target_id is None or entry_target_id == target_id:
                    return entry
        except discord.Forbidden:
            print("⚠️ Anti-Nuke kann Discord-Audit-Logs nicht lesen: dem Bot fehlt „Audit-Log ansehen“.")
        except discord.HTTPException as exc:
            print(f"⚠️ Anti-Nuke-Audit-Log-Abfrage für {action} fehlgeschlagen: HTTP {exc.status}.")
        return None

    async def _alert(self, guild: discord.Guild, config: dict, text: str) -> None:
        channel_id = _bounded_int(config.get("alert_channel_id"), 0, 0, 2**63 - 1)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            print(f"🛡️ Anti-Nuke · Server {guild.id}: {text}")
            return
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            print(f"⚠️ Anti-Nuke konnte keinen Alarm in Kanal {channel_id} senden.")
        except discord.HTTPException as exc:
            print(f"⚠️ Anti-Nuke-Alarm fehlgeschlagen: HTTP {exc.status}.")

    async def _handle_event(self, guild: discord.Guild, action: str, target_id: int | None = None) -> None:
        config = self._config()
        configured_events = config.get("events", [])
        enabled_events = set(configured_events) if isinstance(configured_events, list) else set()
        if not config.get("enabled") or action not in (enabled_events & ANTINUKE_EVENTS):
            return
        bot_member = getattr(guild, "me", None)
        if not getattr(getattr(bot_member, "guild_permissions", None), "view_audit_log", False):
            return

        entry = await self._find_entry(guild, action, target_id)
        if entry is None:
            return
        seen = self._seen_entries[guild.id]
        if entry.id in seen:
            return
        seen.append(entry.id)

        actor = getattr(entry, "user", None)
        if actor is None:
            return
        actor_id = int(actor.id)
        try:
            import pulse_db
            pulse_db.record_event(
                "Anti-Nuke-Aktion erkannt",
                "user",
                actor_id,
                actor_id,
                str(actor),
                {
                    "guild_id": guild.id,
                    "action": action,
                    "target_id": target_id,
                    "audit_entry_id": entry.id,
                },
            )
        except (sqlite3.Error, OSError) as exc:
            print(f"⚠️ Anti-Nuke-Aktion von {actor_id} konnte nicht dauerhaft protokolliert werden: {type(exc).__name__}.")
        member = guild.get_member(actor_id)
        trusted_roles = _ids(config.get("trusted_role_ids"))
        if is_trusted_actor(
            actor_id,
            owner_id=guild.owner_id,
            bot_id=int(getattr(self.bot.user, "id", 0) or 0),
            role_ids=[role.id for role in getattr(member, "roles", [])],
            trusted_user_ids=_ids(config.get("trusted_user_ids")),
            trusted_role_ids=trusted_roles,
        ):
            return

        key = (guild.id, actor_id)
        now = time.monotonic()
        window = _bounded_int(config.get("window_seconds"), ANTINUKE_WINDOW_SECONDS, 5, 60)
        limit = _bounded_int(config.get("action_limit"), ANTINUKE_ACTION_LIMIT, 2, 10)
        events = self._actor_events[key]
        triggered = antinuke_window_exceeded(events, now, limit=limit, window_seconds=window)
        self._event_count += 1
        if self._event_count % 100 == 0:
            for old_key, old_events in list(self._actor_events.items()):
                while old_events and now - old_events[0] > 60:
                    old_events.popleft()
                if not old_events:
                    self._actor_events.pop(old_key, None)
            while len(self._actor_events) > 2000:
                self._actor_events.pop(next(iter(self._actor_events)))

        if not triggered:
            if len(events) == limit - 1:
                await self._alert(
                    guild, config,
                    f"⚠️ Anti-Nuke: Akteur-ID `{actor_id}` hat {len(events)}/{limit} Aktionen "
                    f"(zuletzt: {ACTION_LABELS[action]}) "
                    f"innerhalb von {window} s ausgelöst.",
                )
            return
        if actor_id in self._punished[guild.id]:
            return

        self._punished[guild.id].add(actor_id)
        if len(self._punished[guild.id]) > 2000:
            self._punished[guild.id].pop()
        reason = f"Pulse Anti-Nuke: {limit} destruktive Serveraktionen innerhalb von {window} Sekunden"
        if not getattr(getattr(bot_member, "guild_permissions", None), "ban_members", False):
            print("⚠️ Anti-Nuke konnte den Akteur nicht sperren: dem Bot fehlt „Mitglieder bannen“.")
            await self._alert(
                guild, config,
                f"🚨 Anti-Nuke-Schwelle erreicht: {limit} Aktionen in {window} s. "
                f"Akteur-ID `{actor_id}` konnte nicht gesperrt werden: Bot-Berechtigung „Mitglieder bannen“ fehlt.",
            )
            return
        if actor_id == guild.owner_id or actor_id == int(getattr(self.bot.user, "id", 0) or 0):
            return
        if member is not None and member.top_role >= bot_member.top_role:
            print(f"⚠️ Anti-Nuke konnte {actor_id} wegen der Discord-Rollenhierarchie nicht sperren.")
            await self._alert(
                guild, config,
                f"🚨 Anti-Nuke-Schwelle erreicht: Akteur-ID `{actor_id}` konnte wegen der Rollenhierarchie nicht gesperrt werden.",
            )
            return
        await self._alert(
            guild, config,
            f"🚨 Anti-Nuke-Schwelle erreicht: {limit} Aktionen in {window} s. "
            f"Sperrversuch für Akteur-ID `{actor_id}` läuft.",
        )
        try:
            await guild.ban(actor, reason=reason, delete_message_seconds=0)
            try:
                import pulse_db
                pulse_db.record_event(
                    "Anti-Nuke: Akteur gesperrt",
                    "user",
                    actor_id,
                    int(getattr(self.bot.user, "id", 0) or 0),
                    "Pulse Anti-Nuke",
                    {"guild_id": guild.id, "action_limit": limit, "window_seconds": window},
                )
            except (sqlite3.Error, OSError) as exc:
                print(f"⚠️ Anti-Nuke-Sperre für {actor_id} konnte nicht dauerhaft protokolliert werden: {type(exc).__name__}.")
            await self._alert(guild, config, f"✅ Anti-Nuke: Akteur-ID `{actor_id}` wurde gesperrt.")
        except discord.Forbidden:
            print(f"⚠️ Anti-Nuke konnte {actor_id} nicht sperren: Berechtigung oder Rollenhierarchie.")
            await self._alert(guild, config, f"🚨 Anti-Nuke-Sperre für Akteur-ID `{actor_id}` wurde von Discord abgelehnt.")
        except discord.HTTPException as exc:
            print(f"⚠️ Anti-Nuke-Sperre für {actor_id} fehlgeschlagen: HTTP {exc.status}.")
            await self._alert(guild, config, f"🚨 Anti-Nuke-Sperre für Akteur-ID `{actor_id}` fehlgeschlagen (HTTP {exc.status}).")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await self._handle_event(guild, "ban", user.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._handle_event(member.guild, "kick", member.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            await self._handle_event(member.guild, "bot_add", member.id)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._handle_event(role.guild, "role_delete", role.id)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        if before.permissions == after.permissions and before.position == after.position:
            return
        await self._handle_event(after.guild, "role_update", after.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await self._handle_event(channel.guild, "channel_delete", channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        if before.overwrites != after.overwrites:
            await self._handle_event(after.guild, "channel_update", after.id)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        for action in ("webhook_create", "webhook_delete", "webhook_update"):
            await self._handle_event(channel.guild, action)


async def setup(bot: commands.Bot):
    await bot.add_cog(PulseAntiNuke(bot))
