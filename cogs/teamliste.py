import sqlite3
import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_NAME = "teamliste.db"

# --- CONFIG: ROLLEN-REIHENFOLGE (Namen oder IDs eintragen) ---
HIGHTEAM_ROLES = [
    "Founder",
    "BORP メ Inhaber",
    "BORP メ Stv.Inhaber",
    "BORP メ Owner",
    "BORP メ Co.Owner",
    "BORP メ Leitende Projektleitung",
    "BORP メ Projektleitung",
    "BORP メ Stv.Projektleitung",
    "BORP メ Projektkoordination",
    "BORP メ Stv.Projektkoordination",
    "BORP メ Leitender Direktor",
    "BORP メ Direktor",
    "BORP メ Stv.Direktor",
    "BORP メ Leitende Serverleitung",
    "BORP メ Serverleitung",
    "BORP メ Stv.Serverleitung",
    "BORP メ Leitender Manager",
    "BORP メ Management",
    "BORP メ Stv.Management",
    "BORP メ Leitende Teamleitung",
    "BORP メ Teamleitung",
    "BORP メ Stv.Teamleitung",
    "Teamaufsicht",
    "Supervisor",
    "Ausbilder"
]

TEAM_ROLES = [
    "BORP メ Highteamanwärter",
    "BORP メ Community-Management",
    "BORP メ Stv.Community-Management",
    "BORP メ Fraktionsverwaltung",
    "BORP メ Stv.Fraktionsverwaltung",
    "BORP メ Eventleitung",
    "BORP メ Admin-Leitung",
    "BORP メ Sr.Admin",
    "BORP メ Admin",
    "BORP メ Jr.Admin",
    "BORP メ Mod-Leitung",
    "BORP メ Sr.Moderator",
    "BORP メ Moderator",
    "BORP メ Jr.Moderator",
    "BORP メ Sup-Leitung",
    "BORP メ Sr.Supporter",
    "BORP メ Supporter",
    "BORP メ Test Supporter"
]


# --- DATENBANK HELFER ---
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


class TeamlisteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()
        self.update_list_loop.start()

    def cog_unload(self):
        self.update_list_loop.cancel()

    # Formatiert den Online-Status
    def format_status(self, status: discord.Status) -> str:
        if status == discord.Status.online:
            return "Online"
        elif status == discord.Status.idle:
            return "Abwesend"
        elif status == discord.Status.dnd:
            return "Bitte nicht stören"
        else:
            return "Offline"

    # Generiert den Text für die jeweilige Liste
    def generate_list_text(self, guild: discord.Guild, title: str, role_names: list) -> str:
        lines = [f"**{title} | {guild.name.upper()}**\n"]

        for role_identifier in role_names:
            # Suche Rolle nach ID oder Name
            role = None
            if isinstance(role_identifier, int) or str(role_identifier).isdigit():
                role = guild.get_role(int(role_identifier))
            else:
                role = discord.utils.get(guild.roles, name=role_identifier)

            role_name_display = role.name if role else role_identifier
            lines.append(f"》{role_name_display}")

            if role and role.members:
                for member in role.members:
                    status_text = self.format_status(member.status)
                    lines.append(f"{member.mention}:  {status_text}")
            else:
                lines.append(f"Kein Mitglied des Servers hat die @{role_name_display} Rolle.")
            
            lines.append("")  # Leerzeile

        return "\n".join(lines)

    # Aktualisiert beide Teamlisten-Nachrichten
    async def update_teamlist(self, guild: discord.Guild):
        channel_id = get_config("teamlist_channel_id")
        highteam_msg_id = get_config("highteam_msg_id")
        team_msg_id = get_config("team_msg_id")

        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        highteam_text = self.generate_list_text(guild, "𝐇𝐢𝐠𝐡𝐓𝐞𝐚𝐦-𝐋𝐢𝐬𝐭𝐞", HIGHTEAM_ROLES)
        team_text = self.generate_list_text(guild, "𝐓𝐞𝐚𝐦𝐥𝐢𝐬𝐭𝐞", TEAM_ROLES)

        # HighTeam-Nachricht bearbeiten
        if highteam_msg_id:
            try:
                msg = await channel.fetch_message(highteam_msg_id)
                await msg.edit(content=highteam_text)
            except discord.NotFound:
                new_msg = await channel.send(content=highteam_text)
                set_config("highteam_msg_id", new_msg.id)
        else:
            new_msg = await channel.send(content=highteam_text)
            set_config("highteam_msg_id", new_msg.id)

        # Teamliste-Nachricht bearbeiten
        if team_msg_id:
            try:
                msg = await channel.fetch_message(team_msg_id)
                await msg.edit(content=team_text)
            except discord.NotFound:
                new_msg = await channel.send(content=team_text)
                set_config("team_msg_id", new_msg.id)
        else:
            new_msg = await channel.send(content=team_text)
            set_config("team_msg_id", new_msg.id)

    # AUTOMATISCHER REFRESH (Alle 2 Minuten)
    @tasks.loop(minutes=2)
    async def update_list_loop(self):
        await self.bot.wait_until_ready()
        channel_id = get_config("teamlist_channel_id")
        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel and channel.guild:
                await self.update_teamlist(channel.guild)

    # EVENT-TRIGGER: Aktualisiert sofort, wenn sich Rollen oder der Status von Mitgliedern ändern
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles or before.status != after.status:
            await self.update_teamlist(after.guild)

    # COMMANDS
    @app_commands.command(name="setup_teamliste", description="[Admin] Richtet den Kanal für die automatische Teamliste ein")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(kanal="Der Textkanal, in dem die Teamlisten gesendet & aktualisiert werden")
    async def setup_teamliste(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        set_config("teamlist_channel_id", kanal.id)
        set_config("highteam_msg_id", 0)
        set_config("team_msg_id", 0)

        await interaction.response.send_message(
            f"✅ Teamliste eingerichtet im Kanal {kanal.mention}! Nachrichten werden jetzt generiert...",
            ephemeral=True
        )
        await self.update_teamlist(interaction.guild)

    @app_commands.command(name="update_teamliste", description="[Admin] Erzwingt eine sofortige Aktualisierung der Teamliste")
    @app_commands.checks.has_permissions(administrator=True)
    async def force_update_teamliste(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.update_teamlist(interaction.guild)
        await interaction.followup.send("✅ Teamliste wurde manuell aktualisiert!", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TeamlisteCog(bot))
