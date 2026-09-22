import discord
from discord import app_commands
from discord.ext import commands

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
            custom_id="rp_verwaltung_select"  # Wichtig, damit das Menü nach Bot-Neustarts funktioniert
        )

    async def callback(self, interaction: discord.Interaction):
        # Wenn "RP Start" ausgewählt wird
        if self.values[0] == "rp_start":
            embed = discord.Embed(
                title="🔥 ROLEPLAY GESTARTET",
                description="Das Roleplay ist ab sofort offiziell eröffnet! Viel Spaß allen Beteiligten.",
                color=discord.Color.green()
            )
            # Sendet eine öffentliche Ankündigung in den Kanal
            await interaction.response.send_message(embed=embed)

        # Wenn "RP Stop" ausgewählt wird
        elif self.values[0] == "rp_stop":
            embed = discord.Embed(
                title="🛑 ROLEPLAY BEENDET",
                description="Das Roleplay wurde offiziell beendet. Vielen Dank fürs Mitspielen!",
                color=discord.Color.red()
            )
            # Sendet eine öffentliche Ankündigung in den Kanal
            await interaction.response.send_message(embed=embed)


# --- VIEW (BEHÄLTER FÜR DAS DROPDOWN) ---
class RPView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # Dank timeout=None bleibt das Menü für immer aktiv
        self.add_item(RPSelect())


# --- COG ---
class RPVerwaltungCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Registriert die View beim Bot-Start (überlebt Server-Neustarts)
        self.bot.add_view(RPView())

    @app_commands.command(name="setup_rp", description="[Admin] Erstellt das RP-Verwaltungs-Dashboard")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_rp(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="💻 Roleplay Verwaltung",
            description="Nutze das Menü unten, um das RP offiziell zu starten oder zu beenden.",
            color=discord.Color.blue()
        )
        
        # Falls der Server ein Icon hat, wird es oben rechts als Thumbnail gezeigt
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        # Nachricht mit Embed und Dropdown im Kanal senden
        await interaction.channel.send(embed=embed, view=RPView())
        await interaction.response.send_message("✅ RP-Verwaltungs-Panel wurde erstellt!", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RPVerwaltungCog(bot))
