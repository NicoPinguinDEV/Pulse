import sqlite3
import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_NAME = "teamliste.db"


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
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role_id INTEGER UNIQUE,
            category TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            key TEXT PRIMARY KEY,
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

# Rollen-Verwaltung in DB
def add_role_to_db(role_id: int, category: str):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO roles (role_id, category) VALUES (?, ?)", (role_id, category))
    conn.commit()
    conn.close()

def remove_role_from_db(role_id: int):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM roles WHERE role_id = ?", (role_id,))
    conn.commit()
    conn.close()

def get_roles_by_category(category: str) -> list[int]:
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT role_id FROM roles WHERE category = ? ORDER BY id ASC", (category,))
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]

# Nachricht-IDs speichern
def set_msg_id(key: str, msg_id: int):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO messages (key, msg_id) VALUES (?, ?)", (key, msg_id))
    conn.commit()
    conn.close()

def get_msg_id(key: str):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT msg_id FROM messages WHERE key = ?", (key,))
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

    # Formatiert den Online-Status mit Status-Punkten
    def format_status(self, status: discord.Status) -> str:
        if status == discord.Status.online:
            return "🟢 Online"
        elif status == discord.Status.idle:
            return "🟡 Abwesend"
        elif status == discord.Status.dnd:
            return "🔴 Bitte nicht stören"
        else:
            return "⚪ Offline"

    # Erstellt das Embed im SCNX-Stil mit rotem Rand
    def create_team_embed(self, guild: discord.Guild, title: str, category: str) -> discord.Embed:
        role_ids = get_roles_by_category(category)
        lines = []

        if not role_ids:
            lines.append(f"*Keine Rollen für {title} konfiguriert. Nutze `/teamrolle_hinzufuegen`.*")
        else:
            for role_id in role_ids:
                role = guild.get_role(role_id)
                if not role:
                    continue

                lines.append(f"❯ **{role.name}**")

                if role.members:
                    for member in role.members:
                        status_text = self.format_status(member.status)
                        lines.append(f"• {member.mention}:  {status_text}")
                else:
                    lines.append(f"Kein Mitglied des Servers hat die {role.mention} Rolle.")
                
                lines.append("")

        description_text = "\n".join(lines)
        
        # Rot gefärbtes Embed
        embed = discord.Embed(
            title=f"{title} | {guild.name.upper()}",
            description=description_text[:4000],
            color=discord.Color.red()
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.set_footer(text="Powered by TeamBot ⚡")
        return embed

    # Aktualisiert die 3 Embed-Nachrichten
    async def update_teamlist(self, guild: discord.Guild):
        channel_id = get_config("teamlist_channel_id")
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        # 3 Embeds generieren
        categories = [
            ("fuehrungsebene", "Führungsebenen-Liste"),
            ("highteam", "HighTeam-Liste"),
            ("lowteam", "LowTeam-Liste")
        ]

        for key, title in categories:
            embed = self.create_team_embed(guild, title, key)
            msg_id = get_msg_id(key)

            if msg_id:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(embed=embed)
                except discord.NotFound:
                    new_msg = await channel.send(embed=embed)
                    set_msg_id(key, new_msg.id)
            else:
                new_msg = await channel.send(embed=embed)
                set_msg_id(key, new_msg.id)

    # LOOP (Alle 2 Minuten)
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
    @app_commands.command(name="setup_teamliste", description="[Admin] Richtet den Kanal für die Teamlisten ein")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_teamliste(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        set_config("teamlist_channel_id", kanal.id)
        set_msg_id("fuehrungsebene", 0)
        set_msg_id("highteam", 0)
        set_msg_id("lowteam", 0)

        await interaction.response.send_message(
            f"✅ Teamliste-Kanal auf {kanal.mention} gesetzt! Generiere Embeds...",
            ephemeral=True
        )
        await self.update_teamlist(interaction.guild)

    @app_commands.command(name="teamrolle_hinzufuegen", description="[Admin] Fügt eine Rolle zur Teamliste hinzu")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(liste=[
        app_commands.Choice(name="Führungsebenen-Liste", value="fuehrungsebene"),
        app_commands.Choice(name="HighTeam-Liste", value="highteam"),
        app_commands.Choice(name="LowTeam-Liste", value="lowteam")
    ])
    async def add_role(self, interaction: discord.Interaction, liste: app_commands.Choice[str], rolle: discord.Role):
        add_role_to_db(rolle.id, liste.value)
        await interaction.response.send_message(
            f"✅ Rolle {rolle.mention} wurde zur **{liste.name}** hinzugefügt!",
            ephemeral=True
        )
        await self.update_teamlist(interaction.guild)

    @app_commands.command(name="teamrolle_entfernen", description="[Admin] Entfernt eine Rolle aus der Teamliste")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_role(self, interaction: discord.Interaction, rolle: discord.Role):
        remove_role_from_db(rolle.id)
        await interaction.response.send_message(
            f"🗑️ Rolle {rolle.mention} wurde entfernt!",
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
