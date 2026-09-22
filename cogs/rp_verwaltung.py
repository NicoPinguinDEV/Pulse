import discord
from discord import app_commands
from discord.ext import commands

# --- POP-UP FENSTER FÜR RP STOP (Uhrzeit abfragen) ---
class RPStopModal(discord.ui.Modal, title="RP Stop - Nächster RP Start"):
    uhrzeit = discord.ui.TextInput(
        label="Geplanter RP-Start für morgen:",
        placeholder="z.B. 14:30 Uhr oder Offen",
        default="14:30 Uhr",
        max_length=30
    )

    async def on_submit(self, interaction: discord.Interaction):
        msg_content = (
            "#  :minus:   RP Stop  :minus:  \n\n"
            "> **Das RP wird hiermit offiziell gestopt!**\n"
            "> ***Informationen zu dem folgenden Tag***\n"
            f"> **:RPStart: Geplanter RP-Start: {self.uhrzeit.value}** \n"
            "> # Kommt gerne morgen wieder auf den Server!\n"
            "@everyone"
        )
        await interaction.response.send_message(
            content=msg_content,
            allowed_mentions=discord.AllowedMentions(everyone=True)
        )

# --- DROPDOWN MENÜ ---
class RPSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="RP Start",
                value="rp_start",
                description="Eröffnet das heutige Roleplay",
                emoji="🔥"
            ),
            discord.SelectOption(
                label="RP Stop",
                value="rp_stop",
                description="Beendet das aktuelle Roleplay",
                emoji="🦆"
            )
        ]
        super().__init__(
            placeholder="Wähle eine RP-Aktion aus...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="rp_verwaltung_select"
        )

    async def callback(self, interaction: discord.Interaction):
        # RP START ANKÜNDIGUNG
        if self.values[0] == "rp_start":
            msg_content = (
                "#  :check:  RP Start  :check:  \n\n"
                "> **Das RP wird hiermit offiziell eröffnet!**\n"
                "> ***Informationen zu dem folgenden Tag***\n"
                "> **:RPStart: Geplanter RP-Stop: Offen** \n"
                "> # Kommt gerne auf den Server!\n"
                "@everyone"
            )
            await interaction.response.send_message(
                content=msg_content,
                allowed_mentions=discord.AllowedMentions(everyone=True)
            )

        # RP STOP ANKÜNDIGUNG (Öffnet Modal für Uhrzeit)
        elif self.values[0] == "rp_stop":
            await interaction.response.send_modal(RPStopModal())


# --- VIEW ---
class RPView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RPSelect())


# --- COG ---
class RPVerwaltungCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(RPView())

    @app_commands.command(name="setup_rp", description="[Admin] Erstellt das RP-Verwaltungs-Dashboard")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_rp(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="💻 Roleplay Verwaltung",
            description="Nutze das Menü unten, um das RP offiziell zu starten oder zu beenden.",
            color=discord.Color.blue()
        )
        
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        await interaction.channel.send(embed=embed, view=RPView())
        await interaction.response.send_message("✅ RP-Verwaltungs-Panel wurde erstellt!", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RPVerwaltungCog(bot))
