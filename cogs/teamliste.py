import sqlite3
import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_NAME = "teamliste.db"

# --- CONFIG: ROLLEN-REIHENFOLGE ---
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id INTEGER
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

def get_saved_message_ids():
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT msg_id FROM messages ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

def save_message_ids(msg_ids: list[int]):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages")
    for m_id in msg_ids:
        cursor.execute("INSERT INTO messages (msg_id) VALUES (?)", (m_id,))
    conn.commit()
    conn.close()


# --- HELFER ZUM ZERLEGEN VON TEXTEN (unter 2000 Zeichen) ---
def chunk_text(text: str, limit: int = 1850) -> list[str]:
    lines = text.split("\n")
    chunks = []
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > limit:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


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
            
            lines.append("")

        return "\n".join(lines)

    # Aktualisiert alle Nachrichten im Kanal
    async def update_teamlist(self, guild: discord.Guild):
        channel_id = get_config("teamlist_channel_id")
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        highteam_text = self.generate_list_text(guild, "𝐇𝐢𝐠𝐡𝐓𝐞𝐚𝐦-𝐋𝐢𝐬𝐭𝐞", HIGHTEAM_ROLES)
        team_text = self.generate_list_text(guild, "𝐓𝐞𝐚𝐦𝐥𝐢𝐬𝐭𝐞", TEAM_ROLES)

        # In Teilen von max. 1850 Zeichen aufteilen
        all_chunks = chunk_text(highteam_text) + chunk_text(team_text)

        old_msg_ids = get_saved_message_ids()
        new_msg_ids = []

        for idx, chunk in enumerate(all_chunks):
            msg = None
            if idx < len(old_msg_ids):
                try:
                    msg = await channel.fetch_message(old_msg_ids[idx])
                    await msg.edit(content=chunk)
                except discord.NotFound:
                    msg = await channel.send(content=chunk)
                except Exception:
                    msg = await channel.send(content=chunk)
            else:
                msg = await channel.send(content=chunk)

            if msg:
                new_msg_ids.append(msg.id)

        # Überflüssige alte Nachrichten löschen, falls die Liste kürzer wurde
        if len(old_msg_ids) > len(all_chunks):
            for old_id in old_msg_ids[len(all_chunks):]:
                try:
                    msg_to_del = await channel.fetch_message(old_id)
                    await msg_to_del.delete()
                except Exception:
                    pass

        save_message_ids(new_msg_ids)

    # AUTOMATISCHER REFRESH (Alle 2 Minuten)
    @tasks.loop(minutes=2)
    async def update_list_loop(self):
        await self.bot.wait_until_ready()
        channel_id = get_config("teamlist_channel_id")
        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel and channel.guild:
                await self.update_teamlist(channel.guild)

    # EVENT-TRIGGER
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
        save_message_ids([])  # Nachrichten-Liste zurücksetzen

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
