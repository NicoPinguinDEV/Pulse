import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

DB_NAME = "abmeldungen.db"

# --- DATENBANK SETUP ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Tabelle für Abmeldungen
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS abmeldungen (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT NOT NULL,
            grund TEXT NOT NULL,
            bis TEXT NOT NULL
        )
    """)
    
    # Tabelle für Bot-Einstellungen (Kanal- & Nachrichten-ID der Liste)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# Hilfsfunktionen für Config
def set_config(key: str, value: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_config(key: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


# --- HELFER-FUNKTION: LISTE LIVE AKTUALISIEREN ---
async def update_live_list(bot_instance: commands.Bot):
    channel_id = get_config("list_channel_id")
    message_id = get_config("list_message_id")

    if not channel_id or not message_id:
        return  # Liste wurde noch nicht mit /setup_liste eingerichtet

    channel = bot_instance.get_channel(channel_id)
    if not channel:
        try:
            channel = await bot_instance.fetch_channel(channel_id)
        except Exception:
            return

    # Daten aus Datenbank laden
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, user_name, grund, bis FROM abmeldungen")
    rows = cursor.fetchall()
    conn.close()

    # Embed aufbauen
    embed = discord.Embed(
        title="📋 Aktuelle Abmeldungen",
        color=discord.Color.blue()
    )

    if not rows:
        embed.description = "*Aktuell ist niemand abgemeldet.*"
    else:
        embed.description = f"Insgesamt abgemeldet: **{len(rows)}**\n───────────────"
        for user_id, user_name, grund, bis in rows:
            embed.add_field(
                name=f"👤 {user_name}",
                value=f"**Grund:** {grund}\n**Bis:** {bis}",
                inline=False
            )

    embed.set_footer(text="Automatisch aktualisiert")

    # Nachricht bearbeiten
    try:
        message = await channel.fetch_message(message_id)
        await message.edit(embed=embed)
    except Exception as e:
        print(f"❌ Fehler beim Aktualisieren der Liste: {e}")


# --- BOT INITIALISIERUNG ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot online als {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 {len(synced)} Slash Commands synchronisiert!")
        # Beim Start Liste einmalig auffrischen
        await update_live_list(bot)
    except Exception as e:
        print(f"❌ Fehler: {e}")


# --- COMMANDS ---

# 1. Admin-Befehl: Erstellt die Nachricht, die ab jetzt immer aktualisiert wird
@bot.tree.command(name="setup_liste", description="[Admin] Erstellt die automatische Abmeldungsliste im aktuellen Kanal")
@app_commands.checks.has_permissions(administrator=True)
async def setup_liste(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Aktuelle Abmeldungen",
        description="*Aktuell ist niemand abgemeldet.*",
        color=discord.Color.blue()
    )
    
    # Sendet die erste Nachricht
    await interaction.response.send_message("Liste wird erstellt...", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    
    # Speichert Kanal- und Nachrichten-ID
    set_config("list_channel_id", interaction.channel_id)
    set_config("list_message_id", msg.id)
    
    await update_live_list(bot)


# 2. /abmeldung (Niemand sieht eine Nachricht im Chat, nur die Liste aktualisiert sich)
@bot.tree.command(name="abmeldung", description="Melde dich für einen bestimmten Zeitraum ab")
@app_commands.describe(
    grund="Warum bist du abgemeldet?",
    bis="Bis wann bist du abgemeldet? (z.B. 25.10. oder 2 Wochen)"
)
async def abmeldung(interaction: discord.Interaction, grund: str, bis: str):
    user_id = interaction.user.id
    user_name = interaction.user.display_name

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO abmeldungen (user_id, user_name, grund, bis)
        VALUES (?, ?, ?, ?)
    """, (user_id, user_name, grund, bis))
    conn.commit()
    conn.close()

    # Nur für den User sichtbar (kein Spam im Chat)
    await interaction.response.send_message("✅ Deine Abmeldung wurde eingetragen und die Liste aktualisiert.", ephemeral=True)
    
    # Feste Liste aktualisieren
    await update_live_list(bot)


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
        await interaction.response.send_message("👋 Willkommen zurück! Du wurdest aus der Liste entfernt.", ephemeral=True)
        await update_live_list(bot)
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
        await interaction.response.send_message(f"Die Abmeldung von {mitglied.mention} wurde entfernt.", ephemeral=True)
        await update_live_list(bot)
    else:
        await interaction.response.send_message(f"{mitglied.display_name} war nicht abgemeldet.", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        print("❌ FEHLER: Kein DISCORD_TOKEN in der .env-Datei gefunden!")
    else:
        bot.run(TOKEN)
