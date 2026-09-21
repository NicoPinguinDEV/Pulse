import discord
from discord import app_commands
from discord.ext import commands
import asyncio

# --- DROPDOWN FÜR TICKET-KATEGORIEN ---
class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Allgemeiner Support",
                description="Fragen & Hilfe zum Server oder Bot",
                emoji="💬",
                value="support"
            ),
            discord.SelectOption(
                label="Bug / Fehler melden",
                description="Melde ein technisches Problem",
                emoji="🐛",
                value="bug"
            ),
            discord.SelectOption(
                label="Bewerbung / Sonstiges",
                description="Bewerbe dich fürs Team oder sonstiges Anliegen",
                emoji="📩",
                value="apply"
            )
        ]
        super().__init__(
            placeholder="Wähle eine Ticket-Kategorie aus...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="galaxy_ticket_select"
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        category_type = self.values[0]

        # Kanalname formatieren (z. B. ticket-username)
        channel_name = f"ticket-{user.name}".lower().replace(" ", "-")

        # Prüfen, ob der Nutzer bereits ein offenes Ticket hat
        existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
        if existing_channel:
            await interaction.response.send_message(
                f"❌ Du hast bereits ein offenes Ticket: {existing_channel.mention}",
                ephemeral=True
            )
            return

        # Rechte für den neuen Ticket-Kanal festlegen
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                attach_files=True,
                embed_links=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_channels=True
            )
        }

        # Ticket-Kanal erstellen
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            topic=f"Ticket von {user.name} ({user.id}) | Kategorie: {category_type}"
        )

        # Willkommens-Embed im Ticket-Kanal
        embed = discord.Embed(
            title=f"📩 Support Ticket — {category_type.capitalize()}",
            description=(
                f"Hallo {user.mention}!\n\n"
                f"Vielen Dank für deine Anfrage. Ein Teammitglied wird sich in Kürze um dich kümmern.\n\n"
                f"**Bitte beschreibe dein Anliegen so genau wie möglich.**"
            ),
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Klicke auf den Button unten, um das Ticket zu schließen.")

        # Nachricht mit Schließen-Button senden
        await ticket_channel.send(embed=embed, view=CloseTicketView())

        # Bestätigung an den Nutzer senden (nur für ihn sichtbar)
        await interaction.response.send_message(
            f"✅ Dein Ticket wurde erstellt: {ticket_channel.mention}",
            ephemeral=True
        )


# --- TICKET PANEL VIEW (DAUERHAFT AKTIV) ---
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


# --- BUTTON ZUM SCHLIESSEN DES TICKETS ---
class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ticket schließen",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="galaxy_close_ticket_btn"
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🔒 Ticket wird geschlossen",
            description="Dieses Ticket wird in **5 Sekunden** automatisch gelöscht...",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        await asyncio.sleep(5)
        await interaction.channel.delete()


# --- COG-KLASSE ---
class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Registriert die Views, damit Buttons auch nach einem Bot-Neustart funktionieren
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(CloseTicketView())

    @app_commands.command(name="ticket-panel", description="Sendet das Ticket-Support-Panel in den Kanal")
    @app_commands.default_permissions(administrator=True)
    async def send_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎫 Support & Help Desk",
            description=(
                "Benötigst du Hilfe, möchtest einen Fehler melden oder hast eine Frage?\n\n"
                "Wähle im Menü unten die passende Kategorie aus, um ein virtuelles Ticket zu öffnen. "
                "Ein privater Kanal wird für dich und unser Team erstellt."
            ),
            color=discord.Color.blurple()
        )
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        await interaction.channel.send(embed=embed, view=TicketPanelView())
        await interaction.response.send_message("✅ Ticket-Panel wurde erfolgreich erstellt!", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
