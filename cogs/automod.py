"""Configurable, local-first AutoMod with warnings and escalating repeat-offence timeouts.

Configuration lives in config.json → "pulse_automod" and is managed at /admin/security. Everything is
normalised by pulse_moderation.normalize_automod, so the dashboard and the bot use identical values.

* mode "observe": nothing is deleted or punished – matches are only logged and reported (safe rollout).
* mode "enforce": the message is deleted; the n-th violation inside the strike window escalates
  (warning → timeout, doubling up to a maximum, see pulse_moderation.automod_step).
* Message contents are never stored: the flood detector keeps only a hash per recent message,
  and the case log only rule, channel, action and strike number.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from pulse_moderation import AUTOMOD_RULE_LABELS, automod_step, match_rules, normalize_automod

RULE_LABELS = AUTOMOD_RULE_LABELS
STRIKE_WINDOW_SECONDS = 60 * 60  # default; the configured window (strike_window_minutes) wins
TIMEOUT_MINUTES = 10
MAX_TRACKED_USERS = 5000


def _ids(values) -> set[int]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return set()
    return {int(value) for value in values if str(value).isdigit() and int(value) > 0}


class PulseAutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild, user) -> recent (monotonic time, hash of the folded content); bounded per user and in total.
        self._messages: dict[tuple[int, int], deque[tuple[float, int]]] = defaultdict(lambda: deque(maxlen=20))
        self._strikes: dict[tuple[int, int], deque[float]] = defaultdict(lambda: deque(maxlen=10))
        self._message_events = 0

    def _prune_state(self, strike_window: float = STRIKE_WINDOW_SECONDS) -> None:
        self._message_events += 1
        if self._message_events % 200:
            return
        now = time.monotonic()
        for key, events in list(self._messages.items()):
            while events and now - events[0][0] > 60:
                events.popleft()
            if not events:
                self._messages.pop(key, None)
        for key, strikes in list(self._strikes.items()):
            while strikes and now - strikes[0] > strike_window:
                strikes.popleft()
            if not strikes:
                self._strikes.pop(key, None)
        while len(self._messages) > MAX_TRACKED_USERS:
            self._messages.pop(next(iter(self._messages)))
        while len(self._strikes) > MAX_TRACKED_USERS:
            self._strikes.pop(next(iter(self._strikes)))

    def _config(self) -> dict:
        import webserver
        config = webserver.load_config().get("pulse_automod", {})
        return config if isinstance(config, dict) else {}

    def _hits(self, message: discord.Message, config: dict) -> list[tuple[str, str]]:
        if not config.get("enabled") or not config.get("rules"):
            return []
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        recent = self._messages[key]
        cutoff = now - config["flood_seconds"]
        while recent and recent[0][0] < cutoff:
            recent.popleft()
        content = (message.clean_content or "").strip()
        digest = hash(content.casefold())
        recent.append((now, digest))
        duplicate_count = sum(1 for _, value in recent if value == digest) if content else 0
        return match_rules(
            content,
            rules=config["rules"],
            mention_count=len(message.mentions) + len(message.role_mentions) + int(message.mention_everyone),
            mention_limit=config["mention_limit"],
            duplicate_count=duplicate_count,
            message_count=len(recent),
            blocked_terms=config["blocked_terms"],
            blocked_domains=config["blocked_domains"],
            allowed_domains=config["allowed_domains"],
            caps_percent=config["caps_percent"],
            caps_min_letters=config["caps_min_letters"],
            duplicate_limit=config["duplicate_limit"],
            flood_messages=config["flood_messages"],
        )

    def _rule(self, message: discord.Message, config: dict) -> str | None:
        hits = self._hits(message, normalize_automod(config))
        return hits[0][0] if hits else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        guild = message.guild
        if guild is None or message.author.bot or message.webhook_id or not isinstance(message.author, discord.Member):
            return
        if guild.owner_id == message.author.id or message.author.guild_permissions.manage_messages:
            return

        config = normalize_automod(self._config())
        self._prune_state(config["strike_window_minutes"] * 60)
        if not config["enabled"]:
            return
        if set(config["exempt_role_ids"]) & {role.id for role in message.author.roles}:
            return
        if message.channel.id in set(config["exempt_channel_ids"]):
            return
        parent_id = getattr(getattr(message.channel, "category", None), "id", None)
        if parent_id and parent_id in set(config["exempt_channel_ids"]):
            return  # a category ID in the channel list exempts all its channels

        hits = self._hits(message, config)
        if not hits:
            return
        rule, reason = hits[0]
        label = RULE_LABELS[rule]
        observe = config["mode"] == "observe"

        if observe:
            action = "Nur beobachtet (keine Maßnahme)"
            strike_count = 0
        else:
            try:
                await message.delete()
            except discord.NotFound:
                pass  # already removed by someone else – still counts as a violation
            except discord.Forbidden:
                print(f"⚠️ AutoMod konnte eine Nachricht nicht löschen: fehlende Berechtigung in Kanal {message.channel.id}.")
                return
            except discord.HTTPException as exc:
                print(f"⚠️ AutoMod konnte eine Nachricht nicht löschen: HTTP {exc.status}.")
                return

            user_key = (guild.id, message.author.id)
            now = time.monotonic()
            strikes = self._strikes[user_key]
            while strikes and now - strikes[0] > config["strike_window_minutes"] * 60:
                strikes.popleft()
            strikes.append(now)
            strike_count = len(strikes)
            step = automod_step(strike_count, config)

            if step["action"] == "timeout":
                minutes = step["minutes"]
                action = f"Timeout {minutes} Min. vorbereitet"
                try:
                    await message.author.timeout(
                        timedelta(minutes=minutes),
                        reason=f"Pulse AutoMod: wiederholter Verstoß ({label}, Fall {strike_count})",
                    )
                    action = f"Timeout {minutes} Min."
                except discord.Forbidden:
                    action = "Timeout fehlgeschlagen (Berechtigung/Rollenhierarchie)"
                    print(f"⚠️ AutoMod konnte {message.author.id} nicht timeouten: Berechtigung oder Rollenposition.")
                except discord.HTTPException as exc:
                    action = "Timeout fehlgeschlagen"
                    print(f"⚠️ AutoMod-Timeout für {message.author.id} fehlgeschlagen: HTTP {exc.status}.")
            else:
                action = "Nachricht entfernt"
                if config["warn_dm"]:
                    action = "Warnung gesendet"
                    try:
                        await message.author.send(
                            f"Deine Nachricht auf **{guild.name}** wurde wegen **{label}** entfernt. "
                            "Bitte beachte die Serverregeln; ein erneuter Verstoß kann zu einem Timeout führen."
                        )
                    except discord.Forbidden:
                        action = "Nachricht entfernt (DM geschlossen)"
                        print(f"ℹ️ AutoMod konnte {message.author.id} nicht per DM warnen (DMs geschlossen).")
                    except discord.HTTPException as exc:
                        print(f"⚠️ AutoMod-Warnung für {message.author.id} fehlgeschlagen: HTTP {exc.status}.")

        try:
            import pulse_db
            bot_user = getattr(self.bot, "user", None)
            pulse_db.record_event(
                "AutoMod-Maßnahme",
                "user",
                message.author.id,
                str(getattr(bot_user, "id", 0) or 0),
                str(bot_user or "Pulse AutoMod"),
                {
                    "guild_id": guild.id,
                    "rule": rule,
                    "reason": reason[:160],
                    "channel_id": message.channel.id,
                    "action": action,
                    "strike": strike_count,
                    "mode": config["mode"],
                },
            )
        except (sqlite3.Error, OSError) as exc:
            print(f"⚠️ AutoMod-Maßnahme für {message.author.id} konnte nicht dauerhaft protokolliert werden: {type(exc).__name__}.")

        alert_id = config["alert_channel_id"]
        alert_channel = guild.get_channel(alert_id) if alert_id else None
        if alert_channel is not None:
            prefix = "👁️ AutoMod (Beobachtung)" if observe else "🛡️ AutoMod"
            try:
                await alert_channel.send(
                    f"{prefix}: {label} · <@{message.author.id}> (`{message.author.id}`) · "
                    f"<#{message.channel.id}> · {action}" + (f" · Fall {strike_count}" if strike_count else ""),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.Forbidden:
                print(f"⚠️ AutoMod konnte den Protokolleintrag in Kanal {alert_id} nicht senden.")
            except discord.HTTPException as exc:
                print(f"⚠️ AutoMod-Protokollierung fehlgeschlagen: HTTP {exc.status}.")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # A harmless post edited into a violation must not slip through. Unchanged text (embeds loading,
        # pins) is ignored; the edit is not counted for flood/duplicate detection a second time.
        if (before.content or "") == (after.content or ""):
            return
        key = (getattr(after.guild, "id", 0), getattr(after.author, "id", 0))
        recent = self._messages.get(key)
        snapshot = list(recent) if recent is not None else None
        await self.on_message(after)
        if snapshot is not None and key in self._messages:
            self._messages[key].clear()
            self._messages[key].extend(snapshot)


async def setup(bot: commands.Bot):
    await bot.add_cog(PulseAutoMod(bot))
