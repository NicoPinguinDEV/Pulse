"""Audit-log based protection against bursts of destructive server actions.

Configuration: config.json → "pulse_antinuke", managed at /admin/security (pulse_guard_admin.py) and
normalised by pulse_moderation.normalize_antinuke (the dashboard shows exactly what the bot enforces).

Flow per Discord event: find the matching audit-log entry (who did it?) → every entry is counted once →
skip owner, Pulse and trusted users/roles → for every rule that watches the event: count the actor's actions
in that rule's sliding window (own counter per guild, rule and actor) → at the threshold run the rule's action
chain once per actor and cooldown.

Rules (pulse_moderation.effective_rules): the "globale Standardregel" is the pre-7.18 behaviour (all globally
monitored events share one counter, action_limit/window_seconds/response); individual rules take over the
events they watch. Actions come from a fixed allow-list and always run in a safe order:

* log / alert / notify_role / notify_admins – report only
* lockdown   – deny @everyone writing in the configured channels; previous overwrites are stored and can be
               restored from the dashboard (LOCKDOWN_KEY)
* quarantine – remove dangerous (or explicitly configured) roles; restorable from the dashboard
* kick / ban – remove the actor from the server

Mode "alert" (and a rule in test mode) only reports what would have happened. A failed step never stops the
remaining steps and is reported as failed, never as done. Deleted channels/roles are NOT restored – Discord
reports the action after it happened.

Optionally, a bot added by an untrusted person is kicked right away (kick_unverified_bots).
All state is bounded (RAM limit 250 MB).
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

import pulse_moderation as pm
from pulse_moderation import (
    ANTINUKE_EVENT_LABELS,
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
    "channel_create": discord.AuditLogAction.channel_create,
    "channel_settings": discord.AuditLogAction.channel_update,
    "role_create": discord.AuditLogAction.role_create,
    "member_role_grant": discord.AuditLogAction.member_role_update,
    "guild_update": discord.AuditLogAction.guild_update,
}
# channel_update also covers permission overwrites, which Discord logs as separate actions.
EXTRA_AUDIT_ACTIONS = {
    "channel_update": (discord.AuditLogAction.overwrite_create, discord.AuditLogAction.overwrite_update,
                       discord.AuditLogAction.overwrite_delete),
}
ACTION_LABELS = ANTINUKE_EVENT_LABELS
QUARANTINE_KEY = "pulse_antinuke_quarantine"
LOCKDOWN_KEY = "pulse_antinuke_lockdown"
MAX_QUARANTINE_ENTRIES = 200
RESPONSE_COOLDOWN_SECONDS = 600  # default of rule_cooldown_seconds
MAX_TRACKED_ACTORS = 2000
# Permissions a lockdown denies for @everyone (text and voice separately – Discord rejects nothing, but
# only these are meaningful per channel type).
LOCK_TEXT_PERMS = ("send_messages", "send_messages_in_threads", "create_public_threads", "add_reactions")
LOCK_VOICE_PERMS = ("connect", "speak")
LOCK_EDIT_PAUSE = 0.25  # seconds between channel edits (rate limits)
GUILD_CRITICAL_FIELDS = ("name", "verification_level", "explicit_content_filter", "mfa_level")
CHANNEL_CRITICAL_FIELDS = ("category_id", "nsfw", "permissions_synced")


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




def load_lockdown() -> dict:
    import pulse_db
    data = pulse_db.get_setting(LOCKDOWN_KEY, {})
    return data if isinstance(data, dict) else {}


def save_lockdown(data: dict) -> None:
    import pulse_db
    pulse_db.set_setting(LOCKDOWN_KEY, data)


def _lock_perms(channel) -> tuple[str, ...]:
    kind = str(getattr(channel, "type", ""))
    return LOCK_VOICE_PERMS if kind in ("voice", "stage_voice") else LOCK_TEXT_PERMS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def start_lockdown(guild, channel_ids, *, reason: str, by: str) -> dict:
    """Deny @everyone writing/connecting in the given channels. {"status": ok|partial|failed|skipped, "detail": str}.

    Reversible: the previous @everyone overwrite values are stored per channel; lift_lockdown restores them.
    Only one lockdown is active at a time, so a second trigger never overwrites the stored originals.
    """
    state = load_lockdown()
    if state.get("active"):
        return {"status": "skipped", "detail": "Lockdown ist bereits aktiv."}
    ids = [int(c) for c in channel_ids or [] if str(c).isdigit()][:pm.MAX_LOCKDOWN_CHANNELS]
    if not ids:
        return {"status": "failed", "detail": "Lockdown nicht möglich: keine Lockdown-Kanäle konfiguriert."}
    me = getattr(guild, "me", None)
    if not getattr(getattr(me, "guild_permissions", None), "manage_roles", False):
        return {"status": "failed", "detail": "Lockdown nicht möglich: Bot-Berechtigung „Rollen verwalten“ (Berechtigungen) fehlt."}
    everyone = getattr(guild, "default_role", None)
    if everyone is None:
        return {"status": "failed", "detail": "Lockdown nicht möglich: @everyone-Rolle nicht gefunden."}
    locked, failed = {}, []
    for cid in ids:
        channel = guild.get_channel(cid) if hasattr(guild, "get_channel") else None
        if channel is None:
            failed.append(f"{cid} (nicht gefunden)")
            continue
        overwrite = channel.overwrites_for(everyone)
        perms = _lock_perms(channel)
        previous = {name: getattr(overwrite, name, None) for name in perms}
        for name in perms:
            setattr(overwrite, name, False)
        try:
            await channel.set_permissions(everyone, overwrite=overwrite, reason=reason[:400])
            locked[str(cid)] = {"name": str(getattr(channel, "name", cid))[:80], "previous": previous}
        except discord.Forbidden:
            failed.append(f"#{getattr(channel, 'name', cid)} (von Discord abgelehnt)")
        except discord.HTTPException as exc:
            failed.append(f"#{getattr(channel, 'name', cid)} (HTTP {exc.status})")
        await asyncio.sleep(LOCK_EDIT_PAUSE)  # stay well below Discord's channel-edit rate limit
    if not locked:
        return {"status": "failed", "detail": "Lockdown fehlgeschlagen: " + ", ".join(failed)[:500]}
    save_lockdown({"active": True, "at": _now_iso(), "by": by[:80], "reason": reason[:300],
                   "channels": locked, "failed": failed[:pm.MAX_LOCKDOWN_CHANNELS]})
    detail = f"{len(locked)} Kanal/Kanäle gesperrt" + (f", fehlgeschlagen: {', '.join(failed)}"[:500] if failed else "")
    return {"status": "partial" if failed else "ok", "detail": detail + ". Aufheben: Dashboard → Verwaltung → Sicherheit."}


async def lift_lockdown(guild, *, by: str) -> dict:
    """Restore the stored @everyone overwrites. Channels that fail stay in the state for another try."""
    state = load_lockdown()
    if not state.get("active"):
        return {"restored": [], "failed": [], "error": "Es ist kein Lockdown aktiv."}
    everyone = getattr(guild, "default_role", None)
    restored, failed, remaining = [], [], {}
    for cid, entry in (state.get("channels") or {}).items():
        entry = entry if isinstance(entry, dict) else {}
        channel = guild.get_channel(int(cid)) if str(cid).isdigit() else None
        name = entry.get("name", cid)
        if channel is None or everyone is None:
            failed.append(f"#{name} (nicht mehr vorhanden)")  # nothing left to restore
            continue
        overwrite = channel.overwrites_for(everyone)
        for perm, value in (entry.get("previous") or {}).items():
            if perm in LOCK_TEXT_PERMS + LOCK_VOICE_PERMS and value in (True, False, None):
                setattr(overwrite, perm, value)
        try:
            await channel.set_permissions(everyone, overwrite=None if overwrite.is_empty() else overwrite,
                                          reason=f"Pulse Anti-Nuke: Lockdown aufgehoben durch {by}"[:400])
            restored.append(f"#{name}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            failed.append(f"#{name} ({type(exc).__name__})")
            remaining[cid] = entry
        await asyncio.sleep(LOCK_EDIT_PAUSE)
    if remaining:
        save_lockdown({**state, "channels": remaining})
    else:
        save_lockdown({"active": False, "lifted_at": _now_iso(), "lifted_by": by[:80], "previous_at": state.get("at", "")})
    return {"restored": restored, "failed": failed, "error": ""}


class PulseAntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild, rule, actor) -> timestamps inside the rule window; bounded per key (threshold) and in total.
        self._rule_events: dict[tuple[int, str, int], deque[float]] = {}
        self._seen_entries: dict[int, deque[int]] = {}
        # ("rule"|"action", guild, rule|action, actor) -> monotonic expiry; prevents repeated chains and double bans.
        self._cooldowns: OrderedDict[tuple, float] = OrderedDict()
        self._event_count = 0

    def _config(self) -> dict:
        import webserver
        config = webserver.load_config().get("pulse_antinuke", {})
        return config if isinstance(config, dict) else {}

    def _seen(self, guild_id: int) -> deque[int]:
        seen = self._seen_entries.get(guild_id)
        if seen is None:
            seen = self._seen_entries[guild_id] = deque(maxlen=1000)
        return seen

    async def _find_entry(self, guild: discord.Guild, action, target_id: int | None):
        """Oldest not yet counted audit-log entry of the last 20 s for one or several action kinds."""
        names = list(action) if isinstance(action, (list, tuple, set)) else [action]
        wanted = {AUDIT_ACTIONS[a] for a in names}
        for name in names:
            wanted.update(EXTRA_AUDIT_ACTIONS.get(name, ()))
        single = next(iter(wanted)) if len(wanted) == 1 else None
        try:
            kwargs = {"limit": 10 if single else 25, "after": datetime.now(timezone.utc) - timedelta(seconds=20)}
            if single is not None:
                kwargs["action"] = single
            async for entry in guild.audit_logs(**kwargs):
                if getattr(entry, "action", single) not in wanted:
                    continue
                if entry.id in self._seen(guild.id):
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

    async def _alert(self, guild: discord.Guild, config: dict, text: str, ping_role_id: int = 0) -> bool:
        channel_id = _bounded_int(config.get("alert_channel_id"), 0, 0, 2**63 - 1)
        channel = guild.get_channel(channel_id) if channel_id and hasattr(guild, "get_channel") else None
        if channel is None:
            print(f"🛡️ Anti-Nuke · Server {guild.id}: {text}")
            return False
        mentions = discord.AllowedMentions.none()
        if ping_role_id:
            text = f"<@&{int(ping_role_id)}> {text}"
            mentions = discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(id=int(ping_role_id))])
        try:
            await channel.send(text[:1900], allowed_mentions=mentions)
            return True
        except discord.Forbidden:
            print(f"⚠️ Anti-Nuke konnte keinen Alarm in Kanal {channel_id} senden.")
        except discord.HTTPException as exc:
            print(f"⚠️ Anti-Nuke-Alarm fehlgeschlagen: HTTP {exc.status}.")
        return False

    def _notify_admins(self, guild, config: dict, title: str, text: str, *, force: bool = False) -> int:
        if not force and not config.get("notify_admins", True):
            return 0
        sent = 0
        try:
            import pulse_db
            for member in getattr(guild, "members", []) or []:
                if getattr(member, "bot", False):
                    continue
                if member.id == guild.owner_id or getattr(member.guild_permissions, "administrator", False):
                    pulse_db.notify(member.id, title, text[:300], "warning", "/admin/security", f"antinuke:{title}", 300)
                    sent += 1
        except Exception as exc:  # notifications must never stop the protection
            print(f"⚠️ Anti-Nuke-Benachrichtigung fehlgeschlagen: {type(exc).__name__}.")
        return sent

    def _record(self, title: str, actor_id: int, actor_name: str, payload: dict) -> None:
        try:
            import pulse_db
            pulse_db.record_event(title, "user", actor_id, actor_id, actor_name, payload)
        except (sqlite3.Error, OSError) as exc:
            print(f"⚠️ Anti-Nuke-Ereignis für {actor_id} konnte nicht dauerhaft protokolliert werden: {type(exc).__name__}.")

    async def _dispatch(self, trigger: str, context: dict) -> None:
        """Hand the event to the existing automation engine (pulse_ultimate); never breaks the protection."""
        try:
            import pulse_ultimate
            await pulse_ultimate.dispatch_event(self.bot, trigger, context)
        except Exception as exc:
            print(f"⚠️ Automationen für {trigger} konnten nicht ausgeführt werden: {type(exc).__name__}.")

    # ---------------------------------------------------------------- bounded state
    def _cooldown_active(self, key: tuple, now: float) -> bool:
        expires = self._cooldowns.get(key)
        if expires is None:
            return False
        if expires <= now:
            self._cooldowns.pop(key, None)
            return False
        return True

    def _set_cooldown(self, key: tuple, now: float, seconds: int) -> None:
        self._cooldowns.pop(key, None)
        self._cooldowns[key] = now + max(0, int(seconds))
        if len(self._cooldowns) > MAX_TRACKED_ACTORS:
            for old_key, expires in list(self._cooldowns.items()):
                if expires <= now:
                    self._cooldowns.pop(old_key, None)
            while len(self._cooldowns) > MAX_TRACKED_ACTORS:
                self._cooldowns.popitem(last=False)

    def _prune(self, now: float) -> None:
        self._event_count += 1
        if self._event_count % 100 and len(self._rule_events) <= MAX_TRACKED_ACTORS:
            return
        for old_key, old_events in list(self._rule_events.items()):
            while old_events and now - old_events[0] > pm.RULE_MAX_WINDOW:
                old_events.popleft()
            if not old_events:
                self._rule_events.pop(old_key, None)
        while len(self._rule_events) > MAX_TRACKED_ACTORS:
            self._rule_events.pop(next(iter(self._rule_events)))

    # ---------------------------------------------------------------- event handling
    async def _handle_event(self, guild: discord.Guild, action, target_id: int | None = None) -> None:
        try:
            config = normalize_antinuke(self._config())
            rules = pm.effective_rules(config)
            monitored = pm.monitored_events(config)
        except Exception as exc:  # a broken config must never crash the event listeners
            print(f"⚠️ Anti-Nuke-Konfiguration nicht lesbar: {type(exc).__name__}.")
            return
        if not config["enabled"]:
            return
        candidates = list(action) if isinstance(action, (list, tuple)) else [action]
        candidates = [a for a in candidates if a in monitored and a in AUDIT_ACTIONS]
        if not candidates:
            return
        bot_member = getattr(guild, "me", None)
        if not getattr(getattr(bot_member, "guild_permissions", None), "view_audit_log", False):
            return

        entry = await self._find_entry(guild, candidates if len(candidates) > 1 else candidates[0], target_id)
        if entry is None:
            return
        seen = self._seen(guild.id)
        if entry.id in seen:
            return
        seen.append(entry.id)  # every audit-log entry is counted exactly once
        entry_action = getattr(entry, "action", None)
        keys = [name for name in candidates if entry_action is None or entry_action == AUDIT_ACTIONS[name]
                or entry_action in EXTRA_AUDIT_ACTIONS.get(name, ())] or candidates[:1]
        action = keys[0]

        actor = getattr(entry, "user", None)
        if actor is None:
            return
        actor_id = int(actor.id)
        bot_id = int(getattr(self.bot.user, "id", 0) or 0)
        member = guild.get_member(actor_id)
        role_ids = [role.id for role in getattr(member, "roles", []) or []]
        trusted = is_trusted_actor(
            actor_id,
            owner_id=guild.owner_id,
            bot_id=bot_id,
            role_ids=role_ids,
            trusted_user_ids=config["trusted_user_ids"],
            trusted_role_ids=config["trusted_role_ids"],
        )
        target = target_id or getattr(getattr(entry, "target", None), "id", None)
        self._record("Anti-Nuke-Aktion erkannt", actor_id, str(actor), {
            "guild_id": guild.id, "action": action, "events": keys, "target_id": target,
            "audit_entry_id": entry.id, "trusted": trusted,
        })
        if trusted:
            return
        await self._dispatch("antinuke_event", {"events": keys, "actor_id": actor_id, "actor_name": str(actor)[:80],
                                                "target_id": target, "audit_entry_id": entry.id})

        if "bot_add" in keys and config["kick_unverified_bots"]:
            await self._kick_added_bot(guild, config, entry, actor_id)

        now = time.monotonic()
        for rule in rules:
            if not set(rule["events"]) & set(keys):
                continue
            if actor_id in rule["exempt_user_ids"] or set(role_ids) & set(rule["exempt_role_ids"]):
                continue
            key = (guild.id, rule["id"], actor_id)
            events = self._rule_events.setdefault(key, deque())
            limit, window = rule["limit"], rule["window_seconds"]
            triggered = pm.rule_window_exceeded(events, now, limit=limit, window_seconds=window)
            if not triggered:
                if limit >= 2 and len(events) == limit - 1 and "alert" in rule["actions"]:
                    label = "" if rule["id"] == pm.GLOBAL_RULE_ID else f" · Regel „{rule['label']}“"
                    await self._alert(
                        guild, config,
                        f"⚠️ Anti-Nuke: <@{actor_id}> (`{actor_id}`) hat {len(events)}/{limit} Aktionen "
                        f"(zuletzt: {ACTION_LABELS[action]}) innerhalb von {window} s ausgelöst{label}.",
                    )
                continue
            cooldown_key = ("rule", guild.id, rule["id"], actor_id)
            if self._cooldown_active(cooldown_key, now):
                continue  # this chain already ran for this person – no repetition, no loop
            self._set_cooldown(cooldown_key, now, rule["cooldown_seconds"])
            try:
                await self._run_rule(guild, config, rule, actor, member, action)
            except Exception as exc:  # one broken step must not disable the listeners
                print(f"⚠️ Anti-Nuke-Regel {rule['id']} fehlgeschlagen: {type(exc).__name__}.")
        self._prune(now)

    async def _kick_added_bot(self, guild, config: dict, entry, actor_id: int) -> None:
        target = getattr(entry, "target", None)
        bot_member = guild.get_member(getattr(target, "id", 0) or 0)
        if bot_member is None or not getattr(bot_member, "bot", False):
            return
        if config.get("mode") != "protect":
            await self._alert(guild, config, f"🤖 Anti-Nuke (Alarmmodus): Bot `{bot_member}` wurde von <@{actor_id}> hinzugefügt, "
                                             "die/der nicht freigegeben ist. Der Bot wurde **nicht** entfernt.")
            return
        try:
            await bot_member.kick(reason=f"Pulse Anti-Nuke: Bot von nicht freigegebener Person ({actor_id}) hinzugefügt")
            self._record("Anti-Nuke: Bot entfernt", actor_id, str(getattr(entry, "user", actor_id)),
                         {"guild_id": guild.id, "bot_id": bot_member.id})
            await self._alert(guild, config, f"🤖 Anti-Nuke: Bot `{bot_member}` wurde entfernt – hinzugefügt von <@{actor_id}>, "
                                             "die/der nicht freigegeben ist.")
        except (discord.Forbidden, discord.HTTPException) as exc:
            await self._alert(guild, config, f"⚠️ Anti-Nuke: Hinzugefügter Bot `{bot_member}` konnte nicht entfernt werden ({type(exc).__name__}).")

    # ---------------------------------------------------------------- action chain
    @staticmethod
    def _above_bot(member, bot_member) -> bool:
        if member is None or bot_member is None:
            return False
        try:
            return bool(member.top_role >= bot_member.top_role)
        except (TypeError, AttributeError):
            return False

    async def _run_rule(self, guild, config: dict, rule: dict, actor, member, action: str) -> dict:
        started = time.monotonic()
        actor_id = int(actor.id)
        limit, window = rule["limit"], rule["window_seconds"]
        is_global = rule["id"] == pm.GLOBAL_RULE_ID
        dry = pm.rule_is_dry_run(rule, config)
        actions = rule["actions"]
        sanctions = [a for a in pm.SANCTION_ACTIONS if a in actions]
        headline = f"🚨 Anti-Nuke-Schwelle erreicht: {limit} Aktionen in {window} s durch <@{actor_id}> (`{actor_id}`)"
        if not is_global:
            headline += f" · Regel „{rule['label']}“ ({pm.RULE_SEVERITY_LABELS.get(rule['severity'], rule['severity'])})"
        steps: list[dict] = [{"action": "log", "status": "ok", "detail": "Vorfall protokolliert"}]
        payload = {"guild_id": guild.id, "action_limit": limit, "window_seconds": window,
                   "response": config["response"] if is_global else ",".join(sanctions) or "alert",
                   "last_action": action, "rule_id": rule["id"], "rule_label": rule["label"],
                   "severity": rule["severity"], "mode": config["mode"], "dry_run": dry}

        if "notify_admins" in actions:
            sent = self._notify_admins(guild, config, "🚨 Anti-Nuke ausgelöst",
                                       f"{rule['label']}: {limit} Aktionen in {window} s durch {actor}"
                                       f" – {'nur gemeldet' if dry or not sanctions else ', '.join(sanctions)}.", force=not is_global)
            steps.append({"action": "notify_admins", "status": "ok", "detail": f"{sent} Person(en) benachrichtigt"})

        bot_id = int(getattr(self.bot.user, "id", 0) or 0)
        protected = actor_id in {int(getattr(guild, "owner_id", 0) or 0), bot_id}
        now = time.monotonic()
        for sanction in sanctions:
            if dry:
                why = "Testmodus der Regel" if rule.get("test_mode") else "Alarmmodus"
                steps.append({"action": sanction, "status": "skipped", "detail": f"{why} – nicht ausgeführt"})
                continue
            if protected and sanction != "lockdown":
                steps.append({"action": sanction, "status": "skipped", "detail": "Serverinhaber und Pulse sind geschützt"})
                continue
            dedupe = ("action", guild.id, sanction, actor_id)
            if sanction != "lockdown" and self._cooldown_active(dedupe, now):
                steps.append({"action": sanction, "status": "skipped", "detail": "bereits ausgeführt (Aktions-Cooldown)"})
                continue
            try:
                result = await self._sanction(sanction, guild, config, rule, actor, member, payload)
            except Exception as exc:
                result = {"status": "failed", "detail": f"unerwarteter Fehler ({type(exc).__name__})"}
            steps.append({"action": sanction, **result})
            if result["status"] in ("ok", "partial") and sanction != "lockdown":
                self._set_cooldown(dedupe, now, config["action_cooldown_seconds"])

        failed = [s for s in steps if s["status"] == "failed"]
        icons = {"ok": "✅", "partial": "🟠", "failed": "❌", "skipped": "⏭️"}
        if not sanctions:
            text = headline + ". Reaktion: **nur melden** – bitte sofort prüfen."
        else:
            text = headline + "." + (" **Alarmmodus/Testmodus** – Sanktionen wurden NICHT ausgeführt." if dry else "")
            for step in steps:
                if step["action"] in pm.SANCTION_ACTIONS:
                    text += f"\n{icons.get(step['status'], '•')} {pm.RULE_ACTION_LABELS[step['action']]}: {step['detail']}"
        want_alert = "alert" in actions or "notify_role" in actions or (failed and config["on_missing_permissions"] == "alert")
        if want_alert:
            ping = config["manager_role_id"] if "notify_role" in actions else 0
            delivered = await self._alert(guild, config, text, ping_role_id=ping)
            for kind in ("alert", "notify_role"):
                if kind in actions and (kind == "alert" or ping):
                    steps.append({"action": kind, "status": "ok" if delivered else "failed",
                                  "detail": "gesendet" if delivered else "kein erreichbarer Alarmkanal – nur Server-Log"})
            if "notify_role" in actions and not ping:
                steps.append({"action": "notify_role", "status": "failed", "detail": "keine Verwaltungsrolle konfiguriert"})

        done = {s["action"] for s in steps if s["status"] == "ok"}
        if "ban" in done:
            title, by = "Anti-Nuke: Akteur gesperrt", "Pulse Anti-Nuke"
        elif "kick" in done:
            title, by = "Anti-Nuke: Akteur gekickt", "Pulse Anti-Nuke"
        else:
            title, by = "Anti-Nuke ausgelöst", str(actor)
        sanction_steps = [s for s in steps if s["action"] in pm.SANCTION_ACTIONS]
        summary = "; ".join(f"{pm.RULE_ACTION_LABELS[s['action']]}: {s['detail']}" for s in sanction_steps) or "nur gemeldet"
        payload.update(steps=steps, result=summary[:500], duration_ms=int((time.monotonic() - started) * 1000))
        self._record(title, actor_id, by, payload)
        await self._dispatch("antinuke_rule", {"rule_id": rule["id"], "rule_label": rule["label"], "events": rule["events"],
                                               "actor_id": actor_id, "actor_name": str(actor)[:80], "dry_run": dry,
                                               "result": summary[:300]})
        return payload

    async def _sanction(self, kind: str, guild, config: dict, rule: dict, actor, member, payload: dict) -> dict:
        if kind == "lockdown":
            return await start_lockdown(guild, config["lockdown_channel_ids"], by="Pulse Anti-Nuke",
                                        reason=f"Pulse Anti-Nuke: Notfall-Lockdown (Regel „{rule['label']}“, Akteur {actor.id})")
        if kind == "quarantine":
            return await self._quarantine(guild, config, actor, member, payload)
        return await self._remove(kind, guild, rule, actor, member)

    async def _remove(self, kind: str, guild, rule: dict, actor, member) -> dict:
        actor_id = int(actor.id)
        bot_member = getattr(guild, "me", None)
        bot_perms = getattr(bot_member, "guild_permissions", None)
        needed, label, verb = (("ban_members", "Mitglieder bannen", "gesperrt") if kind == "ban"
                               else ("kick_members", "Mitglieder kicken", "gekickt"))
        if not getattr(bot_perms, needed, False):
            print(f"⚠️ Anti-Nuke konnte den Akteur nicht entfernen: dem Bot fehlt „{label}“.")
            return {"status": "failed", "detail": f"Die Person konnte nicht {verb} werden: Bot-Berechtigung „{label}“ fehlt."}
        if kind == "kick" and member is None:
            return {"status": "failed", "detail": "Die Person ist nicht mehr auf dem Server."}
        if self._above_bot(member, bot_member):
            print(f"⚠️ Anti-Nuke konnte {actor_id} wegen der Discord-Rollenhierarchie nicht entfernen.")
            return {"status": "failed", "detail": "Nicht möglich: die Person steht in der Rollenhierarchie über oder gleich der Bot-Rolle."}
        reason = f"Pulse Anti-Nuke: {rule['limit']} destruktive Serveraktionen innerhalb von {rule['window_seconds']} Sekunden"
        if rule["id"] != pm.GLOBAL_RULE_ID:
            reason += f" (Regel „{rule['label']}“)"
        try:
            if kind == "ban":
                await guild.ban(actor, reason=reason[:400], delete_message_seconds=0)
            else:
                await guild.kick(actor, reason=reason[:400])
            return {"status": "ok", "detail": f"<@{actor_id}> wurde {verb}"}
        except discord.Forbidden:
            print(f"⚠️ Anti-Nuke konnte {actor_id} nicht entfernen: Berechtigung oder Rollenhierarchie.")
            return {"status": "failed", "detail": "von Discord abgelehnt (Berechtigung oder Rollenhierarchie)"}
        except discord.HTTPException as exc:
            print(f"⚠️ Anti-Nuke-Maßnahme für {actor_id} fehlgeschlagen: HTTP {exc.status}.")
            return {"status": "failed", "detail": f"fehlgeschlagen (HTTP {exc.status})"}

    async def _quarantine(self, guild, config: dict, actor, member, payload: dict) -> dict:
        actor_id = int(actor.id)
        bot_member = getattr(guild, "me", None)
        if member is None:
            return {"status": "failed", "detail": "Die Person ist nicht mehr auf dem Server."}
        if self._above_bot(member, bot_member):
            return {"status": "failed", "detail": "Pulse kann nicht eingreifen: die Person steht in der Rollenhierarchie "
                                                  "über oder gleich der Bot-Rolle."}
        if not getattr(getattr(bot_member, "guild_permissions", None), "manage_roles", False):
            return {"status": "failed", "detail": "Quarantäne nicht möglich: Bot-Berechtigung „Rollen verwalten“ fehlt."}
        bot_top = getattr(getattr(bot_member, "top_role", None), "position", 0)
        configured = set(config.get("strip_role_ids") or [])
        if configured:  # only the explicitly configured roles
            strip = [r for r in getattr(member, "roles", []) or [] if r.id in configured and not getattr(r, "managed", False)
                     and int(getattr(r, "position", 0)) < int(bot_top)]
        else:
            strip = roles_to_strip(member.roles, bot_top)
        if not strip:
            return {"status": "skipped", "detail": "Keine entziehbare Rolle gefunden – bitte Rechte manuell prüfen."}
        try:
            await member.remove_roles(*strip, reason="Pulse Anti-Nuke: Quarantäne nach destruktiven Serveraktionen")
        except (discord.Forbidden, discord.HTTPException) as exc:
            return {"status": "failed", "detail": f"Quarantäne fehlgeschlagen ({type(exc).__name__})"}
        data = load_quarantine()
        previous = data.get(str(actor_id), {}) if isinstance(data.get(str(actor_id)), dict) else {}
        role_ids = list(dict.fromkeys([*previous.get("role_ids", []), *[r.id for r in strip]]))
        data[str(actor_id)] = {
            "name": str(actor)[:80], "role_ids": role_ids, "role_names": [r.name[:60] for r in strip],
            "at": _now_iso(), "last_action": payload.get("last_action"), "rule_id": payload.get("rule_id"),
            "permissions": sorted({p for r in strip for p in dangerous_names(r.permissions)}),
        }
        save_quarantine(data)
        self._record("Anti-Nuke: Quarantäne", actor_id, "Pulse Anti-Nuke", {**payload, "result": "Quarantäne",
                                                                          "role_ids": [r.id for r in strip]})
        names = ", ".join(r.name for r in strip)[:600]
        return {"status": "ok", "detail": f"entzogen: {names}. Wiederherstellen: Dashboard → Verwaltung → Sicherheit."}

    # ---------------------------------------------------------------- listeners
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
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        before_ids = {role.id for role in getattr(before, "roles", []) or []}
        added = [role for role in getattr(after, "roles", []) or [] if role.id not in before_ids]
        if any(pm.has_dangerous_permission(getattr(role, "permissions", None)) for role in added):
            await self._handle_event(after.guild, "member_role_grant", after.id)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await self._handle_event(role.guild, "role_create", role.id)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._handle_event(role.guild, "role_delete", role.id)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        if before.permissions == after.permissions and before.position == after.position:
            return
        await self._handle_event(after.guild, "role_update", after.id)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        await self._handle_event(channel.guild, "channel_create", channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await self._handle_event(channel.guild, "channel_delete", channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        overwrites = before.overwrites != after.overwrites
        settings = any(getattr(before, field, None) != getattr(after, field, None) for field in CHANNEL_CRITICAL_FIELDS)
        if overwrites and settings:
            await self._handle_event(after.guild, ["channel_update", "channel_settings"], after.id)
        elif overwrites:
            await self._handle_event(after.guild, "channel_update", after.id)
        elif settings:
            await self._handle_event(after.guild, "channel_settings", after.id)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        if any(getattr(before, field, None) != getattr(after, field, None) for field in GUILD_CRITICAL_FIELDS):
            await self._handle_event(after, "guild_update", after.id)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        # One audit-log request for all three webhook kinds (before: three requests per event).
        await self._handle_event(channel.guild, ["webhook_create", "webhook_delete", "webhook_update"])


async def setup(bot: commands.Bot):
    await bot.add_cog(PulseAntiNuke(bot))
