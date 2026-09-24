import io
import datetime
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands

DB_NAME = "tickets.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
    """)
    conn.commit()
    conn.close()

def set_config(key: str, value: int):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_config(key: str):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ticket Schließen", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 Ticket wird geschlossen und Transkript erstellt...", ephemeral=True)
        channel = interaction.channel

        # Transkript erstellen
        messages = []
        async for msg in channel.history(limit=500, oldest_first=True):
            time_str = msg.created_at.strftime("%d.%m.%Y %H:%M:%S")
            messages.append(f"[{time_str}] {msg.author} ({msg.author.id}): {msg.content}")

        transcript_text = "\n".join(messages)
        file_bytes = transcript_text.encode("utf-8")
        transcript_file = discord.File(io.BytesIO(file_bytes), filename=f"transcript-{channel.name}.txt")

        log_channel_id = get_config("ticket_log_channel_id")
        if log_channel_id:
            log_channel = interaction.guild.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="📜 Ticket Transkript",
                    description=f"Ticket: **{channel.name}**\nGeschlossen von: {interaction.user.mention}",
                    color=discord.Color.red(),
                    timestamp=datetime.datetime.now()
                )
                await log_channel.send(embed=embed, file=transcript_file)

        await channel.delete()


class TicketLaunchView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def create_ticket(self, interaction: discord.Interaction, category_name: str):
        guild = interaction.guild
        category_id = get_config("ticket_category_id")
        support_role_id = get_config("ticket_support_role_id")

        category = guild.get_channel(category_id) if category_id else None
        support_role = guild.get_role(support_role_id) if support_role_id else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel_name = f"{category_name.lower()}-{interaction.user.name}"
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites
        )

        embed = discord.Embed(
            title=f"🎫 {category_name}-Ticket",
            description=f"Hallo {interaction.user.mention},\nvielen Dank für deine Anfrage! Ein Teammitglied wird sich in Kürze um dich kümmern.",
            color=discord.Color.blue()
        )
        await ticket_channel.send(embed=embed, view=TicketCloseView())
        await interaction.response.send_message(f"✅ Dein Ticket wurde erstellt: {ticket_channel.mention}", ephemeral=True)

    @discord.ui.button(label="Support", style=discord.ButtonStyle.primary, emoji="❓", custom_id="ticket_btn_support")
    async def btn_support(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "Support")

    @discord.ui.button(label="Bewerbung", style=discord.ButtonStyle.success, emoji="📝", custom_id="ticket_btn_bewerbung")
    async def btn_bewerbung(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "Bewerbung")

    @discord.ui.button(label="RP-Fehlermeldung", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="ticket_btn_rp")
    async def btn_rp(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.create_ticket(interaction, "RP-Fehler")


class TicketSystemCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(TicketLaunchView())
        self.bot.add_view(TicketCloseView())

    @app_commands.command(name="setup_tickets", description="[Admin] Sendet das Ticket-Panel und konfiguriert das System")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_tickets(
        self, 
        interaction: discord.Interaction, 
        kategorie: discord.CategoryChannel, 
        support_rolle: discord.Role,
        log_kanal: discord.TextChannel
    ):
        set_config("ticket_category_id", kategorie.id)
        set_config("ticket_support_role_id", support_rolle.id)
        set_config("ticket_log_channel_id", log_kanal.id)

        embed = discord.Embed(
            title="🎫 SUPPORT & TICKETS",
            description="Klicke auf einen der Buttons unten, um ein Ticket für dein Anliegen zu öffnen.",
            color=discord.Color.red()
        )
        embed.set_footer(text="Emergency Hamburg Support System")

        await interaction.channel.send(embed=embed, view=TicketLaunchView())
        await interaction.response.send_message("✅ Ticket-Panel erfolgreich eingerichtet!", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketSystemCog(bot))
