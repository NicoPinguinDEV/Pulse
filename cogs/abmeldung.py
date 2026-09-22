import sqlite3
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands

DB_NAME = "abmeldungen.db"

class AbmeldungCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.init_db()

    # --- DATENBANK ---
    def init_db(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS abmeldungen (
                user_id INTEGER PRIMARY KEY,
                user_name TEXT NOT NULL,
                grund TEXT NOT NULL,
                bis TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value INTEGER
            )
        """)
        conn.commit()
        conn.close()

    def set_config(self, key: str, value: int):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()

    def get_config(self, key: str):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    # --- LIVE-LISTE AKTUALISIEREN ---
    async def update_live_list(self):
        channel_id = self.get_config("list_channel_id")
        message_id = self.get_config("list_message_id")

        if not channel_id or not message_id:
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, user_name, grund, bis FROM abmeldungen")
        rows = cursor.fetchall()
        conn.close()

        # Rotes Embed mit Zeitstempel
        embed = discord.Embed(
            title="📌 AKTUELLE ABMELDUNGEN",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )

        if not rows:
            embed.description = ">>> *Aktuell liegen keine Abmeldungen vor.*"
        else:
            embed.description = f"Anzahl der Abmeldungen: **{len(rows)}**\n───────────────"
            for user_id, user_name, grund, bis in rows:
                embed.add_field(
                    name=f"👤 {user_name}",
                    value=(
                        f"┣ 📝 **Grund:** {grund}\n"
                        f"┗ 📅 **Bis:** {bis}\n"
                    ),
                    inline=False
                )

        embed.set_footer(text="Zuletzt aktualisiert")

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=embed)
        except Exception as e:
            print(f"❌ Fehler beim Aktualisieren der Liste: {e}")

    # --- COMMANDS ---

    @app_commands.command(name="setup_liste", description="[Admin] Erstellt die automatische Abmeldungsliste im aktuellen Kanal")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_liste(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📌 AKTUELLE ABMELDUNGEN",
            description=">>> *Aktuell liegen keine Abmeldungen vor.*",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.set_footer(text="Zuletzt aktualisiert")
        
        await interaction.response.send_message("Liste wird erstellt...", ephemeral=True)
        msg = await interaction.channel.send(embed=embed)
        
        self.set_config("list_channel_id", interaction.channel_id)
        self.set_config("list_message_id", msg.id)
        
        await self.update_live_list()

    @app_commands.command(name="abmeldung", description="Melde dich für einen bestimmten Zeitraum ab")
    @app_commands.describe(
        grund="Warum bist du abgemeldet?",
        bis="Format: TT.MM.JJJJ (z.B. 25.09.2026)"
    )
    async def abmeldung(self, interaction: discord.Interaction, grund: str, bis: str):
        try:
            datum_obj = datetime.strptime(bis, "%d.%m.%Y")
            bis_formatted = datum_obj.strftime("%d.%m.%Y")
        except ValueError:
            await interaction.response.send_message(
                "❌ **Ungültiges Datumsformat!** Bitte benutze genau das Format `TT.MM.JJJJ` (z. B. `25.09.2026`).",
                ephemeral=True
            )
            return

        user_id = interaction.user.id
        user_name = interaction.user.display_name

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO abmeldungen (user_id, user_name, grund, bis)
            VALUES (?, ?, ?, ?)
        """, (user_id, user_name, grund, bis_formatted))
        conn.commit()
        conn.close()

        await interaction.response.send_message(f"✅ Deine Abmeldung bis zum **{bis_formatted}** wurde eingetragen.", ephemeral=True)
        await self.update_live_list()

    @app_commands.command(name="anmeldung", description="Melde dich wieder zurück")
    async def anmeldung(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (user_id,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()

        if deleted > 0:
            await interaction.response.send_message("👋 Willkommen zurück! Du wurdest aus der Liste entfernt.", ephemeral=True)
            await self.update_live_list()
        else:
            await interaction.response.send_message("Du warst gar nicht abgemeldet!", ephemeral=True)

    @app_commands.command(name="abmeldung_entfernen", description="[Admin] Entferne die Abmeldung eines Mitglieds")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(mitglied="Das Mitglied, dessen Abmeldung gelöscht werden soll")
    async def abmeldung_entfernen(self, interaction: discord.Interaction, mitglied: discord.Member):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (mitglied.id,))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()

        if deleted > 0:
            await interaction.response.send_message(f"Die Abmeldung von {mitglied.mention} wurde entfernt.", ephemeral=True)
            await self.update_live_list()
        else:
            await interaction.response.send_message(f"{mitglied.display_name} war nicht abgemeldet.", ephemeral=True)

    @app_commands.command(name="list", description="Zeigt die Abmeldungsliste an")
    async def old_list(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "ℹ️ Der Befehl `/list` wird nicht mehr benötigt! Die Liste wird jetzt automatisch im festgelegten Kanal aktualisiert.",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(AbmeldungCog(bot))
