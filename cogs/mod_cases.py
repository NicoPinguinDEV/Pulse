"""BOT-09 (E-8): /mod warn|timeout|kick|ban|akte – citizen moderation with case file, proof, expiry,
DM with the appeal route and audit. Kick and ban need an explicit confirmation. Nothing here runs on
its own: every action is a person's decision (no automatic sanction).
"""
from __future__ import annotations

from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import pulse_mod_cases as mc
from pulse_interactions import PulseView


def pulse_perms(guild, user_id):
    import webserver
    perms, _ = webserver.compute_perms(guild, user_id, webserver.load_config())
    return perms


class ConfirmAction(PulseView):
    def __init__(self, owner_id: int, run):
        super().__init__(timeout=60)
        self.owner_id, self.run, self.used = owner_id, run, False

    @discord.ui.button(label="Bestätigen", style=discord.ButtonStyle.danger, emoji="✅", custom_id="pulse_mod:confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id or self.used:
            await interaction.response.send_message("Nur wer die Aktion gestartet hat, kann sie bestätigen.", ephemeral=True)
            return
        self.used = True
        self.stop()
        await interaction.response.edit_message(content="⏳ Wird ausgeführt …", view=None)
        await self.run(interaction)

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary, emoji="❌", custom_id="pulse_mod:cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Nur wer die Aktion gestartet hat, kann sie abbrechen.", ephemeral=True)
            return
        self.used = True
        self.stop()
        await interaction.response.edit_message(content="❌ Abgebrochen – nichts wurde ausgeführt.", view=None)


class ModCases(commands.GroupCog, group_name="mod", group_description="Bürger-Moderation mit Fallakte"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def _check(self, interaction: discord.Interaction, action: str, target: discord.Member) -> bool:
        import webserver
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Nur auf dem Server.", ephemeral=True)
            return False
        if not mc.may_moderate(action, pulse_perms(interaction.guild, interaction.user.id), interaction.user.guild_permissions):
            await interaction.response.send_message(
                f"❌ Für **{mc.ACTIONS[action]}** brauchst du das Pulse-Recht und das passende Discord-Recht.", ephemeral=True)
            return False
        problem = mc.target_problem(interaction.user, target, interaction.guild, webserver.load_config().get("team_role_ids", []))
        if problem:
            await interaction.response.send_message("❌ " + problem, ephemeral=True)
            return False
        return True

    async def _execute(self, interaction: discord.Interaction, action: str, target: discord.Member, grund: str,
                       beweis: str, minutes: int | None, respond) -> None:
        """Creates the case, sends the DM first (kick/ban remove the person), then acts on Discord."""
        import webserver
        try:
            case = mc.create_case(interaction.guild.id, target, action, grund, beweis, minutes, interaction.user)
        except ValueError as exc:
            await respond("❌ " + str(exc))
            return
        appeal = (mc.appeal_text(interaction.guild.id, case["id"]) if action != "ban"
                  else f"Schreibe dem Serverteam per Direktnachricht und nenne die Fall-ID {case['id']}.")
        dm_ok = False
        try:
            await target.send(mc.dm_text(case, interaction.guild.name, appeal))
            mc.mark(case["id"], dm_sent=1)
            dm_ok = True
        except (discord.Forbidden, discord.HTTPException):
            pass
        reason = f"{case['id']} · {interaction.user.display_name}: {case['reason']}"[:512]
        try:
            if action == "timeout":
                await target.timeout(timedelta(minutes=minutes), reason=reason)
            elif action == "kick":
                await target.kick(reason=reason)
            elif action == "ban":
                await interaction.guild.ban(target, reason=reason, delete_message_seconds=0)
        except (discord.Forbidden, discord.HTTPException) as exc:
            mc.mark(case["id"], status="failed", close_note=type(exc).__name__)
            await respond(f"❌ Discord hat die Aktion verweigert ({type(exc).__name__}). Fall {case['id']} ist als fehlgeschlagen vermerkt.")
            return
        webserver.log_audit(interaction.user.display_name, str(interaction.user.id), f"Bürger-Moderation: {mc.ACTIONS[action]}",
                            f"{case['id']} · {target.display_name} ({target.id}) · {case['reason']}"
                            + (f" · bis {case['expires_at']}" if case.get("expires_at") else ""))
        await respond(f"✅ {mc.ACTIONS[action]} für **{target.display_name}** – Fall `{case['id']}`"
                      + (f", bis {case['expires_at'][:16].replace('T', ' ')} UTC" if case.get("expires_at") else "")
                      + ("" if dm_ok else " · ⚠️ DM nicht zustellbar (DMs geschlossen)"))

    @app_commands.command(name="warn", description="Bürger verwarnen (Fallakte, DM mit Einspruchsweg).")
    @app_commands.describe(mitglied="Wer", grund="Pflichtgrund", beweis="Link zum Beweis (optional)")
    async def warn(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str, beweis: str = ""):
        if not await self._check(interaction, "warn", mitglied):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._execute(interaction, "warn", mitglied, grund, beweis, None,
                            lambda text: interaction.followup.send(text, ephemeral=True))

    @app_commands.command(name="timeout", description="Bürger stummschalten (Timeout mit Ablauf).")
    @app_commands.describe(mitglied="Wer", minuten="Dauer in Minuten (1 bis 40320 = 28 Tage)", grund="Pflichtgrund", beweis="Link zum Beweis (optional)")
    async def timeout(self, interaction: discord.Interaction, mitglied: discord.Member,
                      minuten: app_commands.Range[int, 1, mc.MAX_TIMEOUT_MINUTES], grund: str, beweis: str = ""):
        if not await self._check(interaction, "timeout", mitglied):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._execute(interaction, "timeout", mitglied, grund, beweis, minuten,
                            lambda text: interaction.followup.send(text, ephemeral=True))

    async def _confirm_then(self, interaction, action, mitglied, grund, beweis, minutes):
        async def run(button_interaction):
            await self._execute(interaction, action, mitglied, grund, beweis, minutes,
                                lambda text: button_interaction.edit_original_response(content=text))
        label = mc.ACTIONS[action]
        await interaction.response.send_message(
            f"⚠️ **{label}** für {mitglied.mention} wirklich ausführen?\nGrund: {grund.strip()[:500]}"
            + (f"\nDauer: {minutes // 1440} Tage" if minutes else "") + "\nDie Person bekommt vorher eine DM mit der Fall-ID.",
            view=ConfirmAction(interaction.user.id, run), ephemeral=True)

    @app_commands.command(name="kick", description="Bürger vom Server kicken (mit Bestätigung und Fallakte).")
    async def kick(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str, beweis: str = ""):
        if not await self._check(interaction, "kick", mitglied):
            return
        await self._confirm_then(interaction, "kick", mitglied, grund, beweis, None)

    @app_commands.command(name="ban", description="Bürger bannen (mit Bestätigung, optional befristet).")
    @app_commands.describe(tage="0 = unbefristet, sonst Tage bis zur automatischen Aufhebung")
    async def ban(self, interaction: discord.Interaction, mitglied: discord.Member, grund: str,
                  tage: app_commands.Range[int, 0, 365] = 0, beweis: str = ""):
        if not await self._check(interaction, "ban", mitglied):
            return
        await self._confirm_then(interaction, "ban", mitglied, grund, beweis, tage * 1440 if tage else None)

    @app_commands.command(name="akte", description="Moderationsfälle einer Person anzeigen.")
    async def akte(self, interaction: discord.Interaction, mitglied: discord.User):
        perms = pulse_perms(interaction.guild, interaction.user.id) if interaction.guild else {}
        if not (perms.get("is_admin") or perms.get("can_moderate_kicks") or perms.get("can_moderate_bans")):
            await interaction.response.send_message("❌ Kein Zugriff auf die Fallakte.", ephemeral=True)
            return
        cases = mc.list_cases(mitglied.id, limit=15)
        lines = [f"`{c['id']}` · {mc.ACTIONS.get(c['action'], c['action'])} · {c['created_at'][:10]} · {c['status']} · {c['reason'][:80]}"
                 for c in cases] or ["Keine Fälle."]
        await interaction.response.send_message(f"📁 **Fallakte {mitglied}**\n" + "\n".join(lines), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())


async def lift_expired_bans(bot) -> int:
    """Called by the existing automation loop: lifts bans whose expiry has passed (no new loop)."""
    lifted = 0
    for case in mc.expired_bans():
        guild = bot.get_guild(int(case["guild_id"]))
        if guild is None:
            continue
        try:
            await guild.unban(discord.Object(id=int(case["target_id"])), reason=f"{case['id']}: befristeter Ban abgelaufen")
            mc.mark(case["id"], status="expired", closed_at=mc._now().isoformat(timespec="seconds"), closed_by="Pulse (Ablauf)")
            lifted += 1
        except discord.NotFound:
            mc.mark(case["id"], status="expired", closed_at=mc._now().isoformat(timespec="seconds"), closed_by="Pulse (bereits entbannt)")
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"⚠️ Bürger-Moderation: Ban {case['id']} konnte nicht aufgehoben werden ({type(exc).__name__}).")
    return lifted


async def setup(bot: commands.Bot):
    await bot.add_cog(ModCases(bot))
