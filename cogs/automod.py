"""Configurable, local-first AutoMod with warnings and repeat-offence timeouts."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from pulse_moderation import AUTOMOD_RULES, classify_message

RULE_LABELS = {
    "spam": "Spam / Nachrichtenflut",
    "mentions": "Massen-Erwähnungen",
    "invites": "Discord-Einladungslink",
    "keywords": "Gesperrter Begriff",
    "domains": "Gesperrter Link",
}
STRIKE_WINDOW_SECONDS = 60 * 60
TIMEOUT_MINUTES = 10


def _ids(values) -> set[int]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return set()
    return {int(value) for value in values if str(value).isdigit() and int(value) > 0}


def _list(values) -> list:
    return values if isinstance(values, list) else []


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


class PulseAutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._messages: dict[tuple[int, int], deque[tuple[float, str]]] = defaultdict(lambda: deque(maxlen=20))
        self._strikes: dict[tuple[int, int], deque[float]] = defaultdict(lambda: deque(maxlen=5))
        self._message_events = 0

    def _prune_state(self) -> None:
        self._message_events += 1
        if self._message_events % 200:
            return
        now = time.monotonic()
        for key, events in list(self._messages.items()):
            while events and now - events[0][0] > 10:
                events.popleft()
            if not events:
                self._messages.pop(key, None)
        for key, strikes in list(self._strikes.items()):
            while strikes and now - strikes[0] > STRIKE_WINDOW_SECONDS:
                strikes.popleft()
            if not strikes:
                self._strikes.pop(key, None)
        while len(self._messages) > 5000:
            self._messages.pop(next(iter(self._messages)))
        while len(self._strikes) > 5000:
            self._strikes.pop(next(iter(self._strikes)))

    def _config(self) -> dict:
        import webserver
        config = webserver.load_config().get("pulse_automod", {})
        return config if isinstance(config, dict) else {}

    def _rule(self, message: discord.Message, config: dict) -> str | None:
        rules = set(_list(config.get("rules"))) & AUTOMOD_RULES
        if not config.get("enabled") or not rules:
            return None

        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        recent = self._messages[key]
        cutoff = now - 10
        while recent and recent[0][0] < cutoff:
            recent.popleft()
        content = (message.clean_content or "").strip()
        recent.append((now, content.casefold()))
        duplicate_count = sum(1 for _, value in recent if value == content.casefold() and content)
        return classify_message(
            content,
            rules=rules,
            mention_count=len(message.mentions) + len(message.role_mentions) + int(message.mention_everyone),
            mention_limit=_bounded_int(config.get("mention_limit"), 5, 3, 30),
            duplicate_count=duplicate_count,
            message_count=len(recent),
            blocked_terms=_list(config.get("blocked_terms")),
            blocked_domains=_list(config.get("blocked_domains")),
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        guild = message.guild
        if guild is None or message.author.bot or message.webhook_id or not isinstance(message.author, discord.Member):
            return
        if guild.owner_id == message.author.id or message.author.guild_permissions.manage_messages:
            return

        self._prune_state()
        config = self._config()
        if not config.get("enabled"):
            return
        exempt_roles = _ids(config.get("exempt_role_ids"))
        if exempt_roles & {role.id for role in message.author.roles}:
            return
        if message.channel.id in _ids(config.get("exempt_channel_ids")):
            return

        rule = self._rule(message, config)
        if not rule:
            return
        try:
            await message.delete()
        except discord.Forbidden:
            print(f"⚠️ AutoMod konnte eine Nachricht nicht löschen: fehlende Berechtigung in Kanal {message.channel.id}.")
            return
        except discord.HTTPException as exc:
            print(f"⚠️ AutoMod konnte eine Nachricht nicht löschen: HTTP {exc.status}.")
            return

        user_key = (guild.id, message.author.id)
        now = time.monotonic()
        strikes = self._strikes[user_key]
        while strikes and now - strikes[0] > STRIKE_WINDOW_SECONDS:
            strikes.popleft()
        strikes.append(now)
        label = RULE_LABELS[rule]
        repeated = len(strikes) >= 2

        action = "Timeout vorbereitet" if repeated else "Warnung gesendet"
        if repeated:
            try:
                await message.author.timeout(
                    timedelta(minutes=TIMEOUT_MINUTES),
                    reason=f"Pulse AutoMod: wiederholter Verstoß ({label})",
                )
                action = f"Timeout {TIMEOUT_MINUTES} Min."
            except discord.Forbidden:
                action = "Timeout fehlgeschlagen (Berechtigung/Rollenhierarchie)"
                print(f"⚠️ AutoMod konnte {message.author.id} nicht timeouten: Berechtigung oder Rollenposition.")
            except discord.HTTPException as exc:
                action = "Timeout fehlgeschlagen"
                print(f"⚠️ AutoMod-Timeout für {message.author.id} fehlgeschlagen: HTTP {exc.status}.")
        else:
            try:
                await message.author.send(
                    f"Deine Nachricht auf **{guild.name}** wurde wegen **{label}** entfernt. "
                    "Bitte beachte die Serverregeln; ein erneuter Verstoß kann zu einem Timeout führen."
                )
            except discord.Forbidden:
                print(f"ℹ️ AutoMod konnte {message.author.id} nicht per DM warnen (DMs geschlossen).")
            except discord.HTTPException as exc:
                print(f"⚠️ AutoMod-Warnung für {message.author.id} fehlgeschlagen: HTTP {exc.status}.")

        alert_id = _bounded_int(config.get("alert_channel_id"), 0, 0, 2**63 - 1)
        alert_channel = guild.get_channel(alert_id) if alert_id else None
        if alert_channel is not None:
            try:
                await alert_channel.send(
                    f"🛡️ AutoMod: {label} · Nutzer-ID `{message.author.id}` · "
                    f"Kanal-ID `{message.channel.id}` · {action} · Fall {len(strikes)}"
                )
            except discord.Forbidden:
                print(f"⚠️ AutoMod konnte den Protokolleintrag in Kanal {alert_id} nicht senden.")
            except discord.HTTPException as exc:
                print(f"⚠️ AutoMod-Protokollierung fehlgeschlagen: HTTP {exc.status}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(PulseAutoMod(bot))
