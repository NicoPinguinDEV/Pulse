import discord
from discord import app_commands
from discord.ext import commands

class MessageDeleteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="messagedelete", 
        description="[Admin] Löscht alle Nachrichten im aktuellen Kanal"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def messagedelete(self, interaction: discord.Interaction):
        # Rückmeldung verzögern, da das Löschen mehrerer Nachrichten kurz dauern kann
        await interaction.response.defer(ephemeral=True)

        try:
            # Löscht alle Nachrichten im aktuellen Kanal
            deleted_messages = await interaction.channel.purge(limit=None)
            
            # Rückmeldung an den Admin (nur für dich sichtbar)
            await interaction.followup.send(
                f"🧹 Es wurden erfolgreich **{len(deleted_messages)}** Nachricht(en) gelöscht!", 
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ **Fehler:** Der Bot benötigt die Berechtigungen `Nachrichten verwalten` und `Nachrichtenverlauf anzeigen`!", 
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Ein Fehler ist aufgetreten: `{e}`", 
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(MessageDeleteCog(bot))
