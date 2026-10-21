"""BOT-03: start, pause, resume and end a shift directly in Discord.

Same logic and data as the dashboard (webserver.apply_shift_action): the same warning lock, the same
LOA hint, the same Voice-Support-Bereitschaft sync, the same end-of-shift summary. Two ways in:
* `/dienst start|pause|weiter|ende|status` for the person themself,
* a persistent panel (`/dienst panel`, admins) with four buttons – survives restarts.
Rights are checked on every click: only people who may see the dashboard (team members) can use it.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import pulse_db
from pulse_interactions import PulseView

PANEL_SETTING = "duty_panel"  # {"channel_id": …, "message_id": …} – also shown in the panel overview (BOT-14)
BUTTONS = (("start", "Dienst starten", "🟢", discord.ButtonStyle.success),
           ("break", "Pause", "⏸️", discord.ButtonStyle.secondary),
           ("resume", "Weiter", "▶️", discord.ButtonStyle.primary),
           ("end", "Dienst beenden", "🔴", discord.ButtonStyle.danger))


def may_use_duty(guild, user_id) -> bool:
    import webserver
    perms, _ = webserver.compute_perms(guild, user_id, webserver.load_config())
    return bool(perms.get("can_view_dashboard") or perms.get("is_admin"))


async def run_duty_action(interaction: discord.Interaction, action: str) -> None:
    """Shared by the slash commands and the panel buttons."""
    import webserver
    if interaction.guild is None or not may_use_duty(interaction.guild, interaction.user.id):
        await interaction.response.send_message("❌ Den Dienst können nur Teammitglieder starten und beenden.", ephemeral=True)
        return
    if action == "status":
        await interaction.response.send_message("⏱ " + webserver.shift_status_text(interaction.user.id), ephemeral=True)
        return
    ok, message = webserver.apply_shift_action(interaction.user.id, action)
    await interaction.response.send_message(("✅ " if ok else "❌ ") + message, ephemeral=True)
    if ok:
        await webserver.sync_duty_after_shift(interaction.client, interaction.guild, interaction.user.id)
        try:
            import pulse_presence
            await pulse_presence.update(interaction.client)
        except Exception:
            pass


class DutyPanelView(PulseView):
    def __init__(self):
        super().__init__(timeout=None)
        for action, label, emoji, style in BUTTONS:
            button = discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"pulse_dienst:{action}")
            button.callback = self._make_callback(action)
            self.add_item(button)

    @staticmethod
    def _make_callback(action):
        async def callback(interaction: discord.Interaction):
            await run_duty_action(interaction, action)
        return callback


def panel_embed(guild_name: str = "") -> discord.Embed:
    embed = discord.Embed(
        title="⏱ Dienst",
        description=("Starte, pausiere und beende deine Schicht direkt hier – genau wie im Dashboard.\n"
                     "Die Zeit wird gleich gezählt, die Voice-Support-Bereitschaft folgt automatisch.\n\n"
                     "Deinen Stand siehst du mit `/dienst status`."),
        color=discord.Color.from_rgb(91, 92, 240),
    )
    embed.set_footer(text=f"Pulse TeamOS{' · ' + guild_name if guild_name else ''}")
    return embed


class Dienst(commands.GroupCog, group_name="dienst", group_description="Eigene Schicht starten, pausieren und beenden"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="start", description="Startet deine Schicht.")
    async def start(self, interaction: discord.Interaction):
        await run_duty_action(interaction, "start")

    @app_commands.command(name="pause", description="Pausiert deine laufende Schicht.")
    async def pause(self, interaction: discord.Interaction):
        await run_duty_action(interaction, "break")

    @app_commands.command(name="weiter", description="Setzt deine pausierte Schicht fort.")
    async def weiter(self, interaction: discord.Interaction):
        await run_duty_action(interaction, "resume")

    @app_commands.command(name="ende", description="Beendet deine Schicht und schreibt die Zeit gut.")
    async def ende(self, interaction: discord.Interaction):
        await run_duty_action(interaction, "end")

    @app_commands.command(name="status", description="Zeigt, ob du im Dienst bist und deine Zeit diese Woche.")
    async def status(self, interaction: discord.Interaction):
        await run_duty_action(interaction, "status")

    @app_commands.command(name="panel", description="Veröffentlicht das Dienst-Panel in diesem Kanal (nur Admins).")
    @app_commands.default_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction):
        import webserver
        perms, _ = webserver.compute_perms(interaction.guild, interaction.user.id, webserver.load_config()) if interaction.guild else ({}, None)
        if not perms.get("is_admin"):
            await interaction.response.send_message("❌ Nur Admins können das Dienst-Panel veröffentlichen.", ephemeral=True)
            return
        try:
            message = await interaction.channel.send(embed=panel_embed(interaction.guild.name), view=DutyPanelView())
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"❌ Pulse kann hier nicht senden ({type(exc).__name__}). Kanalrechte prüfen.", ephemeral=True)
            return
        pulse_db.set_setting(PANEL_SETTING, {"channel_id": interaction.channel.id, "message_id": message.id})
        webserver.log_audit(interaction.user.display_name, str(interaction.user.id), "Dienst-Panel veröffentlicht",
                            f"Kanal {interaction.channel.id}")
        await interaction.response.send_message("✅ Dienst-Panel veröffentlicht.", ephemeral=True)


async def setup(bot: commands.Bot):
    bot.add_view(DutyPanelView())  # persistent: the buttons keep working after a restart
    await bot.add_cog(Dienst(bot))
