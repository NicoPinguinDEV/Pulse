import discord
from discord.ext import commands
from discord.ui import View, Select, Button

class TicketSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Support", description="Hilfe bei Fragen oder Problemen", emoji="🛠️", value="support"),
            discord.SelectOption(label="Bewerbung", description="Bewirb dich für das Team", emoji="📝", value="bewerbung"),
            discord.SelectOption(label="Admin-Hilfe", description="Wichtige Angelegenheiten", emoji="⚡", value="admin")
        ]
        super().__init__(placeholder="Wähle eine Kategorie für dein Ticket...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="Tickets")
        if not category:
            category = await guild.create_category("Tickets")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }

        # Team-Rolle Berechtigungen optional anpassen (z.B. Team-Rolle ID hier einfügen falls gewollt)
        channel = await guild.create_text_channel(
            f"ticket-{self.values[0]}-{interaction.user.name}",
            category=category,
            overwrites=overwrites
        )

        embed = discord.Embed(
            title=f"Ticket: {self.values[0].capitalize()}",
            description=f"Wilkommen {interaction.user.mention}!\nDas Team wird dir gleich helfen. Nutze den Button unten, um das Ticket zu schließen.",
            color=discord.Color.blue()
        )
        await channel.send(embed=embed, view=CloseTicketView())
        await interaction.response.send_message(f"Dein Ticket wurde erstellt: {channel.mention}", ephemeral=True)

class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())

class CloseTicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ticket schließen", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Ticket wird in 5 Sekunden gelöscht...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="ticketsetup")
    @commands.has_permissions(administrator=True)
    async def ticketsetup(self, ctx):
        embed = discord.Embed(
            title="Support-Tickets",
            description="Wähle im Dropdown-Menü unten die passende Kategorie aus, um ein privates Ticket zu öffnen.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed, view=TicketView())

async def setup(bot):
    import asyncio
    await bot.add_cog(Tickets(bot))
