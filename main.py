import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Token aus .env Datei laden (Sicherheits-Best-Practice)
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# --- DATENBANK SETUP ---
DB_NAME = "abmeldungen.db"

def init_db():
    """Erstellt die Datenbank-Tabelle, falls sie noch nicht existiert."""
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
    conn.commit()
    conn.close()

# Datenbank beim Start initialisieren
init_db()

# --- BOT INITIALISIERUNG ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot online als {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} Slash Commands synchronisiert!")
    except Exception as e:
        print(f"❌ Fehler beim Synchronisieren: {e}")

# --- SLASH COMMANDS ---

# 1. /abmeldung
@bot.tree.command(name="abmeldung", description="Melde dich für einen bestimmten Zeitraum ab")
@app_commands.describe(
    grund="Warum bist du abgemeldet?",
    bis="Bis wann bist du abgemeldet? (z.B. 25.10. oder 2 Wochen)"
)
async def abmeldung(interaction: discord.Interaction, grund: str, bis: str):
    user_id = interaction.user.id
    user_name = interaction.user.display_name

    # In Datenbank speichern oder aktualisieren
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO abmeldungen (user_id, user_name, grund, bis)
        VALUES (?, ?, ?, ?)
    """, (user_id, user_name, grund, bis))
    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="✅ Abmeldung eingetragen",
        color=discord.Color.green()
    )
    embed.add_field(name="Mitglied", value=interaction.user.mention, inline=False)
    embed.add_field(name="Grund", value=grund, inline=True)
    embed.add_field(name="Abgemeldet bis", value=bis, inline=True)
    embed.set_footer(text="Die Abmeldung wurde dauerhaft gespeichert.")

    await interaction.response.send_message(embed=embed)

# 2. /list
@bot.tree.command(name="list", description="Zeigt alle aktuell abgemeldeten Personen an")
async def list_abmeldungen(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, user_name, grund, bis FROM abmeldungen")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("Aktuell ist niemand abgemeldet! 🎉", ephemeral=True)
        return

    embed = discord.Embed(
        title="📋 Aktuelle Abmeldungen",
        description=f"Insgesamt abgemeldet: **{len(rows)}**",
        color=discord.Color.blue()
    )

    for user_id, user_name, grund, bis in rows:
        embed.add_field(
            name=f"👤 {user_name}",
            value=f"**Grund:** {grund}\n**Bis:** {bis}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

# 3. /anmeldung
@bot.tree.command(name="anmeldung", description="Melde dich wieder zurück")
async def anmeldung(interaction: discord.Interaction):
    user_id = interaction.user.id

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (user_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
        await interaction.response.send_message("Willkommen zurück! Du wurdest aus der Liste entfernt. 👋")
    else:
        await interaction.response.send_message("Du warst gar nicht abgemeldet!", ephemeral=True)

# 4. /abmeldung_entfernen (Admin Command)
@bot.tree.command(name="abmeldung_entfernen", description="[Admin] Entferne die Abmeldung eines Mitglieds")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(mitglied="Das Mitglied, dessen Abmeldung gelöscht werden soll")
async def abmeldung_entfernen(interaction: discord.Interaction, mitglied: discord.Member):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (mitglied.id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
        await interaction.response.send_message(f"Die Abmeldung von {mitglied.mention} wurde manuell entfernt.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{mitglied.display_name} war nicht abgemeldet.", ephemeral=True)

# Fehlerbehandlung für fehlende Admin-Rechte
@abmeldung_entfernen.error
async def admin_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Du benötigst Administrator-Rechte für diesen Befehl.", ephemeral=True)

# Bot starten
if __name__ == "__main__":
    if not TOKEN:
        print("❌ FEHLER: Kein DISCORD_TOKEN in der .env-Datei gefunden!")
    else:
        bot.run(TOKEN)
