"""Verification for new members: unverified role on join, citizen role via a persistent button.

Configured in Verwaltung → Discord-Auftritt → Verifikation. The button uses the
stable custom_id "pulse:verify:v1" and is registered on startup, so the panel keeps
working after restarts. Role IDs are fixed by the operator (see pulse_verification).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

import pulse_db
from pulse_embed_design import to_discord_embed
from pulse_verification import (
    BUTTON_CUSTOM_ID, CITIZEN_ROLE_ID, SETTING_KEY, UNVERIFIED_ROLE_ID, add_error, build_panel_spec,
    normalize_settings, plan_join, plan_verify, role_problems,
)

CLICK_COOLDOWN_SECONDS = 5
JOIN_DEDUPE_SECONDS = 60
MAX_TRACKED = 500


def _guild_id() -> int:
    try:
        from webserver import GUILD_ID
        return int(GUILD_ID or 0)
    except Exception:
        return 0


def load_settings() -> dict:
    return normalize_settings(pulse_db.get_setting(SETTING_KEY, {}))


def save_settings(settings: dict) -> None:
    pulse_db.set_setting(SETTING_KEY, settings)


def problems_for(guild: discord.Guild) -> list[str]:
    me = guild.me
    unverified = guild.get_role(UNVERIFIED_ROLE_ID)
    citizen = guild.get_role(CITIZEN_ROLE_ID)
    return role_problems(
        bot_manage_roles=bool(me and me.guild_permissions.manage_roles),
        bot_top_position=me.top_role.position if me else 0,
        roles={"unverified": unverified.position if unverified else None,
               "citizen": citizen.position if citizen else None},
    )


class VerifyButton(discord.ui.Button):
    def __init__(self, cog: "Verification", label: str = "Jetzt verifizieren"):
        super().__init__(label=label, style=discord.ButtonStyle.success, emoji="✅", custom_id=BUTTON_CUSTOM_ID)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_click(interaction)


class VerifyView(discord.ui.View):
    def __init__(self, cog: "Verification", label: str = "Jetzt verifizieren"):
        super().__init__(timeout=None)
        self.add_item(VerifyButton(cog, label))


class Verification(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}
        self._last_click: dict[int, float] = {}
        self._recent_joins: dict[int, float] = {}
        self._settings_lock = asyncio.Lock()
        self.guild_id = _guild_id()
        # Persistent view: Discord routes clicks on old panel messages to this callback after restarts.
        bot.add_view(VerifyView(self))

    # ------------------------------------------------------------------ helpers
    def _bounded(self, mapping: dict, key: int, value) -> None:
        mapping[key] = value
        while len(mapping) > MAX_TRACKED:
            mapping.pop(next(iter(mapping)))

    async def record_error(self, text: str) -> None:
        print(f"⚠️ Verifikation: {text}")
        try:
            async with self._settings_lock:
                settings = await asyncio.to_thread(load_settings)
                settings = add_error(settings, datetime.now(timezone.utc).isoformat(timespec="seconds"), text)
                await asyncio.to_thread(save_settings, settings)
        except Exception as exc:
            print(f"⚠️ Verifikationsfehler konnte nicht gespeichert werden: {type(exc).__name__}")

    # ------------------------------------------------------------------ join
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if self.guild_id and guild.id != self.guild_id:
            return
        now = time.monotonic()
        if now - self._recent_joins.get(member.id, -JOIN_DEDUPE_SECONDS) < JOIN_DEDUPE_SECONDS:
            return
        self._bounded(self._recent_joins, member.id, now)
        settings = await asyncio.to_thread(load_settings)
        decision = plan_join([r.id for r in member.roles], is_bot=member.bot, enabled=settings["enabled"])
        if decision != "assign":
            return
        role = guild.get_role(UNVERIFIED_ROLE_ID)
        problems = problems_for(guild)
        if role is None or problems:
            await self.record_error(f"Unverifiziert-Rolle konnte Mitglied {member.id} nicht gegeben werden: {' '.join(problems) or 'Rolle fehlt.'}")
            return
        try:
            await member.add_roles(role, reason="Pulse-Verifikation: neues Mitglied")
        except discord.Forbidden:
            await self.record_error(f"Discord verweigert die Unverifiziert-Rolle für Mitglied {member.id} (Rechte/Hierarchie).")
        except discord.HTTPException as exc:
            await self.record_error(f"Unverifiziert-Rolle für Mitglied {member.id} fehlgeschlagen: HTTP {exc.status}.")

    # ------------------------------------------------------------------ button
    async def _reply(self, interaction: discord.Interaction, text: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    async def handle_click(self, interaction: discord.Interaction) -> str:
        """Verify the clicking member. Returns a short status code (used by tests)."""
        guild = interaction.guild
        member = interaction.user
        # A discord.User (no server context) has no roles: only real server members can verify.
        is_member = getattr(member, "roles", None) is not None and hasattr(member, "add_roles")
        if guild is None or not is_member or (self.guild_id and guild.id != self.guild_id):
            await self._reply(interaction, "❌ Die Verifikation funktioniert nur direkt auf dem Server.")
            return "wrong_guild"
        now = time.monotonic()
        if now - self._last_click.get(member.id, -CLICK_COOLDOWN_SECONDS) < CLICK_COOLDOWN_SECONDS:
            await self._reply(interaction, "⏳ Einen Moment – deine Verifikation wird schon bearbeitet.")
            return "cooldown"
        self._bounded(self._last_click, member.id, now)
        lock = self._locks.setdefault(member.id, asyncio.Lock())
        if len(self._locks) > MAX_TRACKED:
            for key in [k for k, l in self._locks.items() if not l.locked()][: len(self._locks) - MAX_TRACKED]:
                self._locks.pop(key, None)
        async with lock:
            settings = await asyncio.to_thread(load_settings)
            if not settings["enabled"]:
                await self._reply(interaction, "ℹ️ Die Verifikation ist gerade deaktiviert. Bitte wende dich an den Support.")
                return "disabled"
            plan = plan_verify([r.id for r in member.roles])
            if plan["already_done"]:
                await self._reply(interaction, "✅ Du bist bereits verifiziert.")
                return "already"
            problems = problems_for(guild)
            citizen = guild.get_role(CITIZEN_ROLE_ID)
            unverified = guild.get_role(UNVERIFIED_ROLE_ID)
            if problems or citizen is None:
                await self.record_error("Verifikation nicht möglich: " + " ".join(problems or ["Bürgerrolle fehlt."]))
                await self._reply(interaction, "❌ Die Verifikation ist gerade nicht möglich. Das Team wurde informiert – bitte versuche es später erneut.")
                return "misconfigured"
            if not interaction.response.is_done():
                try:
                    await interaction.response.defer(ephemeral=True, thinking=True)
                except discord.HTTPException:
                    pass
            # Citizen first: if this fails, the member keeps the unverified protection.
            if plan["add_citizen"]:
                try:
                    await member.add_roles(citizen, reason="Pulse-Verifikation abgeschlossen")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    await self.record_error(f"Bürgerrolle für Mitglied {member.id} fehlgeschlagen ({type(exc).__name__}). Unverifiziert-Rolle bleibt bestehen.")
                    await self._reply(interaction, "❌ Die Verifikation konnte nicht abgeschlossen werden. Bitte versuche es später erneut oder melde dich beim Support.")
                    return "citizen_failed"
            if plan["remove_unverified"] and unverified is not None:
                try:
                    await member.remove_roles(unverified, reason="Pulse-Verifikation abgeschlossen")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    await self.record_error(f"Unverifiziert-Rolle von Mitglied {member.id} nicht entfernt ({type(exc).__name__}); Bürgerrolle wurde vergeben.")
                    await self._reply(interaction, "⚠️ Du hast die Bürgerrolle erhalten, aber eine alte Rolle konnte nicht entfernt werden. Das Team wurde informiert.")
                    return "partial"
            await self._reply(interaction, "✅ Verifikation abgeschlossen – willkommen! Du siehst jetzt alle freigegebenen Bereiche.")
            return "verified"

    # ------------------------------------------------------------------ panel
    async def publish_panel(self, guild: discord.Guild) -> dict:
        """Edit the stored panel message, or send exactly one new one if it is gone."""
        async with self._settings_lock:
            settings = await asyncio.to_thread(load_settings)
            if not settings["channel_id"]:
                raise ValueError("Bitte zuerst einen Verifikationskanal speichern.")
            channel = guild.get_channel(int(settings["channel_id"]))
            if channel is None or not hasattr(channel, "send"):
                raise ValueError("Der Verifikationskanal ist nicht erreichbar.")
            perms = channel.permissions_for(guild.me) if guild.me else None
            if perms is not None and not (perms.send_messages and perms.embed_links):
                raise ValueError(f"Pulse fehlen in #{channel.name} die Rechte „Nachrichten senden“ und/oder „Links einbetten“.")
            icon = guild.icon.url if getattr(guild, "icon", None) else ""
            embed = to_discord_embed(build_panel_spec(settings, icon_url=icon))
            view = VerifyView(self, settings["button_label"])
            message = None
            if settings["message_id"] and settings["published_channel_id"] == str(channel.id):
                try:
                    message = await channel.fetch_message(int(settings["message_id"]))
                except discord.NotFound:
                    message = None
                except discord.HTTPException as exc:
                    raise ValueError(f"Die bestehende Panel-Nachricht konnte nicht geladen werden (HTTP {exc.status}). Es wurde keine neue erstellt.") from exc
            if message is not None:
                await message.edit(embed=embed, view=view)
                action = "aktualisiert"
            else:
                message = await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
                action = "veröffentlicht"
            settings["message_id"] = str(message.id)
            settings["published_channel_id"] = str(channel.id)
            await asyncio.to_thread(save_settings, settings)
            self.bot.add_view(view)
            return {"action": action, "channel": f"#{channel.name}", "message_id": message.id}


async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))
