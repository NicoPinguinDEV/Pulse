"""Audit-log based protection against bursts of destructive server actions.

Configuration: config.json → "pulse_antinuke", managed at /admin/security (pulse_guard_admin.py) and
normalised by pulse_moderation.normalize_antinuke.

Flow per Discord event: find the matching audit-log entry (who did it?) → skip owner, Pulse and trusted
users/roles → count the actor's actions in the sliding window → at the threshold run the configured
response once per actor and cooldown:

* alert       – only report (alert channel, dashboard notifications, case log)
* quarantine  – remove every role with dangerous permissions the bot can remove; the removed role IDs are
                stored (QUARANTINE_KEY) and can be restored from the dashboard with one click
* kick / ban  – remove the actor from the server ("ban" stays the default for existing installations)

Optionally, a bot added by an untrusted person is kicked right away (kick_unverified_bots).
All state is bounded (RAM limit 250 MB).
"""
from __future__ import annotations

import sqlite3
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from pulse_moderation import (
    ANTINUKE_EVENT_LABELS,
    ANTINUKE_EVENTS,
    antinuke_window_exceeded,
    dangerous_names,
    is_trusted_actor,
    normalize_antinuke,
    roles_to_strip,
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
# channel_update also covers permission overwrites, which Discord logs as separate actions.
EXTRA_AUDIT_ACTIONS = {
    "channel_update": (discord.AuditLogAction.overwrite_create, discord.AuditLogAction.overwrite_update,
                       discord.AuditLogAction.overwrite_delete),
}
ACTION_LABELS = ANTINUKE_EVENT_LABELS
QUARANTINE_KEY = "pulse_antinuke_quarantine"
MAX_QUARANTINE_ENTRIES = 200
RESPONSE_COOLDOWN_SECONDS = 600
MAX_TRACKED_ACTORS = 2000


def _ids(values) -> set[int]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return set()
    return {int(value) for value in values if str(value).isdigit() and int(value) > 0}


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def load_quarantine() -> dict:
    import pulse_db
    data = pulse_db.get_setting(QUARANTINE_KEY, {})
    return data if isinstance(data, dict) else {}


def save_quarantine(data: dict) -> None:
    import pulse_db
    items = sorted(data.items(), key=lambda kv: str(kv[1].get("at", "")) if isinstance(kv[1], dict) else "")
    pulse_db.set_setting(QUARANTINE_KEY, dict(items[-MAX_QUARANTINE_ENTRIES:]))


async def restore_quarantine(guild, user_id: int, reason: str) -> dict:
    """Give the stored roles back (used by the dashboard). {"restored": [...], "missing": [...], "error": str}."""
    data = load_quarantine()
    entry = data.get(str(user_id))
    if not isinstance(entry, dict):
        return {"restored": [], "missing": [], "error": "Für diese Person gibt es keinen Quarantäne-Eintrag."}
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except (discord.NotFound, discord.HTTPException):
            member = None
    if member is None:
        return {"restored": [], "missing": [], "error": "Die Person ist nicht (mehr) auf dem Server."}
    roles, missing = [], []
    for rid in entry.get("role_ids", []):
        role = guild.get_role(int(rid)) if str(rid).isdigit() else None
        (roles if role is not None else missing).append(role if role is not None else rid)
    if roles:
        try:
            await member.add_roles(*roles, reason=reason[:400])
        except discord.Forbidden:
            return {"restored": [], "missing": missing, "error": "Discord hat abgelehnt (Bot-Rolle steht unter den Rollen oder Recht „Rollen verwalten“ fehlt)."}
        except discord.HTTPException as exc:
            return {"restored": [], "missing": missing, "error": f"Discord-Fehler HTTP {exc.status}."}
    data.pop(str(user_id), None)
    save_quarantine(data)
    return {"restored": [getattr(r, "name", str(r.id)) for r in roles], "missing": missing, "error": ""}


class PulseAntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._actor_events: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._seen_entries: dict[int, deque[int]] = defaultdict(lambda: deque(maxlen=1000))
        # (guild, actor) -> monotonic time of the last response; bounded, expires after the cooldown.
        self._responded: OrderedDict[tuple[int, int], float] = OrderedDict()
        self._event_count = 0

    def _config(self) -> dict:
        import webserver
        config = webserver.load_config().get("pulse_antinuke", {})
        return config if isinstance(config, dict) else {}

    async def _find_entry(self, guild: discord.Guild, action: str, target_id: int | None):
        """Oldest not yet counted audit-log entry of the last 20 s for one or several action kinds."""
        wanted = {AUDIT_ACTIONS[a] for a in (action if isinstance(action, (list, tuple, set)) else [action])}
        for name in (action if isinstance(action, (list, tuple, set)) else [action]):
            wanted.update(EXTRA_AUDIT_ACTIONS.get(name, ()))
        single = next(iter(wanted)) if len(wanted) == 1 else None
        try:
            kwargs = {"limit": 10 if single else 25, "after": datetime.now(timezone.utc) - timedelta(seconds=20)}
            if single is not None:
                kwargs["action"] = single
            async for entry in guild.audit_logs(**kwargs):
                if getattr(entry, "action", single) not in wanted:
                    continue
                if entry.id in self._seen_entries[guild.id]:
                    continue  # already counted – keep looking, a burst has several entries in 20 s
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
            await channel.send(text[:1900], allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            print(f"⚠️ Anti-Nuke konnte keinen Alarm in Kanal {channel_id} senden.")
        except discord.HTTPException as exc:
            print(f"⚠️ Anti-Nuke-Alarm fehlgeschlagen: HTTP {exc.status}.")

    def _notify_admins(self, guild, config: dict, title: str, text: str) -> None:
        if not config.get("notify_admins", True):
            return
        try:
            import pulse_db
            for member in getattr(guild, "members", []) or []:
                if getattr(member, "bot", False):
                    continue
                if member.id == guild.owner_id or getattr(member.guild_permissions, "administrator", False):
                    pulse_db.notify(member.id, title, text[:300], "warning", "/admin/security", f"antinuke:{title}", 300)
        except Exception as exc:  # notifications must never stop the protection
            print(f"⚠️ Anti-Nuke-Benachrichtigung fehlgeschlagen: {type(exc).__name__}.")

    def _record(self, title: str, actor_id: int, actor_name: str, payload: dict) -> None:
        try:
            import pulse_db
            pulse_db.record_event(title, "user", actor_id, actor_id, actor_name, payload)
        except (sqlite3.Error, OSError) as exc:
            print(f"⚠️ Anti-Nuke-Ereignis für {actor_id} konnte nicht dauerhaft protokolliert werden: {type(exc).__name__}.")

    def _recently_responded(self, key: tuple[int, int], now: float) -> bool:
        for old_key, at in list(self._responded.items()):
            if now - at <= RESPONSE_COOLDOWN_SECONDS:
                break
            self._responded.pop(old_key, None)
        return key in self._responded

    def _mark_responded(self, key: tuple[int, int], now: float) -> None:
        self._responded.pop(key, None)
        self._responded[key] = now
        while len(self._responded) > MAX_TRACKED_ACTORS:
            self._responded.popitem(last=False)

    def _prune(self, now: float) -> None:
        self._event_count += 1
        if self._event_count % 100:
            return
        for old_key, old_events in list(self._actor_events.items()):
            while old_events and now - old_events[0] > 60:
                old_events.popleft()
            if not old_events:
                self._actor_events.pop(old_key, None)
        while len(self._actor_events) > MAX_TRACKED_ACTORS:
            self._actor_events.pop(next(iter(self._actor_events)))

    async def _handle_event(self, guild: discord.Guild, action, target_id: int | None = None) -> None:
        config = normalize_antinuke(self._config())
        actions = list(action) if isinstance(action, (list, tuple)) else [action]
        actions = [a for a in actions if a in set(config["events"]) & ANTINUKE_EVENTS]
        if not config["enabled"] or not actions:
            return
        bot_member = getattr(guild, "me", None)
        if not getattr(getattr(bot_member, "guild_permissions", None), "view_audit_log", False):
            return

        entry = await self._find_entry(guild, actions if len(actions) > 1 else actions[0], target_id)
        if entry is None:
            return
        seen = self._seen_entries[guild.id]
        if entry.id in seen:
            return
        seen.append(entry.id)
        entry_action = getattr(entry, "action", None)
        action = next((name for name in actions if entry_action == AUDIT_ACTIONS[name]
                       or entry_action in EXTRA_AUDIT_ACTIONS.get(name, ())), actions[0])

        actor = getattr(entry, "user", None)
        if actor is None:
            return
        actor_id = int(actor.id)
        bot_id = int(getattr(self.bot.user, "id", 0) or 0)
        member = guild.get_member(actor_id)
        trusted = is_trusted_actor(
            actor_id,
            owner_id=guild.owner_id,
            bot_id=bot_id,
            role_ids=[role.id for role in getattr(member, "roles", [])],
            trusted_user_ids=config["trusted_user_ids"],
            trusted_role_ids=config["trusted_role_ids"],
        )
        self._record("Anti-Nuke-Aktion erkannt", actor_id, str(actor), {
            "guild_id": guild.id, "action": action, "target_id": target_id or getattr(getattr(entry, "target", None), "id", None),
            "audit_entry_id": entry.id, "trusted": trusted,
        })
        if trusted:
            return

        if action == "bot_add" and config["kick_unverified_bots"]:
            await self._kick_added_bot(guild, config, entry, actor_id)

        key = (guild.id, actor_id)
        now = time.monotonic()
        limit, window = config["action_limit"], config["window_seconds"]
        events = self._actor_events[key]
        triggered = antinuke_window_exceeded(events, now, limit=limit, window_seconds=window)
        self._prune(now)

        if not triggered:
            if len(events) == limit - 1:
                await self._alert(
                    guild, config,
                    f"⚠️ Anti-Nuke: <@{actor_id}> (`{actor_id}`) hat {len(events)}/{limit} Aktionen "
                    f"(zuletzt: {ACTION_LABELS[action]}) innerhalb von {window} s ausgelöst.",
                )
            return
        if self._recently_responded(key, now):
            return
        self._mark_responded(key, now)
        await self._respond(guild, config, actor, member, action)

    async def _kick_added_bot(self, guild, config: dict, entry, actor_id: int) -> None:
        target = getattr(entry, "target", None)
        bot_member = guild.get_member(getattr(target, "id", 0) or 0)
        if bot_member is None or not getattr(bot_member, "bot", False):
            return
        try:
            await bot_member.kick(reason=f"Pulse Anti-Nuke: Bot von nicht freigegebener Person ({actor_id}) hinzugefügt")
            self._record("Anti-Nuke: Bot entfernt", actor_id, str(getattr(entry, "user", actor_id)),
                         {"guild_id": guild.id, "bot_id": bot_member.id})
            await self._alert(guild, config, f"🤖 Anti-Nuke: Bot `{bot_member}` wurde entfernt – hinzugefügt von <@{actor_id}>, "
                                             "die/der nicht freigegeben ist.")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await self._alert(guild, config, f"⚠️ Anti-Nuke: Hinzugefügter Bot `{bot_member}` konnte nicht entfernt werden ({type(exc).__name__}).")

    async def _respond(self, guild, config: dict, actor, member, action: str) -> None:
        actor_id = int(actor.id)
        limit, window = config["action_limit"], config["window_seconds"]
        response = config["response"]
        bot_member = getattr(guild, "me", None)
        bot_perms = getattr(bot_member, "guild_permissions", None)
        headline = f"🚨 Anti-Nuke-Schwelle erreicht: {limit} Aktionen in {window} s durch <@{actor_id}> (`{actor_id}`)"
        self._notify_admins(guild, config, "🚨 Anti-Nuke ausgelöst", f"{limit} destruktive Aktionen in {window} s durch {actor} – Reaktion: {response}.")
        payload = {"guild_id": guild.id, "action_limit": limit, "window_seconds": window, "response": response, "last_action": action}

        if response == "alert":
            self._record("Anti-Nuke ausgelöst", actor_id, str(actor), {**payload, "result": "nur gemeldet"})
            await self._alert(guild, config, headline + ". Reaktion: **nur melden** – bitte sofort prüfen.")
            return
        if member is not None and bot_member is not None and member.top_role >= bot_member.top_role and response != "ban":
            self._record("Anti-Nuke ausgelöst", actor_id, str(actor), {**payload, "result": "Rollenhierarchie"})
            await self._alert(guild, config, headline + ". Pulse kann nicht eingreifen: die Person steht in der Rollenhierarchie "
                                                       "über oder gleich der Bot-Rolle.")
            return

        if response == "quarantine":
            await self._quarantine(guild, config, actor, member, headline, payload)
            return

        reason = f"Pulse Anti-Nuke: {limit} destruktive Serveraktionen innerhalb von {window} Sekunden"
        needed = "ban_members" if response == "ban" else "kick_members"
        if not getattr(bot_perms, needed, False):
            label = "Mitglieder bannen" if response == "ban" else "Mitglieder kicken"
            print(f"⚠️ Anti-Nuke konnte den Akteur nicht entfernen: dem Bot fehlt „{label}“.")
            self._record("Anti-Nuke ausgelöst", actor_id, str(actor), {**payload, "result": f"Recht fehlt: {label}"})
            verb = "gesperrt" if response == "ban" else "gekickt"
            await self._alert(guild, config, headline + f". Die Person konnte nicht {verb} werden: Bot-Berechtigung „{label}“ fehlt.")
            return
        if actor_id == guild.owner_id or actor_id == int(getattr(self.bot.user, "id", 0) or 0):
            return
        if member is not None and bot_member is not None and member.top_role >= bot_member.top_role:
            print(f"⚠️ Anti-Nuke konnte {actor_id} wegen der Discord-Rollenhierarchie nicht sperren.")
            self._record("Anti-Nuke ausgelöst", actor_id, str(actor), {**payload, "result": "Rollenhierarchie"})
            await self._alert(guild, config, headline + ". Sperre nicht möglich: Rollenhierarchie.")
            return
        verb = "Sperrversuch" if response == "ban" else "Kick-Versuch"
        await self._alert(guild, config, headline + f". {verb} läuft.")
        try:
            if response == "ban":
                await guild.ban(actor, reason=reason, delete_message_seconds=0)
                result = "gesperrt"
            else:
                await guild.kick(actor, reason=reason)
                result = "gekickt"
            self._record("Anti-Nuke: Akteur gesperrt" if response == "ban" else "Anti-Nuke: Akteur gekickt",
                         actor_id, "Pulse Anti-Nuke", {**payload, "result": result})
            await self._alert(guild, config, f"✅ Anti-Nuke: <@{actor_id}> (`{actor_id}`) wurde {result}.")
        except discord.Forbidden:
            print(f"⚠️ Anti-Nuke konnte {actor_id} nicht entfernen: Berechtigung oder Rollenhierarchie.")
            self._record("Anti-Nuke ausgelöst", actor_id, str(actor), {**payload, "result": "von Discord abgelehnt"})
            await self._alert(guild, config, f"🚨 Anti-Nuke-Maßnahme für `{actor_id}` wurde von Discord abgelehnt.")
        except discord.HTTPException as exc:
            print(f"⚠️ Anti-Nuke-Maßnahme für {actor_id} fehlgeschlagen: HTTP {exc.status}.")
            self._record("Anti-Nuke ausgelöst", actor_id, str(actor), {**payload, "result": f"HTTP {exc.status}"})
            await self._alert(guild, config, f"🚨 Anti-Nuke-Maßnahme für `{actor_id}` fehlgeschlagen (HTTP {exc.status}).")

    async def _quarantine(self, guild, config: dict, actor, member, headline: str, payload: dict) -> None:
        actor_id = int(actor.id)
        bot_member = getattr(guild, "me", None)
        if member is None:
            self._record("Anti-Nuke ausgelöst", actor_id, str(actor), {**payload, "result": "nicht mehr auf dem Server"})
            await self._alert(guild, config, headline + ". Die Person ist nicht mehr auf dem Server.")
            return
        if not getattr(getattr(bot_member, "guild_permissions", None), "manage_roles", False):
            self._record("Anti-Nuke ausgelöst", actor_id, str(actor), {**payload, "result": "Recht fehlt: Rollen verwalten"})
            await self._alert(guild, config, headline + ". Quarantäne nicht möglich: Bot-Berechtigung „Rollen verwalten“ fehlt.")
            return
        strip = roles_to_strip(member.roles, getattr(getattr(bot_member, "top_role", None), "position", 0))
        if not strip:
            self._record("Anti-Nuke ausgelöst", actor_id, str(actor), {**payload, "result": "keine entziehbaren Rollen"})
            await self._alert(guild, config, headline + ". Keine gefährliche Rolle unterhalb der Bot-Rolle gefunden – "
                                                       "bitte Rechte manuell prüfen.")
            return
        try:
            await member.remove_roles(*strip, reason="Pulse Anti-Nuke: Quarantäne nach destruktiven Serveraktionen")
        except (discord.Forbidden, discord.HTTPException) as exc:
            self._record("Anti-Nuke ausgelöst", actor_id, str(actor), {**payload, "result": f"Quarantäne fehlgeschlagen ({type(exc).__name__})"})
            await self._alert(guild, config, headline + f". Quarantäne fehlgeschlagen ({type(exc).__name__}).")
            return
        data = load_quarantine()
        previous = data.get(str(actor_id), {}) if isinstance(data.get(str(actor_id)), dict) else {}
        role_ids = list(dict.fromkeys([*previous.get("role_ids", []), *[r.id for r in strip]]))
        data[str(actor_id)] = {
            "name": str(actor)[:80], "role_ids": role_ids, "role_names": [r.name[:60] for r in strip],
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "last_action": payload.get("last_action"),
            "permissions": sorted({p for r in strip for p in dangerous_names(r.permissions)}),
        }
        save_quarantine(data)
        self._record("Anti-Nuke: Quarantäne", actor_id, "Pulse Anti-Nuke", {**payload, "result": "Quarantäne",
                                                                          "role_ids": [r.id for r in strip]})
        names = ", ".join(r.name for r in strip)[:600]
        await self._alert(guild, config, headline + f". ✅ **Quarantäne:** entzogen wurden {names}. "
                                                   "Wiederherstellen: Dashboard → Verwaltung → Sicherheit.")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await self._handle_event(guild, "ban", user.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # A normal leave has no kick entry in the audit log and is therefore ignored.
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
        # One audit-log request for all three webhook kinds (before: three requests per event).
        await self._handle_event(channel.guild, ["webhook_create", "webhook_delete", "webhook_update"])


async def setup(bot: commands.Bot):
    await bot.add_cog(PulseAntiNuke(bot))
